"""Contact sheet at native 0.025 m of gap targets from data/generated/work/poi_dev/variants.json (poi_dev_eval.py):
by default the deviation targets that are not fixed-rule targets and carry no gap-tab label. The stretch is drawn
0.8 m beside its axis (magenta, end ticks crossing the axis) so the row itself stays visible. Run from backend/:
uv run --frozen python ../research/probes/poi_dev_sheet.py [variant] [count] [seed]"""

import json
import random
import sys

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds
from shapely.geometry import box

from marcaj.tiles import DATA_DIR, REPO_ROOT, load_tiles

PX_M = 0.025
SIDE_M = 10.0
OUT = REPO_ROOT / "data" / "generated" / "work" / "poi_dev"
variant, count, seed = (sys.argv[1:] + ["dev k3 f3", "24", "0"][len(sys.argv) - 1:])[:3]
variants = json.loads((OUT / "variants.json").read_text())
fixed = {(p["row_id"], tuple(p["gap_start"])) for p in variants["fixed"]}
labelled = set(json.loads((OUT / "labelled.json").read_text())) if (OUT / "labelled.json").is_file() else set()
pool = [p for p in variants[variant] if (p["row_id"], tuple(p["gap_start"])) not in fixed and p["id"] not in labelled]
random.Random(int(seed)).shuffle(pool)
pool = sorted(pool[:int(count)], key=lambda p: p["gap_m"])
TILES = load_tiles(DATA_DIR)


def crop(x0: float, y0: float, x1: float, y1: float) -> Image.Image:
    w, h = round((x1 - x0) / PX_M), round((y1 - y0) / PX_M)
    canvas = np.zeros((3, h, w), np.uint8)
    area = box(x0, y0, x1, y1)
    for tile in TILES:
        part = tile.bounds.intersection(area)
        if part.is_empty or part.area < 1e-6:
            continue
        a, b, c, d = part.bounds
        with rasterio.open(tile.path) as source:
            data = source.read(window=from_bounds(a, b, c, d, source.transform), out_shape=(3, max(round((d - b) / PX_M), 1), max(round((c - a) / PX_M), 1)))
        col, row = round((a - x0) / PX_M), round((y1 - d) / PX_M)
        canvas[:, row:row + data.shape[1], col:col + data.shape[2]] = data[:, :h - row, :w - col]
    return Image.fromarray(np.moveaxis(canvas, 0, -1))


thumbs = []
for p in pool:
    a, b = np.array(p["gap_start"]), np.array(p["gap_end"])
    mid = (a + b) / 2
    side = max(SIDE_M, p["gap_m"] + 4)
    x0, y1 = mid[0] - side / 2, mid[1] + side / 2
    image = crop(x0, mid[1] - side / 2, mid[0] + side / 2, y1)
    normal = np.array([-(b - a)[1], (b - a)[0]]) / max(np.linalg.norm(b - a), 1e-6) * 0.8
    px = lambda q: ((q[0] - x0) / PX_M, (y1 - q[1]) / PX_M)
    draw = ImageDraw.Draw(image)
    draw.line([px(a + normal), px(b + normal)], fill=(255, 0, 255), width=2)
    for q in (a, b):  # end ticks cross the axis, so the row is the one they cut
        draw.line([px(q - normal * 0.6), px(q + normal * 1.2)], fill=(255, 0, 255), width=4)
    image = image.resize((400, 400))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 400, 16], fill=(0, 0, 0))
    draw.text((3, 2), f"{p['row_id']} {p['gap_m']:.1f} m g{p['green_share']:.2f} {side:.0f} m box", fill=(255, 255, 255))
    thumbs.append(image)
cols = 4
sheet = Image.new("RGB", (cols * 402, -(-len(thumbs) // cols) * 402), (40, 40, 40))
for i, image in enumerate(thumbs):
    sheet.paste(image, ((i % cols) * 402, (i // cols) * 402))
path = OUT / f"sheet_{variant.replace(' ', '_')}_new_unlabelled.jpg"
sheet.save(path, quality=88)
print(f"{len(thumbs)} of {len([p for p in variants[variant] if (p['row_id'], tuple(p['gap_start'])) not in fixed])} new targets -> {path}")
print("\n".join(f"{i + 1:2} {p['id']} {p['gap_m']:.2f} m" for i, p in enumerate(pool)))
