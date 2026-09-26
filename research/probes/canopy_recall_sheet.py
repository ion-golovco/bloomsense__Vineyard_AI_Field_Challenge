"""Before/after contact sheet: the longest gap POIs of OLD that NEW closes (no NEW gap POI within 2 m), one per tile,
each as image | OLD canopies (yellow) with the gap (red) | NEW canopies (magenta = added). 12 m crops at 0.03 m/px.
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_recall_sheet.py OLD NEW [N]"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import LineString, Point, box, shape

sys.path.insert(0, str(Path(__file__).parent))
import poi_render  # noqa: E402
from canopy_recall_lib import WORK, gap_stats  # noqa: E402

old_tag, new_tag = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 8
load = lambda tag: json.loads((WORK / f"canopies_{tag}.json").read_text())
old, new = load(old_tag), load(new_tag)
old_pois = [p for p in gap_stats(old)["_pois"] if p["properties"]["reason"] == "gap"]
new_points = [Point(p["geometry"]["coordinates"]) for p in gap_stats(new)["_pois"] if p["properties"]["reason"] == "gap"]
closed = [p for p in old_pois if all(Point(p["geometry"]["coordinates"]).distance(q) > 2 for q in new_points)]
closed.sort(key=lambda p: -p["properties"]["gap_m"])
picks, seen = [], set()
for p in closed:
    tile = p["properties"]["source_ref"][0]
    if tile not in seen:
        picks.append(p)
        seen.add(tile)
    if len(picks) == n:
        break
print(f"{len(old_pois)} gaps before, {len(new_points)} after, {len(closed)} closed; sheet of {len(picks)}")
og, ng = [shape(f["geometry"]) for f in old], [shape(f["geometry"]) for f in new]
ot, nt = STRtree(og), STRtree(ng)
PX, SIZE = 0.03, 400
poi_render.PX_M = PX
sheet = Image.new("RGB", (3 * SIZE, len(picks) * SIZE), "white")
for k, p in enumerate(picks):
    pr = p["properties"]
    cx, cy = p["geometry"]["coordinates"]
    half = SIZE * PX / 2
    x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
    image = poi_render.crop(x0, y0, x1, y1)
    to = lambda x, y: ((x - x0) / PX, (y1 - y) / PX)
    before, after = image.copy(), image.copy()
    db, da = ImageDraw.Draw(before), ImageDraw.Draw(after)
    olds = [og[i] for i in ot.query(box(x0, y0, x1, y1))]
    for g in olds:
        db.line([to(*c) for c in g.exterior.coords], fill=(255, 255, 0), width=1)
    db.line([to(*pr["gap_start"]), to(*pr["gap_end"])], fill=(255, 0, 0), width=2)
    for i in nt.query(box(x0, y0, x1, y1)):
        g = ng[i]
        is_new = sum(g.intersection(o).area for o in olds) < 0.3 * g.area
        da.line([to(*c) for c in g.exterior.coords], fill=(255, 0, 255) if is_new else (255, 255, 0), width=2 if is_new else 1)
    for im, text in ((image, f"{pr['source_ref'][0][7:16]} {pr['row_id']}"), (before, f"before: gap {pr['gap_m']} m"), (after, "after (magenta = added)")):
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, SIZE, 12], fill="black")
        d.text((2, 0), text, fill="white")
    for j, im in enumerate((image, before, after)):
        sheet.paste(im, (j * SIZE, k * SIZE))
out = WORK / f"before_after_{old_tag}_{new_tag}.jpg"
sheet.save(out, quality=85)
print(out)
