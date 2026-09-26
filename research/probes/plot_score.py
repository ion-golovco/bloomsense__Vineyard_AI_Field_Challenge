"""Scores `marcaj.plots.detect_plots` with PlotParams overrides against the lab outlines (as drawn and clipped to the
imagery), and saves the plots under data/generated/work/plots/. Evaluation only (reads data/review/).
Run from backend/: uv run --frozen python ../research/probes/plot_score.py [out=name.geojson] [name=value ...]"""

import json
import sys
import time
from dataclasses import fields, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from plot_experiment import WORK, _fmt, scores, tile_footprint  # noqa: E402

from marcaj.plots import PlotParams, detect_plots  # noqa: E402
from marcaj.review import load_verdicts  # noqa: E402

args = dict(a.split("=") for a in sys.argv[1:])
out = args.pop("out", "")
base = PlotParams()
kinds = {f.name: type(getattr(base, f.name)) for f in fields(base)}
params = replace(base, **{k: kinds[k](v) for k, v in args.items()})
started = time.perf_counter()
features = detect_plots(params)
print(f"{args or 'defaults'}: {sum(f['properties']['label'] == 'block' for f in features)} plots in {time.perf_counter() - started:.1f} s")
for kind, value in scores(features, load_verdicts(), tile_footprint()).items():
    print(f"  {kind:8s} {_fmt(value)}")
if out:
    (WORK / out).write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}))
