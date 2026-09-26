"""Crops of every `obstacle` POI: the stretch (red if it was a challenge target, yellow if not), obstacles in magenta/cyan.
Run from backend/: uv run --frozen python ../research/probes/obstacles_poi_sheet.py [poi.geojson]"""

import json
import sys
from pathlib import Path

import rasterio
from PIL import Image, ImageDraw
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from shapely.geometry import shape

from marcaj.tiles import REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work"
pois = json.loads((Path(sys.argv[1]) if len(sys.argv) > 1 else WORK / "obstacles" / "poi.geojson").read_text())["features"]
before = {f["properties"]["row_id"] + str(f["properties"]["gap_start"]): f["properties"]["challenge"]
          for f in json.loads((WORK / "poi" / "poi.geojson").read_text())["features"] if "gap_start" in f["properties"]}
obstacles = json.loads((WORK / "obstacles" / "obstacles.geojson").read_text())["features"]
chosen = [f["properties"] for f in pois if f["properties"]["reason"] == "obstacle"]
CROP, CELL, cols = 36.0, 300, 6
sheet = Image.new("RGB", (cols * CELL, (len(chosen) + cols - 1) // cols * CELL), (30, 30, 30))
with rasterio.open(REPO_ROOT / "data" / "generated" / "mosaic_20cm.tif") as source:
    for k, p in enumerate(chosen):
        (x0, y0), (x1, y1) = p["gap_start"], p["gap_end"]
        L, T = (x0 + x1) / 2 - CROP / 2, (y0 + y1) / 2 + CROP / 2
        rgb = source.read(window=from_bounds(L, T - CROP, L + CROP, T, source.transform), out_shape=(3, CELL, CELL), resampling=Resampling.bilinear)
        im = Image.fromarray(rgb.transpose(1, 2, 0))
        draw = ImageDraw.Draw(im)
        to = lambda x, y: ((x - L) * CELL / CROP, (T - y) * CELL / CROP)
        for o in obstacles:
            g = shape(o["geometry"])
            for part in getattr(g, "geoms", [g]):
                draw.line([to(*c) for c in part.exterior.coords], fill=(0, 255, 255) if o["properties"]["obstacle_type"] == "building" else (255, 0, 255), width=2)
        draw.line([to(x0, y0), to(x1, y1)], fill=(255, 0, 0) if before.get(p["row_id"] + str(p["gap_start"])) else (255, 255, 0), width=3)
        draw.rectangle([0, 0, CELL, 12], fill=(0, 0, 0))
        draw.text((2, 0), f"{k} {p['row_id']} {p['stretch']} {p['gap_m']} m", fill=(255, 255, 255))
        sheet.paste(im, ((k % cols) * CELL, (k // cols) * CELL))
sheet.save(WORK / "obstacles" / "obstacle_pois.jpg", quality=85)
print(len(chosen), "->", WORK / "obstacles" / "obstacle_pois.jpg")
