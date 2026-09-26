"""Whole-site canopy as predict.py makes it (`canopy_net.tile_canopies(..., combine="and", threshold=0.2, flips=False)`)
before and after the young-block minimum area and the grass-strip rule: counts, area, canopies longer than 5 / 10 m,
area outside every hand-drawn vineyard outline and on orchard outlines. Evaluation only (reads data/review/).
Saves what each rule adds or removes for the contact sheets.
Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_rules_site2.py [plots.json]"""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from marcaj import canopy, canopy_net
from marcaj.canopy import CanopyParams, plot_rows
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT, load_tiles

WORK = REPO_ROOT / "data" / "generated" / "work" / "canopy_rules"
P = CanopyParams()
VARIANTS = {"before": replace(P, young_pieces=0, strip_gr=0), "young only": replace(P, strip_gr=0),
            "strips only": replace(P, young_pieces=0), "after (defaults)": P}


def length(g) -> float:
    c = list(g.minimum_rotated_rectangle.exterior.coords)
    return max(np.hypot(c[0][0] - c[1][0], c[0][1] - c[1][1]), np.hypot(c[1][0] - c[2][0], c[1][1] - c[2][1]))


rows = plot_rows(json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else WORK / "plots_now.json").read_text()))
verdicts = load_verdicts()
vine = unary_union([shape(v["geometry"]) for v in verdicts if v["kind"] == "plot" and v["label"] == "vineyard"])
orchard = unary_union([shape(v["geometry"]) for v in verdicts if v["kind"] == "plot" and v["label"] == "orchard"])
model = canopy_net.load()
found = {k: [] for k in VARIANTS}
spent = {k: 0.0 for k in VARIANTS}
changes = {"young added": [], "strips removed": []}
for tile in load_tiles():
    if not any(a.intersects(tile.bounds) for p in rows for a in p.axes):
        continue
    rgb = canopy.read_rgb(tile)
    prob = canopy_net.probabilities(rgb[0], model, False)
    per = {}
    for key, params in VARIANTS.items():
        started = time.perf_counter()
        per[key] = [(shape(f["geometry"]), f["properties"]["vineyard_id"]) for f in canopy_net.tile_canopies(
            tile, rows, params, model, threshold=0.2, combine="and", flips=False, rgb=rgb, prob=prob)]
        spent[key] += time.perf_counter() - started
        found[key] += per[key]
    before = unary_union([g for g, _ in per["before"]])
    strips = unary_union([g for g, _ in per["strips only"]])
    changes["young added"] += [(tile.name, g, v) for g, v in per["young only"] if not g.intersects(before)]
    changes["strips removed"] += [(tile.name, g, v) for g, v in per["before"] if g.area > 1 and g.intersection(strips).area < 0.2 * g.area]
for key, polygons in found.items():
    union = unary_union([g for g, _ in polygons])
    lengths = np.array([length(g) for g, _ in polygons])
    print(f"{key:18s} {len(polygons):6d} canopies, {union.area:7.0f} m2, longer than 5 m {int((lengths > 5).sum())}, than 10 m {int((lengths > 10).sum())} | "
          f"outside every vineyard outline {union.difference(vine).area:5.0f} m2, on orchard outlines {union.intersection(orchard).area:4.0f} m2 | {spent[key]:.0f} s")
for key, items in changes.items():
    by_plot: dict = {}
    for _, g, v in items:
        by_plot[v] = by_plot.get(v, 0) + 1
    print(f"{key}: {len(items)} canopies, {sum(g.area for _, g, _ in items):.0f} m2, "
          f"outside every outline {sum(g.difference(vine).area for _, g, _ in items):.0f} m2; by plot {sorted(by_plot.items(), key=lambda x: -x[1])[:12]}")
(WORK / "site2_changes.json").write_text(json.dumps({k: [(t, mapping(g), v) for t, g, v in items] for k, items in changes.items()}))
