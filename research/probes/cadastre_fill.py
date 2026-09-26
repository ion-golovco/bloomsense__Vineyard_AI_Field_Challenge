"""Extends the kept detector's plots (data/generated/work/plots/final.geojson) to cover parcels they already mostly
cover, when the parcel's uncovered part shows the plot's own rows (across-row ExG wave at the plot's angle and spacing
at least `gate` times the plot's), and optionally clips plots to their parcels. Scores with `judge.plot_scores`.
Evaluation only (reads data/review/). Run from backend/: uv run --frozen python ../research/probes/cadastre_fill.py [share=0.5] [gate=0.5] [clip=0]"""

import json
import sys

import numpy as np
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

sys.path.insert(0, __import__("os").path.dirname(__file__))
from plot_experiment import _fmt, scores, tile_footprint  # noqa: E402

from marcaj import plots  # noqa: E402
from marcaj.cadastre import load_parcels  # noqa: E402
from marcaj.review import load_verdicts  # noqa: E402
from marcaj.tiles import REPO_ROOT  # noqa: E402

args = {k: float(v) for k, v in (a.split("=") for a in sys.argv[1:])}
share, gate, clip, grow_max = args.get("share", 0.5), args.get("gate", 0.5), args.get("clip", 0.0), args.get("grow", 1.0)
parcels = [p for p in load_parcels() if p.area > 50]
tree = STRtree(parcels)
excess, roads = plots.load_excess(), plots.exclusions()
features = json.loads((REPO_ROOT / "data/generated/work/plots/final.geojson").read_text())["features"]
blocks = sorted((f for f in features if f["properties"]["label"] == "block"), key=lambda f: -f["properties"]["area_m2"])
out, taken, added = [], Polygon(), 0
for f in blocks:
    p, polygon = f["properties"], shape(f["geometry"])
    own = plots._wave(excess, polygon, p["row_angle"], p["row_spacing_m"])
    extra, keep = [], []
    for k in tree.query(polygon):
        c = parcels[k]
        inter = c.intersection(polygon).area
        if inter >= share * c.area:
            keep.append(c)
            rest = c.difference(polygon)
            if rest.area > 20 and rest.area <= grow_max * polygon.area and (not gate or plots._wave(excess, plots._largest(rest), p["row_angle"], p["row_spacing_m"]) >= gate * own):
                extra.append(rest)
    grown = unary_union([polygon, *extra]).difference(roads.buffer(1.0))
    if clip and keep:
        grown = grown.intersection(unary_union(keep).buffer(0.5))
    added += len(extra)
    grown = plots._largest(grown.difference(taken))
    taken = taken.union(grown)
    out.append({**f, "geometry": mapping(grown)})
if "out" in args or True:
    (REPO_ROOT / "data/generated/work/fields").mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "data/generated/work/fields/fill.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": out}))
print(f"share {share} gate {gate} clip {clip} grow {grow_max}: {added} parcel parts added")
for kind, value in scores(out, load_verdicts(), tile_footprint()).items():
    print(f"  {kind:8s} {_fmt(value)}")
