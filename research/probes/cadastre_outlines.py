"""How well cadastral parcels explain the hand-drawn plot outlines: per vineyard outline the best single parcel IoU, the
IoU of the union of parcels mostly inside it, and the share of its boundary within 1 / 2 m of a parcel edge. Also the
kept detector's plots (data/generated/work/plots/final.geojson). Evaluation only (reads data/review/).
Run from backend/: uv run --frozen python ../research/probes/cadastre_outlines.py [sheet]"""

import json
import sys

import numpy as np
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from marcaj.cadastre import load_parcels
from marcaj.judge import PLOT_SPLIT_NORTHING
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "fields"
WORK.mkdir(parents=True, exist_ok=True)
parcels = load_parcels()
tree = STRtree(parcels)
edges = unary_union([p.boundary for p in parcels])


def iou(a, b) -> float:
    return a.intersection(b).area / a.union(b).area if a.union(b).area else 0.0


def boundary_near(polygon, step: float = 0.5) -> np.ndarray:
    ring = polygon.exterior
    return np.array([edges.distance(ring.interpolate(d)) for d in np.arange(0, ring.length, step)])


outlines = [(i, shape(v["geometry"]), v) for i, v in enumerate(v for v in load_verdicts() if v["kind"] == "plot")]
rows = []
for i, outline, v in outlines:
    near = [parcels[k] for k in tree.query(outline)]
    best = max((iou(outline, p) for p in near), default=0.0)
    inside = [p for p in near if p.intersection(outline).area > 0.5 * p.area]
    union = unary_union(inside) if inside else None
    d = boundary_near(outline)
    rows.append({"n": i, "label": v["label"], "half": "N" if outline.centroid.y >= PLOT_SPLIT_NORTHING else "S",
                 "m2": round(outline.area), "best": round(best, 2), "k": len(inside), "union": round(iou(outline, union), 2) if union else 0.0,
                 "in1": round(float((d <= 1).mean()), 2), "in2": round(float((d <= 2).mean()), 2), "med_m": round(float(np.median(d)), 2)})
print(f"{'#':>3} {'cls':9} h {'m2':>6} {'best1':>5} {'k':>3} {'union':>5} {'<=1m':>5} {'<=2m':>5} {'med':>5}")
for r in rows:
    print(f"{r['n']:>3} {r['label']:9} {r['half']} {r['m2']:>6} {r['best']:>5} {r['k']:>3} {r['union']:>5} {r['in1']:>5} {r['in2']:>5} {r['med_m']:>5}")
for label in ("vineyard", "orchard", "overgrown"):
    sel = [r for r in rows if r["label"] == label]
    print(f"{label}: n {len(sel)}, median best-parcel IoU {np.median([r['best'] for r in sel]):.2f}, median union IoU "
          f"{np.median([r['union'] for r in sel]):.2f}, union IoU>=0.75 {sum(r['union'] >= 0.75 for r in sel)}, "
          f"boundary within 1 m {np.median([r['in1'] for r in sel]):.2f}, 2 m {np.median([r['in2'] for r in sel]):.2f}")
kept = [shape(f["geometry"]) for f in json.loads((REPO_ROOT / "data/generated/work/plots/final.geojson").read_text())["features"]
        if f["properties"]["label"] == "block"]
d = np.concatenate([boundary_near(p) for p in kept])
print(f"kept plots: {len(kept)}, boundary within 1 m of a parcel edge {(d <= 1).mean():.2f}, 2 m {(d <= 2).mean():.2f}, median {np.median(d):.2f} m")
(WORK / "cadastre_outlines.json").write_text(json.dumps(rows, indent=1))

if "sheet" in sys.argv:
    from PIL import Image, ImageDraw
    from marcaj.mosaic import MOSAIC_PX_M, load_mosaic
    rgb, t = load_mosaic()
    tiles = []
    for i, outline, v in outlines:
        if v["label"] != "vineyard":
            continue
        x0, y0, x1, y1 = outline.buffer(15).bounds
        c0, r0 = int((x0 - t.c) / MOSAIC_PX_M), int((t.f - y1) / MOSAIC_PX_M)
        c1, r1 = int((x1 - t.c) / MOSAIC_PX_M), int((t.f - y0) / MOSAIC_PX_M)
        crop = np.moveaxis(rgb[:3, max(r0, 0):r1, max(c0, 0):c1], 0, -1) if rgb.shape[0] <= 4 else rgb[max(r0, 0):r1, max(c0, 0):c1, :3]
        img = Image.fromarray(np.ascontiguousarray(crop)).convert("RGB")
        draw = ImageDraw.Draw(img)
        px = lambda g: [((x - t.c) / MOSAIC_PX_M - max(c0, 0), (t.f - y) / MOSAIC_PX_M - max(r0, 0)) for x, y in g.coords]
        for p in tree.query(outline.buffer(15)):
            for g in getattr(parcels[p], "geoms", [parcels[p]]):
                draw.line(px(g.exterior), fill=(255, 220, 0), width=1)
        for p in kept:
            if p.intersects(outline):
                draw.line(px(p.exterior), fill=(0, 160, 255), width=2)
        draw.line(px(outline.exterior), fill=(255, 0, 80), width=2)
        draw.text((4, 4), f"#{i}", fill=(255, 255, 255))
        img.thumbnail((420, 420))
        tiles.append(img)
    cols = 6
    sheet = Image.new("RGB", (cols * 420, -(-len(tiles) // cols) * 420), "black")
    for k, img in enumerate(tiles):
        sheet.paste(img, ((k % cols) * 420, (k // cols) * 420))
    sheet.save(WORK / "cadastre_sheet.jpg", quality=85)
    print(WORK / "cadastre_sheet.jpg")
