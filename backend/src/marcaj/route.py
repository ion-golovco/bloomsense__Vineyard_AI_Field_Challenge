"""Challenge route solver: one closed walk from START that passes within 2 m of every reachable target and keeps to
passable space (inter-rows plus passages, minus forbidden zones), in EPSG:32635. `marcaj-export` calls `solve`.

The route plans on `robust_space`, the scene's inter-rows eroded by 0.3 m plus the passages, rasterised on a 0.5 m
grid with each cell's passable cover taken from a 3x finer raster. Cells outside it but within `CORRIDOR_M` stay
walkable at `OUTSIDE_WEIGHT` times the cost unless forbidden, on a row strip (axis ± 0.3 m, the trellis) or on a
canopy: that is how it reaches blocks behind a bare headland and crosses between the two passage components. Inside
passable space a step costs up to twice as much within `MARGIN_M` of the edge, so the route keeps to lane centres.
Steps are 16-directional (length error at most 2.7%) with no corner cutting past a blocked cell, so the route
cannot slip through a 1-cell row strip.

Each target attaches to the cheapest walkable cell within `ATTACH_M` of it in each local component (up to 3, e.g.
both lanes beside a row gap). One Dijkstra per candidate gives costs, lengths and outside metres between all
candidates. The tour is nearest neighbour + 2-opt + Or-opt, with a Viterbi pass that re-picks each target's candidate
for the current order. While the tour is more than `OUTSIDE_BUDGET` outside the robust space, the stop, or run of
stops behind the same outside access, that saves the most outside metres per target is dropped and reported. A gap
POI's two ends are then walked past as well (out and back along its lane), and the simplified line is measured
exactly against the robust space and checked with `routing.check_route` against the scene's own."""

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy import ndimage
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import unary_union

from marcaj.routing import VISIT_RADIUS_M, _geometries, _length_in, check_route
from marcaj.scene import is_scored

GRID_M = 0.5
FINE = 3
MARGIN_M = 0.75
EDGE_PENALTY = 1.0
OUTSIDE_WEIGHT = 6.0
CORRIDOR_M = 12.0
ATTACH_M = 1.75  # + SIMPLIFY_M stays inside the 2 m visit radius
SIMPLIFY_M = 0.2
OUTSIDE_BUDGET = 0.012
ROBUST_M = 0.3
ROW_HALF_M = 0.3
MAX_CANDIDATES = 3
DETOUR_LIMIT = 80.0
# (drow, dcol, cells the step passes through that must be walkable too)
_STEPS = (
    (0, 1, ()), (1, 0, ()), (1, 1, ((1, 0), (0, 1))), (1, -1, ((1, 0), (0, -1))),
    (1, 2, ((0, 1), (1, 1))), (2, 1, ((1, 0), (1, 1))), (2, -1, ((1, 0), (1, -1))), (1, -2, ((0, -1), (1, -1))),
)


@dataclass
class Grid:
    transform: Affine
    node: np.ndarray      # (h, w) node id, -1 where not walkable
    rows: np.ndarray      # node -> grid row
    cols: np.ndarray      # node -> grid column
    outside: np.ndarray   # node -> share of the cell outside passable space
    cost: np.ndarray      # node -> cost per metre
    graph: csr_matrix     # symmetric step costs

    def xy(self, nodes: np.ndarray) -> np.ndarray:
        x, y = self.transform * (self.cols[nodes] + 0.5, self.rows[nodes] + 0.5)
        return np.column_stack([x, y])


def robust_space(features: list[dict[str, Any]]):
    """Passable space as the route plans and budgets it: passages plus the inter-rows eroded by ROBUST_M, so lane
    ends sit 0.3 m back from passages and sides 0.3 m in, in case the organizers' inter-rows are drawn tighter
    than ours. Planning on the scene's own inter-rows put 0.97% of the site route outside them but 1.89% outside
    these (every lane entry and exit adds its 0.3 m); budgeting 1.2% on these left 0.54% outside the scene's own
    and 1.72% if every inter-row stopped 1 m short of its passage."""
    passages = unary_union(_geometries(features, "passage"))
    interrows = unary_union(_geometries(features, "interrow_area")).buffer(-ROBUST_M)
    return unary_union([passages, interrows]).difference(unary_union(_geometries(features, "forbidden")))


def build_grid(features: list[dict[str, Any]], space=None) -> Grid:
    """The walkable grid over `space` (default `robust_space`). Site prediction: 3.5k × 3.6k cells, 1.5 M nodes, 2 s."""
    space = robust_space(features) if space is None else space
    area = unary_union(_geometries(features, "study_area") or [space.envelope])
    left, bottom, right, top = area.bounds
    shape_ = (math.ceil((top - bottom) / GRID_M), math.ceil((right - left) / GRID_M))
    transform = Affine(GRID_M, 0, left, 0, -GRID_M, top)

    def burn(geometries: list, touched: bool = False) -> np.ndarray:
        geometries = [geometry for geometry in geometries if not geometry.is_empty]
        if not geometries:
            return np.zeros(shape_, bool)
        return rasterize(((geometry, 1) for geometry in geometries), out_shape=shape_, transform=transform, all_touched=touched, dtype="uint8").astype(bool)

    # passable cover per cell from a 3x finer raster, so a 0.3 m sliver of outside (a lane end short of its passage)
    # still counts; on the site the whole-cell test undercounted the exact outside metres 3x
    fine = rasterize([(space, 1)], out_shape=(shape_[0] * FINE, shape_[1] * FINE), transform=transform * Affine.scale(1 / FINE), dtype="uint8")
    cover = fine.reshape(shape_[0], FINE, shape_[1], FINE).mean(axis=(1, 3), dtype=np.float32)
    del fine
    passable = cover >= 0.5
    obstacles = burn([row.buffer(ROW_HALF_M, cap_style="flat") for row in _geometries(features, "row")] + _geometries(features, "vineyard"), True)
    blocked = burn(_geometries(features, "forbidden"), True) | (obstacles & ~passable)
    corridor = (ndimage.distance_transform_edt(~passable) * GRID_M <= CORRIDOR_M) & burn([area]) & ~blocked
    walkable = passable | corridor
    margin = ndimage.distance_transform_edt(passable) * GRID_M - GRID_M / 2

    flat = np.flatnonzero(walkable)
    node = np.full(shape_, -1, np.int32)
    node.flat[flat] = np.arange(flat.size, dtype=np.int32)
    rows, cols = np.divmod(flat, shape_[1])
    outside = 1 - cover.flat[flat]
    inside_cost = 1 + EDGE_PENALTY * np.clip((MARGIN_M - margin.flat[flat]) / MARGIN_M, 0, 1)
    # a canopy or row strip overlapping passable space stays walkable (it would cut lanes) but costs as much as outside
    cost = np.where(obstacles.flat[flat], OUTSIDE_WEIGHT, (1 - outside) * inside_cost + outside * OUTSIDE_WEIGHT)

    padded = np.pad(node, 2, constant_values=-1)
    h, w = shape_

    def shifted(dr: int, dc: int) -> np.ndarray:
        return padded[2 + dr:2 + dr + h, 2 + dc:2 + dc + w]

    sources, targets, weights = [], [], []
    for dr, dc, through in _STEPS:
        a, b = node, shifted(dr, dc)
        ok = (a >= 0) & (b >= 0)
        for mr, mc in through:
            ok &= shifted(mr, mc) >= 0
        a, b = a[ok], b[ok]
        sources.append(a)
        targets.append(b)
        weights.append(math.hypot(dr, dc) * GRID_M * (cost[a] + cost[b]) / 2)
    a, b, weight = np.concatenate(sources), np.concatenate(targets), np.concatenate(weights)
    graph = coo_matrix((np.concatenate([weight, weight]), (np.concatenate([a, b]), np.concatenate([b, a]))), shape=(flat.size, flat.size)).tocsr()
    return Grid(transform, node, rows.astype(np.int32), cols.astype(np.int32), outside, cost, graph)


def route_target_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scored inspection points and waste boxes as {id, label, point}; a box's target is its centroid."""
    targets = []
    for index, feature in enumerate(feature for feature in features if feature.get("properties", {}).get("label") in {"inspection", "waste"} and is_scored(feature)):
        geometry = shape(feature["geometry"])
        properties = feature["properties"]
        targets.append({
            "id": str(properties.get("id") or f"{properties['label']}-{index:03d}"), "label": properties["label"],
            "point": geometry if geometry.geom_type == "Point" else geometry.centroid,
        })
    return targets


def poi_targets(path: Path, confidence_over: float | None = None) -> list[dict[str, Any]]:
    """The `challenge: true` inspection points of a POI GeoJSON (marcaj.poi output; their `source` is ignored, as
    they are derived from the scene), optionally only those with `confidence` above a threshold. A gap's
    `gap_start` / `gap_end` become its `ends`, which the route walks past too."""
    targets = []
    for index, feature in enumerate(json.loads(path.read_text(encoding="utf-8"))["features"]):
        properties = feature["properties"]
        if feature["geometry"]["type"] != "Point" or not properties.get("challenge", True):
            continue
        if confidence_over is not None and properties.get("confidence", 1.0) <= confidence_over:
            continue
        ends = [Point(properties[key]) for key in ("gap_start", "gap_end") if properties.get(key)]
        targets.append({
            "id": str(properties.get("id") or f"poi-{index:03d}"), "label": "inspection", "point": shape(feature["geometry"]),
            "ends": ends, "confidence": properties.get("confidence"),
        })
    return targets


def _cell(grid: Grid, point: Point) -> tuple[float, float]:
    col, row = ~grid.transform * (point.x, point.y)
    return row, col


def _attach(grid: Grid, point: Point) -> list[int]:
    """The cheapest walkable cell within ATTACH_M of the point in each local component, cheapest first."""
    row, col = _cell(grid, point)
    reach = ATTACH_M / GRID_M
    r0, c0 = max(int(row - reach), 0), max(int(col - reach), 0)
    r1, c1 = min(int(row + reach) + 1, grid.node.shape[0] - 1), min(int(col + reach) + 1, grid.node.shape[1] - 1)
    window = grid.node[r0:r1 + 1, c0:c1 + 1]
    rr, cc = np.mgrid[r0:r1 + 1, c0:c1 + 1]
    distance = np.hypot(rr + 0.5 - row, cc + 0.5 - col) * GRID_M
    labels, count = ndimage.label((window >= 0) & (distance <= ATTACH_M))
    candidates = []
    for label in range(1, count + 1):
        nodes, near = window[labels == label], distance[labels == label]
        score = grid.cost[nodes] + 0.25 * near
        candidates.append((score.min(), int(nodes[score.argmin()])))
    return [node for _, node in sorted(candidates)[:MAX_CANDIDATES]]


def _trace(grid: Grid, pred: np.ndarray, source: int, stops: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Length and outside metres of the shortest-path-tree paths from `source` to every stop, all at once, and
    that part of the tree as (node, predecessor) rows sorted by node, so a leg is rebuilt without a second Dijkstra."""
    current = stops.copy()
    length, outside = np.zeros(stops.size), np.zeros(stops.size)
    on_path = np.zeros(pred.size, bool)
    live = (current != source) & (pred[current] >= 0)
    while live.any():
        on_path[current[live]] = True
        previous = np.where(live, pred[current], current)
        step = np.hypot(grid.rows[current] - grid.rows[previous], grid.cols[current] - grid.cols[previous]) * GRID_M
        length += step
        outside += step * (grid.outside[current] + grid.outside[previous]) / 2
        current = previous
        live = (current != source) & (pred[current] >= 0)
    nodes = np.flatnonzero(on_path)
    return length, outside, np.column_stack([nodes, pred[nodes]]).astype(np.int32)


def _two_opt(cost: np.ndarray, tour: np.ndarray) -> np.ndarray:
    """First-improvement 2-opt with tour[0] (START) fixed."""
    n, improved = len(tour), True
    while improved:
        improved = False
        for i in range(1, n - 1):
            j = np.arange(i + 1, n)
            delta = cost[tour[i - 1], tour[j]] + cost[tour[i], tour[(j + 1) % n]] - cost[tour[i - 1], tour[i]] - cost[tour[j], tour[(j + 1) % n]]
            k = int(delta.argmin())
            if delta[k] < -1e-6:
                tour[i:j[k] + 1] = tour[i:j[k] + 1][::-1].copy()
                improved = True
    return tour


def _or_opt(cost: np.ndarray, tour: np.ndarray) -> np.ndarray:
    """Move runs of 1-3 stops, either way round, to their cheapest position; tour[0] stays."""
    improved = True
    while improved:
        improved = False
        n = len(tour)
        for size in (1, 2, 3):
            for i in range(1, n - size + 1):
                run, before, after = tour[i:i + size], tour[i - 1], tour[(i + size) % n]
                gain = cost[before, run[0]] + cost[run[-1], after] - cost[before, after]
                rest = np.concatenate([tour[:i], tour[i + size:]])
                a, b = rest, np.roll(rest, -1)
                forward = cost[a, run[0]] + cost[run[-1], b] - cost[a, b]
                backward = cost[a, run[-1]] + cost[run[0], b] - cost[a, b]
                k = int(np.minimum(forward, backward).argmin())
                if min(forward[k], backward[k]) < gain - 1e-6:
                    moved = run if forward[k] <= backward[k] else run[::-1]
                    tour = np.concatenate([rest[:k + 1], moved, rest[k + 1:]])
                    improved = True
                    break
            if improved:
                break
    return tour


def _viterbi(cost: np.ndarray, groups: list[np.ndarray], order: list[int]) -> np.ndarray:
    """For a fixed visiting order of groups (order[0] is START's group), the cheapest candidate in each."""
    best, back = cost[groups[order[0]][0], groups[order[1]]], []
    for previous, group in zip(order[1:], order[2:]):
        step = best[:, None] + cost[np.ix_(groups[previous], groups[group])]
        back.append(step.argmin(0))
        best = step.min(0)
    pick = [int((best + cost[groups[order[-1]], groups[order[0]][0]]).argmin())]
    for pointers in reversed(back):
        pick.append(int(pointers[pick[-1]]))
    pick.reverse()
    return np.array([groups[order[0]][0]] + [int(groups[group][choice]) for group, choice in zip(order[1:], pick)])


def _tour(cost: np.ndarray, groups: list[np.ndarray]) -> np.ndarray:
    """Closed tour over one stop per group; groups[0] is START. Returns stop indices starting at START."""
    if len(groups) == 1:
        return np.array([groups[0][0]])
    group_cost = np.array([[cost[np.ix_(a, b)].min() for b in groups] for a in groups])
    order, left = [0], set(range(1, len(groups)))
    while left:
        order.append(min(left, key=lambda group: group_cost[order[-1], group]))
        left.remove(order[-1])
    order = list(_or_opt(group_cost, _two_opt(group_cost, np.array(order))))
    group_of = {int(stop): index for index, group in enumerate(groups) for stop in group}
    tour, total = _viterbi(cost, groups, order), math.inf
    while True:
        tour = _or_opt(cost, _two_opt(cost, tour))
        tour = _viterbi(cost, groups, [group_of[int(stop)] for stop in tour])
        new_total = cost[tour, np.roll(tour, -1)].sum()
        if new_total >= total - 1e-6:
            return tour
        total = new_total


def _drop(tour: np.ndarray, outside: np.ndarray, access: np.ndarray) -> tuple[list[int], float]:
    """Tour positions whose removal saves the most outside metres per target, and the saving: a single stop, or a
    whole run of consecutive stops that all need outside metres from START (e.g. a block behind a headland, where
    dropping one stop saves nothing because the others still share the access)."""
    n = len(tour)
    runs, run = [], []
    for i in range(1, n + 1):
        if i < n and access[tour[i]] > 0.3:
            run.append(i)
        else:
            runs += [run] if len(run) > 1 else []
            run = []
    best, best_ratio, best_saving = [], -np.inf, 0.0
    for positions in [[i] for i in range(1, n)] + runs:
        i, j = positions[0], positions[-1]
        before, after = tour[i - 1], tour[(j + 1) % n]
        saving = outside[before, tour[i]] + outside[tour[i:j], tour[i + 1:j + 1]].sum() + outside[tour[j], after] - outside[before, after]
        if saving / len(positions) > best_ratio:
            best, best_ratio, best_saving = positions, saving / len(positions), saving
    return best, float(best_saving)


def _out_and_back(grid: Grid, node: int, end: Point) -> list[int]:
    """A walk from `node` to the cheapest-to-reach walkable cell within ATTACH_M of `end`, and back."""
    goals = _attach(grid, end)
    if not goals:
        return [node]
    dist, pred = dijkstra(grid.graph, indices=node, limit=DETOUR_LIMIT, return_predecessors=True)
    goal = min(goals, key=lambda goal: dist[goal])
    if not np.isfinite(dist[goal]):
        return [node]
    path = [goal]
    while path[-1] != node:
        path.append(int(pred[path[-1]]))
    path.reverse()
    return path + path[-2::-1]


def _path(tree: np.ndarray, source: int, target: int) -> list[int]:
    path = [target]
    while path[-1] != source:
        path.append(int(tree[np.searchsorted(tree[:, 0], path[-1]), 1]))
    return path[::-1]


@dataclass
class Plan:
    """Everything the tour needs that does not depend on the outside budget: the grid, each target's candidate
    stops, and costs, lengths, outside metres and path trees between all stops."""
    features: list[dict[str, Any]]
    targets: list[dict[str, Any]]
    space: Any
    grid: Grid
    start: Point
    stops: list[int]
    groups: list[np.ndarray]
    group_target: list[int]
    group_of_stop: np.ndarray
    cost: np.ndarray
    length: np.ndarray
    outside: np.ndarray
    trees: list[np.ndarray]
    unreachable: dict[int, str]
    seconds: dict[str, float]


def plan(features: list[dict[str, Any]], targets: list[dict[str, Any]] | None = None) -> Plan:
    """Grid, stops and all-pairs matrices; 0.3 s per stop (one Dijkstra over 1.4 M nodes), 2 min for the 428 stops
    of the site prediction's 220 targets."""
    started = time.perf_counter()
    targets = route_target_features(features) if targets is None else targets
    starts = _geometries(features, "start")
    if len(starts) != 1:
        raise ValueError(f"Expected one start point, found {len(starts)}")
    start = starts[0]
    space = robust_space(features)
    grid = build_grid(features, space)
    grid_s = time.perf_counter() - started
    row, col = _cell(grid, start)
    start_node = int(grid.node[int(row), int(col)])
    if start_node < 0:
        raise ValueError("START is not on walkable space")
    stops, groups, group_target, unreachable = [start_node], [np.array([0])], [-1], {}
    for index, target in enumerate(targets):
        candidates = _attach(grid, target["point"])
        if not candidates:
            away = space.distance(target["point"])
            unreachable[index] = (f"{away:.1f} m from passable space, beyond the {CORRIDOR_M:.0f} m outside corridor" if away > CORRIDOR_M
                                  else f"no walkable cell within {ATTACH_M} m: forbidden zone, canopy or row strip")
            continue
        groups.append(np.arange(len(stops), len(stops) + len(candidates)))
        group_target.append(index)
        stops.extend(candidates)
    stops_array = np.array(stops)
    cost, length, outside = (np.full((len(stops), len(stops)), np.inf) for _ in range(3))
    trees = []
    for index, node in enumerate(stops):
        dist, pred = dijkstra(grid.graph, indices=node, return_predecessors=True)
        cost[index] = dist[stops_array]
        length[index], outside[index], tree = _trace(grid, pred, node, stops_array)
        trees.append(tree)
    for group in range(1, len(groups)):
        if not np.isfinite(cost[0, groups[group]]).any():
            unreachable[group_target[group]] = "no walkable connection to START"
    return Plan(
        features, targets, space, grid, start, stops, groups, group_target,
        np.repeat(np.arange(len(groups)), [len(group) for group in groups]), cost, length, outside, trees, unreachable,
        {"grid_s": round(grid_s, 1), "matrix_s": round(time.perf_counter() - started - grid_s, 1)},
    )


def route(plan: Plan, budget: float = OUTSIDE_BUDGET, include: set[int] | None = None) -> tuple[LineString, list[dict[str, Any]], dict[str, Any]]:
    """The closed tour over the planned targets (or the `include` indices of them) with at most `budget` of its
    length outside `robust_space`, expanded to a line (plus the walks past gap ends) and checked. Returns the line,
    one row per included target (visited / unreachable / over_budget / missed, with distances) and the check_route
    report on those targets plus the robust outside share and timings."""
    started = time.perf_counter()
    grid, stops, groups, start = plan.grid, plan.stops, plan.groups, plan.start
    included = set(range(len(plan.targets))) if include is None else include
    targets = plan.targets
    cost, length, outside = plan.cost, plan.length, plan.outside
    status = dict(plan.unreachable)
    active = [0] + [group for group in range(1, len(groups)) if plan.group_target[group] not in status and plan.group_target[group] in included]
    dropped: list[int] = []
    scale, stuck = 1.0, False
    while True:
        tour = _tour(cost, [groups[g] for g in active])
        after = np.roll(tour, -1)
        route_length, route_outside = length[tour, after].sum(), outside[tour, after].sum()
        if not stuck and scale * route_outside > budget * route_length and len(tour) > 1:
            positions, saving = _drop(tour, outside, outside[0])
            # outside metres no target's removal saves (shared by all): keep this tour and report its share
            stuck = saving < 0.1
        if not stuck and scale * route_outside > budget * route_length and len(tour) > 1:
            for position in positions:
                group = int(plan.group_of_stop[tour[position]])
                status[plan.group_target[group]] = f"over the outside budget: dropping it{f' and {len(positions) - 1} more behind the same outside access' if len(positions) > 1 else ''} saves {scale * saving:.1f} m outside passable space"
                active.remove(group)
                dropped.append(group)
            continue
        # dropping a whole run can overshoot the budget: put back, cheapest outside access first, what still fits
        for group in sorted(dropped, key=lambda group: outside[0, groups[group]].min()):
            a, b = tour, np.roll(tour, -1)
            added, k, stop = min(((cost[a, c] + cost[c, b] - cost[a, b]).min(), int((cost[a, c] + cost[c, b] - cost[a, b]).argmin()), int(c)) for c in groups[group])
            extra_outside = outside[a[k], stop] + outside[stop, b[k]] - outside[a[k], b[k]]
            extra_length = length[a[k], stop] + length[stop, b[k]] - length[a[k], b[k]]
            if np.isfinite(added) and scale * (route_outside + extra_outside) <= budget * (route_length + extra_length):
                tour = np.insert(tour, k + 1, stop)
                route_outside, route_length = route_outside + extra_outside, route_length + extra_length
                active.append(group)
                dropped.remove(group)
                del status[plan.group_target[group]]
        tour = _or_opt(cost, _two_opt(cost, tour))
        after = np.roll(tour, -1)
        route_length, route_outside = length[tour, after].sum(), outside[tour, after].sum()
        nodes, at = [], []
        for a, b in zip(tour, after):
            leg = _path(plan.trees[a], stops[a], stops[b])
            joined = bool(nodes) and nodes[-1] == leg[0]
            at.append(len(nodes) - 1 if joined else len(nodes))
            nodes.extend(leg[1:] if joined else leg)
        walked = LineString(grid.xy(np.array(nodes))) if len(nodes) > 1 else start
        walks = {}
        for position, stop in zip(at[1:], tour[1:]):
            for end in targets[plan.group_target[int(plan.group_of_stop[stop])]].get("ends", ()):
                if walked.distance(end) > VISIT_RADIUS_M - SIMPLIFY_M - 0.1:
                    walks.setdefault(position, []).append(_out_and_back(grid, stops[stop], end))
        for position in sorted(walks, reverse=True):
            for walk in walks[position]:
                nodes[position + 1:position + 1] = walk[1:]
        coords = [(start.x, start.y)] + [tuple(xy) for xy in grid.xy(np.array(nodes))] + [(start.x, start.y)]
        line = LineString(coords).simplify(SIMPLIFY_M)
        robust_outside = line.length - _length_in(line, plan.space)
        # the raster undercounts the exact outside metres (1.5x on the site, at lane ends); rescale and re-budget
        if robust_outside <= budget * line.length or route_outside == 0 or len(tour) == 1 or stuck:
            break
        scale = max(scale * 1.05, robust_outside / route_outside)
    report = check_route(line, plan.features, [target["point"] for index, target in enumerate(targets) if index in included])
    report.update({
        "robust_outside_m": robust_outside, "robust_outside_share": robust_outside / line.length, "budget": budget,
        "tour_cost": float(cost[tour, np.roll(tour, -1)].sum()), "gap_walks": sum(len(items) for items in walks.values()),
        "grid_nodes": int(grid.rows.size), "stops": len(stops), "raster_outside_m": float(route_outside), "outside_scale": round(float(scale), 3),
        **plan.seconds, "tour_s": round(time.perf_counter() - started, 1),
    })
    rows = []
    for index, target in enumerate(targets):
        if index not in included:
            continue
        distance = line.distance(target["point"])
        reason = status.get(index, "")
        rows.append({
            "id": target["id"], "label": target["label"], "x": round(target["point"].x, 3), "y": round(target["point"].y, 3),
            "status": "visited" if distance <= VISIT_RADIUS_M else "over_budget" if reason.startswith("over") else "unreachable" if reason else "missed",
            "distance_m": round(distance, 2),
            "gap_ends_m": " ".join(f"{line.distance(end):.2f}" for end in target.get("ends", ())),
            "reason": reason,
        })
    return line, rows, report


def solve(features: list[dict[str, Any]], targets: list[dict[str, Any]] | None = None, budget: float = OUTSIDE_BUDGET) -> tuple[LineString, list[dict[str, Any]], dict[str, Any]]:
    """`route(plan(features, targets), budget)`: the scored world is `features` as check_route sees it (predictions
    excluded); `targets` are {id, label, point, optional ends: a gap's two end points, walked past too}, by default
    the scored inspection and waste features."""
    return route(plan(features, targets), budget)


def routes_by_confidence(features: list[dict[str, Any]], targets: list[dict[str, Any]], cutoffs: tuple[float | None, ...] = (None, 0.5, 0.7),
                         budget: float = OUTSIDE_BUDGET) -> list[tuple[dict[str, Any], LineString, list[dict[str, Any]], dict[str, Any]]]:
    """One route per POI confidence cutoff (None: every target; waste and targets without a confidence always
    count), planned once. Each item: the client's route feature (label/source "route", min_confidence, length_m,
    targets, visited, unreachable, over_budget, outside shares, start/end gaps, legal, closed), the line, the target
    rows and the full report. Site prediction, 193 targets: 2 min to plan, ~10 s per cutoff."""
    prepared = plan(features, targets)
    result = []
    for cutoff in cutoffs:
        include = {index for index, target in enumerate(targets) if cutoff is None or target.get("confidence") is None or target["confidence"] >= cutoff}
        line, rows, report = route(prepared, budget, include)
        line = LineString(np.round(np.asarray(line.coords), 3))
        report |= check_route(line, features, [targets[index]["point"] for index in sorted(include)])
        feature = {"type": "Feature", "geometry": mapping(line), "properties": {
            "label": "route", "source": "route", "min_confidence": cutoff, "length_m": round(line.length, 3),
            "targets": len(rows), "visited": report["visited"], "unreachable": len(rows) - report["visited"],
            "over_budget": sum(item["status"] == "over_budget" for item in rows),
            "outside_share": round(report["outside_share"], 5), "robust_outside_share": round(report["robust_outside_share"], 5),
            "start_gap_m": round(report["start_gap_m"], 3), "end_gap_m": round(report["end_gap_m"], 3),
            "legal": bool(report["legal"]), "closed": bool(report["closed"]),
        }}
        result.append((feature, line, rows, report))
    return result
