"""The whole prediction: plots and row axes, canopies, row axes moved onto the canopy, plots verified by their canopy, inter-rows, waste boxes, and per-tile
row and inter-row attributes, written to data/generated/predictions.geojson for the lab and marcaj-pack.
Run: uv run --frozen --group sam python -m marcaj.predict [--plots P.json] [--output O.geojson]"""

import argparse
import csv
import json
import math
import time
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from marcaj import canopy, canopy_net, obstacles, plots, rows, waste
from marcaj.layers import Layers
from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, Tile, load_tiles

PREDICTIONS_PATH = REPO_ROOT / "data" / "generated" / "predictions.geojson"
FLAGS_PATH = REPO_ROOT / "data" / "generated" / "canopy_flags.csv"
DROPPED_NAME = "dropped_plots.geojson"  # beside the output: the plots verify_plots dropped, for the review tool
LONG_CANOPY_M = 3.0  # a vine canopy is 1-2 m along the row; longer is probably merged plants, worth a look in Marcaj


def predict(params: plots.PlotParams = plots.PlotParams(), data_dir: Path = DATA_DIR, tiles: list[Tile] | None = None,
            layers: Layers | None = None, excess: Any = None, found: list[dict[str, Any]] | None = None,
            dropped_out: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """`found` replaces `plots.detect_plots` with its (pattern-keyed) `block` and `row` features, to compare other stages.
    `dropped_out` receives the plots `plots.verify_plots` dropped (block features with their vine evidence and reason)."""
    tiles = tiles or load_tiles(data_dir)
    found = list(found) if found is not None else plots.detect_plots(params, data_dir, layers, excess)
    plot_rows = canopy.plot_rows(found)
    # ponytail: canopy and per-tile attributes each read the vineyard tiles; share one read if the 70 s matters
    # the network keeps the rule's colour pixels it accepts: 0.855, a tie with the rules; combine="net" alone gives 0.843
    canopies = [feature for tile in tiles for feature in canopy_net.tile_canopies(tile, plot_rows, combine="and", threshold=0.2, flips=False)]
    # the lattice rows sit a median 7 cm (p95 0.19 m) off the canopy; canopy.py re-fits per tile, the exported rows follow here
    found = canopy.refit_rows(found, canopies)[0]
    # a plot whose canopy does not look like vine rows goes with its rows and canopies, before inter-rows and waste see it
    found, canopies, dropped = plots.verify_plots(found, canopies, params, data_dir)
    if dropped_out is not None:
        dropped_out += dropped
    # trees and buildings in or at a block: cut out of inter-rows (the rules), and poi reads them to skip stretches that end at one
    blocked = obstacles.detect(found, plots.exclusions(data_dir))
    found += rows.interrow_areas(found, plots.exclusions(data_dir), blocked) + canopies + blocked
    found += waste.detect(tiles, found, data_dir, waste.WasteParams(workers=2))[0]  # 6 workers froze the laptop beside other runs
    # detect_plots keys everything by row pattern; blocks (the organizer 5 m rule) are assigned last, on the whole prediction
    return plots.assign_blocks(rows.per_tile(found, tiles))


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
    parser.add_argument("--output", type=Path, default=PREDICTIONS_PATH, help="canopy_flags.csv is written beside it")
    args = parser.parse_args()
    started = time.perf_counter()
    dropped: list[dict[str, Any]] = []
    features = predict(found=json.loads(args.plots.read_text(encoding="utf-8")) if args.plots else None, dropped_out=dropped)
    print(f"{len(dropped)} plots dropped by verify_plots: {[(f['properties']['pattern_id'], f['properties']['dropped']) for f in dropped]} "
          f"-> {write(dropped, args.output.parent / DROPPED_NAME)}")
    counts: dict[str, int] = {}
    for feature in features:
        counts[feature["properties"]["label"]] = counts.get(feature["properties"]["label"], 0) + 1
    print(f"{counts} in {time.perf_counter() - started:.1f} s -> {write(features, args.output)}")
    flags = canopy_flags(features)
    print(f"{len(flags)} canopies longer than {LONG_CANOPY_M} m to check in Marcaj -> {write_flags(flags, args.output.parent / FLAGS_PATH.name)}")
