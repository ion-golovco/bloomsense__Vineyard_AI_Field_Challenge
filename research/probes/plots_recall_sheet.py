"""Contact sheet of the vineyard outlines a plot run covers worst, over the 0.2 m mosaic (plot_sheet colours; rows red).
Evaluation only (reads data/review/). Run from backend/:
uv run --frozen python ../research/probes/plots_recall_sheet.py run.geojson out.jpg [max_iou=0.6]   (paths under work/plots_recall)"""

import json
import sys

from shapely.geometry import shape

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from plot_sheet import sheet  # noqa: E402
from plots_recall_eval import WORK, outlines  # noqa: E402

from marcaj.judge import _iou  # noqa: E402

run, out = sys.argv[1], sys.argv[2]
limit = float(dict(a.split("=") for a in sys.argv[3:]).get("max_iou", 0.6))
features = json.loads((WORK / run).read_text())["features"]
blocks = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"]
items = []
for iou, i, g in sorted((max((_iou(g, b) for b in blocks), default=0.0), i, g) for i, label, g in outlines() if label == "vineyard"):
    if iou < limit:
        minx, miny, maxx, maxy = g.bounds
        items.append(((minx + maxx) / 2, (miny + maxy) / 2, max(maxx - minx, maxy - miny) / 2 + 15, f"#{i} IoU {iou:.2f}"))
sheet(features, items, WORK / out)
