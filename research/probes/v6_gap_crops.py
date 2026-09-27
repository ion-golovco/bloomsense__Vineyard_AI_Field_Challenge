"""v6: crops of gap stretches for eyeballing: RGB with canopy outlines (cyan = ours, magenta = organizers' on the example
tiles) and the stretch (yellow). Usage: ... v6_gap_crops.py OUT.jpg TAG [organizer|vines_present|real_gap]"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import LineString, shape

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import canopy, poi  # noqa: E402
from marcaj.tiles import PIXEL_M  # noqa: E402

SIZE = 12.0  # m


def crop(line: LineString, ours: list, theirs: list) -> Image.Image:
    mid = line.interpolate(0.5, normalized=True)
    name = next(n for n, t in L.TILES.items() if t.bounds.contains(mid))
    tile = L.TILES[name]
    img, _ = canopy.read_rgb(tile)
    c, r = int((mid.x - tile.left) / PIXEL_M), int((tile.top - mid.y) / PIXEL_M)
    h = int(SIZE / 2 / PIXEL_M)
    r0, c0 = max(r - h, 0), max(c - h, 0)
    sub = np.transpose(img[:, r0:r + h, c0:c + h], (1, 2, 0))
    im = Image.fromarray(sub.astype(np.uint8)).convert("RGB")
    d = ImageDraw.Draw(im)
    px = lambda x, y: ((x - tile.left) / PIXEL_M - c0, (tile.top - y) / PIXEL_M - r0)
    for geoms, colour in ((ours, (0, 255, 255)), (theirs, (255, 0, 255))):
        for g in geoms:
            if g.distance(mid) < SIZE:
                d.line([px(*p) for p in g.exterior.coords], fill=colour, width=2)
    d.line([px(*p) for p in line.coords], fill=(255, 255, 0), width=1)
    d.text((4, 4), f"{name[7:16]} {line.length:.1f} m", fill=(255, 255, 255))
    return im.resize((240, 240))


if __name__ == "__main__":
    out, tag, which = sys.argv[1], sys.argv[2], sys.argv[3]
    canopies = json.loads((L.OUT / f"canopies_{tag}.json").read_text())
    ours = [shape(f["geometry"]) for f in canopies]
    theirs = [shape(f["geometry"]) for f in L.organizer() if f["properties"]["label"] == "vineyard"]
    if which == "organizer":
        lines = L.organizer_gaps()
    else:
        why = json.loads((L.OUT / f"gap_why_{tag}.json").read_text())
        rows = L.sampled(L.ref_pieces(), canopies)
        pois = [p["properties"] for p in poi.gap_pois(rows, obstacles=L.obstacles()) if p["properties"]["challenge"]]
        labels = L.gap_label_lines()
        if which == "short_unlabelled":
            lines = [LineString([p["gap_start"], p["gap_end"]]) for p in pois if p["gap_m"] < 5 and p["reason"] == "gap"
                     and L.label_of(LineString([p["gap_start"], p["gap_end"]]), labels) is None][:24]
        else:
            lines = [LineString([p["gap_start"], p["gap_end"]]) for p in pois if L.label_of(LineString([p["gap_start"], p["gap_end"]]), labels) == which][:30]
    tree = STRtree(ours)
    ims = [crop(line, [ours[i] for i in tree.query(line.buffer(SIZE))], theirs) for line in lines]
    cols = 6
    sheet = Image.new("RGB", (240 * cols, 240 * ((len(ims) + cols - 1) // cols)))
    for k, im in enumerate(ims):
        sheet.paste(im, (240 * (k % cols), 240 * (k // cols)))
    sheet.save(out, quality=85)
    print(out, len(ims))
