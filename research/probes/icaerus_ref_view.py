"""Reference canopies (yellow), ours (blue) and ICAERUS instances >= 0.25 (magenta) on a 12 m crop of an organizer tile,
drawn 3x. Evaluation view only. Run from backend/: uv run --frozen python ../research/probes/icaerus_ref_view.py TILE X Y ZOOM"""

import json
import sys

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from shapely.geometry import shape

from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT

name, x0, y0, zoom = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
S, K = 480, 3
WORK = REPO_ROOT / "data" / "generated" / "work" / "icaerus"
with rasterio.open(DATA_DIR / "tiles" / name) as source:
    rgb, t = source.read().transpose(1, 2, 0), source.transform
image = Image.fromarray(rgb[y0:y0 + S, x0:x0 + S]).resize((S * K, S * K), Image.LANCZOS)
draw = ImageDraw.Draw(image)
px = lambda xy: [(((x - t.c) / PIXEL_M - x0) * K, ((t.f - y) / PIXEL_M - y0) * K) for x, y in xy]
scene = json.loads((REPO_ROOT / "data" / "generated" / "scene.json").read_text())
for f in scene["features"]:
    p = f["properties"]
    if p.get("label") != "vineyard" or (p.get("source") == "reference" and p.get("tile") != name):
        continue
    g = shape(f["geometry"])
    if g.geom_type != "Polygon" or g.distance(shape({"type": "Point", "coordinates": [t.c + (x0 + S / 2) * PIXEL_M, t.f - (y0 + S / 2) * PIXEL_M]})) > S * PIXEL_M:
        continue
    draw.line(px(g.exterior.coords), fill=(255, 255, 0) if p["source"] == "reference" else (0, 128, 255), width=2)
data = np.load(WORK / f"inst_z{zoom:g}" / (name[:-4] + ".npz"))
for ring, c in zip(np.split(data["xy"], data["ends"][:-1]), data["conf"]):
    if c >= 0.25 and ((ring - (x0, y0)) > -20).all() and ((ring - (x0, y0)) < S + 20).all():
        draw.line([((x - x0) * K, (y - y0) * K) for x, y in np.vstack([ring, ring[:1]])], fill=(255, 0, 255), width=2)
image.save(WORK / f"ref_{name[7:16]}_z{zoom:g}.jpg", quality=85)
