"""Challenge route solver: one closed walk from START that passes within 2 m of every reachable target and keeps to
passable space (inter-rows plus passages, minus forbidden zones), in EPSG:32635. `marcaj-export` calls `solve`.

The route plans on `robust_space`, the scene's inter-rows eroded by 0.3 m plus the passages, rasterised on a 0.5 m
grid with each cell's passable cover taken from a 3x finer raster. Cells outside it but within `CORRIDOR_M` stay
walkable at `OUTSIDE_WEIGHT` times the cost unless forbidden, on a row strip (axis ± 0.3 m, the trellis), on a
canopy or on the imagery's no-data margin: that is how it reaches blocks behind a bare headland and crosses between the two passage components. Inside
passable space a step costs up to twice as much within `MARGIN_M` of the edge, so the route keeps to lane centres.
Steps are 16-directional (length error at most 2.7%) with no corner cutting past a blocked cell, so the route
cannot slip through a 1-cell row strip.

Each target attaches to the cheapest walkable cell within `ATTACH_M` of it in each local component (up to 3, e.g.
both lanes beside a row gap). One Dijkstra per candidate gives costs, lengths and outside metres between all
candidates. The tour is nearest neighbour + 2-opt + Or-opt, with a Viterbi pass that re-picks each target's candidate
for the current order. While the tour is more than `OUTSIDE_BUDGET` outside the robust space, the stop, or run of
stops behind the same outside access, that saves the most outside metres per target is dropped; `_add` then puts
back, cluster by cluster (the gaps of one lane share its access), whatever still fits, most targets per outside
metre first. A gap POI's two ends are walked past as well (out and back along its lane), and the simplified line
is measured exactly against the robust space and checked with `routing.check_route` against the scene's own.
`routes_by_confidence` solves the site-wide route and one route per field (vineyard_id) for each POI confidence
cutoff on one plan, warm-starting each cutoff from the others' tours."""

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
from scipy.sparse.csgraph import connected_components, dijkstra
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import unary_union

from marcaj.mosaic import load_mosaic
from marcaj.routing import VISIT_RADIUS_M, _geometries, _length_in, check_route, check_spaces
from marcaj.scene import is_scored

GRID_M = 0.5
FINE = 3
SAMPLES = 8
CHUNK = 1 << 20
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
UNDERCOUNT = 1.3  # sampled raster outside metres of an unmeasured leg, times this, against exact: 78 / 63 on the site
MAX_ROUNDS = 8
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
    fine: np.ndarray      # (FINE h, FINE w) 1 where passable, at GRID_M / FINE
    cost: np.ndarray      # node -> cost per metre
    graph: csr_matrix     # symmetric step costs

    def xy(self, nodes: np.ndarray) -> np.ndarray:
        x, y = self.transform * (self.cols[nodes] + 0.5, self.rows[nodes] + 0.5)
        return np.column_stack([x, y])

    def outside(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Share of each straight step a -> b outside passable space, sampled along the step on the fine raster.
        Cell-average cover missed the steps that clip an eroded lane's corner: it put the site route's outside
        metres at 48 m against 87 m exact, where sampling gives 86 m."""
        t = (np.arange(SAMPLES) + 0.5) / SAMPLES
        rows = ((self.rows[a, None] + 0.5 + t * (self.rows[b] - self.rows[a])[:, None]) * FINE).astype(np.int32)
        cols = ((self.cols[a, None] + 0.5 + t * (self.cols[b] - self.cols[a])[:, None]) * FINE).astype(np.int32)
        return 1 - self.fine[rows, cols].mean(axis=1)


def robust_space(features: list[dict[str, Any]]):
    """Passable space as the route plans and budgets it: passages plus the inter-rows eroded by ROBUST_M, so lane
    ends sit 0.3 m back from passages and sides 0.3 m in, in case the organizers' inter-rows are drawn tighter
    than ours. Planning on the scene's own inter-rows put 0.97% of the site route outside them but 1.89% outside
    these (every lane entry and exit adds its 0.3 m); budgeting 1.2% on these left 0.54% outside the scene's own
    and 1.72% if every inter-row stopped 1 m short of its passage."""
    passages = unary_union(_geometries(features, "passage"))
    interrows = unary_union(_geometries(features, "interrow_area")).buffer(-ROBUST_M)
    return unary_union([passages, interrows]).difference(unary_union(_geometries(features, "forbidden")))


def _visible(transform: Affine, shape_: tuple[int, int]) -> np.ndarray:
    """Grid cells whose centre lies on imagery: the 0.2 m mosaic's pixels with R + G + B > 30 (`layers.exg`'s rule),
    holes filled so dark shadows count. Nobody can see, or annotate, what the route would cross on the no-data
    margin."""
    rgb, mosaic = load_mosaic()
    valid = ndimage.binary_fill_holes(rgb.sum(axis=0, dtype=np.uint16) > 30)
    del rgb
    x = transform.c + (np.arange(shape_[1]) + 0.5) * transform.a
    y = transform.f + (np.arange(shape_[0]) + 0.5) * transform.e
    col, row = np.floor((x - mosaic.c) / mosaic.a).astype(int), np.floor((y - mosaic.f) / mosaic.e).astype(int)
    in_col, in_row = (col >= 0) & (col < valid.shape[1]), (row >= 0) & (row < valid.shape[0])
    visible = np.zeros(shape_, bool)
    visible[np.ix_(in_row, in_col)] = valid[np.ix_(row[in_row], col[in_col])]
    return visible


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

    # a 3x finer raster gives each cell's passable cover and each step's outside share, so a 0.3 m sliver of outside
    # (a lane end short of its passage) still counts
    fine = rasterize([(space, 1)], out_shape=(shape_[0] * FINE, shape_[1] * FINE), transform=transform * Affine.scale(1 / FINE), dtype="uint8")
    cover = fine.reshape(shape_[0], FINE, shape_[1], FINE).mean(axis=(1, 3), dtype=np.float32)
    passable = cover >= 0.5
    obstacles = burn([row.buffer(ROW_HALF_M, cap_style="flat") for row in _geometries(features, "row")] + _geometries(features, "vineyard"), True)
    blocked = burn(_geometries(features, "forbidden"), True) | (obstacles & ~passable)
    corridor = (ndimage.distance_transform_edt(~passable) * GRID_M <= CORRIDOR_M) & burn([area]) & ~blocked & _visible(transform, shape_)
    walkable = passable | corridor
    margin = ndimage.distance_transform_edt(passable) * GRID_M - GRID_M / 2

    flat = np.flatnonzero(walkable)
    node = np.full(shape_, -1, np.int32)
    node.flat[flat] = np.arange(flat.size, dtype=np.int32)
    rows, cols = np.divmod(flat, shape_[1])
    outside = 1 - cover.flat[flat]
    # a canopy or row strip overlapping passable space stays walkable (it would cut lanes) but costs as much as outside
    inside_cost = np.where(obstacles.flat[flat], OUTSIDE_WEIGHT, 1 + EDGE_PENALTY * np.clip((MARGIN_M - margin.flat[flat]) / MARGIN_M, 0, 1))
    cost = (1 - outside) * inside_cost + outside * OUTSIDE_WEIGHT
    grid = Grid(transform, node, rows.astype(np.int32), cols.astype(np.int32), fine, cost, csr_matrix((flat.size, flat.size)))

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
        share = np.concatenate([grid.outside(a[i:i + CHUNK], b[i:i + CHUNK]) for i in range(0, max(a.size, 1), CHUNK)])
        sources.append(a)
        targets.append(b)
        weights.append(math.hypot(dr, dc) * GRID_M * ((1 - share) * (inside_cost[a] + inside_cost[b]) / 2 + share * OUTSIDE_WEIGHT))
    a, b, weight = np.concatenate(sources), np.concatenate(targets), np.concatenate(weights)
    grid.graph = coo_matrix((np.concatenate([weight, weight]), (np.concatenate([a, b]), np.concatenate([b, a]))), shape=(flat.size, flat.size)).tocsr()
    return grid


def route_target_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scored inspection points and waste boxes as {id, label, point, vineyard_id}; a box's target is its centroid."""
    targets = []
    for index, feature in enumerate(feature for feature in features if feature.get("properties", {}).get("label") in {"inspection", "waste"} and is_scored(feature)):
        geometry = shape(feature["geometry"])
        properties = feature["properties"]
        targets.append({
            "id": str(properties.get("id") or f"{properties['label']}-{index:03d}"), "label": properties["label"],
            "point": geometry if geometry.geom_type == "Point" else geometry.centroid, "vineyard_id": properties.get("vineyard_id") or "",
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
            "ends": ends, "confidence": properties.get("confidence"), "vineyard_id": properties.get("vineyard_id") or "",
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
        outside[live] += step[live] * grid.outside(previous[live], current[live])
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


def _gap_walk(plan: "Plan", stop: int, end_index: int) -> list[int] | None:
    """An out-and-back walk from `stop` to the cheapest cell within ATTACH_M of its target's gap end, if the walk
    stays entirely inside the planning space (a walk that needs outside metres is skipped: the gap is still visited
    at its midpoint). Cached on the plan."""
    key = (stop, end_index)
    if key not in plan.walks:
        grid, node = plan.grid, plan.stops[stop]
        end = plan.targets[plan.group_target[int(plan.group_of_stop[stop])]]["ends"][end_index]
        walk, goals = None, _attach(grid, end)
        if goals:
            dist, pred = dijkstra(grid.graph, indices=node, limit=DETOUR_LIMIT, return_predecessors=True)
            goal = min(goals, key=lambda goal: dist[goal])
            if np.isfinite(dist[goal]) and goal != node:
                path = [goal]
                while path[-1] != node:
                    path.append(int(pred[path[-1]]))
                line = LineString(grid.xy(np.array(path)))
                if line.length - _length_in(line, plan.space) <= 0.05:
                    walk = path[::-1] + path[1:]
        plan.walks[key] = walk
    return plan.walks[key]


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
    checks: dict = None           # routing.check_spaces of the features
    measured: np.ndarray = None  # (stops, stops) True where `outside` holds the exact metres of that leg
    walks: dict = None            # (stop, end index) -> legal out-and-back walk to a gap end, or None


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
        check_spaces(features), np.zeros((len(stops), len(stops)), bool), {},
    )


def _insertion(cost: np.ndarray, tour: np.ndarray, stops: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cheapest insertion of each stop into the closed tour: the added cost and the edge (tour[k], tour[k + 1])."""
    after = np.roll(tour, -1)
    added = cost[np.ix_(stops, tour)] + cost[np.ix_(stops, after)] - cost[tour, after]
    edge = added.argmin(1)
    return added[np.arange(len(stops)), edge], edge


def _add(plan: Plan, tour: np.ndarray, pool: list[int], budget: float, outside: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """Insert pool groups at their cheapest place while the budget holds on `outside` (exact where measured). Groups that add no outside
    metres go first; otherwise the one with the most targets per outside metre, counting the pool groups its
    insertion makes free (the other gaps of a lane behind a headland share that lane's access), provided it fits.
    Returns the tour and the groups added."""
    cost, length, groups = plan.cost, plan.length, plan.groups
    pool, added = list(pool), []
    after = np.roll(tour, -1)
    route_length, route_outside = float(length[tour, after].sum()), float(outside[tour, after].sum())
    while pool:
        stops = np.concatenate([groups[g] for g in pool])
        owner = np.repeat(np.arange(len(pool)), [len(groups[g]) for g in pool])
        extra, edge = _insertion(cost, tour, stops)
        after = np.roll(tour, -1)
        a, b = tour[edge], after[edge]
        extra_outside = outside[a, stops] + outside[stops, b] - outside[a, b]
        extra_length = length[a, stops] + length[stops, b] - length[a, b]
        fits = np.isfinite(extra) & (route_outside + extra_outside <= budget * (route_length + extra_length))
        # each group's cheapest stop
        order = np.lexsort((extra, owner))
        first = order[np.r_[True, owner[order][1:] != owner[order][:-1]]]
        free = first[fits[first] & (extra_outside[first] <= 0.05)]
        if free.size:
            pick = int(free[extra[free].argmin()])
        else:
            fitting = first[fits[first]]
            if not fitting.size:
                break
            best_ratio, pick = -np.inf, -1
            for i in fitting:
                stop, k = stops[i], edge[i]
                # the pool's cheapest insertion once `stop` sits between a[i] and b[i]: next to it, or where it was
                via_a = cost[a[i], stops] + cost[stops, stop] - cost[a[i], stop]
                via_b = cost[stop, stops] + cost[stops, b[i]] - cost[stop, b[i]]
                out_a = outside[a[i], stops] + outside[stops, stop] - outside[a[i], stop]
                out_b = outside[stop, stops] + outside[stops, b[i]] - outside[stop, b[i]]
                new_outside = np.where(via_a < extra, out_a, extra_outside)
                new_outside = np.where((via_b < np.minimum(via_a, extra)), out_b, new_outside)
                freed = np.unique(owner[(new_outside <= 0.05) & (owner != owner[i])]).size
                ratio = (1 + freed) / max(extra_outside[i], 0.05)
                if ratio > best_ratio:
                    best_ratio, pick = ratio, int(i)
        k = int(edge[pick])
        tour = np.insert(tour, k + 1, stops[pick])
        route_length += float(extra_length[pick])
        route_outside += float(extra_outside[pick])
        group = pool.pop(int(owner[pick]))
        added.append(group)
    return tour, added


def _target_rows(line: LineString, targets: list[dict[str, Any]], included: set[int], status: dict[int, str]) -> list[dict[str, Any]]:
    rows = []
    for index in sorted(included):
        target = targets[index]
        distance = line.distance(target["point"])
        reason = status.get(index, "")
        rows.append({
            "id": target["id"], "label": target["label"], "x": round(target["point"].x, 3), "y": round(target["point"].y, 3),
            "status": "visited" if distance <= VISIT_RADIUS_M else "over_budget" if reason.startswith("over") else "unreachable" if reason else "missed",
            "distance_m": round(distance, 2),
            "gap_ends_m": " ".join(f"{line.distance(end):.2f}" for end in target.get("ends", ())),
            "reason": reason,
        })
    return rows


def _measure(plan: Plan, tour: np.ndarray) -> None:
    """Replace the raster outside metres of the tour's legs by their exact metres outside the planning space."""
    for a, b in zip(tour, np.roll(tour, -1)):
        if not plan.measured[a, b]:
            nodes = _path(plan.trees[a], plan.stops[a], plan.stops[b])
            leg = LineString(plan.grid.xy(np.array(nodes))) if len(nodes) > 1 else None
            plan.outside[a, b] = plan.outside[b, a] = 0.0 if leg is None else max(leg.length - _length_in(leg, plan.space), 0.0)
            plan.measured[a, b] = plan.measured[b, a] = True


def route(plan: Plan, budget: float = OUTSIDE_BUDGET, include: set[int] | None = None, seed: list[int] | None = None,
          scale: float = UNDERCOUNT, build: bool = False) -> tuple[LineString, list[dict[str, Any]], dict[str, Any]]:
    """The closed tour over the planned targets (or the `include` indices of them) with at most `budget` of its
    length outside `robust_space`, expanded to a line (plus legal walks past gap ends) and checked.

    Without a seed, every target starts in the tour and the drop step thins it to the budget; then `_add` puts
    back whatever still fits. `build` starts from START alone and lets `_add` build the whole tour. A `seed` (a
    tour of stop indices from START, e.g. another cutoff's; stops of targets outside `include` are removed) is the
    starting tour instead. The raster's outside metres of a leg are scaled by `scale` until the leg is measured
    exactly; every expanded tour's legs are measured (and kept on the plan), and a tour over budget is trimmed
    again on the exact numbers. Returns the line, one row per included target (visited / unreachable /
    over_budget / missed, with distances) and the check_route report plus the robust outside share, the tour
    (`tour`), the reasons (`status`) and timings."""
    started = time.perf_counter()
    grid, stops, groups, start = plan.grid, plan.stops, plan.groups, plan.start
    included = set(range(len(plan.targets))) if include is None else set(include)
    targets, cost, length = plan.targets, plan.cost, plan.length
    status = dict(plan.unreachable)
    candidates = [group for group in range(1, len(groups)) if plan.group_target[group] not in status and plan.group_target[group] in included]
    if build:
        seed = [0]
    tour = None if seed is None else np.array([stop for stop in seed if stop == 0 or plan.group_target[int(plan.group_of_stop[stop])] in included])
    active = candidates if tour is None else sorted({int(plan.group_of_stop[stop]) for stop in tour[1:]})
    for _ in range(MAX_ROUNDS):
        outside = plan.outside * np.where(plan.measured, 1.0, scale)
        if tour is None:
            tour = _tour(cost, [groups[g] for g in [0] + active])
        while len(tour) > 1:
            after = np.roll(tour, -1)
            if outside[tour, after].sum() <= budget * length[tour, after].sum():
                break
            positions, saving = _drop(tour, outside, outside[0])
            if saving < 0.1:  # outside metres that no removal saves (shared by all): build up from START instead
                tour, active = np.array([0]), []
                break
            for position in positions:
                group = int(plan.group_of_stop[tour[position]])
                status[plan.group_target[group]] = f"over the outside budget: dropping it{f' and {len(positions) - 1} more behind the same outside access' if len(positions) > 1 else ''} saves {saving:.1f} m outside passable space"
                active.remove(group)
            tour = _tour(cost, [groups[g] for g in [0] + active])
        tour, added = _add(plan, tour, [group for group in candidates if group not in active], budget, outside)
        for group in added:
            status.pop(plan.group_target[group], None)
        active = active + added
        tour = _or_opt(cost, _two_opt(cost, tour))
        _measure(plan, tour)
        after = np.roll(tour, -1)
        route_length, route_outside = length[tour, after].sum(), plan.outside[tour, after].sum()
        nodes, at = [], []
        for a, b in zip(tour, after):
            leg = _path(plan.trees[a], stops[a], stops[b])
            joined = bool(nodes) and nodes[-1] == leg[0]
            at.append(len(nodes) - 1 if joined else len(nodes))
            nodes.extend(leg[1:] if joined else leg)
        walked = LineString(grid.xy(np.array(nodes))) if len(nodes) > 1 else start
        walks = {}
        for position, stop in zip(at[1:], tour[1:]):
            for end_index, end in enumerate(targets[plan.group_target[int(plan.group_of_stop[stop])]].get("ends", ())):
                if walked.distance(end) > VISIT_RADIUS_M - SIMPLIFY_M - 0.1 and (walk := _gap_walk(plan, int(stop), end_index)):
                    walks.setdefault(position, []).append(walk)
        for position in sorted(walks, reverse=True):
            for walk in walks[position]:
                nodes[position + 1:position + 1] = walk[1:]
        coords = [(start.x, start.y)] + [tuple(xy) for xy in grid.xy(np.array(nodes))] + [(start.x, start.y)]
        line = LineString(coords).simplify(SIMPLIFY_M)
        robust_outside = line.length - _length_in(line, plan.space)
        if robust_outside <= budget * line.length or len(tour) == 1:
            break
        scale *= 1.1  # the measured legs are exact now; unmeasured estimates were too low as well
    for group in candidates:
        if group not in active:
            status.setdefault(plan.group_target[group], "over the outside budget: not added")
    report = check_route(line, plan.features, [targets[index]["point"] for index in sorted(included)], plan.checks)
    report.update({
        "robust_outside_m": robust_outside, "robust_outside_share": robust_outside / line.length, "budget": budget,
        "tour_cost": float(cost[tour, np.roll(tour, -1)].sum()), "gap_walks": sum(len(items) for items in walks.values()),
        "grid_nodes": int(grid.rows.size), "stops": len(stops), "legs_outside_m": float(route_outside), "outside_scale": round(float(scale), 3),
        **plan.seconds, "tour_s": round(time.perf_counter() - started, 1), "tour": [int(stop) for stop in tour], "status": status,
    })
    return line, _target_rows(line, targets, included, status), report


def solve(features: list[dict[str, Any]], targets: list[dict[str, Any]] | None = None, budget: float = OUTSIDE_BUDGET) -> tuple[LineString, list[dict[str, Any]], dict[str, Any]]:
    """`route(plan(features, targets), budget)`: the scored world is `features` as check_route sees it (predictions
    excluded); `targets` are {id, label, point, optional ends: a gap's two end points, walked past too}, by default
    the scored inspection and waste features."""
    return route(plan(features, targets), budget)


def _route_feature(cutoff: float | None, vineyard_id: str, line: LineString, rows: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": mapping(line), "properties": {
        "label": "route", "source": "route", "scope": "field" if vineyard_id else "site", "vineyard_id": vineyard_id,
        "min_confidence": cutoff, "length_m": round(line.length, 3),
        "targets": len(rows), "visited": report["visited"], "unreachable": len(rows) - report["visited"],
        "over_budget": sum(item["status"] == "over_budget" for item in rows),
        "outside_share": round(report["outside_share"], 5), "robust_outside_share": round(report["robust_outside_share"], 5),
        "start_gap_m": round(report["start_gap_m"], 3), "end_gap_m": round(report["end_gap_m"], 3),
        "legal": bool(report["legal"]), "closed": bool(report["closed"]),
    }}


def _best_routes(prepared: Plan, includes: list[set[int]], budget: float) -> list[tuple]:
    """The best route for each target set: built up from START, then warm-started from every other set's tour and
    offered that route's line as it is, keeping (fits the budget, most targets visited, shortest). A route over a
    subset of a set's targets is a legal route for the set, so a set's route visits at least as many of its
    targets as any other set's route does."""
    targets = prepared.targets

    def checked(index: int, line: LineString, status: dict[int, str], report: dict[str, Any], borrowed: bool = False) -> tuple:
        line = LineString(np.round(np.asarray(line.coords), 3))
        if borrowed:  # another set's line: its unvisited targets of this set were never offered to it
            status = status | {k: "over the outside budget: not added" for k in includes[index] - status.keys() if line.distance(targets[k]["point"]) > VISIT_RADIUS_M}
        report = report | check_route(line, prepared.features, [targets[i]["point"] for i in sorted(includes[index])], prepared.checks)
        report["robust_outside_m"] = line.length - _length_in(line, prepared.space)
        report["robust_outside_share"] = report["robust_outside_m"] / line.length
        rows = _target_rows(line, targets, includes[index], status)
        fits = report["legal"] and report["closed"] and report["robust_outside_share"] <= budget + 1e-9
        return (fits, report["visited"], -line.length), line, rows, report

    best, solved = [], {}
    for i, include in enumerate(includes):
        key = frozenset(include)
        if key not in solved:
            line, _, report = route(prepared, budget, include, build=True)
            solved[key] = checked(i, line, report["status"], report)
        best.append(solved[key])
    version, tried, improved = [0] * len(includes), set(), True
    while improved:
        improved = False
        for i, j in ((i, j) for i in range(len(includes)) for j in range(len(includes)) if i != j and includes[i] != includes[j]):
            other = best[j]
            reach = sum(other[1].distance(targets[k]["point"]) <= VISIT_RADIUS_M for k in includes[i])
            if not other[0][0] or reach < best[i][0][1] or (i, j, version[j]) in tried:
                continue
            tried.add((i, j, version[j]))
            line, _, report = route(prepared, budget, includes[i], other[3]["tour"])
            for candidate in (checked(i, line, report["status"], report), checked(i, other[1], other[3]["status"], other[3], borrowed=True)):
                if candidate[0] > best[i][0]:
                    best[i], improved = candidate, True
                    version[i] += 1
    return best


def routes_by_confidence(features: list[dict[str, Any]], targets: list[dict[str, Any]], cutoffs: tuple[float | None, ...] = (None, 0.5, 0.7),
                         budget: float = OUTSIDE_BUDGET, fields: bool = True) -> list[tuple[dict[str, Any], LineString, list[dict[str, Any]], dict[str, Any]]]:
    """One site-wide route per POI confidence cutoff (None: every target; waste and targets without a confidence
    always count), and with `fields` one per field (vineyard_id) and cutoff over that field's targets, all planned
    once and chosen by `_best_routes`. Site routes come first, in `cutoffs` order; the first is the submission.
    Each item: the client's route feature (label/source "route", scope "site" or "field", vineyard_id,
    min_confidence, length_m, targets, visited, unreachable, over_budget, outside shares, start/end gaps, legal,
    closed), the line, the target rows and the full report."""
    prepared = plan(features, targets)
    kept = [{index for index, target in enumerate(targets) if cutoff is None or target.get("confidence") is None or target["confidence"] >= cutoff}
            for cutoff in cutoffs]
    scopes = [("", kept)]
    if fields:
        for vineyard_id in sorted({target.get("vineyard_id") or "" for target in targets} - {""}):
            field = {index for index, target in enumerate(targets) if target.get("vineyard_id") == vineyard_id}
            scopes.append((vineyard_id, [field & include for include in kept]))
    result = []
    for vineyard_id, includes in scopes:
        pairs = [(cutoff, include) for cutoff, include in zip(cutoffs, includes) if include or not vineyard_id]
        for (cutoff, _), (_, line, rows, report) in zip(pairs, _best_routes(prepared, [include for _, include in pairs], budget)):
            result.append((_route_feature(cutoff, vineyard_id, line, rows, report), line, rows, report))
    return result
