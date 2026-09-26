"""Before/after sheet on the four central-field tiles (like the user's mid_fields.jpg): two 14 m crops per tile, before
(saved canopies A) in the left cell and after (B) in the right cell, magenta. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v3_tiles.py A B"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "review"))
from build import crop  # noqa: E402
from canopy_v3_quick import FIELD_TILES, WORK  # noqa: E402

from marcaj.tiles import load_tiles  # noqa: E402

MAGENTA = (255, 0, 255)
a, b = sys.argv[1:3]
sets = {k: [shape(f["geometry"]) for f in json.loads((WORK / f"canopies_{k}.json").read_text())] for k in (a, b)}
trees = {k: STRtree(v) for k, v in sets.items()}
tiles = {t.name: t for t in load_tiles()}
px, cells = 460, []
for name in FIELD_TILES:
    t = tiles[name]
    for fx in (0.3, 0.62):
        cx, cy = t.left + fx * 51.2, t.top - fx * 51.2
        window = box(cx - 8, cy - 8, cx + 8, cy + 8)
        for k in (a, b):
            overlays = [(sets[k][i], MAGENTA) for i in trees[k].query(window)]
            rgb = crop(cx, cy, 14.0, px, overlays)
            cells.append((Image.fromarray(np.moveaxis(rgb, 0, -1)).resize((px, px)), f"{name[7:16]} x{fx} {k}"))
cols = 4
out = Image.new("RGB", (cols * px, (len(cells) + cols - 1) // cols * (px + 16)), "white")
draw = ImageDraw.Draw(out)
for i, (image, caption) in enumerate(cells):
    x, y = (i % cols) * px, (i // cols) * (px + 16)
    out.paste(image, (x, y + 16))
    draw.text((x + 2, y + 2), caption, fill="black")
path = WORK / f"fields_{a}_vs_{b}.jpg"
out.save(path, quality=85)
print(path)
