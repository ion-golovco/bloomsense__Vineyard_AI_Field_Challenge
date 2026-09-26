"""Row hops and on-request endpoints: the hop penalty sweep, open-route checks and the controls that must fail, on the
world the client's routes plan on (`route.planning_features` of the app's scene). Writes data/generated/work/route_v2/.
Run from backend/: uv run --frozen python ../research/probes/route_hops_probe.py [smoke] [sweep] [controls] [regenerate]"""

import json
import sys
import time

import numpy as np
import shapely
from shapely.geometry import LineString, Point

from marcaj import route as solver
from marcaj.routing import CROSSING_TOLERANCE_M, _geometries, check_route, check_spaces
from marcaj.scene import OVERLAYS, load_projected_scene, scene_path
from marcaj.tiles import REPO_ROOT

OUT = REPO_ROOT / "data" / "generated" / "work" / "route_v2"
OUT.mkdir(parents=True, exist_ok=True)


def world() -> tuple[list[dict], list[dict], str]:
    scene = load_projected_scene()
    features, name = solver.planning_features(scene)
    targets = solver.route_target_features(features) + (solver.poi_targets(OVERLAYS[0]) if OVERLAYS[0].is_file() else [])
    stat = scene_path().stat()
    print(f"world {name}: {len(features)} features, {len(targets)} targets")
    return features, targets, f"{scene_path()}:{stat.st_size}:{stat.st_mtime_ns}:{name}"


def line_of(item: dict) -> str:
    return (f"{item['length_m']:.0f} m, {item['visited']}/{item['targets']} visited, {item['outside_share']:.2%} outside "
            f"({item['robust_outside_share']:.2%} robust), {item['hops']} hops / {item['hop_m']:.1f} m, canopy {item['canopy_m']:.2f} m, "
            f"forbidden {item['forbidden_m']:.2f} m, legal {item['legal']}, closed {item['closed']}")


def smoke() -> None:
    features, targets, token = world()
    started = time.perf_counter()
    grid = solver.build_grid(features, None, solver.HOP_PENALTY_M)
    print(f"grid with hops: {grid.rows.size} nodes, {len(grid.hop_tier)} hop edges, {time.perf_counter() - started:.1f} s")


def sweep() -> None:
    features, targets, token = world()
    results = {}
    for penalty in (None, 10.0, 25.0, 50.0):
        started = time.perf_counter()
        prepared = solver.cached_plan(features, targets, token, penalty)
        plan_s = time.perf_counter() - started
        started = time.perf_counter()
        solved = solver.routes_by_confidence(features, targets, (None, 0.5, 0.7), fields=False, hop_penalty=penalty, prepared=prepared)
        route_s = time.perf_counter() - started
        for feature, line, rows, report in solved:
            item = feature["properties"]
            print(f"penalty {penalty} confidence {item['min_confidence']}: {line_of(item)}; plan {plan_s:.0f} s, routes {route_s:.0f} s")
        results[str(penalty)] = [feature["properties"] for feature, *_ in solved]
        started = time.perf_counter()
        line, rows, report = solver.route(solver.with_endpoints(prepared), include=None, build=True)
        print(f"  single build route (the request path): {report['length_m']:.0f} m, {report['visited']}/{report['targets']}, {report['hops']} hops, "
              f"{report['robust_outside_share']:.2%} robust, {time.perf_counter() - started:.1f} s")
    (OUT / "hop_sweep.json").write_text(json.dumps(results, indent=1))



def weighted() -> None:
    """Hops whose outside metres cost HOP_OUTSIDE_WEIGHT (6) per metre, like every other outside step, against plain
    metres (weight 1, the `sweep`)."""
    features, targets, token = world()
    for weight, penalty in ((6.0, 10.0), (6.0, 25.0), (6.0, 5.0)):
        solver.HOP_OUTSIDE_WEIGHT = weight
        prepared = solver.cached_plan(features, targets, f"{token}:hop-weight-{weight}", penalty)
        started = time.perf_counter()
        solved = solver.routes_by_confidence(features, targets, (None, 0.5, 0.7), fields=False, hop_penalty=penalty, prepared=prepared)
        for feature, *_ in solved:
            print(f"weight {weight} penalty {penalty} confidence {feature['properties']['min_confidence']}: {line_of(feature['properties'])}; routes {time.perf_counter() - started:.0f} s", flush=True)


def controls() -> None:
    """Checks that must fail: a 3% outside detour is illegal, and a hop through a mature canopy is never built and
    is illegal if drawn by hand. The client's official route passes, as a positive control."""
    features, targets, token = world()
    spaces = check_spaces(features)
    start = _geometries(features, "start")[0]
    official = next(item for item in json.loads((REPO_ROOT / "data" / "generated" / "routes.geojson").read_text())["features"]
                    if item["properties"]["scope"] == "site" and item["properties"]["min_confidence"] is None)
    line = LineString(official["geometry"]["coordinates"])
    base = check_route(line, features, [], spaces)
    print(f"positive control, the client's official route: {base['outside_share']:.2%} outside, canopy {base['canopy_m']:.2f} m, legal {base['legal']} (must be True)")
    assert base["legal"] and base["closed"]
    # an out-and-back spur from START straight into the space outside inter-rows and passages, 1.5% of the length each way
    reach = 0.015 * line.length / (1 - 0.03)
    for angle in np.linspace(0, 2 * np.pi, 72, endpoint=False):
        tip = Point(start.x + reach * np.cos(angle), start.y + reach * np.sin(angle))
        if LineString([start, tip]).difference(spaces["passable"]).length > 0.99 * reach - 1:
            break
    detour = LineString([start.coords[0], tip.coords[0]] + list(line.coords))
    detour = LineString(list(detour.coords[:1]) + [tip.coords[0], start.coords[0]] + list(line.coords[1:]))
    report = check_route(detour, features, [], spaces)
    print(f"control 1, a {2 * reach:.0f} m outside detour: {report['outside_share']:.2%} outside, scores {report['scores']}, legal {report['legal']} (must be False)")
    assert report["outside_share"] > 0.02 and not report["legal"] and not report["scores"]
    grid = solver.build_grid(features, None, solver.HOP_PENALTY_M)
    rows = _geometries(features, "row")
    mature = [item for item in _geometries(features, "vineyard") if item.area >= 1.0]
    tree = shapely.STRtree(rows)
    tried = rejected = 0
    for canopy in mature[:200]:
        centre = canopy.centroid
        row = rows[int(tree.nearest(centre))]
        if row.distance(centre) > 0.3:
            continue
        a, b = np.asarray(row.coords[0]), np.asarray(row.coords[-1])
        normal = np.array([-(b - a)[1], (b - a)[0]]) / np.linalg.norm(b - a)
        ends = [np.asarray(centre.coords[0]) + side * solver.HOP_REACH_M * normal for side in (1, -1)]
        cells = [grid.node[int(r), int(c)] for c, r in (~grid.transform * tuple(end) for end in ends)]
        tried += 1
        rejected += (min(cells), max(cells)) not in grid.hop_tier
        hop = LineString(ends + ends[:1])
        drawn = check_route(hop, features, [], spaces, Point(ends[0]), Point(ends[0]))
        assert drawn["canopy_m"] > CROSSING_TOLERANCE_M and not drawn["legal"], drawn
    print(f"control 2, hops across {tried} mature canopies (>= 1 m2) on a row axis: {rejected} not built by the hop builder "
          f"(must be all), and each drawn by hand is illegal")
    assert rejected == tried > 0
    corridors = shapely.buffer(shapely.linestrings(np.stack([grid.xy(np.array([a for a, _ in grid.hop_tier])), grid.xy(np.array([b for _, b in grid.hop_tier]))], axis=1)), solver.HOP_WIDTH_M / 2 - 0.01, cap_style="flat")
    touching = int(shapely.intersects(corridors, spaces["canopy"]).sum())
    print(f"control 3, the {len(grid.hop_tier)} built hops: {touching} corridors touch a mature canopy (must be 0)")
    assert touching == 0


def trial(weight: float, *penalties: float) -> None:
    """Site routes for hop penalties at a hop outside weight, plus none, on the current grid."""
    features, targets, token = world()
    solver.HOP_OUTSIDE_WEIGHT = weight
    for penalty in penalties:
        prepared = solver.cached_plan(features, targets, token, penalty)
        started = time.perf_counter()
        solved = solver.routes_by_confidence(features, targets, (None, 0.5, 0.7), fields=False, hop_penalty=penalty, prepared=prepared)
        for feature, *_ in solved:
            print(f"v{solver.PLAN_VERSION} weight {weight} penalty {penalty} confidence {feature['properties']['min_confidence']}: {line_of(feature['properties'])}; "
                  f"routes {time.perf_counter() - started:.0f} s", flush=True)


def plain_v4() -> None:
    trial(1.0, None, 50.0, 75.0)


def weighted_v4() -> None:
    trial(6.0, 25.0, 50.0)


def regenerate() -> None:
    """The client's routes and the official route on the client's world, as marcaj-export writes them (hops kept
    only where not worse than without), into route_v2/; compared with data/generated/routes.geojson."""
    from marcaj.export import _write_route
    features, targets, token = world()
    started = time.perf_counter()
    _write_route({"features": features}, OUT, OVERLAYS[0], None, solver.OUTSIDE_BUDGET, OUT / "routes.geojson", solver.HOP_PENALTY_M)
    print(f"regenerated in {time.perf_counter() - started:.0f} s")
    old = {(f["properties"]["scope"], f["properties"]["vineyard_id"], f["properties"]["min_confidence"]): f["properties"]
           for f in json.loads((REPO_ROOT / "data" / "generated" / "routes.geojson").read_text())["features"]}
    new = {(f["properties"]["scope"], f["properties"]["vineyard_id"], f["properties"]["min_confidence"]): f["properties"]
           for f in json.loads((OUT / "routes.geojson").read_text())["features"]}
    worse = [key for key in old if key in new and new[key]["visited"] < old[key]["visited"]]
    print(f"{len(new)} routes against {len(old)}; legal {sum(item['legal'] and item['closed'] for item in new.values())}; "
          f"fewer visited than before: {worse}; total visited {sum(item['visited'] for item in new.values())} against "
          f"{sum(item['visited'] for item in old.values())}, total length {sum(item['length_m'] for item in new.values()):.0f} m against "
          f"{sum(item['length_m'] for item in old.values()):.0f} m, hops {sum(item['hops'] for item in new.values())}")
    for key in [k for k in old if k[0] == "site"]:
        print(f"  site {key[2]}: {old[key]['length_m']:.0f} m {old[key]['visited']}/{old[key]['targets']} -> {line_of(new[key])}")


def merge() -> None:
    """routes_merged.geojson: per route (scope, field, cutoff) the better of data/generated/routes.geojson and the
    regenerated route_v2/routes.geojson (legal and closed under today's check_route, then more visited, then
    shorter), every one re-checked on the client's world."""
    features, targets, token = world()
    spaces = check_spaces(features)
    points = {target["id"]: target["point"] for target in targets}
    kept = {"cutoff": {}}

    def load(path) -> dict:
        return {(f["properties"]["scope"], f["properties"]["vineyard_id"], f["properties"]["min_confidence"]): f
                for f in json.loads(path.read_text())["features"]}

    old, new = load(REPO_ROOT / "data" / "generated" / "routes.geojson"), load(OUT / "routes.geojson")
    field_of = {target["id"]: target.get("vineyard_id") or "" for target in targets}
    conf = {target["id"]: target.get("confidence") for target in targets}

    def checked(feature: dict) -> tuple:
        properties = feature["properties"]
        ids = [t for t in points if (not properties["vineyard_id"] or field_of[t] == properties["vineyard_id"])
               and (properties["min_confidence"] is None or conf[t] is None or conf[t] >= properties["min_confidence"])]
        line = LineString(feature["geometry"]["coordinates"])
        report = check_route(line, features, [points[t] for t in ids], spaces)
        robust = 1 - solver._length_in(line, solver.robust_space(features)) / line.length if "robust" not in kept else 1 - solver._length_in(line, kept["robust"]) / line.length
        fits = report["legal"] and report["closed"] and robust <= solver.OUTSIDE_BUDGET + 1e-9
        properties.update({"visited": report["visited"], "targets": len(ids), "unreachable": len(ids) - report["visited"], "legal": bool(report["legal"]),
                           "closed": bool(report["closed"]), "outside_share": round(report["outside_share"], 5), "robust_outside_share": round(robust, 5),
                           "forbidden_m": round(report["forbidden_m"], 3), "canopy_m": round(report["canopy_m"], 3)})
        properties.setdefault("hops", 0)
        properties.setdefault("hop_m", 0.0)
        properties.setdefault("open", False)
        return (fits, report["visited"], -line.length)

    kept["robust"] = solver.robust_space(features)
    merged, picks = [], {"old": 0, "new": 0}
    for key in old.keys() | new.keys():
        options = [(checked(item), name, item) for name, item in (("old", old.get(key)), ("new", new.get(key))) if item]
        rank, name, best = max(options, key=lambda option: option[0])
        if rank[0]:
            merged.append(best)
            picks[name] += 1
    merged.sort(key=lambda f: (f["properties"]["scope"] != "site", f["properties"]["vineyard_id"], f["properties"]["min_confidence"] is not None, f["properties"]["min_confidence"] or 0))
    (OUT / "routes_merged.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": merged}) + "\n")
    visited_old = sum(f["properties"]["visited"] for f in old.values())
    print(f"merged {len(merged)} routes ({picks}); all legal and closed: {all(f['properties']['legal'] and f['properties']['closed'] for f in merged)}; "
          f"visited {sum(f['properties']['visited'] for f in merged)} against {visited_old} before; worse than before: "
          f"{[k for k in old if next(f for f in merged if (f['properties']['scope'], f['properties']['vineyard_id'], f['properties']['min_confidence']) == k)['properties']['visited'] < old[k]['properties']['visited']]}")
    for f in merged:
        if f["properties"]["scope"] == "site":
            print("  site", f["properties"]["min_confidence"], line_of(f["properties"]))


if __name__ == "__main__":
    for step in sys.argv[1:] or ["smoke"]:
        globals()[step]()
