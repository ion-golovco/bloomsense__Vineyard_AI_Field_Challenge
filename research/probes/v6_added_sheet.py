"""v6: contact sheet of canopy area a variant adds over another (OUT/canopies_A.json vs _B.json): the tiles with the most
added area, a 16 m crop each at the added centroid; cyan = added, magenta = both. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/v6_added_sheet.py NEW BASE OUT.jpg [N]"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.tiles import PIXEL_M  # noqa: E402

new, base, out = sys.argv[1], sys.argv[2], sys.argv[3]
count = int(sys.argv[4]) if len(sys.argv) > 4 else 18
HALF_M = float(sys.argv[5]) if len(sys.argv) > 5 else 8.0
SKIP = int(sys.argv[6]) if len(sys.argv) > 6 else 0
A = [shape(f["geometry"]) for f in json.loads((L.OUT / f"canopies_{new}.json").read_text())]
B = [shape(f["geometry"]) for f in json.loads((L.OUT / f"canopies_{base}.json").read_text())]
tree = STRtree(B)
added = [g for g in A if not any(g.intersects(B[i]) for i in tree.query(g))]
by = defaultdict(list)
for g in added:
    c = g.centroid
    name = next((n for n, t in L.TILES.items() if t.bounds.contains(c)), None)
    if name:
        by[name].append(g)
ranked = sorted(by.items(), key=lambda kv: -sum(g.area for g in kv[1]))[SKIP:SKIP + count]
print(f"added {len(added)} pieces {sum(g.area for g in added):.0f} m2 on {len(by)} tiles; top: " +
      ", ".join(f"{n[7:16]} {sum(g.area for g in gs):.1f}" for n, gs in ranked))
ims = []
PX = 320 if HALF_M >= 8 else 480
pieces = [shape(f["geometry"]) for f in L.ref_pieces()]
for name, gs in ranked:
    tile = L.TILES[name]
    img, _ = canopy.read_rgb(tile)
    biggest = max(gs, key=lambda g: g.area).centroid
    h = int(HALF_M / PIXEL_M)
    c, r = int((biggest.x - tile.left) / PIXEL_M), int((tile.top - biggest.y) / PIXEL_M)
    r0, c0 = max(min(r - h, 2048 - 2 * h), 0), max(min(c - h, 2048 - 2 * h), 0)
    im = Image.fromarray(np.transpose(img[:, r0:r0 + 2 * h, c0:c0 + 2 * h], (1, 2, 0)).astype(np.uint8)).convert("RGB")
    d = ImageDraw.Draw(im)
    px = lambda x, y: ((x - tile.left) / PIXEL_M - c0, (tile.top - y) / PIXEL_M - r0)
    for p in pieces:
        if p.distance(biggest) < 12:
            d.line([px(*q) for q in p.coords], fill=(255, 255, 0), width=1)
    for g in B:
        if g.distance(biggest) < 12:
            d.line([px(*q) for q in g.exterior.coords], fill=(255, 0, 255), width=2)
    for g in gs:
        if g.distance(biggest) < 12:
            d.line([px(*q) for q in g.exterior.coords], fill=(0, 255, 255), width=3)
    d.text((4, 4), f"{name[7:16]} +{sum(g.area for g in gs):.1f} m2", fill=(255, 255, 255))
    ims.append(im.resize((PX, PX)))
cols = 6 if PX <= 320 else 3
sheet = Image.new("RGB", (PX * cols, PX * ((len(ims) + cols - 1) // cols)))
for k, im in enumerate(ims):
    sheet.paste(im, (PX * (k % cols), PX * (k // cols)))
sheet.save(out, quality=85)
