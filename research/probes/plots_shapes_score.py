"""Plots shapes: judge.plot_scores of runs under data/generated/work/plots_shapes, as-is and with the overgrown outlines
counted as vineyards, plus cover / IoU per overgrown outline. Re-reads data/review/verdicts.json every run.
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/plots_shapes_score.py base cand ..."""

import json
import sys
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union

from marcaj.judge import plot_scores
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

W = REPO_ROOT / "data" / "generated" / "work" / "plots_shapes"
verdicts = load_verdicts()
as_vine = [{**v, "label": "vineyard"} if v["kind"] == "plot" and v["label"] == "overgrown" else v for v in verdicts]
over = [(i, shape(v["geometry"])) for i, v in enumerate(v for v in verdicts if v["kind"] == "plot") if v["label"] == "overgrown"]
print(f"outlines: {sum(v['kind'] == 'plot' for v in verdicts)} ({sum(v['kind'] == 'plot' and v['label'] == 'vineyard' for v in verdicts)} vineyard, {len(over)} overgrown)")
fmt = lambda s: " ".join(f"{h[0].upper()} {s[h]['f1_50']:.3f}/{s[h]['f1_75']:.3f} iou {s[h]['area_iou']:.3f} false {s[h]['false_m2']:,.0f}" for h in ("north", "south") if h in s)
for name in sys.argv[1:]:
    features = [f for f in json.loads((Path(name) if name.endswith(".geojson") else W / f"{name}.geojson").read_text())["features"] if f["properties"]["label"] in ("block", "row")]
    blocks = unary_union([shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"])
    per = [(i, o.intersection(blocks).area / o.area) for i, o in over]
    cover = sum(o.intersection(blocks).area for _, o in over) / max(sum(o.area for _, o in over), 1)
    print(f"{name:10s} {fmt(plot_scores(features, verdicts))} | overgrown cover {cover:.2f} ({' '.join(f'#{i} {c:.2f}' for i, c in per)})")
    print(f"{'':10s} as vine: {fmt(plot_scores(features, as_vine))}")
