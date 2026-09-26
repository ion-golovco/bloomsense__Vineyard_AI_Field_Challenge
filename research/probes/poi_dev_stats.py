"""Along-row canopy spacing per field (vineyard_id) from the v5 predictions: centre-to-centre distance between
consecutive canopy runs on the axis, and the empty stretch between them. Caches the sampled rows.
Run from backend/: uv run --frozen python ../research/probes/poi_dev_stats.py"""

import json
import pickle
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from marcaj import poi
from marcaj.tiles import REPO_ROOT

V5 = REPO_ROOT / "data" / "generated" / "work" / "v5"
OUT = REPO_ROOT / "data" / "generated" / "work" / "poi_dev"
OUT.mkdir(parents=True, exist_ok=True)
cache = OUT / "rows.pkl"
if cache.is_file():
    rows = pickle.loads(cache.read_bytes())
else:
    started = time.perf_counter()
    rows = poi.sample_rows(json.loads((V5 / "predictions.geojson").read_text())["features"])
    cache.write_bytes(pickle.dumps(rows))
    print(f"{len(rows)} rows sampled in {time.perf_counter() - started:.1f} s")

empty_by, pitch_by, runs_by = defaultdict(list), defaultdict(list), defaultdict(list)
for row in rows:
    hidden = ~row.seen | row.dark
    planted = [(a, b) for a, b in poi._runs(row.canopy)]
    for (a0, b0), (a1, b1) in zip(planted, planted[1:]):
        if hidden[b0:a1].mean() > poi.MAX_HIDDEN:
            continue
        empty_by[row.vineyard_id].append((a1 - b0) * poi.SAMPLE_M)
        pitch_by[row.vineyard_id].append(((a1 + b1) - (a0 + b0)) / 2 * poi.SAMPLE_M)
    runs_by[row.vineyard_id] += [(b - a) * poi.SAMPLE_M for a, b in planted]


def robust(values: np.ndarray) -> tuple[float, float, float, float]:
    median = float(np.median(values))
    return median, float(1.4826 * np.median(np.abs(values - median))), *np.percentile(values, [25, 75])


stats = {}
print(f"{'field':10} {'n':>5} | empty m: med  MAD  p25  p75  p90  p99 | c2c m: med  MAD | run m med | >=5m  >=3m")
for field in sorted(empty_by, key=lambda f: -len(empty_by[f])):
    empty, pitch = np.array(empty_by[field]), np.array(pitch_by[field])
    if len(empty) < 20:
        continue
    em, emad, e25, e75 = robust(empty)
    pm, pmad, _, _ = robust(pitch)
    stats[field] = {"n": len(empty), "empty_median": em, "empty_mad": emad, "empty_p25": e25, "empty_p75": e75,
                    "empty_p90": float(np.percentile(empty, 90)), "pitch_median": pm, "pitch_mad": pmad,
                    "run_median": float(np.median(runs_by[field]))}
    print(f"{field:10} {len(empty):5} | {em:9.2f} {emad:4.2f} {e25:4.2f} {e75:4.2f} {np.percentile(empty, 90):4.2f} {np.percentile(empty, 99):4.2f} |"
          f" {pm:9.2f} {pmad:4.2f} | {np.median(runs_by[field]):9.2f} | {(empty >= 5).sum():4} {(empty >= 3).sum():4}")
allempty = np.concatenate([np.array(v) for v in empty_by.values()])
print("all fields: empty median %.2f MAD %.2f p90 %.2f p99 %.2f; n %d" % (*robust(allempty)[:2], *np.percentile(allempty, [90, 99]), len(allempty)))
(OUT / "spacing_stats.json").write_text(json.dumps(stats, indent=1))
