"""Visual sweep of the inter-rows on the northern tiles (row index <= 23): a low-threshold review set from the
detector's inter-row candidates (data/generated/work/waste/candidates.json), clustered within 0.25 m, sorted by
tile, rendered 64 per sheet as 2.4 m native crops (box drawn, number and tile in the caption).
Review set: white blobs >= 0.01 m2 with luminance >= 205, chroma <= 30, length <= 4x width, >= 0.45 m from a row
axis, plus grey-white ones >= 0.1 m2 down to luminance 190; every coloured blob; black blobs >= 0.08 m2, length
<= 2.5x width, >= 0.7 m from a row axis.
Run from backend/: uv run --frozen python ../research/probes/waste_sweep.py  (writes sweep_north.json, sweep_north_*.jpg)"""

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import Window
from shapely.geometry import box
from shapely.ops import unary_union

from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
CELL, COLS, ROWS, SIZE_M = 170, 8, 8, 2.4
MAX_ROW = int(sys.argv[1]) if len(sys.argv) > 1 else 23


def elongation(c: dict) -> float:
    return c["length_m"] / max(c["width_m"], 1e-3)


def wanted(c: dict) -> bool:
    if c["kind"] == "white":
        return (c["area_m2"] >= 0.01 and c["chroma"] <= 30 and elongation(c) <= 4 and c["row_m"] >= 0.45
                and (c["lum"] >= 205 or (c["lum"] >= 190 and c["area_m2"] >= 0.1)))
    if c["kind"] == "colour":
        return True
    return c["area_m2"] >= 0.08 and elongation(c) <= 2.5 and c["row_m"] >= 0.7


def render(items: list[dict], prefix: str) -> None:
    """Sheets of 64 numbered 2.4 m native crops, box drawn."""
    per = COLS * ROWS
    for page in range(0, len(items), per):
        chunk = items[page:page + per]
        sheet = Image.new("RGB", (COLS * CELL, ((len(chunk) - 1) // COLS + 1) * (CELL + 13)), (40, 40, 40))
        draw = ImageDraw.Draw(sheet)
        for i, item in enumerate(chunk):
            x0, y0, x1, y1 = item["px"]
            cx, cy, half = (x0 + x1) / 2, (y0 + y1) / 2, SIZE_M / 2 / PIXEL_M
            with rasterio.open(DATA_DIR / "tiles" / item["tile"]) as source:
                rgb = source.read(window=Window(cx - half, cy - half, 2 * half, 2 * half), boundless=True, fill_value=0, out_shape=(3, CELL, CELL))
            crop = Image.fromarray(np.moveaxis(rgb, 0, -1))
            s = CELL / (2 * half)
            ImageDraw.Draw(crop).rectangle([CELL / 2 + (x0 - cx) * s - 4, CELL / 2 + (y0 - cy) * s - 4, CELL / 2 + (x1 - cx) * s + 4, CELL / 2 + (y1 - cy) * s + 4], outline=(255, 0, 255))
            px, py = (i % COLS) * CELL, (i // COLS) * (CELL + 13)
            sheet.paste(crop, (px, py + 13))
            draw.text((px + 2, py), f"#{item['n']} {item['tile'][7:-4]} {''.join(k[0] for k in item['kinds'])}", fill="white")
        sheet.save(W / f"{prefix}_{page // per + 1:02d}.jpg", quality=88)


if __name__ == "__main__":
    candidates = [c for c in json.loads((W / "candidates.json").read_text()) if int(c["tile"][8:11]) <= MAX_ROW and wanted(c)]
    items = []
    for tile in sorted({c["tile"] for c in candidates}):
        mine = [c for c in candidates if c["tile"] == tile]
        merged = unary_union([box(*c["px"]).buffer(10) for c in mine])
        for part in getattr(merged, "geoms", [merged]):
            members = [c for c in mine if part.intersects(box(*c["px"]))]
            x0, y0 = min(c["px"][0] for c in members), min(c["px"][1] for c in members)
            x1, y1 = max(c["px"][2] for c in members), max(c["px"][3] for c in members)
            items.append({"tile": tile, "px": [x0, y0, x1, y1], "kinds": sorted({c["kind"] for c in members}),
                          "area_m2": round(sum(c["area_m2"] for c in members), 3), "vineyard_id": members[0]["vineyard_id"],
                          "lum": round(max(c["lum"] for c in members)), "row_m": round(min(c["row_m"] for c in members), 2)})
    items.sort(key=lambda i: (i["tile"], i["px"][1], i["px"][0]))
    for number, item in enumerate(items, start=1):
        item["n"] = number
    (W / "sweep_north.json").write_text(json.dumps(items, indent=0))
    render(items, "sweep_north")
    print(len(items), "items")
