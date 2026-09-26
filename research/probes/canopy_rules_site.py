"""Whole-site canopy with the new and the old `CanopyParams`: runtime, counts, and false-canopy proxies from the
hand-drawn plot outlines in data/review/verdicts.json (canopy on `orchard` and `overgrown` outlines and outside every
`vineyard` outline; the review holds no `no_vineyard` tile verdicts, so the judge's false-canopy penalty is empty).
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_rules_site.py"""

import json
import time
from dataclasses import replace

import numpy as np
from shapely.geometry import shape
from shapely.ops import unary_union

from canopy_rules_lib import WORK, predicted_rows
from marcaj.canopy import CanopyParams, read_rgb, tile_canopies
from marcaj.review import load_verdicts
from marcaj.tiles import load_tiles

NEW = CanopyParams()
OLD = replace(NEW, green_dn=0, refine2_m=0, row_contrast=1.3, row_gap=0, row_value=0, close_m=0, inset_m=0)
outlines = {label: unary_union([shape(v["geometry"]) for v in load_verdicts() if v["kind"] == "plot" and v["label"] == label])
            for label in ("vineyard", "orchard", "overgrown")}
tiles = load_tiles()
found = {"new": [], "old": []}
spent = {"new": 0.0, "old": 0.0, "read": 0.0}
read = 0
for tile in tiles:
    if not any(axis.intersects(tile.bounds) for plot in predicted_rows for axis in plot.axes):
        continue
    started = time.perf_counter()
    rgb = read_rgb(tile)
    spent["read"] += time.perf_counter() - started
    read += 1
    for key, params in (("new", NEW), ("old", OLD)):
        started = time.perf_counter()
        found[key] += [shape(f["geometry"]) for f in tile_canopies(tile, predicted_rows, params, rgb)]
        spent[key] += time.perf_counter() - started
print(f"{read} of {len(tiles)} tiles cross predicted rows; reading them took {spent['read']:.1f} s")
for key, polygons in found.items():
    union = unary_union(polygons)
    area = np.array([p.area for p in polygons])
    print(f"{key}: {spent[key]:.1f} s ({spent[key] / read:.2f} s per tile, without the read), {len(polygons)} canopies, {union.area:.0f} m2, "
          f"median {np.median(area):.2f} m2 | on orchard outlines {union.intersection(outlines['orchard']).area:.1f} m2, on overgrown "
          f"{union.intersection(outlines['overgrown']).area:.1f} m2, outside every vineyard outline {union.difference(outlines['vineyard']).area:.0f} m2 "
          f"({union.difference(outlines['vineyard']).area / union.area:.1%})")
(WORK / "site_counts.json").write_text(json.dumps({key: len(v) for key, v in found.items()}))
