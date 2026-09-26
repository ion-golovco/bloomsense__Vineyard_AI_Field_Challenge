"""Native-resolution panels of an EPSG:32635 area with predicted inter-rows (cyan) and waste candidates (magenta)
drawn, for a visual search. Run from backend/:
uv run --frozen python ../research/probes/waste_area.py OUT_PREFIX LEFT BOTTOM RIGHT TOP [PANEL_M] [PANEL_PX]"""

import json
import sys

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.merge import merge
from shapely.geometry import box, shape

from marcaj.tiles import REPO_ROOT, load_tiles

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
prefix = sys.argv[1]
left, bottom, right, top = map(float, sys.argv[2:6])
panel_m = float(sys.argv[6]) if len(sys.argv) > 6 else 20.0
panel_px = int(sys.argv[7]) if len(sys.argv) > 7 else 400
area = box(left, bottom, right, top)
paths = [t.path for t in load_tiles() if t.bounds.intersects(area)]
sources = [rasterio.open(p) for p in paths]
rgb, transform = merge(sources, bounds=(left, bottom, right, top), res=0.025, nodata=0)
features = json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]
interrows = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "interrow_area" and shape(f["geometry"]).intersects(area)]
candidates = [c for c in json.loads((W / "candidates.json").read_text()) if box(*c["bounds"]).intersects(area)]
image = Image.fromarray(np.moveaxis(rgb, 0, -1))
draw = ImageDraw.Draw(image)


def px(x: float, y: float) -> tuple[float, float]:
    return (x - left) / 0.025, (top - y) / 0.025


for polygon in interrows:
    for part in getattr(polygon, "geoms", [polygon]):
        draw.line([px(*p) for p in part.exterior.coords], fill=(0, 255, 255), width=1)
panels = []
cols, rows = int(np.ceil((right - left) / panel_m)), int(np.ceil((top - bottom) / panel_m))
step = int(panel_m / 0.025)
sheet = Image.new("RGB", (cols * panel_px, rows * (panel_px + 14)), (40, 40, 40))
sd = ImageDraw.Draw(sheet)
for j in range(rows):
    for i in range(cols):
        crop = image.crop((i * step, j * step, (i + 1) * step, (j + 1) * step)).resize((panel_px, panel_px), Image.LANCZOS)
        sheet.paste(crop, (i * panel_px, j * (panel_px + 14) + 14))
        sd.text((i * panel_px + 2, j * (panel_px + 14)), f"[{j},{i}] E {left + i * panel_m:.0f}-{left + (i + 1) * panel_m:.0f} N {top - (j + 1) * panel_m:.0f}-{top - j * panel_m:.0f}", fill="white")
sheet.save(W / f"{prefix}.jpg", quality=90)
print(W / f"{prefix}.jpg", sheet.size, len(candidates), "candidates", len(interrows), "inter-rows")
