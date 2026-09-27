"""v6 rows contact sheet: mosaic crops with reference rows (green), predicted rows (magenta), predicted plots (cyan)
and predicted canopy (yellow). Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/v6_rows_sheet.py [pred=path.geojson] fields=V09-01,V12-04 [zoom=1] [out=name.jpg]
Without zoom each field is shown whole; with zoom=1, a 30 m crop around the reference row end farthest from the prediction."""

import json
import sys
from pathlib import Path

import numpy as np
import shapely
from PIL import Image, ImageDraw
from shapely.geometry import shape

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import v6_rows_eval as ev  # noqa: E402
from marcaj.mosaic import MOSAIC_PX_M, load_mosaic  # noqa: E402

CELL = 640


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    src = Path(args.get("pred", str(ev.REPO_ROOT / "data" / "generated" / "work" / "v5" / "predictions.geojson")))
    features = json.loads(src.read_text())["features"]
    rgb, t = load_mosaic()
    blocks = json.loads((ev.REF / "blocks.geojson").read_text())["features"]
    ref_rows = [(shape(f["geometry"]), f["properties"]) for f in json.loads((ev.REF / "rows.geojson").read_text())["features"]]
    pred_rows = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "row"]
    pred_plots = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"]
    canopy = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "vineyard"]
    fields = args.get("fields", "").split(",") if args.get("fields") else []
    zoom = args.get("zoom") == "1"
    # at=x,y;x,y... : 30 m crops around given points
    points = [tuple(map(float, p.split(","))) for p in args["at"].split(";")] if args.get("at") else []
    fields += [f"@{x:.0f},{y:.0f}" for x, y in points]
    cells = []
    pred_u = shapely.union_all(pred_rows) if pred_rows else shapely.GeometryCollection()
    for name in fields:
        block = next((shape(f["geometry"]) for f in blocks if f["properties"]["vineyard_id"] == name), None)
        if name.startswith("@"):
            x, y = map(float, name[1:].split(","))
            half = float(args.get("half", 15))
            x0, y0, x1, y1 = x - half, y - half, x + half, y + half
        elif block is None:
            continue
        elif zoom:
            # the reference row end farthest from any predicted row
            ends = [shapely.Point(c) for g, p in ref_rows if p["vineyard_id"] == name for c in np.asarray(g.coords)[[0, -1]]]
            far = max(ends, key=lambda e: e.distance(pred_u))
            x0, y0, x1, y1 = far.x - 15, far.y - 15, far.x + 15, far.y + 15
        else:
            # the reference field and the predicted block of that name
            same = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "block" and f["properties"].get("vineyard_id") == name]
            x0, y0, x1, y1 = shapely.union_all([block, *same]).buffer(5).bounds
        c0, r0 = int((x0 - t.c) / MOSAIC_PX_M), int((t.f - y1) / MOSAIC_PX_M)
        c1, r1 = int((x1 - t.c) / MOSAIC_PX_M), int((t.f - y0) / MOSAIC_PX_M)
        crop = np.moveaxis(rgb[:, max(r0, 0):r1, max(c0, 0):c1], 0, -1)
        image = Image.fromarray(np.ascontiguousarray(crop)).convert("RGB")
        scale = CELL / max(image.size)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
        draw = ImageDraw.Draw(image)
        px = lambda xy: (((xy[:, 0] - t.c) / MOSAIC_PX_M - max(c0, 0)) * scale, ((t.f - xy[:, 1]) / MOSAIC_PX_M - max(r0, 0)) * scale)
        view = shapely.box(x0, y0, x1, y1)

        def line(g, colour, width):
            for part in getattr(g, "geoms", [g]):
                if part.is_empty:
                    continue
                coords = np.asarray(part.exterior.coords if part.geom_type == "Polygon" else part.coords)
                xs, ys = px(coords)
                draw.line(list(zip(xs, ys)), fill=colour, width=width)
        for g in canopy:
            if g.intersects(view):
                line(g, (255, 230, 0), 1)
        for g in pred_plots:
            if g.intersects(view):
                line(g, (0, 220, 255), 2)
        for g, _ in ref_rows:
            if g.intersects(view):
                line(g, (0, 255, 0), 3 if zoom else 2)
        for g in pred_rows:
            if g.intersects(view):
                line(g, (255, 0, 255), 1)
        draw.rectangle([0, 0, 200, 14], fill="black")
        draw.text((2, 1), f"{name} {x0:.0f},{y0:.0f} {x1 - x0:.0f} m", fill="white")
        cells.append(image)
    cols = min(3, len(cells))
    sheet = Image.new("RGB", (cols * CELL, ((len(cells) - 1) // cols + 1) * CELL), "white")
    for k, cell in enumerate(cells):
        sheet.paste(cell, ((k % cols) * CELL, (k // cols) * CELL))
    out = ev.WORK / args.get("out", "sheet.jpg")
    sheet.save(out, quality=85)
    print(out)


if __name__ == "__main__":
    main()
