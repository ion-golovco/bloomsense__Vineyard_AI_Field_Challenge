"""Site-wide gap counts from the predictions, by plot, with canopy-only and canopy + pixel green evidence.
Caches the sampled rows at data/generated/work/poi/rows.pkl for the renders.
Run from backend/: uv run --frozen python ../research/probes/poi_site.py"""

import json
import pickle
import time
from collections import Counter

import numpy as np

from marcaj import poi

OUT = poi.POI_PATH.parent
OUT.mkdir(parents=True, exist_ok=True)
started = time.perf_counter()
features = json.loads(poi.PREDICTIONS_PATH.read_text())["features"]
rows = poi.sample_rows(features)
print(f"{len(rows)} rows sampled in {time.perf_counter() - started:.1f} s")
(OUT / "rows.pkl").write_bytes(pickle.dumps(rows))

for green in (False, True):
    gaps = poi.row_gaps(rows, green=green)
    kept = [g for g in gaps if g["hidden"] <= poi.MAX_HIDDEN]
    lengths = np.array([g["gap_m"] for g in kept])
    print(f"\ngreen={green}: {len(gaps)} gaps >= 5 m, {len(kept)} visible; {len({g['row_id'] for g in kept})} rows; "
          f"length p50 {np.median(lengths):.1f}, p90 {np.percentile(lengths, 90):.1f}, max {lengths.max():.1f} m")
    print("  hidden-dropped:", len(gaps) - len(kept), " row planted share of gap rows p10/p50:",
          np.percentile([g["row_planted"] for g in kept], [10, 50]).round(2))
    print("  by plot:", dict(sorted(Counter(g["vineyard_id"] for g in kept).items())))
