"""Contact sheet: ICAERUS instances on a 16 m crop of a few tiles at several input zooms (the network's view of the ground
at 2.5 / zoom cm/px). Run with data/generated/work/icaerus/.venv/bin/python."""

import sys
import time

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import icaerus_lib as L

TILES = sys.argv[1].split(",") if len(sys.argv) > 1 else ["siret3_r021_c012.tif", "siret3_r006_c004.tif", "siret3_r019_c011.tif", "siret3_r022_c013.tif"]
ZOOMS = [float(z) for z in sys.argv[2].split(",")] if len(sys.argv) > 2 else [0.5, 1.0, 2.0, 3.2]
X0, Y0, S = 704, 704, 640  # the crop in tile px

model, _ = L.load_model()
panels = []
for name in TILES:
    rgb = L.read_tile(name)
    row = []
    for zoom in ZOOMS:
        started = time.perf_counter()
        found = L.detect(model, rgb, zoom=zoom)
        dt = time.perf_counter() - started
        image = Image.fromarray(rgb[Y0:Y0 + S, X0:X0 + S]).convert("RGB")
        draw = ImageDraw.Draw(image)
        inside = 0
        for ring, score in found:
            xy = ring - (X0, Y0)
            if (xy.min(0) > -50).all() and (xy.max(0) < S + 50).all():
                inside += 1
                draw.polygon([tuple(p) for p in xy], outline=(255, 0, 255) if score >= 0.25 else (0, 255, 255))
        draw.text((6, 6), f"{name[7:16]} zoom {zoom} n={len(found)} in crop {inside} {dt:.1f}s", fill=(255, 255, 0))
        row.append(image)
        print(name, zoom, len(found), f"{dt:.1f}s", flush=True)
    panels.append(row)
sheet = Image.new("RGB", (S * len(ZOOMS), S * len(TILES)))
for i, row in enumerate(panels):
    for j, image in enumerate(row):
        sheet.paste(image, (j * S, i * S))
sheet.save(L.WORK / "zoom_sheet.jpg", quality=85)
