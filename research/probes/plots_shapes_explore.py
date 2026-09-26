"""Plots shapes: per predicted block, shape metrics and cadastre agreement; per overgrown outline, its parcels.
Evaluation only (reads data/review/). Run from backend/: uv run --frozen python ../research/probes/plots_shapes_explore.py [src=...]"""

import json
import sys
from pathlib import Path

from shapely.geometry import shape
from shapely.strtree import STRtree

from marcaj.cadastre import load_parcels
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

GEN = REPO_ROOT / "data" / "generated"
args = dict(a.split("=", 1) for a in sys.argv[1:])
src = Path(args.get("src", GEN / "work" / "plots_v5" / "v5_postverify.geojson"))
blocks = [(shape(f["geometry"]), f["properties"]) for f in json.loads(src.read_text())["features"] if f["properties"]["label"] == "block"]
parcels = load_parcels()
tree = STRtree(parcels)
outl = [(i, v["label"], shape(v["geometry"])) for i, v in enumerate(v for v in load_verdicts() if v["kind"] == "plot")]


def iou(a, b):
    return a.intersection(b).area / a.union(b).area if a.intersects(b) else 0.0


for g, p in blocks:
    hull = g.convex_hull
    mrr = g.minimum_rotated_rectangle
    xs = [c for c in mrr.exterior.coords]
    import math
    sides = sorted(math.dist(xs[k], xs[k + 1]) for k in range(2))
    ps = []
    for k in tree.query(g):
        inter = parcels[k].intersection(g).area
        if inter > 1:
            ps.append((k, round(inter / parcels[k].area, 2), round(inter / g.area, 2), round(parcels[k].area)))
    ps.sort(key=lambda t: -t[2])
    inside = sum(t[2] for t in ps)
    best = max(((iou(o, g), i, l) for i, l, o in outl), default=(0, -1, ""))
    print(f"{p['vineyard_id']:8s} {round(g.area):6d} m2 sol {g.area / hull.area:.2f} rect {g.area / mrr.area:.2f} w {sides[0]:.0f} og {p.get('overgrown', False)!s:5s} in-parcels {inside:.2f} "
          f"best #{best[1]} {best[2]} {best[0]:.2f} parcels(k, of-parcel, of-block, m2) {ps[:4]}")
print()
for i, l, o in outl:
    if l != "overgrown":
        continue
    ps = [(k, round(parcels[k].intersection(o).area / parcels[k].area, 2), round(parcels[k].intersection(o).area / o.area, 2), round(parcels[k].area)) for k in tree.query(o) if parcels[k].intersection(o).area > 1]
    ps.sort(key=lambda t: -t[2])
    cov = [(p["vineyard_id"], round(g.intersection(o).area / o.area, 2), round(iou(g, o), 2)) for g, p in blocks if g.intersects(o)]
    print(f"#{i} {l} {round(o.area)} m2 {o.centroid.x:.0f},{o.centroid.y:.0f} parcels {ps[:5]} blocks {cov}")
