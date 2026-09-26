"""Native-resolution panels covering every inter-row of chosen predicted blocks (12.5 m panels, 4x4 per sheet,
inter-rows outlined), for an exhaustive visual sweep. Only panels that hold inter-row area are rendered.
Run from backend/: uv run --frozen python ../research/probes/waste_block_panels.py PREFIX BLOCK [BLOCK ...]"""

import json
import sys

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.merge import merge
from shapely.geometry import box, shape
from shapely.ops import unary_union

from marcaj.tiles import REPO_ROOT, load_tiles

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
PANEL_M, PANEL_PX, COLS = 12.5, 500, 4
prefix, names = sys.argv[1], set(sys.argv[2:])
features = json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]
interrows = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "interrow_area" and f["properties"]["vineyard_id"] in names]
union = unary_union(interrows)
left, bottom, right, top = union.bounds
tiles = load_tiles()
panels = []
for j in range(int(np.ceil((top - bottom) / PANEL_M))):
    for i in range(int(np.ceil((right - left) / PANEL_M))):
        cell = box(left + i * PANEL_M, top - (j + 1) * PANEL_M, left + (i + 1) * PANEL_M, top - j * PANEL_M)
        if union.intersection(cell).area >= 2.0:
            panels.append(cell)
per = COLS * COLS
for page in range(0, len(panels), per):
    chunk = panels[page:page + per]
    sheet = Image.new("RGB", (COLS * PANEL_PX, ((len(chunk) - 1) // COLS + 1) * (PANEL_PX + 14)), (40, 40, 40))
    sd = ImageDraw.Draw(sheet)
    for k, cell in enumerate(chunk):
        l, b, r, t = cell.bounds
        sources = [rasterio.open(tile.path) for tile in tiles if tile.bounds.intersects(cell)]
        rgb, _ = merge(sources, bounds=(l, b, r, t), res=0.025, nodata=0)
        for source in sources:
            source.close()
        image = Image.fromarray(np.moveaxis(rgb[:, :PANEL_PX, :PANEL_PX], 0, -1))
        draw = ImageDraw.Draw(image)
        for polygon in interrows:
            clipped = polygon.intersection(cell.buffer(1))
            for part in getattr(clipped, "geoms", [clipped]):
                if part.geom_type == "Polygon":
                    draw.line([((x - l) / 0.025, (t - y) / 0.025) for x, y in part.exterior.coords], fill=(0, 255, 255), width=1)
        x, y = (k % COLS) * PANEL_PX, (k // COLS) * (PANEL_PX + 14)
        sheet.paste(image, (x, y + 14))
        sd.text((x + 2, y), f"{page + k + 1}: E {l:.1f}-{r:.1f} N {b:.1f}-{t:.1f}", fill="white")
    sheet.save(W / f"{prefix}_{page // per + 1:02d}.jpg", quality=88)
print(len(panels), "panels,", (len(panels) - 1) // per + 1, "sheets")
