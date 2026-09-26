"""Per-outline best IoU of two plot predictions side by side (outlines clipped to the imagery). Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/plot_compare.py a.geojson b.geojson"""

import json
import sys
from pathlib import Path

from shapely.geometry import shape

from marcaj.judge import PLOT_SPLIT_NORTHING, _iou
from marcaj.review import load_verdicts
from plot_experiment import tile_footprint

footprint = tile_footprint()
runs = [[shape(f["geometry"]) for f in json.loads(Path(p).read_text())["features"] if f["properties"]["label"] == "block"] for p in sys.argv[1:3]]
rows = []
for i, v in enumerate(v for v in load_verdicts() if v["kind"] == "plot"):
    if v["label"] != "vineyard":
        continue
    g = shape(v["geometry"]).intersection(footprint)
    a, b = (max((_iou(g, p) for p in run), default=0.0) for run in runs)
    rows.append((b - a, i, a, b, "N" if g.centroid.y >= PLOT_SPLIT_NORTHING else "S"))
for d, i, a, b, h in sorted(rows):
    print(f"#{i:2d} {h} {a:.2f} -> {b:.2f} ({d:+.2f})")
