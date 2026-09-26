"""Contact sheet of every detected obstacle (16 m crops, outline in colour) and a site overview with blocks and obstacles.
Run from backend/: uv run --frozen python ../research/probes/obstacles_sheet.py [obstacles.geojson] [scene.geojson]"""

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from shapely.geometry import shape

from marcaj.tiles import REPO_ROOT

OUT = REPO_ROOT / "data" / "generated" / "work" / "obstacles"
obstacles_path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "obstacles.geojson"
scene_path = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO_ROOT / "data" / "generated" / "predictions.geojson"
obstacles = json.loads(obstacles_path.read_text())["features"]
features = json.loads(scene_path.read_text())["features"]
blocks = [f for f in features if f["properties"]["label"] == "block"]
COLOUR = {"tree": (255, 0, 255), "building": (0, 255, 255)}
CROP_M, CELL = 16.0, 160

with rasterio.open(REPO_ROOT / "data" / "generated" / "mosaic_20cm.tif") as source:
    cols = 16
    sheet = Image.new("RGB", (cols * CELL, (len(obstacles) + cols - 1) // cols * CELL), (30, 30, 30))
    for k, obstacle in enumerate(obstacles):
        polygon = shape(obstacle["geometry"])
        cx, cy = polygon.centroid.x, polygon.centroid.y
        L, T = cx - CROP_M / 2, cy + CROP_M / 2
        rgb = source.read(window=from_bounds(L, T - CROP_M, L + CROP_M, T, source.transform), out_shape=(3, CELL, CELL), resampling=Resampling.bilinear)
        im = Image.fromarray(rgb.transpose(1, 2, 0))
        draw = ImageDraw.Draw(im)
        scale = CELL / CROP_M
        for ring in [polygon.exterior] if polygon.geom_type == "Polygon" else [g.exterior for g in polygon.geoms]:
            draw.line([((x - L) * scale, (T - y) * scale) for x, y in ring.coords], fill=COLOUR[obstacle["properties"]["obstacle_type"]], width=1)
        p = obstacle["properties"]
        draw.rectangle([0, 0, CELL, 12], fill=(0, 0, 0))
        draw.text((2, 0), f"{k} {p['obstacle_type'][0]} {p['area_m2']:.0f}m2 w{p['width_m']} s{p['shadow_share']}", fill=(255, 255, 255))
        sheet.paste(im, ((k % cols) * CELL, (k // cols) * CELL))
    sheet.save(OUT / "obstacles_sheet.jpg", quality=85)

    # overview at 1 m/px
    b = source.bounds
    scale = 1.0
    width, height = round((b.right - b.left) / scale), round((b.top - b.bottom) / scale)
    rgb = source.read(out_shape=(3, height, width), resampling=Resampling.average)
    im = Image.fromarray(rgb.transpose(1, 2, 0))
    draw = ImageDraw.Draw(im)
    to = lambda x, y: ((x - b.left) / scale, (b.top - y) / scale)
    for block in blocks:
        g = shape(block["geometry"])
        for part in getattr(g, "geoms", [g]):
            draw.line([to(*c) for c in part.exterior.coords], fill=(255, 255, 0), width=2)
    for k, obstacle in enumerate(obstacles):
        g = shape(obstacle["geometry"])
        for part in getattr(g, "geoms", [g]):
            draw.polygon([to(*c) for c in part.exterior.coords], outline=COLOUR[obstacle["properties"]["obstacle_type"]], fill=COLOUR[obstacle["properties"]["obstacle_type"]])
        draw.text(to(g.centroid.x + 3, g.centroid.y), str(k), fill=(255, 255, 255))
    im.save(OUT / "obstacles_overview.jpg", quality=85)
print(len(obstacles), "->", OUT / "obstacles_sheet.jpg", OUT / "obstacles_overview.jpg")
