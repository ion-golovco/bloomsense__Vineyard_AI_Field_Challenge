import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw

from marcaj.tiles import DATA_DIR, REPO_ROOT

S = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "data" / "generated"
S.mkdir(parents=True, exist_ok=True)
TILES = DATA_DIR / "tiles"
features = json.loads((S / "tile_features.json").read_text())
order = sorted(features, key=lambda name: -features[name].get("vine_peak", 0))
THUMB, COLS, PER_PAGE = 150, 10, 60
for page in range(0, len(order), PER_PAGE):
    names = order[page:page + PER_PAGE]
    sheet = Image.new("RGB", (COLS * THUMB, ((len(names) - 1) // COLS + 1) * (THUMB + 14)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, name in enumerate(names):
        with rasterio.open(TILES / f"{name}.tif") as src:
            rgb = src.read(out_shape=(3, THUMB, THUMB))
        x, y = (index % COLS) * THUMB, (index // COLS) * (THUMB + 14)
        sheet.paste(Image.fromarray(np.moveaxis(rgb, 0, -1)), (x, y + 14))
        f = features[name]
        draw.text((x + 2, y + 1), f"{page + index + 1} {name[7:]} v{f.get('vine_peak', 0):.0f} g{f.get('green', 0):.2f}", fill="black")
    sheet.save(S / f"sheet_{page // PER_PAGE + 1}.jpg", quality=80)
print("pages", (len(order) - 1) // PER_PAGE + 1)
