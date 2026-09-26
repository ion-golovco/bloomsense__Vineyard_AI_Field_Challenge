"""Challenge route solver: one closed walk from START that passes within 2 m of every reachable target and keeps to
passable space (inter-rows plus passages, minus forbidden zones), in EPSG:32635. `marcaj-export` calls `solve`.

The route plans on `robust_space`, the scene's inter-rows eroded by 0.3 m plus the passages, rasterised on a 0.5 m
grid with each cell's passable cover taken from a 3x finer raster. Cells outside it but within `CORRIDOR_M` stay
walkable at `OUTSIDE_WEIGHT` times the cost unless forbidden, on a row strip (axis ± 0.3 m, the trellis), on a
canopy, on a building or on the imagery's no-data margin: that is how it reaches blocks behind a bare headland and crosses between the two passage components. Inside
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

import hashlib
import json
import math
import pickle
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy import ndimage
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely import STRtree
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import unary_union

from marcaj.mosaic import load_mosaic
from marcaj.routing import VISIT_RADIUS_M, YOUNG_CANOPY_M2, _geometries, _length_in, check_route, check_spaces
from marcaj.scene import PREDICTION, is_scored, waste_id
from marcaj.tiles import REPO_ROOT

GRID_M = 0.5
FINE = 3
SAMPLES = 8
CHUNK = 1 << 20
MARGIN_M = 0.75
EDGE_PENALTY = 1.0
# a metre outside passable space costs as much as this many inside: high, so the route takes long detours through lanes
# and passages rather than spend outside budget. Site all-POI route on v4.5 (177 targets), budget 1.2% robust:
# weight 20: 9,270 m, 120 visited; 40: 10,068 m, 129; 60: 10,492 m, 134 (1.18% robust, 0.60% plain); 100: 10,483 m, 132
OUTSIDE_WEIGHT = 60.0
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
# a target in a block (vineyard_id) the route does not reach yet counts as 1 + BLOCK_VALUE targets, in `_add`'s targets
# per outside metre and in `_best_routes`' choice, so the outside budget goes to one visit of every block with a gap or
# waste before a further target of a block already visited; waste without a vineyard_id never counts as a block
BLOCK_VALUE = 3.0
# Row hops: a walker may step across a vine row where it has no canopy, instead of going round the row end. A hop
# joins the two lane cells HOP_REACH_M either side of the row axis, where a HOP_WIDTH_M wide corridor along the row
# holds no canopy (and no forbidden zone), every HOP_STEP_M along each row. It costs its metres (outside passable
# space at OUTSIDE_WEIGHT, like any other step) plus a penalty in equivalent metres, and its outside metres count
# against the outside budget. On the site prediction (179 targets, closed from START): no hops 9,063 m and 126
# visited; penalty 25: 8,650 m, 123, 26 hops; 50: 8,650 m, 125, 10 hops. Hops spend budget that headland access
# would use, so marcaj-export keeps a hop route only where it is not worse than the route without.
HOP_PENALTY_M = 50.0
HOP_OUTSIDE_WEIGHT = OUTSIDE_WEIGHT
HOP_REACH_M = 0.9
HOP_WIDTH_M = 0.8
HOP_STEP_M = 0.5
HOP_ALIGN_M = 0.3  # gaps in consecutive rows line up for a straight multi-row crossing within this along the row
HOP_MIN_STEP_M = 1.3  # longer than any grid step (1.12 m): a step this long in a path is a hop
SNAP_M = 25.0  # a requested start or end snaps to the nearest passable cell within this distance
OPEN_LINK = 1e6  # the dummy END -> START leg of an open route costs -OPEN_LINK, so every tour move keeps it
PLAN_CACHE = REPO_ROOT / "data" / "generated" / "work" / "route" / "cache"
PLAN_VERSION = 7  # bump when the grid, hops or matrices change, so cached plans are rebuilt
DEV_WORLD_SHARE = 0.25
PLAN_FILES = 3
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
    graph: csr_matrix     # symmetric step costs, row hops included
    inside: np.ndarray = None     # node -> passable (cover >= 0.5)
    hop_tier: dict = field(default_factory=dict)  # (node a, node b), a < b -> 1 over a gap, 2 over young canopy
    hop_rows: dict = field(default_factory=dict)  # (node a, node b), a < b -> rows crossed, for multi-row crossings

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


def _hop_edges(features: list[dict[str, Any]], grid: Grid, penalty: float, young_penalty: float | None,
               rows_max: int = 1) -> tuple[np.ndarray, ...]:
    """Row hops (a, b, cost, tier, rows crossed) between passable cells either side of a row axis: tier 1 where the
    corridor holds no canopy, tier 2 (only with a `young_penalty`) where it holds only canopies under
    YOUNG_CANOPY_M2. Never through a mature canopy or a forbidden zone. With `rows_max` > 1, single hops whose
    gaps line up across consecutive rows (rows parallel within 6 degrees, gap centres within HOP_ALIGN_M along the
    row) chain into one straight crossing of up to `rows_max` rows, allowed only where its whole corridor holds no
    canopy; it costs `penalty` per row crossed."""
    none = (np.zeros(0, np.int32), np.zeros(0, np.int32), np.zeros(0), np.zeros(0, np.int8), np.zeros(0, np.int8))
    rows = [row for row in _geometries(features, "row") if row.length > HOP_STEP_M]
    if not rows:
        return none
    lines = np.array(rows, dtype=object)
    lengths = shapely.length(lines)
    counts = (lengths / HOP_STEP_M).astype(int)
    owner = np.repeat(np.arange(len(lines)), counts)
    at = (np.concatenate([np.arange(count) for count in counts]) + 0.5) / counts[owner]
    delta = 0.25 / lengths[owner]
    point = shapely.get_coordinates(shapely.line_interpolate_point(lines[owner], at, normalized=True))
    ahead = shapely.get_coordinates(shapely.line_interpolate_point(lines[owner], np.minimum(at + delta, 1), normalized=True))
    behind = shapely.get_coordinates(shapely.line_interpolate_point(lines[owner], np.maximum(at - delta, 0), normalized=True))
    tangent = (ahead - behind) / np.linalg.norm(ahead - behind, axis=1)[:, None]
    normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])

    def cell(xy: np.ndarray) -> np.ndarray:
        col, row = ~grid.transform * (xy[:, 0], xy[:, 1])
        row, col = np.floor(row).astype(int), np.floor(col).astype(int)
        ok = (row >= 0) & (row < grid.node.shape[0]) & (col >= 0) & (col < grid.node.shape[1])
        nodes = np.full(len(xy), -1, np.int32)
        nodes[ok] = grid.node[row[ok], col[ok]]
        nodes[nodes >= 0] = np.where(grid.inside[nodes[nodes >= 0]], nodes[nodes >= 0], -1)
        return nodes

    row_tree = STRtree(rows)
    canopies = _geometries(features, "vineyard")
    canopy_tree = STRtree(canopies) if canopies else None
    small_piece = shapely.area(np.array(canopies, dtype=object)) < YOUNG_CANOPY_M2 if canopies else np.zeros(0, bool)
    forbidden = unary_union(_geometries(features, "forbidden"))

    def clear(a: np.ndarray, b: np.ndarray, crossings: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Which straight edges a -> b cross exactly `crossings` rows through a corridor free of mature canopy and
        forbidden zones; their tier and their cost without the penalty."""
        segments = shapely.linestrings(np.stack([grid.xy(a), grid.xy(b)], axis=1))
        crossed = np.bincount(row_tree.query(segments, predicate="intersects")[0], minlength=len(segments))
        corridors = shapely.buffer(segments, HOP_WIDTH_M / 2, cap_style="flat")
        mature, young = np.zeros(len(segments), bool), np.zeros(len(segments), bool)
        if canopy_tree is not None:
            hit, piece = canopy_tree.query(corridors, predicate="intersects")
            mature[hit[~small_piece[piece]]] = True
            young[hit[small_piece[piece]]] = True
        blocked = shapely.intersects(corridors, forbidden) if not forbidden.is_empty else np.zeros(len(segments), bool)
        tier = np.where(young, 2, 1).astype(np.int8)
        keep = (crossed == crossings) & ~mature & ~blocked & ((tier == 1) | (young_penalty is not None and crossings == 1))
        share = grid.outside(a, b)
        return keep, tier, shapely.length(segments) * ((1 - share) + share * HOP_OUTSIDE_WEIGHT)

    a, b = cell(point + HOP_REACH_M * normal), cell(point - HOP_REACH_M * normal)
    ok = (a >= 0) & (b >= 0) & (a != b)
    a, b, point, tangent, normal = a[ok], b[ok], point[ok], tangent[ok], normal[ok]
    keep, tier, metres = clear(a, b, 1)
    a, b, point, tangent, normal, tier, metres = a[keep], b[keep], point[keep], tangent[keep], normal[keep], tier[keep], metres[keep]
    edges = [(a, b, metres + np.where(tier == 1, penalty, young_penalty or 0.0), tier, np.ones(a.size, np.int8))]
    if rows_max > 1 and a.size:
        from scipy.spatial import cKDTree
        # single hops as oriented pieces: `near` -> `far`, both ways round
        near, far = np.concatenate([a, b]), np.concatenate([b, a])
        piece_point, piece_tangent = np.concatenate([point, point]), np.concatenate([tangent, tangent])
        piece_dir = np.concatenate([-normal, normal])  # far - near is +normal for (b, a): a sits at +normal
        tree = cKDTree(grid.xy(near))
        level = (near, far, piece_point, piece_tangent, piece_dir)
        for crossings in range(2, rows_max + 1):
            start, end, last_point, last_tangent, direction = level
            found = tree.query_ball_point(grid.xy(end), r=1.6)
            first, second = [], []
            for index, candidates in enumerate(found):
                for candidate in candidates:
                    if (np.dot(piece_dir[candidate], direction[index]) > 0.995
                            and abs(np.dot(piece_point[candidate] - last_point[index], last_tangent[index])) <= HOP_ALIGN_M
                            and np.dot(piece_point[candidate] - last_point[index], direction[index]) > 1.0):
                        first.append(index)
                        second.append(candidate)
            if not first:
                break
            first, second = np.array(first), np.array(second)
            chained = (start[first], far[second], piece_point[second], piece_tangent[second], piece_dir[second])
            keep, _, metres = clear(chained[0], chained[1], crossings)
            chained = tuple(item[keep] for item in chained)
            edges.append((chained[0], chained[1], metres[keep] + crossings * penalty, np.ones(keep.sum(), np.int8),
                          np.full(keep.sum(), crossings, np.int8)))
            level = chained
    a, b, cost, tier, crossed = (np.concatenate(items) for items in zip(*edges))
    low, high = np.minimum(a, b), np.maximum(a, b)
    order = np.lexsort((cost, high, low))
    first = order[np.r_[True, (low[order][1:] != low[order][:-1]) | (high[order][1:] != high[order][:-1])]]
    return low[first], high[first], cost[first], tier[first], crossed[first]


def build_grid(features: list[dict[str, Any]], space=None, hop_penalty: float | None = None, young_penalty: float | None = None,
               hop_rows: int = 1) -> Grid:
    """The walkable grid over `space` (default `robust_space`), with row hops at `hop_penalty` equivalent metres
    each when given (and over young canopy at `young_penalty`). Site prediction: 3.5k × 3.6k cells, 1.5 M nodes, 2 s."""
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
    obstacles = burn([row.buffer(ROW_HALF_M, cap_style="flat") for row in _geometries(features, "row")], True)
    # a canopy lying mostly in passable space (a reference canopy inside a passage) blocks every cell it touches:
    # walking it at a high cost let a 0.5 m step clip 0.6 m of it. Canopies along a row that only graze passable space
    # (at a lane mouth) stay walkable at the outside cost: blocking those cut the site route from 127 targets to 122
    canopies = np.array(_geometries(features, "vineyard"), dtype=object)
    shapely.prepare(space)
    loose = np.zeros(len(canopies), bool)
    touching = np.flatnonzero(shapely.intersects(canopies, space)) if len(canopies) else np.zeros(0, int)
    if touching.size:
        loose[touching] = shapely.area(shapely.intersection(canopies[touching], space)) >= 0.5 * shapely.area(canopies[touching])
    canopy = burn(list(canopies[loose]), True)
    obstacles |= burn(list(canopies), True)
    # nobody walks through a building (marcaj.obstacles), even outside passable space: on v4.5 the site route crossed a
    # 66 m2 shed for 43 m of its outside metres
    buildings = [shape(item["geometry"]) for item in features if item["properties"].get("label") == "obstacle"
                 and item["properties"].get("obstacle_type") == "building" and is_scored(item)]
    blocked = burn(_geometries(features, "forbidden") + buildings, True) | canopy | (obstacles & ~passable)
    corridor = (ndimage.distance_transform_edt(~passable) * GRID_M <= CORRIDOR_M) & burn([area]) & ~blocked & _visible(transform, shape_)
    walkable = (passable & ~canopy) | corridor
    margin = ndimage.distance_transform_edt(passable) * GRID_M - GRID_M / 2

    flat = np.flatnonzero(walkable)
    node = np.full(shape_, -1, np.int32)
    node.flat[flat] = np.arange(flat.size, dtype=np.int32)
    rows, cols = np.divmod(flat, shape_[1])
    outside = 1 - cover.flat[flat]
    # a row strip or canopy edge overlapping passable space stays walkable (it would cut lanes) but costs as much as outside
    inside_cost = np.where(obstacles.flat[flat], OUTSIDE_WEIGHT, 1 + EDGE_PENALTY * np.clip((MARGIN_M - margin.flat[flat]) / MARGIN_M, 0, 1))
    cost = (1 - outside) * inside_cost + outside * OUTSIDE_WEIGHT
    grid = Grid(transform, node, rows.astype(np.int32), cols.astype(np.int32), fine, cost, csr_matrix((flat.size, flat.size)), passable.flat[flat])

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
    if hop_penalty is not None:
        hop_a, hop_b, hop_cost, hop_tier, hop_crossed = _hop_edges(features, grid, hop_penalty, young_penalty, hop_rows)
        sources.append(hop_a)
        targets.append(hop_b)
        weights.append(hop_cost)
        grid.hop_tier = dict(zip(zip(hop_a.tolist(), hop_b.tolist()), hop_tier.tolist()))
        grid.hop_rows = {pair: int(k) for pair, k in zip(zip(hop_a.tolist(), hop_b.tolist()), hop_crossed.tolist()) if k > 1}
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
            "id": waste_id(feature) if properties["label"] == "waste" else str(properties.get("id") or f"{properties['label']}-{index:03d}"), "label": properties["label"],
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


def snap(grid: Grid, point: Point, radius: float = SNAP_M) -> tuple[int, float] | None:
    """The passable node nearest to `point` within `radius`, and its distance; None if there is none."""
    row, col = _cell(grid, point)
    reach = int(radius / GRID_M) + 1
    r0, c0 = max(int(row) - reach, 0), max(int(col) - reach, 0)
    r1, c1 = min(int(row) + reach + 1, grid.node.shape[0]), min(int(col) + reach + 1, grid.node.shape[1])
    if r0 >= r1 or c0 >= c1:
        return None
    window = grid.node[r0:r1, c0:c1]
    rr, cc = np.mgrid[r0:r1, c0:c1]
    distance = np.hypot(rr + 0.5 - row, cc + 0.5 - col) * GRID_M
    ok = window >= 0
    ok[ok] = grid.inside[window[ok]]
    distance[~ok] = np.inf
    k = np.unravel_index(int(distance.argmin()), distance.shape)
    if not distance[k] <= radius:
        return None
    return int(window[k]), float(distance[k])


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


def _drop(tour: np.ndarray, outside: np.ndarray, access: np.ndarray, keep: int | None = None) -> tuple[list[int], float]:
    """Tour positions whose removal saves the most outside metres per target, and the saving: a single stop, or a
    whole run of consecutive stops that all need outside metres from START (e.g. a block behind a headland, where
    dropping one stop saves nothing because the others still share the access). The `keep` stop (an open route's
    END) is never dropped."""
    n = len(tour)
    runs, run = [], []
    for i in range(1, n + 1):
        if i < n and access[tour[i]] > 0.3 and tour[i] != keep:
            run.append(i)
        else:
            runs += [run] if len(run) > 1 else []
            run = []
    best, best_ratio, best_saving = [], -np.inf, 0.0
    for positions in [[i] for i in range(1, n) if tour[i] != keep] + runs:
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


def _leg(plan: "Plan", a: int, b: int) -> list[int]:
    """Grid nodes of the shortest path from stop a to stop b. A base stop's tree does not reach a stop added by
    `with_endpoints`, so such a leg is that stop's path reversed (the graph is symmetric)."""
    if b in plan.fresh and a not in plan.fresh:
        return _path(plan.trees[b], plan.stops[b], plan.stops[a])[::-1]
    return _path(plan.trees[a], plan.stops[a], plan.stops[b])


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
    end: Point | None = None      # an open route's END (None: the route returns to `start`)
    end_stop: int | None = None   # END's stop index, the last stop
    end_group: int | None = None  # END's group index, the last group
    fresh: frozenset = frozenset()  # stops added by `with_endpoints`, whose trees reach every stop
    hop_penalty: float | None = None
    young_penalty: float | None = None
    start_snap_m: float = 0.0
    end_snap_m: float = 0.0


def plan(features: list[dict[str, Any]], targets: list[dict[str, Any]] | None = None, hop_penalty: float | None = None,
         young_penalty: float | None = None) -> Plan:
    """Grid, stops and all-pairs matrices from the scene's START; 0.3 s per stop (one Dijkstra over 1.4 M nodes),
    2 min for the 428 stops of the site prediction's 220 targets. `hop_penalty` (equivalent metres) enables row
    hops, `young_penalty` also hops over young canopy. `with_endpoints` re-roots it at another start and end."""
    started = time.perf_counter()
    targets = route_target_features(features) if targets is None else targets
    starts = _geometries(features, "start")
    if len(starts) != 1:
        raise ValueError(f"Expected one start point, found {len(starts)}")
    start = starts[0]
    space = robust_space(features)
    grid = build_grid(features, space, hop_penalty, young_penalty)
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
        check_spaces(features), np.zeros((len(stops), len(stops)), bool), {}, hop_penalty=hop_penalty, young_penalty=young_penalty,
    )


def _endpoint(grid: Grid, point: Point, name: str) -> tuple[int, Point, float]:
    """The passable node a requested start or end snaps to, the point the line begins or ends at (the requested
    point itself when it lies in that cell, else the cell centre), and the snap distance."""
    snapped = snap(grid, point)
    if snapped is None:
        raise ValueError(f"The {name} point is more than {SNAP_M:.0f} m from any inter-row or passage")
    node, _ = snapped
    row, col = _cell(grid, point)
    if (int(row), int(col)) == (int(grid.rows[node]), int(grid.cols[node])):
        return node, point, 0.0
    at = Point(grid.xy(np.array([node]))[0])
    return node, at, point.distance(at)


def with_endpoints(base: Plan, start: Point | None = None, end: Point | None = None) -> Plan:
    """`base` re-rooted at `start` (default its START) and, when `end` snaps to another cell, opened into a path
    start -> targets -> end: END becomes one more fixed stop whose leg back to START is a dummy costing -OPEN_LINK
    (length and outside 0), so every tour move keeps the two adjacent and a closed tour over the stops is the open
    path. One Dijkstra per new endpoint (0.3 s); the base's matrices are copied, not recomputed."""
    grid = base.grid
    start_node, start_at, start_snap = _endpoint(grid, start, "start") if start is not None else (base.stops[0], base.start, 0.0)
    end_node, end_at, end_snap = _endpoint(grid, end, "end") if end is not None else (start_node, start_at, 0.0)
    is_open = end_node != start_node
    n0 = len(base.stops)
    n = n0 + is_open
    stops = [start_node] + base.stops[1:] + ([end_node] if is_open else [])
    stops_array = np.array(stops)
    matrices = []
    for matrix, fill in ((base.cost, np.inf), (base.length, np.inf), (base.outside, np.inf), (base.measured, False)):
        grown = np.full((n, n), fill, dtype=matrix.dtype)
        grown[:n0, :n0] = matrix
        matrices.append(grown)
    cost, length, outside, measured = matrices
    trees = list(base.trees) + ([None] if is_open else [])
    fresh = ([0] if start is not None else []) + ([n - 1] if is_open else [])
    for index in fresh:
        dist, pred = dijkstra(grid.graph, indices=stops[index], return_predecessors=True)
        cost[index] = cost[:, index] = dist[stops_array]
        length[index], outside[index], trees[index] = _trace(grid, pred, stops[index], stops_array)
        length[:, index], outside[:, index] = length[index], outside[index]
        measured[index] = measured[:, index] = False
    groups = list(base.groups) + ([np.array([n - 1])] if is_open else [])
    group_target = list(base.group_target) + ([-1] if is_open else [])
    unreachable = {index: reason for index, reason in base.unreachable.items() if reason != "no walkable connection to START"}
    for group in range(1, len(base.groups)):
        if base.group_target[group] not in unreachable and not np.isfinite(cost[0, groups[group]]).any():
            unreachable[base.group_target[group]] = "no walkable connection to the start"
    if is_open:
        if not np.isfinite(cost[0, n - 1]):
            raise ValueError("The end point is not connected to the start point by walkable space")
        cost[0, n - 1] = cost[n - 1, 0] = -OPEN_LINK
        length[0, n - 1] = length[n - 1, 0] = outside[0, n - 1] = outside[n - 1, 0] = 0.0
        measured[0, n - 1] = measured[n - 1, 0] = True
    return replace(
        base, start=start_at, stops=stops, groups=groups, group_target=group_target,
        group_of_stop=np.repeat(np.arange(len(groups)), [len(group) for group in groups]), cost=cost, length=length,
        outside=outside, trees=trees, unreachable=unreachable, measured=measured,
        end=end_at if is_open else None, end_stop=n - 1 if is_open else None, end_group=len(groups) - 1 if is_open else None,
        fresh=frozenset(fresh), start_snap_m=start_snap, end_snap_m=end_snap if is_open else start_snap,
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
    block = [(plan.targets[target].get("vineyard_id") or "") if target >= 0 else "" for target in plan.group_target]
    covered = {block[int(plan.group_of_stop[stop])] for stop in tour} | {""}
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
                ratio = (1 + freed + BLOCK_VALUE * (block[pool[owner[i]]] not in covered)) / max(extra_outside[i], 0.05)
                if ratio > best_ratio:
                    best_ratio, pick = ratio, int(i)
        k = int(edge[pick])
        tour = np.insert(tour, k + 1, stops[pick])
        route_length += float(extra_length[pick])
        route_outside += float(extra_outside[pick])
        group = pool.pop(int(owner[pick]))
        added.append(group)
        covered.add(block[group])
    return tour, added


def _needs_outside(plan: "Plan", tour: list[int], index: int) -> float | None:
    """Outside metres (of the planning space) that inserting target `index` at its cheapest place in `tour` adds,
    exact where the legs are measured; None if it has no stop reachable from the tour."""
    groups = [group for group, target in enumerate(plan.group_target) if target == index]
    if not groups or len(tour) == 0:
        return None
    tour = np.asarray(tour)
    stops = plan.groups[groups[0]]
    extra, edge = _insertion(plan.cost, tour, stops)
    best = int(np.argmin(extra))
    if not np.isfinite(extra[best]):
        return None
    outside = plan.outside * np.where(plan.measured, 1.0, UNDERCOUNT)
    a, b, stop = tour[edge[best]], np.roll(tour, -1)[edge[best]], stops[best]
    return max(float(outside[a, stop] + outside[stop, b] - outside[a, b]), 0.0)


def _target_rows(line: LineString, targets: list[dict[str, Any]], included: set[int], status: dict[int, str],
                 plan: "Plan | None" = None, tour: list[int] | None = None) -> list[dict[str, Any]]:
    """One row per included target: visited within 2 m, over_budget (with `needs_outside_m`, the outside metres
    its cheapest insertion into `tour` would add), unreachable or missed, with its distance and the reason."""
    rows = []
    for index in sorted(included):
        target = targets[index]
        distance = line.distance(target["point"])
        reason = status.get(index, "")
        state = "visited" if distance <= VISIT_RADIUS_M else "over_budget" if reason.startswith("over") else "unreachable" if reason else "missed"
        needs = _needs_outside(plan, tour, index) if state == "over_budget" and plan is not None and tour is not None else None
        rows.append({
            "id": target["id"], "label": target["label"], "x": round(target["point"].x, 3), "y": round(target["point"].y, 3),
            "status": state, "distance_m": round(distance, 2),
            "needs_outside_m": "" if needs is None else round(needs, 1),
            "gap_ends_m": " ".join(f"{line.distance(end):.2f}" for end in target.get("ends", ())),
            "reason": reason,
        })
    return rows


def _measure(plan: Plan, tour: np.ndarray) -> None:
    """Replace the raster outside metres of the tour's legs by their exact metres outside the planning space."""
    for a, b in zip(tour, np.roll(tour, -1)):
        if not plan.measured[a, b]:
            nodes = _leg(plan, a, b)
            leg = LineString(plan.grid.xy(np.array(nodes))) if len(nodes) > 1 else None
            plan.outside[a, b] = plan.outside[b, a] = 0.0 if leg is None else max(leg.length - _length_in(leg, plan.space), 0.0)
            plan.measured[a, b] = plan.measured[b, a] = True


def route(plan: Plan, budget: float = OUTSIDE_BUDGET, include: set[int] | None = None, seed: list[int] | None = None,
          scale: float = UNDERCOUNT, build: bool = False) -> tuple[LineString, list[dict[str, Any]], dict[str, Any]]:
    """The closed tour over the planned targets (or the `include` indices of them) with at most `budget` of its
    length outside `robust_space`, expanded to a line (plus legal walks past gap ends) and checked; for a plan
    opened by `with_endpoints`, the path from its start to its END.

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
    fixed = [0] + ([plan.end_group] if plan.end_group is not None else [])
    fixed_stops = [0] + ([plan.end_stop] if plan.end_stop is not None else [])
    if build:
        seed = fixed_stops
    tour = None if seed is None else np.array([stop for stop in seed if stop in fixed_stops or plan.group_target[int(plan.group_of_stop[stop])] in included])
    if tour is not None and plan.end_stop is not None and plan.end_stop not in tour:
        tour = np.append(tour, plan.end_stop)
    active = candidates if tour is None else sorted({int(plan.group_of_stop[stop]) for stop in tour} - set(fixed))
    for _ in range(MAX_ROUNDS):
        outside = plan.outside * np.where(plan.measured, 1.0, scale)
        if tour is None:
            tour = _tour(cost, [groups[g] for g in fixed + active])
        while len(tour) > len(fixed):
            after = np.roll(tour, -1)
            if outside[tour, after].sum() <= budget * length[tour, after].sum():
                break
            positions, saving = _drop(tour, outside, outside[0], plan.end_stop)
            if saving < 0.1:  # outside metres that no removal saves (shared by all): build up from START instead
                tour, active = np.array(fixed_stops), []
                break
            for position in positions:
                group = int(plan.group_of_stop[tour[position]])
                status[plan.group_target[group]] = f"over the outside budget: dropping it{f' and {len(positions) - 1} more behind the same outside access' if len(positions) > 1 else ''} saves {saving:.1f} m outside passable space"
                active.remove(group)
            tour = _tour(cost, [groups[g] for g in fixed + active])
        tour, added = _add(plan, tour, [group for group in candidates if group not in active], budget, outside)
        for group in added:
            status.pop(plan.group_target[group], None)
        active = active + added
        tour = _or_opt(cost, _two_opt(cost, tour))
        if plan.end_stop is not None and len(tour) > 2 and tour[1] == plan.end_stop:
            tour = np.concatenate([tour[:1], tour[1:][::-1]])  # START, targets..., END; the dummy END -> START leg closes it
        if plan.end_stop is not None and tour[-1] != plan.end_stop:
            raise RuntimeError(f"open tour lost its END -> START link: END at position {list(tour).index(plan.end_stop)} of {len(tour)}")
        _measure(plan, tour)
        after = np.roll(tour, -1)
        route_length, route_outside = length[tour, after].sum(), plan.outside[tour, after].sum()
        nodes, at = [], []
        for a, b in zip(tour, after) if plan.end_stop is None else zip(tour[:-1], tour[1:]):
            leg = _leg(plan, a, b)
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
        end = start if plan.end is None else plan.end
        coords = [(start.x, start.y)] + [tuple(xy) for xy in grid.xy(np.array(nodes))] + [(end.x, end.y)]
        line = LineString(coords).simplify(SIMPLIFY_M)
        robust_outside = line.length - _length_in(line, plan.space)
        if robust_outside <= budget * line.length or len(tour) == len(fixed):
            break
        scale *= 1.1  # the measured legs are exact now; unmeasured estimates were too low as well
    for group in candidates:
        if group not in active:
            status.setdefault(plan.group_target[group], "over the outside budget: not added")
    report = check_route(line, plan.features, [targets[index]["point"] for index in sorted(included)], plan.checks, start, end)
    steps = np.hypot(np.diff(grid.rows[nodes]), np.diff(grid.cols[nodes])) * GRID_M if len(nodes) > 1 else np.zeros(0)
    hops = np.flatnonzero(steps > HOP_MIN_STEP_M)
    pairs = [(min(nodes[i], nodes[i + 1]), max(nodes[i], nodes[i + 1])) for i in hops]
    tiers = [grid.hop_tier.get(pair, 1) for pair in pairs]
    report.update({
        "robust_outside_m": robust_outside, "robust_outside_share": robust_outside / line.length, "budget": budget,
        "open": plan.end is not None, "hops": int(hops.size), "hop_m": float(steps[hops].sum()), "young_hops": sum(tier == 2 for tier in tiers),
        "rows_crossed": sum(grid.hop_rows.get(pair, 1) for pair in pairs),
        "hop_penalty": plan.hop_penalty, "start_snap_m": plan.start_snap_m, "end_snap_m": plan.end_snap_m,
        "tour_cost": float(cost[tour, np.roll(tour, -1)].sum() + (OPEN_LINK if plan.end is not None else 0.0)),
        "gap_walks": sum(len(items) for items in walks.values()),
        "grid_nodes": int(grid.rows.size), "stops": len(stops), "legs_outside_m": float(route_outside), "outside_scale": round(float(scale), 3),
        **plan.seconds, "tour_s": round(time.perf_counter() - started, 1), "tour": [int(stop) for stop in tour], "status": status,
    })
    return line, _target_rows(line, targets, included, status, plan, report["tour"]), report


def solve(features: list[dict[str, Any]], targets: list[dict[str, Any]] | None = None, budget: float = OUTSIDE_BUDGET) -> tuple[LineString, list[dict[str, Any]], dict[str, Any]]:
    """`route(plan(features, targets), budget)`: the scored world is `features` as check_route sees it (predictions
    excluded); `targets` are {id, label, point, optional ends: a gap's two end points, walked past too}, by default
    the scored inspection and waste features."""
    return route(plan(features, targets), budget)


def _route_feature(cutoff: float | None, vineyard_id: str, line: LineString, rows: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": mapping(line), "properties": {
        "label": "route", "source": "route", "scope": "field" if vineyard_id else "site", "vineyard_id": vineyard_id,
        "min_confidence": cutoff, "length_m": round(line.length, 3),
        "targets": len(rows), "visited": report["visited"], "unreachable": sum(item["status"] == "unreachable" for item in rows),
        "over_budget": sum(item["status"] == "over_budget" for item in rows),
        "outside_share": round(report["outside_share"], 5), "robust_outside_share": round(report["robust_outside_share"], 5),
        "start_gap_m": round(report["start_gap_m"], 3), "end_gap_m": round(report["end_gap_m"], 3),
        "hops": report["hops"], "hop_m": round(report["hop_m"], 2), "open": report["open"],
        "forbidden_m": round(report["forbidden_m"], 3), "canopy_m": round(report["canopy_m"], 3),
        "outside_budget_m": round(report["budget"] * line.length, 1), "robust_outside_m": round(report["robust_outside_m"], 1),
        "legal": bool(report["legal"]), "closed": bool(report["closed"]),
        # per target: visited, over_budget (needs_outside_m: what reaching it would add outside the lanes) or unreachable
        "target_status": [{key: item[key] for key in ("id", "status", "distance_m", "needs_outside_m", "reason")} for item in rows],
    }}


def _best_routes(prepared: Plan, includes: list[set[int]], budget: float) -> list[tuple]:
    """The best route for each target set: built up from START, then warm-started from every other set's tour and
    offered that route's line as it is, keeping (fits the budget, most targets visited, shortest). A route over a
    subset of a set's targets is a legal route for the set, so a set's route visits at least as many of its
    targets as any other set's route does."""
    targets = prepared.targets

    def value(line: LineString, include: set[int]) -> float:
        """Targets visited plus BLOCK_VALUE per block (vineyard_id) with at least one of them visited."""
        seen = [k for k in include if line.distance(targets[k]["point"]) <= VISIT_RADIUS_M]
        return len(seen) + BLOCK_VALUE * len({targets[k].get("vineyard_id") or "" for k in seen} - {""})

    def checked(index: int, line: LineString, status: dict[int, str], report: dict[str, Any], borrowed: bool = False) -> tuple:
        line = LineString(np.round(np.asarray(line.coords), 3))
        if borrowed:  # another set's line: its unvisited targets of this set were never offered to it
            status = status | {k: "over the outside budget: not added" for k in includes[index] - status.keys() if line.distance(targets[k]["point"]) > VISIT_RADIUS_M}
        report = report | check_route(line, prepared.features, [targets[i]["point"] for i in sorted(includes[index])], prepared.checks,
                                      prepared.start, prepared.end)
        report["robust_outside_m"] = line.length - _length_in(line, prepared.space)
        report["robust_outside_share"] = report["robust_outside_m"] / line.length
        rows = _target_rows(line, targets, includes[index], status, prepared, report["tour"])
        fits = report["legal"] and report["closed"] and report["robust_outside_share"] <= budget + 1e-9
        return (fits, value(line, includes[index]), -line.length), line, rows, report

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
            reach = value(other[1], includes[i])
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
                         budget: float = OUTSIDE_BUDGET, fields: bool = True, hop_penalty: float | None = HOP_PENALTY_M,
                         start: Point | None = None, end: Point | None = None,
                         prepared: Plan | None = None) -> list[tuple[dict[str, Any], LineString, list[dict[str, Any]], dict[str, Any]]]:
    """One site-wide route per POI confidence cutoff (None: every target; waste and targets without a confidence
    always count), and with `fields` one per field (vineyard_id) and cutoff over that field's targets, all planned
    once and chosen by `_best_routes`. Site routes come first, in `cutoffs` order; the first is the submission.
    Each item: the client's route feature (label/source "route", scope "site" or "field", vineyard_id,
    min_confidence, length_m, targets, visited, unreachable, over_budget, outside shares, start/end gaps, legal,
    closed, hops), the line, the target rows and the full report. Every route runs from `start` (default START) to
    `end` (default back to its start), with row hops at `hop_penalty` (None: no hops); `prepared` is a ready plan
    of these features and targets."""
    prepared = plan(features, targets, hop_penalty) if prepared is None else prepared
    if start is not None or end is not None:
        prepared = with_endpoints(prepared, start, end)
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


def planning_features(scene: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """The world an on-request route plans on, and its name: the scene's scored features ("scored"). A
    development scene whose scored inter-rows are fewer than DEV_WORLD_SHARE of its predicted ones (the organizer
    examples plus the site prediction, before the Marcaj export) plans on its predictions instead, relabelled
    `dev`, plus the organizer features ("prediction"), as the client's routes do; submission files never do this."""
    features = [item for item in scene["features"] if item["properties"].get("label") != "tile"]
    interrows = [item for item in features if item["properties"].get("label") == "interrow_area"]
    predicted = sum(item["properties"].get("source") == PREDICTION for item in interrows)
    if len(interrows) - predicted >= DEV_WORLD_SHARE * predicted:
        return [item for item in features if is_scored(item)], "scored"
    # ponytail: a count rule, not a flag; once the Marcaj export is in the scene its scored inter-rows win
    return [{**item, "properties": {**item["properties"], "source": "dev"}} if item["properties"].get("source") == PREDICTION else item
            for item in features if item["properties"].get("source") in (PREDICTION, "organizer")], "prediction"


def _plan_key(token: str, targets: list[dict[str, Any]], hop_penalty: float | None, young_penalty: float | None) -> str:
    listed = [(item["id"], round(item["point"].x, 3), round(item["point"].y, 3), [(round(end.x, 3), round(end.y, 3)) for end in item.get("ends", ())])
              for item in targets]
    return hashlib.sha1(json.dumps([PLAN_VERSION, OUTSIDE_WEIGHT, HOP_OUTSIDE_WEIGHT, token, hop_penalty, young_penalty, listed]).encode()).hexdigest()[:16]


def cached_plan(features: list[dict[str, Any]], targets: list[dict[str, Any]], token: str, hop_penalty: float | None = HOP_PENALTY_M,
                young_penalty: float | None = None, cache_dir: Path | None = PLAN_CACHE) -> Plan:
    """`plan(features, targets, hop_penalty, young_penalty)`, pickled under `cache_dir` keyed by `token` (which must
    change with the features, e.g. the scene file's path, size and mtime), the targets and the hop settings: the
    2 min of Dijkstras run once per scene, a later load takes about a second."""
    path = None if cache_dir is None else cache_dir / f"plan_{_plan_key(token, targets, hop_penalty, young_penalty)}.pkl"
    if path is not None and path.is_file():
        try:
            with path.open("rb") as source:
                packed, fine_shape, data, indices, indptr = pickle.load(source)
        except (EOFError, TypeError, ValueError, pickle.UnpicklingError) as error:  # a partial or older file: plan again
            print(f"ignoring unreadable plan cache {path.name}: {error}")
        else:
            if not hasattr(packed.grid, "hop_rows"):  # pickled before multi-row crossings; its hops are single-row
                packed.grid.hop_rows = {}
            graph = csr_matrix((data.astype(np.float64), indices, indptr), shape=(packed.grid.rows.size,) * 2)
            fine = np.unpackbits(packed.grid.fine, count=fine_shape[0] * fine_shape[1]).reshape(fine_shape)
            return replace(packed, features=features, grid=replace(packed.grid, fine=fine, graph=graph))
    prepared = plan(features, targets, hop_penalty, young_penalty)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # the fine raster as bits and the step costs as float32 (a 1e-7 relative change): 250 MB on disk, not 490
        grid = prepared.grid
        packed = replace(prepared, features=None, grid=replace(grid, fine=np.packbits(grid.fine), graph=None))
        partial = path.with_suffix(".partial")
        with partial.open("wb") as output:
            pickle.dump((packed, grid.fine.shape, grid.graph.data.astype(np.float32), grid.graph.indices, grid.graph.indptr), output,
                        protocol=pickle.HIGHEST_PROTOCOL)
        partial.replace(path)
        for stale in sorted(path.parent.glob("plan_*.pkl"), key=lambda item: item.stat().st_mtime)[:-PLAN_FILES]:
            stale.unlink()  # a plan is 250 MB: keep the newest few (hops on and off, one scene)
    return prepared


_PLANS: dict[str, Plan] = {}
_LOCK = threading.Lock()


def request_route(features: list[dict[str, Any]], targets: list[dict[str, Any]], token: str, start: Point | None = None, end: Point | None = None,
                  include: set[int] | None = None, hop_penalty: float | None = HOP_PENALTY_M,
                  budget: float = OUTSIDE_BUDGET) -> tuple[LineString, list[dict[str, Any]], dict[str, Any]]:
    """One route on request from `start` (default START) to `end` (default back to its start) over the `include`
    targets: the scene's plan comes from memory or `cached_plan`, so a request runs one Dijkstra per new endpoint
    plus the tour. Legs measured exactly are written back to the shared plan. Raises ValueError for an endpoint
    that snaps to no passable cell or an END not connected to the start."""
    key = _plan_key(token, targets, hop_penalty, None)
    with _LOCK:  # ponytail: one global lock, requests are solved one at a time; per-plan locks if several users matter
        base = _PLANS.get(key)
        if base is None:
            base = cached_plan(features, targets, token, hop_penalty)
            while len(_PLANS) >= 2:  # hops on and off for one scene
                _PLANS.pop(next(iter(_PLANS)))
            _PLANS[key] = base
        view = with_endpoints(base, start, end)
        line, rows, report = route(view, budget, include, build=True)
        known = len(base.stops)
        base.outside[1:known, 1:known] = view.outside[1:known, 1:known]
        base.measured[1:known, 1:known] = view.measured[1:known, 1:known]
    return line, rows, report

