"""Route solver development probe: solves, checks and renders routes on (a) the organizer examples scene with synthetic
targets and (b) the site prediction with its waste boxes plus the POI agent's challenge inspection points, runs the
check_route controls that must fail, and bounds the tour with Held-Karp. Predictions are relabelled `source: dev`
here, for development only, so the solver sees them as the corrected world; the export path never does this.
Writes data/generated/work/route/. Run from backend/:
uv run --frozen python ../research/probes/route_probe.py [controls] [examples] [site] [render:<name>]"""

import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

from marcaj import route as solver
from marcaj.cvat import build_scene
from marcaj.mosaic import load_mosaic
from marcaj.routing import _geometries, _length_in, check_route, load_constraints, passable_space
from marcaj.tiles import DATA_DIR, REPO_ROOT

OUT = REPO_ROOT / "data" / "generated" / "work" / "route"
OUT.mkdir(parents=True, exist_ok=True)
PREDICTIONS = REPO_ROOT / "data" / "generated" / "predictions.geojson"
POI = REPO_ROOT / "data" / "generated" / "work" / "poi" / "poi.geojson"
WASTE = REPO_ROOT / "data" / "generated" / "work" / "waste" / "waste.geojson"
ROUTES = REPO_ROOT / "data" / "generated" / "routes.geojson"  # the client's dev routes until Sunday's export replaces them
EXAMPLES = DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"
EXTRA_ZOOMS = {"site": [("south_lanes", 630222.0, 5219366.0), ("small_passage", 630074.0, 5219407.0)]}


def site_features() -> list[dict]:
    """The site prediction as if corrected; waste boxes from the waste agent's latest file when there is one."""
    predicted = json.loads(PREDICTIONS.read_text())["features"]
    if WASTE.is_file():
        predicted = [f for f in predicted if f["properties"]["label"] != "waste"] + json.loads(WASTE.read_text())["features"]
    return [{**f, "properties": {**f["properties"], "source": "dev"}} for f in predicted] + load_constraints(DATA_DIR / "02_route")


def controls() -> None:
    """Checks that must fail, on the site prediction."""
    features = site_features()
    start = _geometries(features, "start")[0]
    far = Point(629858.7, 5219651.8)  # an old waste candidate in P25's canopy, 28.7 m from any passage
    straight = check_route(LineString([start, far, start]), features)
    print(f"control 1, straight START -> far box -> START: {straight['outside_share']:.1%} outside, legal={straight['legal']} (must be False)")
    assert not straight["legal"]
    spur_line = LineString([(start.x, start.y), (start.x, start.y - 30), (start.x + 40, start.y - 30), (start.x, start.y - 30), (start.x, start.y)])
    spur = check_route(spur_line, features)
    dissolved = spur_line.difference(passable_space(features)).length
    print(f"control 2, a retraced spur: per-segment outside {spur['outside_m']:.1f} m against {dissolved:.1f} m from the old whole-line overlay (must differ)")
    assert spur["outside_m"] > dissolved + 1
    open_line = check_route(LineString([start, (start.x + 8, start.y)]), features)
    print(f"control 3, a route ending 8 m from START: closed={open_line['closed']} (must be False)")
    assert not open_line["closed"]


def held_karp(cost: np.ndarray, upper: float, iterations: int = 400) -> float:
    """Held-Karp 1-tree lower bound on a closed tour through every node of the symmetric `cost`."""
    n = len(cost)
    if n < 3:
        return float(2 * cost[0, 1:].sum())
    pi, best, rate = np.zeros(n), 0.0, 2.0
    for _ in range(iterations):
        weight = cost + pi[:, None] + pi[None, :]
        inner = weight[1:, 1:]
        tree, near, parent, degree, total = np.zeros(n - 1, bool), inner[0].copy(), np.zeros(n - 1, int), np.zeros(n, int), 0.0
        tree[0] = True
        for _ in range(n - 2):
            j = int(np.where(tree, np.inf, near).argmin())
            total += near[j]
            degree[j + 1] += 1
            degree[parent[j] + 1] += 1
            tree[j] = True
            closer = (inner[j] < near) & ~tree
            near, parent = np.where(closer, inner[j], near), np.where(closer, j, parent)
        two = np.argsort(weight[0, 1:])[:2]
        total += weight[0, 1:][two].sum()
        degree[two + 1] += 1
        degree[0] = 2
        bound = total - 2 * pi.sum()
        best = max(best, bound)
        gradient = degree - 2
        if not gradient.any():
            break
        pi += rate * (upper - bound) / (gradient @ gradient) * gradient
        rate *= 0.99
    return best


def bounds(features: list[dict], rows: list[dict], targets: list[dict], report: dict) -> tuple[float, float]:
    """Held-Karp bounds over START and the attachment cells of every visited target: on the grid's plain shortest-path
    metres (a bound on any grid walk's length, outside metres free), and on the solver's own costs (against the tour's
    cost, the ordering's optimality gap)."""
    grid = solver.build_grid(features)
    plain = grid.graph.copy()
    entry_row = np.repeat(np.arange(plain.shape[0]), np.diff(plain.indptr))
    plain.data = np.hypot(grid.rows[entry_row] - grid.rows[plain.indices], grid.cols[entry_row] - grid.cols[plain.indices]) * solver.GRID_M
    start = _geometries(features, "start")[0]
    row, col = solver._cell(grid, start)
    groups = [[int(grid.node[int(row), int(col)])]]
    by_id = {target["id"]: target for target in targets}
    groups += [solver._attach(grid, by_id[item["id"]]["point"]) for item in rows if item["status"] == "visited" and item["reason"] == ""]
    stops = np.array([node for group in groups for node in group])
    index, members = 0, []
    for group in groups:
        members.append(np.arange(index, index + len(group)))
        index += len(group)
    result = []
    for graph, upper in ((plain, report["length_m"]), (grid.graph, report["tour_cost"])):
        distance = dijkstra(graph, indices=stops)[:, stops]
        group_cost = np.array([[distance[np.ix_(a, b)].min() for b in members] for a in members])
        result.append(held_karp(group_cost, upper))
    return result[0], result[1]


def render(name: str, features: list[dict], line: LineString, rows: list[dict], zooms: list[tuple[str, float, float]]) -> None:
    rgb, transform = load_mosaic()
    passages = unary_union(_geometries(features, "passage"))
    interrows = unary_union(_geometries(features, "interrow_area"))
    forbidden = unary_union(_geometries(features, "forbidden"))
    start = _geometries(features, "start")[0]
    colours = {"visited": (40, 230, 60), "over_budget": (255, 150, 0), "unreachable": (255, 0, 255), "missed": (255, 255, 255)}

    def draw_view(left: float, top: float, metres_px: float, width: int, height: int, path: Path, title: str) -> None:
        step = metres_px / transform.a
        c0, r0 = ~transform * (left, top)
        cols = np.clip((c0 + np.arange(width) * step).astype(int), 0, rgb.shape[2] - 1)
        rws = np.clip((r0 + np.arange(height) * step).astype(int), 0, rgb.shape[1] - 1)
        image = Image.fromarray(np.moveaxis(rgb[:, rws][:, :, cols], 0, -1)).convert("RGB")
        pix = lambda coords: [((x - left) / metres_px, (top - y) / metres_px) for x, y in coords]
        view = Affine(metres_px, 0, left, 0, -metres_px, top)
        pixels = np.asarray(image).astype(float)
        for geometry, fill in ((forbidden, (255, 0, 0, 0.25)), (passages, (255, 220, 0, 0.35)), (interrows, (0, 255, 255, 0.3))):
            if not geometry.is_empty:
                mask = rasterize([geometry], out_shape=(height, width), transform=view).astype(bool)
                pixels[mask] = pixels[mask] * (1 - fill[3]) + np.array(fill[:3]) * fill[3]
        image = Image.fromarray(pixels.astype(np.uint8))
        draw = ImageDraw.Draw(image, "RGBA")
        widths = max(2, int(0.25 / metres_px))
        draw.line(pix(line.coords), fill=(255, 30, 30, 255), width=widths)
        radius = max(3, 2.0 / metres_px)
        for item in rows:
            x, y = pix([(item["x"], item["y"])])[0]
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=colours[item["status"]] + (255,), width=2)
        x, y = pix([(start.x, start.y)])[0]
        draw.ellipse((x - 3 * radius, y - 3 * radius, x + 3 * radius, y + 3 * radius), outline=(255, 255, 0, 255), width=3)
        draw.rectangle((0, 0, 11 * len(title), 18), fill=(0, 0, 0, 160))
        draw.text((4, 3), title, fill=(255, 255, 255, 255))
        image.save(path, quality=88)
        print("rendered", path)

    left, bottom, right, top = unary_union(_geometries(features, "study_area")).bounds
    draw_view(left, top, 1.0, int(right - left), int(top - bottom), OUT / f"{name}_site.jpg",
              f"{name}: route {line.length:,.0f} m, red; targets visited green, over budget orange, unreachable magenta")
    for tag, x, y in zooms:
        draw_view(x - 35, y + 35, 0.07, 1000, 1000, OUT / f"{name}_zoom_{tag}.jpg", f"{name} {tag} ({x:.0f}, {y:.0f})")


def zoom_points(features: list[dict], line: LineString, rows: list[dict]) -> list[tuple[str, float, float]]:
    space = passable_space(features)
    outside = line.difference(space)
    pieces = sorted(getattr(outside, "geoms", [outside]), key=lambda piece: -piece.length)
    zooms = [("start", *_geometries(features, "start")[0].coords[0])]
    zooms += [(f"outside{i}_{piece.length:.0f}m", *piece.interpolate(0.5, normalized=True).coords[0]) for i, piece in enumerate(pieces[:2]) if piece.length > 0]
    interrows = unary_union(_geometries(features, "interrow_area"))
    in_lane = [item for item in rows if item["status"] == "visited" and interrows.distance(Point(item["x"], item["y"])) < 1.0]
    zooms += [(f"lane_{item['id']}", item["x"], item["y"]) for item in in_lane[: len(in_lane): max(1, len(in_lane) // 2)][:2]]
    zooms += [(f"{item['status']}_{item['id']}", item["x"], item["y"]) for item in rows if item["status"] != "visited"][:1]
    return zooms


def run(name: str, features: list[dict], targets: list[dict], cutoffs: tuple[float | None, ...] = (None,)) -> None:
    """Routes per POI confidence cutoff (the first is the one bounded and rendered), with stress tests."""
    started = time.perf_counter()
    passages = unary_union(_geometries(features, "passage")).difference(unary_union(_geometries(features, "forbidden")))
    short = passages.union(unary_union(_geometries(features, "interrow_area")).difference(passages.buffer(1.0)))
    solved = solver.routes_by_confidence(features, targets, cutoffs)
    print(f"\n== {name}: {len(targets)} targets, {len(cutoffs)} routes in {time.perf_counter() - started:.1f} s")
    for feature, line, rows, report in solved:
        by_status: dict[str, int] = {}
        for item in rows:
            by_status[item["status"]] = by_status.get(item["status"], 0) + 1
        gaps = [item for item in rows if item["gap_ends_m"] and item["status"] == "visited"]
        covered = sum(all(float(value) <= 2.0 for value in item["gap_ends_m"].split()) for item in gaps)
        print(f"confidence >= {feature['properties']['min_confidence']}: {report['length_m']:.0f} m, {by_status}, gap ends within 2 m on "
              f"{covered}/{len(gaps)} visited gaps, outside {report['outside_share']:.2%} of the scene's passable space, "
              f"{report['robust_outside_share']:.2%} of the robust one, {1 - _length_in(line, short) / line.length:.2%} if inter-rows "
              f"stopped 1 m short of passages; legal {report['legal']}, closed {report['closed']}; tour {report['tour_s']} s")
    if name == "site":
        ROUTES.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": [item[0] for item in solved if item[0]["properties"]["legal"] and item[0]["properties"]["closed"]]}) + "\n")
        print("wrote", ROUTES)
    feature, line, rows, report = solved[0]
    print({key: round(value, 4) if isinstance(value, float) else value for key, value in report.items()})
    for item in rows:
        if item["status"] not in ("visited", "over_budget"):
            print("  ", item)
    bound, cost_bound = bounds(features, rows, targets, report)
    print(f"route {report['length_m']:.1f} m against a Held-Karp bound of {bound:.1f} m on the grid's plain shortest paths "
          f"(gap at most {report['length_m'] / bound - 1:.1%}); tour cost {report['tour_cost']:.1f} against its own Held-Karp "
          f"bound {cost_bound:.1f} (ordering gap at most {report['tour_cost'] / cost_bound - 1:.1%})")
    (OUT / f"{name}_route.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "crs": "EPSG:32635",
        "features": [{"type": "Feature", "geometry": line.__geo_interface__, "properties": {"label": "route", "length_m": round(line.length, 3), **{k: v for k, v in report.items() if not isinstance(v, (list, dict))}, "bound_m": round(bound, 1)}}]
        + [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [item["x"], item["y"]]}, "properties": item} for item in rows],
    }, indent=1, default=float))
    render(name, features, line, rows, zoom_points(features, line, rows) + EXTRA_ZOOMS.get(name, []))


def examples() -> None:
    """The organizer examples with synthetic targets: 6 points on row axes per tile, a box in a passage, and a box 20 m
    into a forbidden zone that must come out unreachable."""
    features = [f for f in build_scene([EXAMPLES], [])["features"] if f["properties"]["label"] != "tile"]
    rng = np.random.default_rng(7)
    rows = _geometries(features, "row")
    targets = [{"id": f"SYN-ROW-{i}", "label": "inspection", "point": rows[k].interpolate(rng.uniform(0.1, 0.9), normalized=True)}
               for i, k in enumerate(rng.choice(len(rows), 12, replace=False))]
    start = _geometries(features, "start")[0]
    forbidden = unary_union(_geometries(features, "forbidden"))
    passage = unary_union(_geometries(features, "passage"))
    targets.append({"id": "SYN-PASSAGE", "label": "waste", "point": passage.intersection(start.buffer(120)).representative_point()})
    deep = next(p for p in (Point(start.x + dx, start.y + dy) for dx in range(0, 400, 10) for dy in range(0, 400, 10)) if p.buffer(20).within(forbidden))
    targets.append({"id": "SYN-FORBIDDEN", "label": "waste", "point": deep})
    run("examples", features, targets)


def rerender(name: str) -> None:
    saved = json.loads((OUT / f"{name}_route.geojson").read_text())["features"]
    features = site_features() if name != "examples" else [f for f in build_scene([EXAMPLES], [])["features"] if f["properties"]["label"] != "tile"]
    line, rows = shape(saved[0]["geometry"]), [item["properties"] for item in saved[1:]]
    render(name, features, line, rows, zoom_points(features, line, rows) + EXTRA_ZOOMS.get(name, []))


if __name__ == "__main__":
    steps = sys.argv[1:] or ["controls", "examples", "site"]
    for step in steps:
        if step.startswith("render:"):
            rerender(step.split(":", 1)[1])
    if "controls" in steps:
        controls()
    if "examples" in steps:
        examples()
    if "site" in steps:
        features = site_features()
        run("site", features, solver.route_target_features(features) + solver.poi_targets(POI), (None, 0.5, 0.7))
