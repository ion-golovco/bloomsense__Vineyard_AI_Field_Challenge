"""Contact sheet of the rows `canopy.refit_rows` moved most: lattice row red, refit row cyan, canopy outlines yellow,
over the native tiles, 10 m around the end that moved most (3 m in from it). Evaluation only.
Run from backend/: PYTHONPATH=../research/probes uv run --frozen python ../research/probes/rows_refit_sheet.py (RR_PREDICTIONS as rows_refit_measure.py)"""

import json

import numpy as np
from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import shape

import poi_render
from marcaj import canopy
from rows_refit_measure import PREDICTIONS, WORK, plots_input

poi_render.PX_M = 0.025
HALF_M, COUNT, COLS = 5.0, 8, 4


def main():
    predicted = json.loads(PREDICTIONS.read_text())["features"]
    canopies = [f for f in predicted if f["properties"]["label"] == "vineyard"]
    frozen = plots_input()
    moved, records = canopy.refit_rows(frozen, canopies)
    before = {f["properties"]["row_id"]: shape(f["geometry"]) for f in frozen if f["properties"]["label"] == "row"}
    after = {f["properties"]["row_id"]: shape(f["geometry"]) for f in moved if f["properties"]["label"] == "row"}
    top = sorted((r for r in records if r["moved"]), key=lambda r: -max(abs(r["shift_start_m"]), abs(r["shift_end_m"])))[:COUNT]
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    size = round(2 * HALF_M / poi_render.PX_M)
    sheet = Image.new("RGB", (COLS * size, ((len(top) - 1) // COLS + 1) * (size + 16)), "white")
    for index, record in enumerate(top):
        line = before[record["row_id"]]
        at = 3.0 if abs(record["shift_start_m"]) >= abs(record["shift_end_m"]) else line.length - 3.0
        cx, cy = line.interpolate(at).coords[0]
        x0, y0, x1, y1 = cx - HALF_M, cy - HALF_M, cx + HALF_M, cy + HALF_M
        image = poi_render.crop(x0, y0, x1, y1).convert("RGB")
        draw = ImageDraw.Draw(image)
        to_px = lambda xy: [((x - x0) / poi_render.PX_M, (y1 - y) / poi_render.PX_M) for x, y in xy]
        for j in tree.query(shape({"type": "Polygon", "coordinates": [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]})):
            draw.line(to_px(geoms[j].exterior.coords), fill=(255, 230, 0), width=1)
        for other in (r for r in before if before[r].distance(line) < 8):
            draw.line(to_px(before[other].coords), fill=(255, 40, 40), width=3)
            draw.line(to_px(after[other].coords), fill=(0, 255, 255), width=2)
        x, y = (index % COLS) * size, (index // COLS) * (size + 16)
        sheet.paste(image, (x, y + 16))
        ImageDraw.Draw(sheet).text((x + 3, y + 2), f"{record['row_id']} shift {record['shift_start_m']:+.2f} / {record['shift_end_m']:+.2f} m, "
                                   f"cover {record['cover_m']:.0f}/{record['length_m']:.0f} m", fill="black")
    path = WORK / "moved_most.jpg"
    sheet.save(path, quality=88)
    print(path)


if __name__ == "__main__":
    main()
