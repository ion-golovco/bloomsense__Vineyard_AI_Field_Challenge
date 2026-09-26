"""marcaj.predict with a second canopy pass on the rows refit_rows moved (the change recommended for predict.py), end to
end with the current plots.py and every other stage exactly as predict runs them: verify_plots is wrapped to re-run the
canopy on the refit rows it receives. Also writes the first-pass canopies (what predict ships
now) beside OUTPUT as pass1_canopies.json. Heavy (a full predict): take data/generated/work/.heavy_lock first.
Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_v3_twopass.py --output OUTPUT.geojson"""

import json
import runpy
import sys
from pathlib import Path

from marcaj import canopy, canopy_net, plots
from marcaj.tiles import load_tiles

_verify = plots.verify_plots


def verify_second_pass(found, canopies, params, data_dir):
    out = Path(sys.argv[sys.argv.index("--output") + 1]).parent / "pass1_canopies.json"
    out.write_text(json.dumps(canopies))  # the one-pass canopies, to judge both passes on the same plots
    rows_ = canopy.plot_rows(found)  # found holds the refit rows here
    canopies = [f for tile in load_tiles(data_dir) for f in canopy_net.tile_canopies(tile, rows_, combine="and", threshold=0.2, flips=False)]
    return _verify(found, canopies, params, data_dir)


if __name__ == "__main__":  # waste's spawned workers import this file again
    plots.verify_plots = verify_second_pass
    runpy.run_module("marcaj.predict", run_name="__main__")
