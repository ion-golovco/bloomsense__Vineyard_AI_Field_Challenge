"""Contact sheet of waste candidates: per candidate a 4 m native crop and a 24 m context view, box drawn in both.
Run from backend/: uv run --frozen python ../research/probes/waste_sheet.py CANDIDATES.json OUT_PREFIX [N]"""

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import Window

from marcaj.tiles import DATA_DIR, PIXEL_M

CELL, COLS, ROWS = 240, 4, 7
CLOSE_M, CONTEXT_M = 4.0, 24.0


def _crop(path: Path, cx: float, cy: float, size_m: float, out_px: int) -> Image.Image:
    half = size_m / 2 / PIXEL_M
    with rasterio.open(path) as source:
        window = Window(cx - half, cy - half, 2 * half, 2 * half)
        rgb = source.read(window=window, boundless=True, fill_value=0, out_shape=(3, out_px, out_px))
    return Image.fromarray(np.moveaxis(rgb, 0, -1))


def cell(candidate: dict, number: int) -> Image.Image:
    c0, r0, c1, r1 = candidate["px"]
    cx, cy = (c0 + c1) / 2, (r0 + r1) / 2
    path = DATA_DIR / "tiles" / candidate["tile"]
    image = Image.new("RGB", (2 * CELL, CELL + 30), "white")
    for i, size_m in enumerate((max(CLOSE_M, 2.5 * max(c1 - c0, r1 - r0) * PIXEL_M), CONTEXT_M)):
        crop = _crop(path, cx, cy, size_m, CELL)
        scale = CELL / (size_m / PIXEL_M)
        draw = ImageDraw.Draw(crop)
        x0, y0 = CELL / 2 + (c0 - cx) * scale, CELL / 2 + (r0 - cy) * scale
        x1, y1 = CELL / 2 + (c1 - cx) * scale, CELL / 2 + (r1 - cy) * scale
        pad = 3 if i == 0 else 4
        draw.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], outline=(255, 0, 255), width=2)
        image.paste(crop, (i * CELL, 0))
    draw = ImageDraw.Draw(image)
    e, n = (candidate["bounds"][0] + candidate["bounds"][2]) / 2, (candidate["bounds"][1] + candidate["bounds"][3]) / 2
    draw.text((3, CELL + 2), f"#{number} {candidate['tile'][7:-4]} {candidate['kind']} {candidate['area_m2']:.2f}m2 "
              f"s={candidate.get('score', 0):.2f} {candidate.get('where', '')}", fill="black")
    draw.text((3, CELL + 15), f"E {e:.1f} N {n:.1f}  row {candidate.get('row_m', -1):.1f}m {candidate.get('vineyard_id', '')}", fill="black")
    return image


def sheets(candidates: list[dict], prefix: Path, first: int = 1) -> list[Path]:
    per_page = COLS * ROWS
    paths = []
    for page in range(0, len(candidates), per_page):
        chunk = candidates[page:page + per_page]
        sheet = Image.new("RGB", (COLS * 2 * CELL + (COLS - 1) * 8, ((len(chunk) - 1) // COLS + 1) * (CELL + 38)), (60, 60, 60))
        for i, candidate in enumerate(chunk):
            sheet.paste(cell(candidate, first + page + i), ((i % COLS) * (2 * CELL + 8), (i // COLS) * (CELL + 38)))
        path = prefix.parent / f"{prefix.name}_{page // per_page + 1:02d}.jpg"
        sheet.save(path, quality=85)
        paths.append(path)
    return paths


if __name__ == "__main__":
    items = json.loads(Path(sys.argv[1]).read_text())
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else len(items)
    for path in sheets(items[:limit], Path(sys.argv[2])):
        print(path)
