"""predict.predict with a second canopy pass on the rows refit_rows moved (the change recommended for predict.py), to
verify it end to end with marcaj-judge. Same stages and arguments as marcaj.predict otherwise. Heavy (a full predict):
take data/generated/work/.heavy_lock first.
Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_v3_twopass.py OUTPUT.geojson"""

import sys
import time
from pathlib import Path

from marcaj import canopy, canopy_net, obstacles, plots, predict, rows, waste
from marcaj.tiles import DATA_DIR, load_tiles


def two_pass(params: plots.PlotParams = plots.PlotParams()) -> list[dict]:
    tiles = load_tiles(DATA_DIR)
    found = plots.detect_plots(params, DATA_DIR, None, None)
    run = lambda rows_: [f for tile in tiles for f in canopy_net.tile_canopies(tile, canopy.plot_rows(rows_), combine="and", threshold=0.2, flips=False)]
    canopies = run(found)
    found = canopy.refit_rows(found, canopies)[0]
    canopies = run(found)  # the second pass: canopy on the refit rows
    found, canopies, dropped = plots.verify_plots(found, canopies, params, DATA_DIR)
    blocked = obstacles.detect(found, plots.exclusions(DATA_DIR))
    found += rows.interrow_areas(found, plots.exclusions(DATA_DIR), blocked) + canopies + blocked
    found += waste.detect(tiles, found, DATA_DIR)[0]
    return plots.assign_blocks(rows.per_tile(found, tiles))


if __name__ == "__main__":
    started = time.perf_counter()
    out = Path(sys.argv[1])
    features = two_pass()
    print(f"{len(features)} features in {time.perf_counter() - started:.0f} s -> {predict.write(features, out)}")
    print(predict.write_flags(predict.canopy_flags(features), out.parent / "canopy_flags_twopass.csv"))
