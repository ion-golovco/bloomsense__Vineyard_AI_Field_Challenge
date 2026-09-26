"""Contact sheet of the rows with the most uncovered vine green (rows_<tag>.json): 16 m crops at 0.04 m/px around the
longest piece's midpoint, uploaded row axis in red, the variant's canopies in yellow. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_recall_rowsheet.py TAG [N] [OUT]"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).parent))
import poi_render  # noqa: E402
from canopy_recall_lib import WORK, uploaded_features  # noqa: E402

tag = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 16
out = sys.argv[3] if len(sys.argv) > 3 else f"rows_{tag}_worst.jpg"
rows = json.loads((WORK / f"rows_{tag}.json").read_text())[:n]
pieces = {}
for f in uploaded_features():
    if f["properties"]["label"] == "row":
        pieces.setdefault(f["properties"]["row_id"], []).append(shape(f["geometry"]))
canopies = [shape(f["geometry"]) for f in json.loads((WORK / f"canopies_{tag}.json").read_text())]
tree = STRtree(canopies)
PX, SIZE = 0.04, 400
poi_render.PX_M = PX
sheet = Image.new("RGB", (4 * SIZE, ((len(rows) - 1) // 4 + 1) * SIZE), "white")
for k, r in enumerate(rows):
    line = max(pieces[r["row_id"]], key=lambda g: g.length)
    mx, my = line.interpolate(0.5, normalized=True).coords[0]
    half = SIZE * PX / 2
    x0, y0, x1, y1 = mx - half, my - half, mx + half, my + half
    im = poi_render.crop(x0, y0, x1, y1)
    draw = ImageDraw.Draw(im)
    to = lambda x, y: ((x - x0) / PX, (y1 - y) / PX)
    for i in tree.query(box(x0, y0, x1, y1)):
        draw.line([to(*p) for p in canopies[i].exterior.coords], fill=(255, 255, 0), width=1)
    for p in pieces[r["row_id"]]:
        draw.line([to(*c) for c in p.coords], fill=(255, 0, 0), width=1)
    draw.rectangle([0, 0, SIZE, 12], fill="black")
    draw.text((2, 0), f"{r['row_id']} {r['m']} m cover {r['cover']} green {r['green']}", fill="white")
    sheet.paste(im, ((k % 4) * SIZE, (k // 4) * SIZE))
sheet.save(WORK / out, quality=85)
