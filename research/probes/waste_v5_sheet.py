"""Waste v5 probe: contact sheets of the in-scope misses and of the false or unlabelled boxes of a verifier (evaluation
only). Per item a close crop (label or box in green, predicted boxes in red, candidates in yellow) and a 16 m context.
Run from backend/: uv run --frozen python ../research/probes/waste_v5_sheet.py PREFIX [margin]"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import waste_v5_eval as v5  # noqa: E402
from waste_sheet import _crop  # noqa: E402
from marcaj import waste  # noqa: E402
from marcaj.tiles import DATA_DIR, PIXEL_M

CELL, COLS = 256, 4
LEFT, TOP, TILE_M = 628992.0, 5221222.4, 51.2


def cell(geometry, text: str, boxes: list, cands: list) -> Image.Image:
    x, y = geometry.centroid.x, geometry.centroid.y
    col, row = int((x - LEFT) // TILE_M), int((TOP - y) // TILE_M)
    left, top = LEFT + col * TILE_M, TOP - row * TILE_M
    path = DATA_DIR / "tiles" / f"siret3_r{row:03d}_c{col:03d}.tif"
    cx, cy = (x - left) / PIXEL_M, (top - y) / PIXEL_M
    image = Image.new("RGB", (2 * CELL, CELL + 30), "white")
    g0, g1 = geometry.bounds[:2], geometry.bounds[2:]
    for i, size_m in enumerate((max(3.0, 2.5 * max(g1[0] - g0[0], g1[1] - g0[1])), 16.0)):
        crop = _crop(path, cx, cy, size_m, CELL)
        scale = CELL / size_m
        draw = ImageDraw.Draw(crop)
        to = lambda b: [CELL / 2 + (b[0] - x) * scale, CELL / 2 - (b[3] - y) * scale, CELL / 2 + (b[2] - x) * scale, CELL / 2 - (b[1] - y) * scale]
        if i == 0:
            for c in cands:
                draw.rectangle(to(c["box"]), outline=(255, 230, 0), width=1)
        for b in boxes:
            draw.rectangle(to(b.bounds), outline=(255, 0, 0), width=2)
        draw.rectangle(to(geometry.bounds), outline=(0, 255, 0), width=1 if boxes else 2)
        image.paste(crop, (i * CELL, 0))
    draw = ImageDraw.Draw(image)
    draw.text((3, CELL + 2), f"r{row:03d}_c{col:03d} E{x:.1f} N{y:.1f} " + text[:60], fill="black")
    draw.text((3, CELL + 15), text[60:130], fill="black")
    return image


def save(cells: list, prefix: Path) -> None:
    for page in range(0, len(cells), 24):
        chunk = cells[page:page + 24]
        sheet = Image.new("RGB", (COLS * (2 * CELL + 6), ((len(chunk) - 1) // COLS + 1) * (CELL + 36)), (60, 60, 60))
        for i, im in enumerate(chunk):
            sheet.paste(im, ((i % COLS) * (2 * CELL + 6), (i // COLS) * (CELL + 36)))
        out = prefix.parent / f"{prefix.name}_{page // 24 + 1:02d}.jpg"
        sheet.save(out, quality=85)
        print(out)


def stats(c: dict) -> str:
    return (f"{c['location'][:3]} {c['kind'][:3]} a{c['area_m2']:.3f} l{c['lum']:.0f} c{c['chroma']:.0f} d{c['dev']:.0f} w{c['width_m']:.2f} "
            f"L{c['length_m']:.2f} row{c['row_m']:.2f}" + (f" clip{c['clipped']:.2f} std{c['lum_std']:.0f} gr{c['ring_green']:.2f} den{c['density']:.2f}" if "fill" in c else ""))


if __name__ == "__main__":
    prefix = v5.OUT / sys.argv[1]
    margin = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    found = v5.in_scope(v5.found_all, margin)
    boxes = [shape(f["geometry"]) for f in waste.boxes(found, v5.predictions, waste.WasteParams(scope="site"))]
    btree, ctree = STRtree(boxes), STRtree([box(*c["box"]) for c in found])
    misses = []
    for i, (g, pos, source, where) in enumerate(v5.items):
        if pos and not len(btree.query(g.buffer(0.2))):
            near = sorted((found[j] for j in ctree.query(g.buffer(0.1))), key=lambda c: -c["area_m2"])
            misses.append(cell(g, f"#{i} {where} {source} | " + (stats(near[0]) if near else "no candidate"), [], near[:12]))
    save(misses, Path(str(prefix) + "_misses"))
    ltree = STRtree([it[0] for it in v5.items])
    wrong = []
    for b in boxes:
        hits = [v5.items[j] for j in ltree.query(b.buffer(0.2))]
        if not any(pos for _, pos, _, _ in hits):
            near = sorted((found[j] for j in ctree.query(b)), key=lambda c: -c["area_m2"])
            wrong.append(cell(b, ("NOT " if hits else "UNLAB ") + (stats(near[0]) if near else ""), [b], near[:12]))
    save(wrong, Path(str(prefix) + "_wrong"))
