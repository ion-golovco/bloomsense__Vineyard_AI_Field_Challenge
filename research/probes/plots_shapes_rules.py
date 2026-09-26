"""Plots shapes: what cadastre rules would do to each predicted block, scored against the lab's plot outlines
(vineyard + overgrown counted as vineyard). Per rule: IoU with the best outline before/after, summed over blocks.
Evaluation only (reads data/review/). Run from backend/: uv run --frozen python ../research/probes/plots_shapes_rules.py [src=...]"""

import json
import sys
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from marcaj import plots
from marcaj.cadastre import load_parcels
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

W = REPO_ROOT / "data" / "generated" / "work" / "plots_shapes"
args = dict(a.split("=", 1) for a in sys.argv[1:])
src = Path(args.get("src", W / "base.geojson"))
blocks = [(shape(f["geometry"]).buffer(0), f["properties"]) for f in json.loads(src.read_text())["features"] if f["properties"]["label"] == "block"]
parcels = load_parcels()
tree = STRtree(parcels)
roads = plots.exclusions().buffer(1.0)
outlines = [(i, v["label"], shape(v["geometry"]).buffer(0)) for i, v in enumerate(v for v in load_verdicts() if v["kind"] == "plot") if v["label"] in ("vineyard", "overgrown")]
orchard = unary_union([shape(v["geometry"]) for v in load_verdicts() if v["kind"] == "plot" and v["label"] == "orchard"])


def iou(a, b):
    return a.intersection(b).area / a.union(b).area if not a.is_empty and a.intersects(b) else 0.0


def best(g):
    return max(((iou(g, o), i, l) for i, l, o in outlines), default=(0.0, -1, ""))


def home(g, share):
    return [parcels[k] for k in tree.query(g) if parcels[k].intersection(g).area >= share * parcels[k].area]


def join(polys):
    return unary_union([p.buffer(0.3, join_style="mitre") for p in polys]).buffer(-0.3, join_style="mitre")


rules = {
    "fill50": lambda g: plots._largest(unary_union([g, *home(g, 0.5)]).difference(roads)),
    "fill70": lambda g: plots._largest(unary_union([g, *home(g, 0.7)]).difference(roads)),
    "frame50": lambda g: plots._largest(join(home(g, 0.5)).difference(roads)) if home(g, 0.5) else g,
    "clip_home1": lambda g: plots._largest(g.intersection(join(home(g, 0.3)).buffer(1.0))) if home(g, 0.3) else g,
    "clip_all1": lambda g: plots._largest(g.intersection(join([parcels[k] for k in tree.query(g)]).buffer(1.0))),
}
totals = {name: [0.0, 0.0, 0.0] for name in rules}
print(f"{'block':9s} {'m2':>6s} {'best':>12s} " + " ".join(f"{n:>14s}" for n in rules))
for g, p in blocks:
    b0, i0, l0 = best(g)
    row = [f"{p['vineyard_id']:9s} {round(g.area):6d} {b0:5.2f} #{i0:<2d}{l0[:4]:4s}"]
    for name, rule in rules.items():
        h = rule(g)
        if h.is_empty:
            h = g
        b1, _, _ = best(h)
        totals[name][0] += b1 - b0
        totals[name][1] += h.difference(g).intersection(orchard).area
        totals[name][2] += (h.area - g.area)
        row.append(f"{b1:5.2f} {h.area - g.area:+7.0f}")
    print(" ".join(row))
print("sum IoU gain / m2 added on orchards / net m2:", {n: [round(v, 2) for v in t] for n, t in totals.items()})
