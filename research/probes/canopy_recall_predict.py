"""The shipped canopy defaults over the whole site on the uploaded rows, through the path predict.py calls
(`canopy_net.tile_canopies(combine="and", threshold=0.2, flips=False)`, network on MPS): the uploaded predictions with
their canopies replaced, written to data/generated/work/canopy_recall/predictions.geojson (not the canonical
predictions). Prints the recall metrics. Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_recall_predict.py"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from canopy_recall_lib import WORK, frozen_plots, line, metrics, uploaded_features, vine_tiles  # noqa: E402

from marcaj import canopy_net  # noqa: E402
from marcaj.canopy import plot_rows  # noqa: E402

started = time.perf_counter()
rows = plot_rows(frozen_plots())
found = [f for tile in vine_tiles() for f in canopy_net.tile_canopies(tile, rows, combine="and", threshold=0.2, flips=False)]
print(f"{len(found)} canopies in {time.perf_counter() - started:.0f} s", flush=True)
features = [f for f in uploaded_features() if f["properties"]["label"] != "vineyard"] + found
path = WORK / "predictions.geojson"
path.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}))
print(f"-> {path}")
m = metrics("shipped defaults (model path)", found)
print(line(m))
(WORK / "predictions_metrics.json").write_text(json.dumps(m, indent=1))
