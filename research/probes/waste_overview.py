"""Overview of the 0.2 m mosaic around START with predicted blocks (magenta, labelled) and, for viewing only, the
user's hand-drawn plot outlines (cyan) from data/review/verdicts.json. Evaluation aid; no prediction code reads this.
Run from backend/: uv run --frozen python ../research/probes/waste_overview.py [HALF_M]"""

import json
import sys

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds
from shapely.geometry import shape

from marcaj.mosaic import MOSAIC_PATH
from marcaj.tiles import REPO_ROOT

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
X, Y = 629504.7, 5220250.75
HALF = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
left, bottom, right, top = X - HALF, Y - HALF, X + HALF, Y + HALF
with rasterio.open(MOSAIC_PATH) as source:
    rgb = source.read(window=from_bounds(left, bottom, right, top, source.transform), boundless=True, fill_value=0)
image = Image.fromarray(np.moveaxis(rgb, 0, -1))
scale = image.width / (right - left)
draw = ImageDraw.Draw(image)


def px(x: float, y: float) -> tuple[float, float]:
    return (x - left) * scale, (top - y) * scale


verdicts = json.loads((REPO_ROOT / "data" / "review" / "verdicts.json").read_text())
for v in verdicts:
    if v.get("kind") == "plot":
        geometry = shape(v["geometry"])
        for part in getattr(geometry, "geoms", [geometry]):
            draw.line([px(*p) for p in part.exterior.coords], fill=(0, 255, 255), width=2)
for f in json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]:
    if f["properties"]["label"] == "block":
        geometry = shape(f["geometry"])
        for part in getattr(geometry, "geoms", [geometry]):
            draw.line([px(*p) for p in part.exterior.coords], fill=(255, 0, 255), width=2)
        draw.text(px(geometry.centroid.x, geometry.centroid.y), f["properties"]["vineyard_id"], fill=(255, 255, 0))
sx, sy = px(X, Y)
draw.ellipse([sx - 8, sy - 8, sx + 8, sy + 8], outline=(255, 0, 0), width=3)
image.save(W / "overview_start.jpg", quality=85)
print(W / "overview_start.jpg", image.size)
