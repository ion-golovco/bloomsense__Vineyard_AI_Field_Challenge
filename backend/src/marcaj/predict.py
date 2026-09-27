"""The whole prediction: plots and row axes, canopies, row axes moved onto the canopy, plots verified by their canopy, inter-rows, waste boxes, and per-tile
row and inter-row attributes, written to data/generated/predictions.geojson for the lab and marcaj-pack.
Run: uv run --frozen --group sam python -m marcaj.predict [--plots P.json | --rows R.geojson] [--output O.geojson]"""

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
from shapely.geometry import LineString, mapping, shape
from shapely.ops import unary_union

from marcaj import canopy, canopy_net, obstacles, plots, rows, waste
from marcaj.layers import Layers
from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, Tile, load_tiles

PREDICTIONS_PATH = REPO_ROOT / "data" / "generated" / "predictions.geojson"
FLAGS_PATH = REPO_ROOT / "data" / "generated" / "canopy_flags.csv"
DROPPED_NAME = "dropped_plots.geojson"  # beside the output: the plots verify_plots dropped, for the review tool
LONG_CANOPY_M = 3.0  # a vine canopy is 1-2 m along the row; longer is probably merged plants, worth a look in Marcaj
LATTICE_DEG = 5.0  # given rows of one vineyard_id within this angle of its longest row are one row pattern
PART_M = 0.6  # given rows this close across the rows are one line for the inter-rows...
JOIN_M = 5.0  # ...and its pieces under this far apart along are one piece (a gap under 5 m is not a gap in the rules)


def _angle(line: LineString) -> float:
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    return float(np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 180)


def carry_plots(given: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`plots.detect_plots`-shaped block and row features from given rows (LineStrings with `vineyard_id` and `row_id`,
    like work/v6/reference/rows.geojson), and the axes `rows.interrow_areas` builds inter-rows from. The rows keep their
    geometry; `row_id` becomes a key that sorts across the rows (as `rows.kept_rows` expects) and the given one waits in
    `carry_row_id`. A vineyard_id's rows within `LATTICE_DEG` of its longest are one pattern (`<vineyard_id>`, or `a`, `b`...
    by row length when there are several), whose block is its rows buffered by half the median spacing. The axes are pairs
    of neighbouring row pieces fitted to the drawn rows' own slope, each pair a pattern `<pattern>~<n>` of its own (see below)."""
    by_vineyard: dict[str, list[tuple[str, LineString]]] = defaultdict(list)
    for feature in given:
        properties = feature["properties"]
        if properties.get("label", "row") == "row":
            if not properties.get("vineyard_id") or not properties.get("row_id"):
                raise ValueError(f"given row without vineyard_id or row_id: {properties}")
            by_vineyard[properties["vineyard_id"]].append((properties["row_id"], shape(feature["geometry"])))
    diff = lambda a, b: min(abs(a - b) % 180, 180 - abs(a - b) % 180)
    found, axes = [], []
    for vineyard_id, members in sorted(by_vineyard.items()):
        patterns: list[list[tuple[str, LineString]]] = []
        for row in sorted(members, key=lambda row: -row[1].length):
            home = next((pattern for pattern in patterns if diff(_angle(row[1]), _angle(pattern[0][1])) <= LATTICE_DEG), None)
            home.append(row) if home else patterns.append([row])
        patterns.sort(key=lambda pattern: -sum(line.length for _, line in pattern))
        for k, pattern in enumerate(patterns):
            pattern_id = vineyard_id if len(patterns) == 1 else vineyard_id + "abcdefghijklmnopqrstuvwxyz"[k]
            angle = _angle(pattern[0][1])
            along = np.array([np.cos(np.radians(angle)), np.sin(np.radians(angle))])
            across = np.array([-along[1], along[0]])
            v = lambda line: float(np.asarray(line.centroid.coords[0]) @ across)
            u = lambda line: float(np.asarray(line.centroid.coords[0]) @ along)
            ordered = sorted(pattern, key=lambda row: (v(row[1]), u(row[1])))
            steps = [float(step) for step in np.diff(sorted(v(line) for _, line in pattern)) if step > 0.5]
            spacing = float(np.median(steps)) if steps else 2.5
            polygon = unary_union([line.buffer(spacing / 2, cap_style="flat", join_style="mitre") for _, line in pattern]).buffer(0.05).buffer(-0.05)
            found.append({"type": "Feature", "geometry": mapping(polygon), "properties": {
                "label": "block", "source": "prediction", "vineyard_id": pattern_id, "block_id": vineyard_id, "pattern_id": pattern_id,
                "area_m2": round(polygon.area), "row_angle": round(angle, 1), "row_spacing_m": round(spacing, 2), "rows": len(pattern), "carried": True}})
            for n, (row_id, line) in enumerate(ordered):
                found.append({"type": "Feature", "geometry": mapping(line), "properties": {
                    "label": "row", "source": "prediction", "vineyard_id": pattern_id, "row_id": f"{pattern_id}|{n:05d}", "carry_row_id": row_id,
                    "row_structure": "regular", "length_m": round(line.length, 2)}})
            # inter-row axes: rows under PART_M apart across are one line, whose pieces join over gaps under JOIN_M; each
            # joined piece is a straight fit through its rows' vertices (every 1 m), so inter-rows follow rows drawn by hand
            # that fan out (V35-25 by 3.6 deg); each overlapping pair of pieces on neighbouring lines is its own two-row
            # pattern `<pattern>~<n>`, so interrow_areas pairs them even where a line has several pieces (it pairs in order)
            lines_: list[list[LineString]] = []
            for _, line in sorted(pattern, key=lambda row: v(row[1])):
                if lines_ and v(line) - v(lines_[-1][-1]) <= PART_M:
                    lines_[-1].append(line)
                else:
                    lines_.append([line])
            spans = []
            for group in lines_:
                joined: list[list[float]] = []
                for t0, t1 in sorted((float(t.min()), float(t.max())) for t in (np.asarray(line.coords) @ along for line in group)):
                    if joined and t0 - joined[-1][1] <= JOIN_M:
                        joined[-1][1] = max(joined[-1][1], t1)
                    else:
                        joined.append([t0, t1])
                points = np.asarray([line.interpolate(d).coords[0] for line in group for d in [*np.arange(0, line.length, 1.0), line.length]])
                pu, pv = points @ along, points @ across
                fits = []
                for u0, u1 in joined:
                    inside = (pu >= u0 - 0.5) & (pu <= u1 + 0.5)
                    slope, offset = np.polyfit(pu[inside], pv[inside], 1) if inside.sum() >= 3 and np.ptp(pu[inside]) > 2 else (0.0, float(pv[inside].mean()))
                    fits.append((float(slope), float(offset), u0, u1))
                spans.append(fits)
            axis = lambda slope, offset, u0, u1: LineString([along * u + across * (offset + slope * u) for u in (u0, u1)])
            for a_line, b_line in zip(spans[:-1], spans[1:]):
                for a in a_line:
                    for b in b_line:
                        if min(a[3], b[3]) > max(a[2], b[2]):
                            mini = f"{pattern_id}~{len(axes) // 2}"
                            axes += [{"type": "Feature", "geometry": mapping(axis(*end_)), "properties": {"label": "row", "vineyard_id": mini, "row_id": f"{mini}|{k}"}}
                                     for k, end_ in enumerate((a, b))]
    return found, axes


def _pieces_on_patterns(pieces: list[dict[str, Any]], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Given row pieces as `row` features of the pattern whose carried row of the same row_id lies nearest."""
    parts: dict[str, list[tuple[LineString, str]]] = defaultdict(list)
    for feature in found:
        if feature["properties"]["label"] == "row":
            parts[feature["properties"]["carry_row_id"]].append((shape(feature["geometry"]), feature["properties"]["vineyard_id"]))
    out = []
    for feature in pieces:
        properties, line = feature["properties"], shape(feature["geometry"])
        if properties["row_id"] not in parts:
            raise ValueError(f"row piece {properties['row_id']} on {properties.get('tile')} has no row in --rows")
        pattern_id = min(parts[properties["row_id"]], key=lambda part: part[0].distance(line))[1]
        out.append({"type": "Feature", "geometry": feature["geometry"], "properties": {
            "label": "row", "source": "prediction", "vineyard_id": pattern_id, "row_id": properties["row_id"], "row_structure": "regular",
            "length_m": round(line.length, 2)}})
    return out


def predict(params: plots.PlotParams = plots.PlotParams(), data_dir: Path = DATA_DIR, tiles: list[Tile] | None = None,
            layers: Layers | None = None, excess: Any = None, found: list[dict[str, Any]] | None = None,
            dropped_out: list[dict[str, Any]] | None = None, carry: list[dict[str, Any]] | None = None,
            carry_pieces: list[dict[str, Any]] | None = None, headland_m: float = 0.0) -> list[dict[str, Any]]:
    """`found` replaces `plots.detect_plots` with its (pattern-keyed) `block` and `row` features, to compare other stages.
    `dropped_out` receives the plots `plots.verify_plots` dropped (block features with their vine evidence and reason).
    `carry` (row features, `carry_plots`) replaces detect_plots, the canopy refit and verify_plots: the carry-over path,
    which keeps the given rows, row_id and vineyard_id exactly. `carry_pieces` (the same rows cut per tile, with `tile`, like
    work/v6/reference/rows_pieces.geojson) are then the rows written, so every tile gets exactly its drawn pieces: a global
    row joined across tile edges jogs where two tiles' pieces do not meet. `headland_m` (carry only, 0 = off) carries an
    inter-row end onto a passage up to this far away instead of `rows.EXTEND_M`: the team's rows end where the vines end,
    3-5 m short of the passage in V36-24, V06-03, V09-01, V35-25, and every lane entry then costs the route that headland.
    It breaks the rule that no inter-row extends past the row ends; the organizers may measure the route against theirs."""
    tiles = tiles or load_tiles(data_dir)
    # ponytail: canopy and per-tile attributes each read the vineyard tiles; share one read if the 70 s matters
    # the network keeps the rule's colour pixels it accepts: 0.855, a tie with the rules; combine="net" alone gives 0.843
    canopy_on = lambda rows_: [feature for tile in tiles for feature in canopy_net.tile_canopies(tile, canopy.plot_rows(rows_), combine="and", threshold=0.2, flips=False)]
    if carry is not None:
        # the given rows are final: no refit, no verification, one canopy pass
        found, axes = carry_plots(carry)
        canopies = canopy_on(found)
    else:
        found = list(found) if found is not None else plots.detect_plots(params, data_dir, layers, excess)
        canopies = canopy_on(found)
        # the lattice rows sit a median 7 cm (p95 0.19 m) off the canopy; canopy.py re-fits per tile, the exported rows follow here
        found = canopy.refit_rows(found, canopies)[0]
        # canopy again on the refit rows, so the tube follows the rows as exported: judge canopy 0.851 -> 0.857 (research/notes/canopy_rules.md)
        canopies = canopy_on(found)
        # a plot whose canopy does not look like vine rows goes with its rows and canopies, before inter-rows and waste see it
        found, canopies, dropped = plots.verify_plots(found, canopies, params, data_dir)
        if dropped_out is not None:
            dropped_out += dropped
    # given rows are all kept: no stray-row filter (0 turns it off) in inter-rows or per tile
    with mock.patch.object(rows, "STRAY_SHARE", 0.0 if carry is not None else rows.STRAY_SHARE), \
            mock.patch.object(rows, "EXTEND_M", max(rows.EXTEND_M, headland_m) if carry is not None else rows.EXTEND_M):
        # trees and buildings in or at a block: cut out of inter-rows (the rules), and poi reads them to skip stretches that end at one
        blocked = obstacles.detect(found, plots.exclusions(data_dir))
        interrow_from = found if carry is None else [f for f in found if f["properties"]["label"] != "row"] + axes
        interrows = rows.interrow_areas(interrow_from, plots.exclusions(data_dir), blocked)
        if carry is not None:  # a carried pair's two-row pattern back to its pattern
            for feature in interrows:
                pattern_id = feature["properties"]["pattern_id"].split("~")[0]
                feature["properties"].update(vineyard_id=pattern_id, pattern_id=pattern_id)
        found += interrows + canopies + blocked
        found += waste.detect(tiles, found, data_dir, waste.WasteParams(workers=2))[0]  # 6 workers froze the laptop beside other runs
        if carry_pieces is not None:
            found = [f for f in found if f["properties"]["label"] != "row"] + _pieces_on_patterns(carry_pieces, found)
        # detect_plots keys everything by row pattern; blocks (the organizer 5 m rule) are assigned last, on the whole prediction
        out = plots.assign_blocks(rows.per_tile(found, tiles))
    if carry is not None:  # the given row_id back; the block (vineyard_id) was the given one all along
        for feature in out:
            if "carry_row_id" in feature["properties"]:
                feature["properties"]["row_id"] = feature["properties"].pop("carry_row_id")
    return out


def write(features: list[dict[str, Any]], path: Path = PREDICTIONS_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}), encoding="utf-8")
    return path


def canopy_flags(features: list[dict[str, Any]], min_m: float = LONG_CANOPY_M) -> list[dict[str, Any]]:
    """Canopies longer than `min_m` (long side of the minimum rotated rectangle), longest first, with the tile and
    pixel position to find them in Marcaj. A review list only: the organizers' own examples hold canopies up to 56 m
    (26% and 42% over 2 m), and cutting by length scored worse there (research/notes/canopy_rules.md)."""
    flags = []
    extent = TILE_PX * PIXEL_M
    for feature in features:
        if feature["properties"]["label"] != "vineyard":
            continue
        geometry = shape(feature["geometry"])
        corners = list(geometry.minimum_rotated_rectangle.exterior.coords)
        length = max(math.dist(corners[0], corners[1]), math.dist(corners[1], corners[2]))
        if length <= min_m:
            continue
        point = geometry.representative_point()
        r, c = math.floor((5221222.4 - point.y) / extent), math.floor((point.x - 628992.0) / extent)
        flags.append({"tile": f"siret3_r{r:03d}_c{c:03d}.tif", "x_px": round((point.x - 628992.0 - c * extent) / PIXEL_M),
                      "y_px": round((5221222.4 - r * extent - point.y) / PIXEL_M), "length_m": round(length, 1),
                      "area_m2": round(geometry.area, 2), "vineyard_id": feature["properties"].get("vineyard_id", ""),
                      "easting": round(point.x, 2), "northing": round(point.y, 2)})
    return sorted(flags, key=lambda flag: -flag["length_m"])


def write_flags(flags: list[dict[str, Any]], path: Path = FLAGS_PATH) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["tile", "x_px", "y_px", "length_m", "area_m2", "vineyard_id", "easting", "northing"])
        writer.writeheader()
        writer.writerows(flags)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The whole prediction to one EPSG:32635 GeoJSON")
    parser.add_argument("--plots", type=Path, help="JSON list of block and row features to use instead of running plots.detect_plots")
    parser.add_argument("--rows", type=Path, help="GeoJSON of rows to keep exactly (carry-over: skips detect_plots, the refit and verify_plots)")
    parser.add_argument("--row-pieces", type=Path, help="with --rows: the same rows cut per tile, written instead of the rows (exact per tile)")
    parser.add_argument("--headland-m", type=float, default=0.0, help="with --rows: carry inter-row ends onto a passage up to this far (past the row ends)")
    parser.add_argument("--output", type=Path, default=PREDICTIONS_PATH, help="canopy_flags.csv is written beside it")
    args = parser.parse_args()
    if args.plots and args.rows:
        parser.error("--plots and --rows exclude each other")
    if args.row_pieces and not args.rows:
        parser.error("--row-pieces needs --rows")
    if args.headland_m and not args.rows:
        parser.error("--headland-m needs --rows")
    started = time.perf_counter()
    dropped: list[dict[str, Any]] = []
    features = predict(found=json.loads(args.plots.read_text(encoding="utf-8")) if args.plots else None, dropped_out=dropped,
                       carry=json.loads(args.rows.read_text(encoding="utf-8"))["features"] if args.rows else None,
                       carry_pieces=json.loads(args.row_pieces.read_text(encoding="utf-8"))["features"] if args.row_pieces else None,
                       headland_m=args.headland_m)
    print(f"{len(dropped)} plots dropped by verify_plots: {[(f['properties']['pattern_id'], f['properties']['dropped']) for f in dropped]} "
          f"-> {write(dropped, args.output.parent / DROPPED_NAME)}")
    counts: dict[str, int] = {}
    for feature in features:
        counts[feature["properties"]["label"]] = counts.get(feature["properties"]["label"], 0) + 1
    print(f"{counts} in {time.perf_counter() - started:.1f} s -> {write(features, args.output)}")
    flags = canopy_flags(features)
    print(f"{len(flags)} canopies longer than {LONG_CANOPY_M} m to check in Marcaj -> {write_flags(flags, args.output.parent / FLAGS_PATH.name)}")
