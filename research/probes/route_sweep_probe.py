"""Coverage sweep experiment: a closed route from START within 2 m of every row axis (walking every second inter-row
covers both neighbouring rows), plus the waste targets, on the client's world (`route.planning_features`), with
single and aligned multi-row crossings; a hybrid (targeted points plus sweeping only rows with gap evidence); and the
expected route score of each against the targeted route under stated assumptions. Writes
data/generated/work/route_v2/sweep/. Run from backend/:
uv run --frozen python -u ../research/probes/route_sweep_probe.py [sweep] [hybrid] [budget] [score]"""

import json
import sys
import time
from collections import defaultdict

import numpy as np
import shapely
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point, mapping
from shapely.ops import unary_union

from marcaj import route as solver
from marcaj.routing import VISIT_RADIUS_M, _geometries, _length_in, check_route, check_spaces
from marcaj.scene import OVERLAYS, load_projected_scene, scene_path
from marcaj.tiles import REPO_ROOT

OUT = REPO_ROOT / "data" / "generated" / "work" / "route_v2" / "sweep"
OUT.mkdir(parents=True, exist_ok=True)
SWEEP_HOP_PENALTY_M = 5.0  # per row crossed: a sweep must cross rows between lanes, the outside weight already discourages it
LOCAL_LIMIT = 150.0  # cost units of the local Dijkstra that finds the next lane end
MIN_LANE_M = 3.0
GAP_EVIDENCE_M = 3.0


def world() -> tuple[list[dict], list[dict], str]:
    scene = load_projected_scene()
    features, name = solver.planning_features(scene)
    targets = solver.route_target_features(features) + (solver.poi_targets(OVERLAYS[0]) if OVERLAYS[0].is_file() else [])
    stat = scene_path().stat()
    print(f"world {name}: {len(features)} features, {len(targets)} targets", flush=True)
    return features, targets, f"{scene_path()}:{stat.st_size}:{stat.st_mtime_ns}:{name}"


def merged_rows(features: list[dict]) -> dict[str, list[dict]]:
    """Rows per field, each row id's segments merged into one straight axis (principal direction, full extent)."""
    pieces = defaultdict(list)
    for feature in features:
        properties = feature["properties"]
        if properties.get("label") == "row" and properties.get("row_id"):
            pieces[(properties.get("vineyard_id") or "", properties["row_id"])].append(np.asarray(feature["geometry"]["coordinates"], float))
    fields = defaultdict(list)
    for (vineyard_id, row_id), parts in pieces.items():
        coords = np.concatenate(parts)
        centre = coords.mean(axis=0)
        direction = np.linalg.svd(coords - centre)[2][0]
        along = (coords - centre) @ direction
        fields[vineyard_id].append({"row_id": row_id, "centre": centre, "direction": direction, "u": (along.min(), along.max()),
                                    "line": unary_union([LineString(part) for part in parts])})
    return fields


def lanes(fields: dict[str, list[dict]], space) -> tuple[list[dict], list[dict]]:
    """Walking segments that cover every row: per field, rows sorted across the row direction, and every second
    inter-row centreline (plus the last one for an odd count) clipped to the planning space. Also returns every
    lane with the rows it covers, for the hybrid."""
    chosen, every = [], []
    shapely.prepare(space)
    for vineyard_id, rows in fields.items():
        if len(rows) < 2:
            continue
        reference = rows[0]["direction"]
        direction = np.mean([row["direction"] * np.sign(row["direction"] @ reference) for row in rows], axis=0)
        direction /= np.linalg.norm(direction)
        normal = np.array([-direction[1], direction[0]])
        origin = np.mean([row["centre"] for row in rows], axis=0)
        for row in rows:
            row["s"] = (row["centre"] - origin) @ normal
            shift = (row["centre"] - origin) @ direction
            sign = np.sign(row["direction"] @ direction)
            row["span"] = sorted((shift + sign * row["u"][0], shift + sign * row["u"][1]))
        rows.sort(key=lambda row: row["s"])
        pair_lanes = {}
        for i in range(len(rows) - 1):
            a, b = rows[i], rows[i + 1]
            if not 1.2 <= b["s"] - a["s"] <= 4.5:
                continue
            middle = (a["s"] + b["s"]) / 2
            u0, u1 = min(a["span"][0], b["span"][0]) - 1.0, max(a["span"][1], b["span"][1]) + 1.0
            centre = LineString([origin + u0 * direction + middle * normal, origin + u1 * direction + middle * normal])
            clipped = centre.intersection(space)
            parts = [part for part in getattr(clipped, "geoms", [clipped]) if part.geom_type == "LineString" and part.length >= MIN_LANE_M]
            if parts:
                pair_lanes[i] = {"vineyard_id": vineyard_id, "rows": (a["row_id"], b["row_id"]), "parts": parts}
                every.append(pair_lanes[i])
        covered = set()
        for i in range(len(rows)):
            if i in covered:
                continue
            if i in pair_lanes:
                chosen.append(pair_lanes[i])
                covered |= {i, i + 1}
            elif i - 1 in pair_lanes:
                chosen.append(pair_lanes[i - 1])
                covered.add(i)
    return chosen, every


def node_at(grid: solver.Grid, xy) -> int | None:
    found = solver.snap(grid, Point(xy), 1.5)
    return None if found is None else found[0]


def tour(grid: solver.Grid, start_node: int, segments: list[tuple[int, int]]) -> tuple[list[int], list[tuple[int, int, int]], int]:
    """Nearest-neighbour tour from START through every required segment (a lane walked end to end, or a point
    with both ends equal), by grid cost; returns the node path, (segment, path index where its access leg starts,
    where it ends) in visiting order, and how many segments were unreachable."""
    ends = np.array([node for segment in segments for node in segment])
    owner = np.repeat(np.arange(len(segments)), 2)
    done = np.zeros(len(segments), bool)
    path, order, current = [start_node], [], start_node

    def walk(source: int, target: int, limit: float = np.inf) -> list[int] | None:
        dist, pred = dijkstra(grid.graph, indices=source, limit=limit, return_predecessors=True)
        if not np.isfinite(dist[target]):
            return None
        nodes = [target]
        while nodes[-1] != source:
            nodes.append(int(pred[nodes[-1]]))
        return nodes[::-1]

    while not done.all():
        dist, pred = dijkstra(grid.graph, indices=current, limit=LOCAL_LIMIT, return_predecessors=True)
        distance = np.where(done[owner], np.inf, dist[ends])
        if not np.isfinite(distance).any():
            dist, pred = dijkstra(grid.graph, indices=current, return_predecessors=True)
            distance = np.where(done[owner], np.inf, dist[ends])
            if not np.isfinite(distance).any():
                break
        k = int(distance.argmin())
        segment = int(owner[k])
        entry, exit_ = ends[k], ends[k ^ 1]
        nodes = [entry]
        while nodes[-1] != current:
            nodes.append(int(pred[nodes[-1]]))
        leg_start = len(path) - 1
        path += nodes[::-1][1:]
        order.append((segment, leg_start, len(path) - 1))
        if exit_ != entry:
            lane = walk(entry, exit_, 3 * np.hypot(*(grid.xy(np.array([entry]))[0] - grid.xy(np.array([exit_]))[0])) + 30)
            lane = lane or walk(entry, exit_)
            if lane:
                path += lane[1:]
                entry = exit_
        current = int(entry)
        done[segment] = True
    back = walk(current, start_node)
    path += (back or [current])[1:]
    return path, order, int((~done).sum())


def evaluate(name: str, grid: solver.Grid, features: list[dict], targets: list[dict], path: list[int], extra: dict) -> dict:
    start = _geometries(features, "start")[0]
    coords = [(start.x, start.y)] + [tuple(xy) for xy in grid.xy(np.array(path))] + [(start.x, start.y)]
    line = LineString(coords).simplify(solver.SIMPLIFY_M)
    spaces = check_spaces(features)
    report = check_route(line, features, [target["point"] for target in targets], spaces)
    robust = line.length - _length_in(line, solver.robust_space(features))
    rows = unary_union(_geometries(features, "row"))
    near = line.buffer(VISIT_RADIUS_M, quad_segs=4)
    steps = np.hypot(np.diff(grid.rows[path]), np.diff(grid.cols[path])) * solver.GRID_M
    hops = np.flatnonzero(steps > solver.HOP_MIN_STEP_M)
    pairs = [(min(path[i], path[i + 1]), max(path[i], path[i + 1])) for i in hops]
    result = {
        "name": name, "length_m": round(line.length, 1), "row_m": round(rows.length, 1),
        "row_share_within_2m": round(rows.intersection(near).length / rows.length, 4),
        "outside_share": round(report["outside_share"], 5), "robust_outside_share": round(robust / line.length, 5),
        "outside_m": round(report["outside_m"], 1), "robust_outside_m": round(robust, 1),
        "forbidden_m": round(report["forbidden_m"], 3), "canopy_m": round(report["canopy_m"], 3),
        "crossings": int(hops.size), "rows_crossed": int(sum(grid.hop_rows.get(pair, 1) for pair in pairs)),
        "multi_row_crossings": int(sum(pair in grid.hop_rows for pair in pairs)),
        "targets": report["targets"], "targets_visited": report["visited"], "legal": bool(report["legal"]), "closed": bool(report["closed"]),
        "scores_under_2pct": bool(report["scores"]), **extra,
    }
    (OUT / f"{name}.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": [
        {"type": "Feature", "geometry": mapping(line), "properties": {"label": "route", "source": "route", **result}}]}) + "\n")
    print(json.dumps(result), flush=True)
    return result


def sweep_grid(features: list[dict]) -> solver.Grid:
    started = time.perf_counter()
    grid = solver.build_grid(features, None, SWEEP_HOP_PENALTY_M, None, 3)
    print(f"sweep grid: {grid.rows.size} nodes, {len(grid.hop_tier)} crossings ({len(grid.hop_rows)} multi-row), {time.perf_counter() - started:.0f} s", flush=True)
    return grid


def segments_of(grid: solver.Grid, chosen: list[dict]) -> list[tuple[int, int]]:
    segments = []
    for lane in chosen:
        for part in lane["parts"]:
            a, b = node_at(grid, part.coords[0]), node_at(grid, part.coords[-1])
            if a is not None and b is not None:
                segments.append((a, b))
    return segments


def run(name: str, features: list[dict], targets: list[dict], grid: solver.Grid, segments: list[tuple[int, int]], budget: float | None) -> dict:
    """The tour, and with a `budget` (robust outside share) the tour again without the segments whose access costs
    the most outside metres, until it fits."""
    start = _geometries(features, "start")[0]
    start_node = solver.snap(grid, start)[0]
    dropped, rounds = 0, 0
    while True:
        started = time.perf_counter()
        path, order, unreachable = tour(grid, start_node, segments)
        result = evaluate(name, grid, features, targets, path, {"segments": len(segments), "unreachable_segments": unreachable,
                                                                 "dropped_segments": dropped, "tour_s": round(time.perf_counter() - started, 1)})
        rounds += 1
        if budget is None or result["robust_outside_share"] <= budget or rounds >= 6:
            return result
        # outside metres of each segment's access leg: drop the costliest tenth of the segments that need any
        legs = []
        xy = grid.xy(np.array(path))
        space = solver.robust_space(features)
        for segment, i, j in order:
            leg = LineString(xy[i:j + 1]) if j > i else None
            legs.append((0.0 if leg is None else leg.length - _length_in(leg, space), segment))
        legs.sort(reverse=True)
        worst = {segment for outside, segment in legs[: max(1, len(legs) // 10)] if outside > 1.0}
        if not worst:
            return result
        segments = [segment for k, segment in enumerate(segments) if k not in worst]
        dropped += len(worst)


def sweep() -> None:
    features, targets, token = world()
    grid = sweep_grid(features)
    fields = merged_rows(features)
    chosen, every = lanes(fields, solver.robust_space(features))
    waste = [target for target in targets if target["label"] == "waste" or str(target["id"]).startswith("WASTE")]
    segments = segments_of(grid, chosen)
    points = [(node, node) for target in waste for node in solver._attach(grid, target["point"])[:1]]
    print(f"{sum(len(rows) for rows in fields.values())} rows in {len(fields)} fields, {len(chosen)} lanes chosen of {len(every)}, "
          f"{len(segments)} segments, {len(points)} waste points", flush=True)
    results = [run("sweep_full", features, targets, grid, segments + points, None)]
    results.append(run("sweep_budget", features, targets, grid, segments + points, solver.OUTSIDE_BUDGET))
    (OUT / "sweep_results.json").write_text(json.dumps(results, indent=1))


def gap_rows(features: list[dict]) -> set[tuple[str, str]]:
    """Rows with gap evidence: a stretch of GAP_EVIDENCE_M or more of the axis with no canopy within 0.3 m."""
    canopy = unary_union(_geometries(features, "vineyard")).buffer(0.3)
    evidence = set()
    for feature in features:
        properties = feature["properties"]
        if properties.get("label") != "row" or not properties.get("row_id"):
            continue
        bare = shapely.geometry.shape(feature["geometry"]).difference(canopy)
        if any(part.length >= GAP_EVIDENCE_M for part in getattr(bare, "geoms", [bare])):
            evidence.add((properties.get("vineyard_id") or "", properties["row_id"]))
    return evidence


def hybrid() -> None:
    features, targets, token = world()
    grid = sweep_grid(features)
    fields = merged_rows(features)
    _, every = lanes(fields, solver.robust_space(features))
    evidence = gap_rows(features)
    need = set(evidence)
    chosen = []
    for lane in sorted(every, key=lambda lane: -sum((lane["vineyard_id"], row) in evidence for row in lane["rows"])):
        rows = {(lane["vineyard_id"], row) for row in lane["rows"]}
        if rows & need:
            chosen.append(lane)
            need -= rows
    segments = segments_of(grid, chosen)
    points = [(node, node) for target in targets for node in solver._attach(grid, target["point"])[:1]]
    print(f"hybrid: {len(evidence)} rows with gap evidence, {len(chosen)} lanes, {len(segments)} segments, {len(points)} target points", flush=True)
    results = [run("hybrid_full", features, targets, grid, segments + points, None),
               run("hybrid_budget", features, targets, grid, segments + points, solver.OUTSIDE_BUDGET)]
    (OUT / "hybrid_results.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    for step in sys.argv[1:] or ["sweep"]:
        globals()[step]()
