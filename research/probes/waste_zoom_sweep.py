"""5x native zooms (2.4 m) of chosen sweep items by number. Run from backend/:
uv run --frozen python ../research/probes/waste_zoom_sweep.py SWEEP.json OUT.jpg N1 N2 ..."""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from waste_sheet import _crop  # noqa: E402

from marcaj.tiles import DATA_DIR  # noqa: E402

PX = 400
items = {i["n"]: i for i in json.loads(Path(sys.argv[1]).read_text())}
numbers = [int(n) for n in sys.argv[3:]]
sheet = Image.new("RGB", (4 * PX, ((len(numbers) - 1) // 4 + 1) * (PX + 16)), "white")
for k, n in enumerate(numbers):
    i = items[n]
    x0, y0, x1, y1 = i["px"]
    crop = _crop(DATA_DIR / "tiles" / i["tile"], (x0 + x1) / 2, (y0 + y1) / 2, 2.4, PX)
    x, y = (k % 4) * PX, (k // 4) * (PX + 16)
    sheet.paste(crop, (x, y + 16))
    ImageDraw.Draw(sheet).text((x + 3, y + 2), f"#{n} {i['tile'][7:-4]} {i['area_m2']:.2f} m2 row {i['row_m']} m", fill="black")
sheet.save(sys.argv[2], quality=90)
