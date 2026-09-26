"""What a variant adds over another: canopies whose area is mostly new (under 30% covered by the other), on the two
organizer tiles scored against the reference (share that overlaps a reference canopy by half or more) and site-wide by
plot and tile; optional contact sheet of the tiles with the most added area. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_recall_added.py OLD NEW [sheet]"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).parent))
import poi_render  # noqa: E402
from canopy_recall_lib import NAMES, WORK  # noqa: E402

from marcaj.cvat import build_scene  # noqa: E402
from marcaj.tiles import DATA_DIR, load_tiles  # noqa: E402

old_tag, new_tag = sys.argv[1], sys.argv[2]
load = lambda tag: json.loads((WORK / f"canopies_{tag}.json").read_text())
old, new = load(old_tag), load(new_tag)
og = [shape(f["geometry"]) for f in old]
tree = STRtree(og)
added = []
for f in new:
    g = shape(f["geometry"])
    covered = sum(g.intersection(og[i]).area for i in tree.query(g))
    if covered < 0.3 * g.area:
        added.append((f, g))
base = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=load_tiles())["features"]
refs = [shape(f["geometry"]) for f in base if f["properties"]["source"] == "reference" and f["properties"]["label"] == "vineyard"]
rtree = STRtree(refs)
for name in NAMES:
    here = [g for f, g in added if f["properties"]["tile_run"] == name]
    good = sum(any(g.intersection(refs[i]).area >= 0.5 * g.area for i in rtree.query(g)) for g in here)
    print(f"{name[7:16]}: {len(here)} added, {good} of them on a reference canopy, {sum(g.area for g in here):.1f} m2")
print(f"site: {len(added)} added, {sum(g.area for _, g in added):.0f} m2, median {np.median([g.area for _, g in added]):.2f} m2")
by_plot = Counter(f["properties"]["vineyard_id"] for f, _ in added)
print("by plot:", by_plot.most_common(12))
area_tile = Counter()
for f, g in added:
    area_tile[f["properties"]["tile_run"]] += g.area
print("by tile m2:", [(k[7:16], round(v, 1)) for k, v in area_tile.most_common(12)])
if len(sys.argv) > 3:
    ng = [g for _, g in added]
    ntree = STRtree(ng)
    tiles = {t.name: t for t in load_tiles()}
    PX, SIZE = 0.03, 400
    poi_render.PX_M = PX
    picks = [k for k, _ in area_tile.most_common(8)]
    sheet = Image.new("RGB", (2 * SIZE, len(picks) * SIZE), "white")
    for k, name in enumerate(picks):
        # the densest 12 m square of added area on the tile
        here = [g for f, g in added if f["properties"]["tile_run"] == name]
        cx, cy = max(((g.centroid.x, g.centroid.y) for g in here), key=lambda c: sum(h.area for h in here if h.centroid.distance(box(c[0] - 6, c[1] - 6, c[0] + 6, c[1] + 6)) == 0))
        half = SIZE * PX / 2
        x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
        base_img = poi_render.crop(x0, y0, x1, y1)
        to = lambda x, y: ((x - x0) / PX, (y1 - y) / PX)
        plain = base_img.copy()
        draw = ImageDraw.Draw(base_img)
        for i in tree.query(box(x0, y0, x1, y1)):
            draw.line([to(*p) for p in og[i].exterior.coords], fill=(255, 255, 0), width=1)
        for i in ntree.query(box(x0, y0, x1, y1)):
            draw.line([to(*p) for p in ng[i].exterior.coords], fill=(255, 0, 255), width=2)
        ImageDraw.Draw(plain).text((2, 2), f"{name[7:16]} image", fill="white")
        draw.text((2, 2), f"{name[7:16]} yellow {old_tag}, magenta added by {new_tag}", fill="white")
        sheet.paste(plain, (0, k * SIZE))
        sheet.paste(base_img, (SIZE, k * SIZE))
    sheet.save(WORK / f"added_{new_tag}_over_{old_tag}.jpg", quality=85)
