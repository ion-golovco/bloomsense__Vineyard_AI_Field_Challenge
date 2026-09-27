"""v6: gap targets when canopy pieces under A m2 do not count as planted (the organizers leave small young plants
undrawn): organizer example gaps found vs the user's labels. Rows from OUT/rows_TAG.pkl (v6_poi_sweep.py).
Run from backend/: uv run --frozen python ../research/probes/v6_poi_small.py TAG"""

import json
import pickle
import sys
from pathlib import Path

from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
import canopy_recall_lib as crl  # noqa: E402
import v6_canopy_lib as L  # noqa: E402

from marcaj import poi  # noqa: E402

tag = sys.argv[1] if len(sys.argv) > 1 else "defaults"
rows = pickle.loads((L.OUT / f"rows_{tag}.pkl").read_bytes())
canopies = json.loads((L.OUT / f"canopies_{tag}.json").read_text())
for area in (0.0, 0.25, 0.3, 0.4):
    keep = [f for f in canopies if shape(f["geometry"]).area >= area]
    crl.resample_canopy(rows, keep)
    pois = poi.gap_pois(rows, obstacles=L.obstacles())
    g = L.gap_metrics(pois)
    print(f"planted from >= {area} m2: targets {g['targets']} prec {g['precision']} flagged {g['labels_flagged']} | example {L.example_recall(pois)}", flush=True)
