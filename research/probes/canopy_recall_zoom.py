"""Zoomed native-resolution views of gap stretches: RGB with predicted canopies (yellow), reference canopies (cyan, only on
the organizer tiles), the gap (red), and beside it the colour index 2g-r-b (white > 25, grey 15-25).
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_recall_zoom.py OUT.jpg TAG ROW_ID:GAP_M ..."""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).parent))
import poi_render  # noqa: E402
from canopy_recall_lib import WORK  # noqa: E402

from marcaj.cvat import build_scene  # noqa: E402
from marcaj.tiles import DATA_DIR, load_tiles  # noqa: E402

PX = 0.02
HALF = 6.0


def panel(mid, start, end, canopies, refs, label):
    poi_render.PX_M = PX
    x0, y0, x1, y1 = mid[0] - HALF, mid[1] - HALF, mid[0] + HALF, mid[1] + HALF
    im = poi_render.crop(x0, y0, x1, y1)
    a = np.asarray(im).astype(np.float32)
    ex = 2 * a[..., 1] - a[..., 0] - a[..., 2]
    idx = np.where(ex > 25, 255, np.where(ex > 15, 120, 0)).astype(np.uint8)
    right = Image.fromarray(np.stack([idx] * 3, -1))
    to = lambda x, y: ((x - x0) / PX, (y1 - y) / PX)
    for image in (im, right):
        draw = ImageDraw.Draw(image)
        for geoms, tree, colour in ((canopies[0], canopies[1], (255, 255, 0)), (refs[0], refs[1], (0, 255, 255))):
            for i in tree.query(box(x0, y0, x1, y1)):
                draw.line([to(*p) for p in geoms[i].exterior.coords], fill=colour, width=1)
        draw.line([to(*start), to(*end)], fill=(255, 0, 0), width=1)
    both = Image.new("RGB", (im.width * 2, im.height + 14), "black")
    both.paste(im, (0, 14))
    both.paste(right, (im.width, 14))
    ImageDraw.Draw(both).text((2, 1), label, fill="white")
    return both


if __name__ == "__main__":
    out, tag, keys = sys.argv[1], sys.argv[2], sys.argv[3:]
    diag = json.loads((WORK / "gaps_diag_uploaded.json").read_text())
    path = WORK / f"canopies_{tag}.json"
    canopies = [shape(f["geometry"]) for f in json.loads(path.read_text())]
    base = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=load_tiles())["features"]
    refs = [shape(f["geometry"]) for f in base if f["properties"]["source"] == "reference" and f["properties"]["label"] == "vineyard"]
    panels = []
    for key in keys:
        row_id, gap = key.split(":")
        d = next(d for d in diag if d["row_id"] == row_id and abs(d["gap_m"] - float(gap)) < 0.05)
        panels.append(panel(d["mid"], d["start"], d["end"], (canopies, STRtree(canopies)), (refs, STRtree(refs)),
                            f"{tag} {row_id} {d['gap_m']} m {d['tile'][7:16]} {d['status']} c25 {d['colour25']} dn15 {d['dn15']} net {d['net02']}"))
    sheet = Image.new("RGB", (panels[0].width, sum(p.height for p in panels)))
    y = 0
    for p in panels:
        sheet.paste(p, (0, y))
        y += p.height
    sheet.save(WORK / out, quality=85)
