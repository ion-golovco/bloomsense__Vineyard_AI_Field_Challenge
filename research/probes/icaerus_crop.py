"""A close look: ICAERUS instances on an 8 m crop (drawn 3x), one panel per zoom, colour by confidence
(red >= 0.5, magenta >= 0.3, cyan below). Run with data/generated/work/icaerus/.venv/bin/python."""

import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import icaerus_lib as L

name, x0, y0 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
zooms = [float(z) for z in sys.argv[4].split(",")]
conf = float(sys.argv[5]) if len(sys.argv) > 5 else 0.1
S, K = 320, 3
model, _ = L.load_model()
rgb = L.read_tile(name)
panels = []
for zoom in zooms:
    found = L.detect(model, rgb, zoom=zoom, conf=conf)
    scores = np.array([s for _, s in found])
    print(zoom, len(found), np.round(np.percentile(scores, [25, 50, 75, 90, 99]), 2), flush=True)
    image = Image.fromarray(rgb[y0:y0 + S, x0:x0 + S]).resize((S * K, S * K), Image.NEAREST)
    draw = ImageDraw.Draw(image)
    for ring, score in sorted(found, key=lambda f: f[1]):
        xy = (ring - (x0, y0)) * K
        if (xy.max(0) < 0).any() or (xy.min(0) > S * K).any():
            continue
        colour = (255, 0, 0) if score >= 0.5 else (255, 0, 255) if score >= 0.3 else (0, 255, 255)
        draw.polygon([tuple(p) for p in xy], outline=colour, width=2 if score >= 0.3 else 1)
    draw.text((6, 6), f"zoom {zoom}", fill=(255, 255, 0))
    panels.append(image)
sheet = Image.new("RGB", (S * K * len(panels), S * K))
for j, image in enumerate(panels):
    sheet.paste(image, (j * S * K, 0))
sheet.save(L.WORK / f"crop_{name[7:16]}_{x0}_{y0}.jpg", quality=85)
