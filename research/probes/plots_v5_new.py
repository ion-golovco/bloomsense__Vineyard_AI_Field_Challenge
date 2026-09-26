"""Plots v5: the plots a run adds over a base run, as a contact sheet over the mosaic with the outline each lies on, and
judge.plot_scores with the overgrown outlines counted as vineyards. Evaluation only (reads data/review/). Run from backend/:
uv run --frozen python ../research/probes/plots_v5_new.py base.geojson run.geojson out.jpg   (under data/generated/work/plots_v5)"""

import json
import sys

from shapely.geometry import shape

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from marcaj.judge import plot_scores  # noqa: E402
from marcaj.review import load_verdicts  # noqa: E402
from plot_sheet import sheet  # noqa: E402
from plots_v5_eval import WORK  # noqa: E402

base, run = (json.loads((WORK / name).read_text())["features"] for name in sys.argv[1:3])
old = [shape(f["geometry"]) for f in base if f["properties"]["label"] == "block"]
verdicts = load_verdicts()
outlines = [(v["label"], shape(v["geometry"])) for v in verdicts if v["kind"] == "plot"]
items = []
for f in run:
    g = shape(f["geometry"])
    if f["properties"]["label"] != "block" or any(g.intersection(o).area > 0.8 * g.area for o in old):
        continue
    on = max(((g.intersection(o).area / g.area, label) for label, o in outlines), default=(0, "none"))
    a, b, c, d = g.bounds
    print(f"{f['properties']['vineyard_id']} {g.area:.0f} m2 at ({g.centroid.x:.0f}, {g.centroid.y:.0f}) on {on[1]} {on[0]:.2f}")
    items.append(((a + c) / 2, (b + d) / 2, max(c - a, d - b) / 2 + 20, f"{f['properties']['vineyard_id']} on {on[1]} {on[0]:.2f}"))
if items:
    sheet(run, items, WORK / sys.argv[3])
as_vine = [{**v, "label": "vineyard"} if v["kind"] == "plot" and v["label"] == "overgrown" else v for v in verdicts]
for name, features in (("base", base), ("run", run)):
    s = plot_scores(features, as_vine)
    print(f"{name} with overgrown = vineyard: N {s['north']['f1_50']:.3f}/{s['north']['f1_75']:.3f} S {s['south']['f1_50']:.3f}/{s['south']['f1_75']:.3f} "
          f"area IoU N {s['north']['area_iou']:.3f} S {s['south']['area_iou']:.3f} false N {s['north']['false_m2']:.0f} S {s['south']['false_m2']:.0f}")
