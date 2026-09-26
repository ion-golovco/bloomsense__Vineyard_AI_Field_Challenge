"""The whole prediction: plots and row axes, inter-rows, canopies, waste boxes, and per-tile row and inter-row attributes, written to
data/generated/predictions.geojson for the lab and marcaj-pack. Run: uv run --frozen python -m marcaj.predict"""

import json
import time
from pathlib import Path
from typing import Any

from marcaj import canopy, canopy_net, plots, rows, waste
from marcaj.layers import Layers
from marcaj.tiles import DATA_DIR, REPO_ROOT, Tile, load_tiles

PREDICTIONS_PATH = REPO_ROOT / "data" / "generated" / "predictions.geojson"


def predict(params: plots.PlotParams = plots.PlotParams(), data_dir: Path = DATA_DIR, tiles: list[Tile] | None = None,
            layers: Layers | None = None, excess: Any = None) -> list[dict[str, Any]]:
    tiles = tiles or load_tiles(data_dir)
    found = plots.detect_plots(params, data_dir, layers, excess)
    plot_rows = canopy.plot_rows(found)
    found += rows.interrow_areas(found, plots.exclusions(data_dir))
    # ponytail: canopy and per-tile attributes each read the vineyard tiles; share one read if the 70 s matters
    # the network keeps the rule's colour pixels it accepts: 0.855, a tie with the rules; combine="net" alone gives 0.843
    found += [feature for tile in tiles for feature in canopy_net.tile_canopies(tile, plot_rows, combine="and", threshold=0.2, flips=False)]
    found += waste.detect(tiles, found, data_dir)[0]
    return rows.per_tile(found, tiles)


def write(features: list[dict[str, Any]], path: Path = PREDICTIONS_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}), encoding="utf-8")
    return path


if __name__ == "__main__":
    started = time.perf_counter()
    features = predict()
    counts: dict[str, int] = {}
    for feature in features:
        counts[feature["properties"]["label"]] = counts.get(feature["properties"]["label"], 0) + 1
    print(f"{counts} in {time.perf_counter() - started:.1f} s -> {write(features)}")
