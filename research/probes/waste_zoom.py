"""Native-resolution zooms (2.4 m square, 5x upscaled) of chosen waste candidates, numbered as in a sheet.
Run from backend/: uv run --frozen python ../research/probes/waste_zoom.py CANDIDATES.json OUT.jpg N1 N2 ..."""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from waste_sheet import _crop  # noqa: E402

from marcaj.tiles import DATA_DIR  # noqa: E402

SIZE_M, PX = 2.4, 480
items = json.loads(Path(sys.argv[1]).read_text())
numbers = [int(n) for n in sys.argv[3:]]
sheet = Image.new("RGB", (4 * PX, ((len(numbers) - 1) // 4 + 1) * (PX + 16)), "white")
for i, number in enumerate(numbers):
    c = items[number - 1]
    c0, r0, c1, r1 = c["px"]
    crop = _crop(DATA_DIR / "tiles" / c["tile"], (c0 + c1) / 2, (r0 + r1) / 2, SIZE_M, PX)
    x, y = (i % 4) * PX, (i // 4) * (PX + 16)
    sheet.paste(crop, (x, y + 16))
    ImageDraw.Draw(sheet).text((x + 3, y + 2), f"#{number} {c['tile'][7:-4]} {c['kind']} {c['area_m2']:.2f} m2", fill="black")
sheet.save(sys.argv[2], quality=90)
