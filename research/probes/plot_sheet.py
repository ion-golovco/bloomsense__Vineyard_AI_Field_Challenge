"""Contact sheets of crops over the 0.2 m mosaic, with the hand-drawn outlines (vineyard green, orchard violet,
overgrown cyan), predicted plots (magenta) and passages/forbidden (brown). Evaluation only: reads data/review/.
Run from backend/: uv run --frozen python ../research/probes/plot_sheet.py predictions.geojson out.jpg [x,y,half_m,label ...]
With no x,y items it draws one crop per vineyard outline, worst IoU first."""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import box, shape

from marcaj.judge import _iou
from marcaj.mosaic import MOSAIC_PX_M, load_mosaic
from marcaj.plots import exclusions
from marcaj.review import load_verdicts

THUMB, COLS = 420, 4
COLOURS = {"vineyard": (0, 255, 0), "orchard": (170, 90, 255), "overgrown": (0, 230, 255), "other": (200, 200, 200)}


def _rings(geometry):
    for part in getattr(geometry, "geoms", [geometry]):
        if part.geom_type == "Polygon":
            yield part.exterior.coords
            yield from (ring.coords for ring in part.interiors)


def sheet(features: list[dict], items: list[tuple[float, float, float, str]], out: Path) -> None:
    rgb, transform = load_mosaic()
    roads = exclusions()
    outlines = [(shape(v["geometry"]), v["label"]) for v in load_verdicts() if v["kind"] == "plot"]
    blocks = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"]
    rows = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "row"]
    page = Image.new("RGB", (COLS * THUMB, ((len(items) - 1) // COLS + 1) * (THUMB + 16)), "white")
    for k, (x, y, half, label) in enumerate(items):
        c0, r0 = int((x - half - transform.c) / MOSAIC_PX_M), int((transform.f - (y + half)) / MOSAIC_PX_M)
        n = int(2 * half / MOSAIC_PX_M)
        crop = np.zeros((n, n, 3), np.uint8)
        rs, cs = slice(max(r0, 0), min(r0 + n, rgb.shape[1])), slice(max(c0, 0), min(c0 + n, rgb.shape[2]))
        crop[rs.start - r0:rs.stop - r0, cs.start - c0:cs.stop - c0] = np.moveaxis(rgb[:, rs, cs], 0, -1)
        image = Image.fromarray(crop).resize((THUMB, THUMB))
        draw = ImageDraw.Draw(image)
        scale = THUMB / (2 * half)
        to_px = lambda coords: [((cx - (x - half)) * scale, ((y + half) - cy) * scale) for cx, cy in coords]
        window = box(x - half, y - half, x + half, y + half)
        for ring in _rings(roads.intersection(window)):
            draw.line(to_px(ring), fill=(150, 100, 40), width=2)
        for row in rows:
            if row.intersects(window):
                draw.line(to_px(row.coords), fill=(255, 60, 40), width=1)
        for geometry, cls in outlines:
            if geometry.intersects(window):
                draw.line(to_px(geometry.exterior.coords), fill=COLOURS[cls], width=3)
        for geometry in blocks:
            if geometry.intersects(window):
                for ring in _rings(geometry):
                    draw.line(to_px(ring), fill=(255, 0, 255), width=2)
        tile = f"r{int((5221222.4 - y) // 51.2):03d}_c{int((x - 628992.0) // 51.2):03d}"
        column, line = k % COLS, k // COLS
        page.paste(image, (column * THUMB, line * (THUMB + 16) + 16))
        ImageDraw.Draw(page).text((column * THUMB + 3, line * (THUMB + 16) + 2), f"{label} ({x:.0f}, {y:.0f}) {tile}", fill="black")
    page.save(out, quality=85)
    print(f"{len(items)} crops -> {out}")


def main() -> None:
    features = json.loads(Path(sys.argv[1]).read_text())["features"]
    out = Path(sys.argv[2])
    items = [tuple(float(v) for v in a.split(",")[:3]) + (a.split(",", 3)[3],) for a in sys.argv[3:]]
    if not items:
        blocks = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"]
        vine = [(i, shape(v["geometry"])) for i, v in enumerate(v for v in load_verdicts() if v["kind"] == "plot") if v["label"] == "vineyard"]
        scored = sorted(((max((_iou(g, b) for b in blocks), default=0.0), i, g) for i, g in vine), key=lambda t: t[0])
        for iou, i, g in scored:
            minx, miny, maxx, maxy = g.bounds
            items.append(((minx + maxx) / 2, (miny + maxy) / 2, max(maxx - minx, maxy - miny) / 2 + 12, f"#{i} IoU {iou:.2f}"))
    sheet(features, items, out)


if __name__ == "__main__":
    main()
