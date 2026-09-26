"""Contact sheet of predicted row patterns at 5 cm from the native tiles (read-only), with canopies (yellow), rows
(red), predicted blocks (magenta) and the lab's outlines (vineyard green, orchard violet, overgrown cyan).
Evaluation only (reads data/review/). Run from backend/:
uv run --frozen python ../research/probes/plots_verify_sheet.py out.jpg PATTERN [PATTERN ...] [src=predictions.geojson]"""

import json
import sys

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds
from shapely.geometry import box, shape
from shapely.ops import unary_union

from marcaj.review import load_verdicts
from marcaj.tiles import DATA_DIR, REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots_verify"
THUMB, COLS, STEP = 520, 3, 0.05
COLOURS = {"vineyard": (0, 255, 0), "orchard": (170, 90, 255), "overgrown": (0, 230, 255), "other": (200, 200, 200)}
LEFT, TOP, TILE_M = 628992.0, 5221222.4, 51.2


def crop(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """RGB at STEP m over the window, from every tile it touches (black where there is none)."""
    w, h = round((x1 - x0) / STEP), round((y1 - y0) / STEP)
    out = np.zeros((h, w, 3), np.uint8)
    for r in range(int((TOP - y1) // TILE_M), int((TOP - y0) // TILE_M) + 1):
        for c in range(int((x0 - LEFT) // TILE_M), int((x1 - LEFT) // TILE_M) + 1):
            path = DATA_DIR / "tiles" / f"siret3_r{r:03d}_c{c:03d}.tif"
            if not path.is_file():
                continue
            tl, tt = LEFT + c * TILE_M, TOP - r * TILE_M
            a0, b0, a1, b1 = max(x0, tl), max(y0, tt - TILE_M), min(x1, tl + TILE_M), min(y1, tt)
            if a1 <= a0 or b1 <= b0:
                continue
            with rasterio.open(path) as src:
                cols, rows = round((a1 - a0) / STEP), round((b1 - b0) / STEP)
                data = src.read(window=from_bounds(a0, b0, a1, b1, src.transform), out_shape=(3, rows, cols))
            i, j = round((y1 - b1) / STEP), round((a0 - x0) / STEP)
            part = np.moveaxis(data, 0, -1)[: h - i, : w - j]
            out[i:i + part.shape[0], j:j + part.shape[1]] = part
    return out


def main() -> None:
    args = [a for a in sys.argv[1:] if "=" not in a]
    options = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    out, wanted = WORK / args[0], args[1:]
    features = json.loads((REPO_ROOT / "data" / "generated" / options.get("src", "predictions.geojson")).read_text())["features"]
    key = lambda p: p.get("pattern_id") or p.get("vineyard_id", "")
    outlines = [(shape(v["geometry"]), v["label"]) for v in load_verdicts() if v["kind"] == "plot"]
    blocks = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"]
    page = Image.new("RGB", (COLS * THUMB, ((len(wanted) - 1) // COLS + 1) * (THUMB + 16)), "white")
    for k, pattern in enumerate(wanted):
        rows = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "row" and key(f["properties"]) == pattern]
        canopies = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "vineyard" and key(f["properties"]) == pattern]
        cx, cy = unary_union(rows).centroid.coords[0]
        minx, miny, maxx, maxy = unary_union(rows).bounds
        half = max(maxx - minx, maxy - miny) / 2 + 6
        x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
        image = Image.fromarray(crop(x0, y0, x1, y1)).resize((THUMB, THUMB))
        draw = ImageDraw.Draw(image)
        scale = THUMB / (2 * half)
        px = lambda coords: [((x - x0) * scale, (y1 - y) * scale) for x, y in coords]
        window = box(x0, y0, x1, y1)
        for g in canopies:
            for part in getattr(g, "geoms", [g]):
                draw.line(px(part.exterior.coords), fill=(255, 230, 0), width=1)
        for row in rows:
            draw.line(px(row.coords), fill=(255, 40, 40), width=1)
        for g, label in outlines:
            if g.intersects(window):
                draw.line(px(g.exterior.coords), fill=COLOURS[label], width=2)
        for g in blocks:
            if g.intersects(window):
                for part in getattr(g, "geoms", [g]):
                    draw.line(px(part.exterior.coords), fill=(255, 0, 255), width=2)
        column, line = k % COLS, k // COLS
        page.paste(image, (column * THUMB, line * (THUMB + 16) + 16))
        ImageDraw.Draw(page).text((column * THUMB + 3, line * (THUMB + 16) + 2), f"{pattern} ({cx:.0f}, {cy:.0f}) {2 * half:.0f} m", fill="black")
    WORK.mkdir(parents=True, exist_ok=True)
    page.save(out, quality=88)
    print(f"{len(wanted)} crops -> {out}")


if __name__ == "__main__":
    main()
