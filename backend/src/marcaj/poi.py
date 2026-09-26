"""Inspection points (app-only `inspection` Points in EPSG:32635, never Marcaj labels; docs/SPEC.md section 12).

Gap POIs (`reason` `gap`) are the challenge route targets: a visible stretch of at least 5 m along a row with no vine,
between two planted parts of the same global row, or a shorter one (from 3 m) that is an outlier against its field's own
canopy spacing (`GAP_RULE` "deviation", `field_gap_m`). Each row is sampled every 5 cm along its axis across all its
per-tile pieces (merged by `row_id`, so a gap across a tile edge is one gap, not two tile-edge stretches). A sample is
planted when a canopy polygon lies within 0.3 m of the axis (the reference canopies are cut there). Pixel vine green
(ExG > 0.11, runs >= 0.55 m, as `rows._row_structure`) is kept per gap as `green_share`, not as planted evidence: on
the example tiles it breaks true reference gaps (recall 4/8 -> 3/8). A sample is hidden off every row piece, on
no-data pixels, or in deep shadow; a gap with more than `MAX_HIDDEN` of it hidden is dropped ("do not invent a gap
where canopy is obscured"). `planting` POIs are row ends: the row's own axis runs on unplanted past its last vine while
its neighbours are planted there (predicted axes run to the plot edge; a reference row stops at its last vine or
runs to a tile edge, so on a Marcaj export it finds only stretches that run into a tile with no piece of the row). Each POI carries `gap_start` / `gap_end`: the route covers the whole stretch
when it walks the adjacent inter-row along it (the axis is about 1.25 m from the inter-row centre).
A gap or planting stretch within `OBSTACLE_M` of a `marcaj.obstacles` polygon (a hut, shed or tree the row runs up to)
is not missing planting: it becomes reason `obstacle` (`stretch` keeps gap/planting), `challenge` false, on the map only.
Sentinel POIs (`sentinel_low_ndvi` / `sentinel_low_ndmi`, `challenge` false) are `marcaj.sentinel` low zones that the
drone rows confirm (`sentinel_pois`).

Measured in research/notes/poi.md. Run: uv run --frozen python -m marcaj.poi [--scene S] [--no-satellite]"""

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy import ndimage
from shapely import STRtree
from shapely.geometry import LineString, Point, mapping, shape

from marcaj.layers import exg
from marcaj.obstacles import OBSTACLES_PATH
from marcaj.scene import is_scored
from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, Tile, load_tiles

POI_PATH = REPO_ROOT / "data" / "generated" / "work" / "poi" / "poi.geojson"
PREDICTIONS_PATH = REPO_ROOT / "data" / "generated" / "predictions.geojson"
FLIGHT_DATE = "2025-05-21"
SAMPLE_M = 0.05
TUBE_M = 0.30       # the reference canopies are cut at 0.30 m from their row line
GREEN_EXG = 0.11    # rows.GREEN_EXG
VINE_RUN_M = 0.55   # rows.VINE_RUN_M: shorter green runs along the row are weeds
GAP_M = 5.0         # the organizers' `disrupted` gap
# gap rule: "fixed" flags every canopy-free stretch of GAP_M; "deviation" also flags a shorter one that is an outlier
# against its own field's canopy spacing (`field_gap_m`, the mentors' hint): its canopy-to-canopy distance above the
# field's median by GAP_K robust sigmas, never under GAP_FLOOR_M (normal variation, one missing plant at ~2 m)
GAP_RULE = "deviation"  # research/probes/poi_dev_eval.py: +46 targets, all 97 fixed ones kept; 19/24 new look real or partly
GAP_K = 3.0
GAP_FLOOR_M = 3.0
EXPECTED_PITCH_M = 2.0  # the agronomists' planting distance: caps a field's median so a gappy field cannot inflate it
MIN_PAIRS = 30      # a field with fewer canopy pairs on visible rows keeps GAP_M
SHORT_GREEN = 0.1   # a deviation-only gap (under GAP_M) with more vine green is dark, sparse-leaved vines (V19-11, V21-13)
DARK_DN = 40.0      # mean RGB under this is deep shadow
MAX_HIDDEN = 0.2    # share of a gap that may be hidden (no piece, no-data, shadow)
MAX_GREEN = 0.75    # share of a gap with vine green (rows.VINE_RUN_M runs) above which it is a missed row, not a gap
LOW_ROW = 0.25      # planted share of a row under which its gaps are in doubt
MAX_PLANTING_M = 15.0  # a longer unplanted row end is a misplaced row, not missing vines
# a stretch this close to an obstacle runs up to it, not into missing vines; a hut standing in a block has its own yard:
# the V21-13 rows stop 4.6-5.0 m short of its roof (a building beyond the block edge gets the tree reach)
OBSTACLE_M = {"tree": 2.0, "building": 6.0}


@dataclass
class _Row:
    vineyard_id: str
    row_id: str
    origin: np.ndarray
    along: np.ndarray
    u0: float
    n: int
    pieces: list[LineString]
    canopy: np.ndarray = field(init=False)
    green: np.ndarray = field(init=False)
    seen: np.ndarray = field(init=False)
    dark: np.ndarray = field(init=False)
    xy: np.ndarray = field(init=False)
    tiles: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.canopy, self.green, self.seen, self.dark = (np.zeros(self.n, bool) for _ in range(4))
        self.xy = np.full((self.n, 2), np.nan)

    def index(self, points: np.ndarray) -> np.ndarray:
        return np.clip(np.round(((points - self.origin) @ self.along - self.u0) / SAMPLE_M).astype(int), 0, self.n - 1)


def _rows(features: list[dict[str, Any]]) -> list[_Row]:
    """One `_Row` per global `row_id`; its pieces must be collinear (rows are straight lines, one per tile)."""
    pieces: dict[tuple[str, str], list[LineString]] = defaultdict(list)
    for feature in features:
        properties = feature["properties"]
        if properties.get("label") == "row" and properties.get("row_id"):
            line = shape(feature["geometry"])
            for part in getattr(line, "geoms", [line]):
                if part.length > SAMPLE_M:
                    pieces[(properties.get("vineyard_id", ""), properties["row_id"])].append(part)
    rows = []
    for (vineyard_id, row_id), lines in pieces.items():
        longest = max(lines, key=lambda line: line.length)
        (x0, y0), (x1, y1) = longest.coords[0], longest.coords[-1]
        along = np.array([x1 - x0, y1 - y0]) / longest.length
        origin = np.array([x0, y0])
        u = np.concatenate([(np.asarray(line.coords) - origin) @ along for line in lines])
        rows.append(_Row(vineyard_id, row_id, origin, along, float(u.min()), int(np.ceil((u.max() - u.min()) / SAMPLE_M)) + 1, lines))
    return rows


def sample_rows(features: list[dict[str, Any]], tiles: list[Tile] | None = None) -> list[_Row]:
    """Every global row with its per-sample canopy, green, seen and dark flags, read from the tiles its pieces cross."""
    tiles = tiles if tiles is not None else load_tiles(DATA_DIR)
    rows = _rows(features)
    canopies = [shape(f["geometry"]) for f in features if f["properties"].get("label") == "vineyard"]
    canopy_tree = STRtree(canopies)
    piece_rows = [(row, piece) for row in rows for piece in row.pieces]
    piece_tree = STRtree([piece for _, piece in piece_rows])
    offsets = np.linspace(-TUBE_M, TUBE_M, 7)
    for tile in tiles:
        bounds = tile.bounds
        hits = piece_tree.query(bounds, predicate="intersects")
        if not len(hits):
            continue
        transform = from_origin(tile.left, tile.top, PIXEL_M, PIXEL_M)
        near = [canopies[i] for i in canopy_tree.query(bounds, predicate="intersects")]
        covered = rasterize(near, out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool) if near else np.zeros((TILE_PX, TILE_PX), bool)
        with rasterio.open(tile.path) as source:
            rgb = source.read()
        excess, valid = exg(rgb)
        brightness = rgb.mean(axis=0, dtype=np.float32)
        del rgb
        for i in hits:
            row, piece = piece_rows[i]
            clipped = piece.intersection(bounds)
            for line in getattr(clipped, "geoms", [clipped]):
                if line.geom_type != "LineString" or line.length < SAMPLE_M:
                    continue
                row.tiles.add(tile.name)
                t = np.arange(0, line.length, SAMPLE_M)
                points = np.array([line.interpolate(d).coords[0] for d in t])
                (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
                normal = np.array([-(y1 - y0), x1 - x0]) / line.length
                index = row.index(points)
                row.xy[index] = points
                hit, green, valid_all, dark = np.zeros(len(t), bool), np.zeros(len(t), bool), np.ones(len(t), bool), np.zeros(len(t))
                for offset in offsets:
                    x, y = (points + offset * normal).T
                    rc = [np.clip((tile.top - y) / PIXEL_M, 0, TILE_PX - 1).astype(int), np.clip((x - tile.left) / PIXEL_M, 0, TILE_PX - 1).astype(int)]
                    hit |= covered[rc[0], rc[1]]
                    green |= excess[rc[0], rc[1]] > GREEN_EXG
                    valid_all &= valid[rc[0], rc[1]]
                    dark += brightness[rc[0], rc[1]] / len(offsets)
                row.canopy[index] |= hit
                row.green[index] |= green
                row.seen[index] |= valid_all
                row.dark[index] |= dark < DARK_DN
    return rows


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of each run of True."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def field_gap_m(rows: list[_Row], k: float = GAP_K, floor: float = GAP_FLOOR_M) -> dict[str, float]:
    """Per `vineyard_id`, the canopy-free length an interior gap needs to deviate from the field's own spacing: the
    canopy-to-canopy (centre) distance of consecutive canopy runs along visible axes has median m and robust sigma s
    (1.4826 MAD), and a gap's distance is its empty length plus the field's median run length r, so the threshold is
    max(`floor`, min(m, `EXPECTED_PITCH_M`) + k s - r), and never over `GAP_M`: a stretch that long is a gap in any
    field, however gappy the field is overall. Fields with under `MIN_PAIRS` pairs are left out (callers use `GAP_M`)."""
    pitch: dict[str, list[float]] = defaultdict(list)
    runs: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        hidden = ~row.seen | row.dark
        planted = _runs(row.canopy)
        runs[row.vineyard_id] += [(b - a) * SAMPLE_M for a, b in planted]
        pitch[row.vineyard_id] += [(a1 + b1 - a0 - b0) / 2 * SAMPLE_M for (a0, b0), (a1, b1) in zip(planted, planted[1:])
                                   if hidden[b0:a1].mean() <= MAX_HIDDEN]
    out = {}
    for vineyard_id, distances in pitch.items():
        if len(distances) >= MIN_PAIRS:
            median = float(np.median(distances))
            sigma = 1.4826 * float(np.median(np.abs(np.array(distances) - median)))
            out[vineyard_id] = min(GAP_M, max(floor, min(median, EXPECTED_PITCH_M) + k * sigma - float(np.median(runs[vineyard_id]))))
    return out


def row_gaps(rows: list[_Row], green: bool = False, min_gap_m: float = GAP_M, field_m: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Every stretch of at least `min_gap_m` without a vine on a row (`sample_rows`): `gap` between two planted parts,
    and `planting` at a row end, where the row's own axis runs on unplanted past its last vine while the median of
    its 2 + 2 nearest planted neighbours in the plot is planted there (a staggered block end cancels in the median).
    Each is a dict with the row's IDs, the end points on the axis, the length, the hidden share and the row's planted
    share. `green` also counts pixel vine green as planted. `field_m` (`field_gap_m`) replaces `min_gap_m` for interior
    gaps of the fields it holds; row ends keep `min_gap_m`."""
    run = np.ones(round(VINE_RUN_M / SAMPLE_M), bool)
    vine_green = {id(row): ndimage.binary_opening(row.green, structure=run) for row in rows}
    planted = {id(row): row.canopy | (vine_green[id(row)] if green else False) for row in rows}
    by_plot: dict[str, list[_Row]] = defaultdict(list)
    for row in rows:
        if planted[id(row)].sum() * SAMPLE_M >= 1.0:
            # neighbours come from the row's own lattice: a block can hold several patterns ("<pattern>-R001")
            by_plot[row.row_id.rsplit("-R", 1)[0]].append(row)
    gaps = []

    def add(row: _Row, kind: str, start: int, end: int, a: np.ndarray, b: np.ndarray, share: float, hidden: np.ndarray) -> None:
        length = float(np.linalg.norm(b - a))
        if length >= ((field_m or {}).get(row.vineyard_id, min_gap_m) if kind == "gap" else min_gap_m):
            gaps.append({"kind": kind, "vineyard_id": row.vineyard_id, "row_id": row.row_id, "start": a, "end": b, "gap_m": length,
                         "hidden": float(hidden[start:end].mean()), "green": float(vine_green[id(row)][start:end].mean()),
                         "row_planted": share, "tiles": sorted(row.tiles)})

    for plot_rows in by_plot.values():
        normal = plot_rows[0].along[::-1] * [-1, 1]
        plot_rows.sort(key=lambda row: float(np.nanmedian(row.xy @ normal)))
        for k, row in enumerate(plot_rows):
            mask = planted[id(row)]
            hidden = ~row.seen | row.dark
            share = float((mask & ~hidden).sum() / max((~hidden).sum(), 1))
            for start, end in _runs(~mask):
                if start and end < row.n:  # from the last planted sample to the next one
                    add(row, "gap", start, end, row.xy[start - 1], row.xy[end], share, hidden)
            neighbours = plot_rows[max(k - 2, 0):k] + plot_rows[k + 1:k + 3]
            if len(neighbours) < 2:
                continue
            where = np.flatnonzero(mask)
            ends = np.array([[row.index(n.xy[planted[id(n)]]).min(), row.index(n.xy[planted[id(n)]]).max()] for n in neighbours])
            first, last = int(np.median(ends[:, 0])), int(np.median(ends[:, 1]))
            sampled = np.flatnonzero(~np.isnan(row.xy[:, 0]))
            first, last = max(first, sampled[0]), min(last, sampled[-1])  # only along the row's own axis
            if first < where[0]:
                add(row, "planting", first, where[0], row.xy[first], row.xy[where[0]], share, hidden)
            if last > where[-1]:
                add(row, "planting", where[-1] + 1, last + 1, row.xy[where[-1]], row.xy[last], share, hidden)
    return gaps


def _reach(obstacle: dict[str, Any]) -> float:
    return OBSTACLE_M[obstacle["obstacle_type"] if obstacle.get("in_block", 1.0) >= 0.5 else "tree"]


def gap_pois(rows: list[_Row], green: bool = False, obstacles: list[dict[str, Any]] = (), rule: str = GAP_RULE,
             k: float = GAP_K, floor: float = GAP_FLOOR_M) -> list[dict[str, Any]]:
    """`inspection` Points at the midpoint of each visible row gap (`rule` "fixed": at least 5 m; "deviation": an outlier
    against the field's canopy spacing, `field_gap_m(rows, k, floor)`) or missing row end of at least 5 m (`row_gaps`).
    `challenge` (a scored route target) is every `gap` with vine green over less than `MAX_GREEN` of it (a gap that is
    green all along is a row the canopy model missed: a 21 m and a 58 m one in view), and every `planting` stretch
    with no vine green at all (under 0.1) and at most `MAX_PLANTING_M` long. Of 16 random row ends 3 looked plausible,
    most were young or grassed rows the canopy misses; of the 16 without green about half did, and the longer ones are
    rows the model placed wrong. On the example tiles the row ends add 1 of 2 reference edge stretches and 1 gap.
    `confidence` is 1 - hidden share, halved for green gaps and for rows with under `LOW_ROW` of their length planted
    (young vines the canopy misses, P09), which also hold the true gaps of the reference block V02.
    A stretch within `OBSTACLE_M` of an `obstacles` polygon is reason `obstacle`, never a target, with `obstacle_type`."""
    blockers = [shape(f["geometry"]) for f in obstacles]
    blocker_tree = STRtree(blockers)
    out = []
    field_m = field_gap_m(rows, k, floor) if rule == "deviation" else None
    for gap in row_gaps(rows, green, field_m=field_m):
        if gap["hidden"] > MAX_HIDDEN:
            continue
        x, y = (gap["start"] + gap["end"]) / 2
        doubt = (gap["green"] >= MAX_GREEN / 2) + (gap["row_planted"] < LOW_ROW) + (gap["kind"] == "planting")
        line = LineString([gap["start"], gap["end"]])
        near = [i for i in (blocker_tree.query(line, predicate="dwithin", distance=max(OBSTACLE_M.values())) if blockers else [])
                if blockers[i].distance(line) <= _reach(obstacles[i]["properties"])]
        reason = "obstacle" if len(near) else gap["kind"]
        extra = {"stretch": gap["kind"], "obstacle_type": obstacles[int(near[0])]["properties"]["obstacle_type"]} if len(near) else {}
        out.append({"type": "Feature", "geometry": mapping(Point(x, y)), "properties": {
            "label": "inspection", "source": "prediction", "id": f"{reason.upper()}-{gap['row_id']}-{round(x) % 10000:04d}-{round(y) % 10000:04d}",
            "reason": reason, "challenge": not len(near) and (gap["green"] < (MAX_GREEN if gap["gap_m"] >= GAP_M else SHORT_GREEN) if gap["kind"] == "gap"
                                                             else gap["green"] < 0.1 and gap["gap_m"] <= MAX_PLANTING_M), **extra,
            "origin": "candidate", "status": "new",
            "vineyard_id": gap["vineyard_id"], "row_id": gap["row_id"], "gap_m": round(gap["gap_m"], 2),
            "gap_rule": rule if gap["kind"] == "gap" else "fixed",
            "gap_start": [round(v, 2) for v in gap["start"]], "gap_end": [round(v, 2) for v in gap["end"]],
            "green_share": round(gap["green"], 2), "row_planted": round(gap["row_planted"], 2),
            "confidence": round((1 - gap["hidden"]) / 2 ** doubt, 2), "source_ref": gap["tiles"], "observed_at": FLIGHT_DATE}})
    return out


SHORT_GAP_M = 2.0     # a canopy-free stretch this long is one or more missing plants (planting distance 1.0-1.5 m)
CANOPY_DEFICIT = 0.75  # a zone whose canopy share along its rows is under this times the plot's is less planted


def _stretches(mask: np.ndarray, min_m: float) -> list[tuple[int, int]]:
    return [(a, b) for a, b in _runs(mask) if (b - a) * SAMPLE_M >= min_m]


def sentinel_pois(rows: list[_Row], zones: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(points, per-zone report) for Sentinel low zones (`marcaj.sentinel.zones`) checked against the drone rows.
    Inside a zone, the plot's rows give: canopy share along the axis against the plot's own share, canopy-free stretches
    of `SHORT_GAP_M` or more per 100 m of row against the plot's rate, and the longest such stretch. A zone is kept
    when its canopy share is under `CANOPY_DEFICIT` times the plot's, or its short-gap rate is at least twice the
    plot's (and 2 or more), or it holds a 5 m gap; the point is the middle of its longest canopy-free stretch, on a row
    axis. Otherwise the drone shows nothing unusual and the zone is dropped. Points are `challenge` false."""
    from shapely import contains_xy
    by_plot: dict[str, list[_Row]] = defaultdict(list)
    for row in rows:
        by_plot[row.vineyard_id].append(row)
    points, report = [], []
    for zone in zones:
        z = zone["properties"]
        polygon = shape(zone["geometry"])
        plot_rows = by_plot.get(z["vineyard_id"], [])
        total = {"visible": 0, "planted": 0, "short": 0}
        inside = {"visible": 0, "planted": 0, "short": 0}
        longest = (0.0, None, None)
        for row in plot_rows:
            visible = row.seen & ~row.dark
            empty = ~row.canopy & visible
            here = contains_xy(polygon, np.nan_to_num(row.xy[:, 0]), np.nan_to_num(row.xy[:, 1])) & visible
            total["visible"] += int(visible.sum())
            total["planted"] += int((row.canopy & visible).sum())
            inside["visible"] += int(here.sum())
            inside["planted"] += int((row.canopy & here).sum())
            total["short"] += len([r for r in _stretches(empty, SHORT_GAP_M) if r[0] and r[1] < row.n])
            for a, b in _stretches(empty & here, SHORT_GAP_M):
                inside["short"] += 1
                if (b - a) * SAMPLE_M > longest[0]:
                    longest = ((b - a) * SAMPLE_M, row, (row.xy[a] + row.xy[b - 1]) / 2)
        entry = {"zone": f"S-{z['index']}-{z['vineyard_id']}-{round(polygon.centroid.x) % 10000:04d}-{round(polygon.centroid.y) % 10000:04d}",
                 "index": z["index"], "vineyard_id": z["vineyard_id"], "pixels": z["pixels"], "z": z["z"], "value": z["value"],
                 "row_m": round(inside["visible"] * SAMPLE_M, 1)}
        if not inside["visible"] or not total["planted"]:
            report.append({**entry, "kept": False, "why": "no drone row or canopy samples"})
            continue
        share = inside["planted"] / inside["visible"]
        plot_share = total["planted"] / max(total["visible"], 1)
        rate = 100 * inside["short"] / (inside["visible"] * SAMPLE_M)
        plot_rate = 100 * total["short"] / max(total["visible"] * SAMPLE_M, 1)
        entry.update({"canopy_share": round(share, 3), "plot_canopy_share": round(plot_share, 3), "short_gaps_per_100m": round(rate, 2),
                      "plot_short_gaps_per_100m": round(plot_rate, 2), "longest_gap_m": round(longest[0], 2)})
        why = [reason for reason, hit in (("canopy deficit", share < CANOPY_DEFICIT * plot_share),
                                          ("short gaps", inside["short"] >= 2 and rate >= 2 * plot_rate),
                                          ("5 m gap", longest[0] >= GAP_M)) if hit]
        report.append({**entry, "kept": bool(why), "why": ", ".join(why) or "drone shows nothing unusual"})
        if not why or longest[1] is None:
            continue
        x, y = longest[2]
        points.append({"type": "Feature", "geometry": mapping(Point(x, y)), "properties": {
            "label": "inspection", "source": "prediction", "id": entry["zone"], "reason": f"sentinel_low_{z['index']}",
            "challenge": False, "origin": "candidate", "status": "new", "vineyard_id": z["vineyard_id"], "row_id": longest[1].row_id,
            "gap_m": round(longest[0], 2), "evidence": why, "zone_z": z["z"], "zone_value": z["value"], "plot_median": z["plot_median"],
            "canopy_share": entry["canopy_share"], "plot_canopy_share": entry["plot_canopy_share"],
            "short_gaps_per_100m": entry["short_gaps_per_100m"], "plot_short_gaps_per_100m": entry["plot_short_gaps_per_100m"],
            "confidence": 0.5, "source_ref": [f"sentinel-2-c1-l2a {d}" for d in z["dates"]] + sorted(longest[1].tiles),
            "observed_at": z["dates"][0]}})
    return points, report


def write(features: list[dict[str, Any]], path: Path = POI_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}), encoding="utf-8")
    return path


def main(scene_path: Path = PREDICTIONS_PATH, out: Path = POI_PATH, satellite: bool = True, obstacles_path: Path = OBSTACLES_PATH,
         rule: str = GAP_RULE, k: float = GAP_K, floor: float = GAP_FLOOR_M) -> None:
    from marcaj import sentinel

    started = time.perf_counter()
    features = json.loads(scene_path.read_text(encoding="utf-8"))["features"]
    obstacles = [f for f in features if f["properties"].get("label") == "obstacle"]
    if not obstacles and obstacles_path.is_file():  # a scene written before predict detected obstacles, or a Marcaj export
        obstacles = json.loads(obstacles_path.read_text(encoding="utf-8"))["features"]
    if any(f["properties"].get("label") == "row" and f["properties"].get("source") == "reference" for f in features):
        features = [f for f in features if is_scored(f)]  # a Marcaj-export scene: its rows and canopies, not the model's
    rows = sample_rows(features)
    pois = gap_pois(rows, obstacles=obstacles, rule=rule, k=k, floor=floor)
    counts = {key: sum(p["properties"]["reason"] == key[0] and p["properties"]["challenge"] == key[1] for p in pois) for key in (("gap", True), ("gap", False), ("planting", True), ("planting", False), ("obstacle", False))}
    print(f"{counts} (reason, challenge) from {len(rows)} rows in {time.perf_counter() - started:.1f} s")
    if satellite:
        chosen = sentinel.scenes()
        stats, zones = sentinel.zones(features, chosen)
        points, report = sentinel_pois(rows, zones)
        pois += points
        out.parent.mkdir(parents=True, exist_ok=True)
        (out.parent / "sentinel_zones.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": zones}), encoding="utf-8")
        (out.parent / "sentinel_report.json").write_text(json.dumps({"scenes": [{"date": s.date, "item": s.item_id, "scene_cloud": s.cloud_cover, "study_clear": s.clear} for s in chosen],
                                                                     "plots": stats, "zones": report}, indent=1), encoding="utf-8")
        print(f"Sentinel {[s.date for s in chosen]}: {len(zones)} low zones, {len(points)} kept by the drone evidence")
    print(f"{len(pois)} POIs in {time.perf_counter() - started:.1f} s -> {write(pois, out)}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inspection points from a scene's rows and canopies, plus Sentinel-2 low zones")
    parser.add_argument("--scene", type=Path, default=PREDICTIONS_PATH, help="EPSG:32635 GeoJSON with row and vineyard features (predictions, or a Marcaj-export scene)")
    parser.add_argument("--output", type=Path, default=POI_PATH)
    parser.add_argument("--no-satellite", action="store_true")
    parser.add_argument("--obstacles", type=Path, default=OBSTACLES_PATH, help="obstacle polygons, used when the scene holds none")
    parser.add_argument("--gap-rule", choices=("fixed", "deviation"), default=GAP_RULE)
    parser.add_argument("--gap-k", type=float, default=GAP_K)
    parser.add_argument("--gap-floor", type=float, default=GAP_FLOOR_M)
    args = parser.parse_args()
    main(args.scene, args.output, not args.no_satellite, args.obstacles, args.gap_rule, args.gap_k, args.gap_floor)
