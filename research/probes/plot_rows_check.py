"""Stray-row count of a plot prediction: rows closer than 0.6 of their plot's spacing to a neighbour (grass strips beside a
real row). Run from backend/: uv run --frozen python ../research/probes/plot_rows_check.py plots.geojson"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import shape

features = json.loads(Path(sys.argv[1]).read_text())["features"]
spacing = {f["properties"]["vineyard_id"]: f["properties"]["row_spacing_m"] for f in features if f["properties"]["label"] == "block"}
angle = {f["properties"]["vineyard_id"]: np.radians(f["properties"]["row_angle"]) for f in features if f["properties"]["label"] == "block"}
offsets = defaultdict(list)
for f in features:
    if f["properties"]["label"] == "row":
        plot = f["properties"]["vineyard_id"]
        offsets[plot].append(np.asarray(shape(f["geometry"]).centroid.coords[0]) @ [-np.sin(angle[plot]), np.cos(angle[plot])])
close = sum(int((np.diff(sorted(v)) < 0.6 * spacing[p]).sum()) for p, v in offsets.items())
print(f"{sum(len(v) for v in offsets.values())} rows, {close} neighbour pairs under 0.6 spacing apart")
