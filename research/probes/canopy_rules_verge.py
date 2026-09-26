"""Axes on uniform grass: with the contrast test off, an axis the row detector ran over a grass verge gets a full
tube of "canopy". Compares two axis tests on the reference tiles and on the whole site: the old green-share contrast
(`row_contrast`) and a value contrast (mean excess green, clipped at 0, within `tube_m` over 0.6-1.0 m beside).
Canopy outside every hand-drawn vineyard outline is the site proxy (it also counts vineyards nobody outlined).
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_rules_verge.py [site]"""

import sys
from dataclasses import replace

import numpy as np
from scipy import ndimage
from shapely.geometry import shape
from shapely.ops import unary_union

from canopy_rules_lib import NAMES, images, predicted_rows, run, tiles
from marcaj.canopy import (FLANK_M, CanopyParams, _pixels, canopy_polygons, close, excess_green, kept_axes, read_rgb, row_spacing,
                           tile_canopies, tube)
from marcaj.review import load_verdicts
from marcaj.tiles import load_tiles

P = CanopyParams()


def value_contrast(axis, excess, transform) -> float:
    rows, cols = _pixels(axis.buffer(FLANK_M[1]), transform, excess.shape)
    (x0, y0), (x1, y1) = axis.coords[0], axis.coords[-1]
    d = np.array([x1 - x0, y1 - y0]) / axis.length
    dx, dy = transform.c + (cols + 0.5) * transform.a - x0, transform.f + (rows + 0.5) * transform.e - y0
    v = np.abs(-dx * d[1] + dy * d[0])
    e = np.clip(excess[rows, cols], 0, None)
    return float(e[v <= P.tube_m].mean() / max(e[(v >= FLANK_M[0]) & (v <= FLANK_M[1])].mean(), 1.0))


def polygons(tile, rgb, params, vmin):
    image, transform = rgb
    excess, valid = excess_green(image, params)
    green = (excess > params.green_dn) & valid
    out = []
    for plot in predicted_rows:
        axes = [a for a in plot.axes if a.intersects(tile.bounds)]
        if not axes:
            continue
        kept = [a for a in kept_axes(axes, green, transform, params, row_spacing(plot.axes)) if not vmin or value_contrast(a, excess, transform) >= vmin]
        band = tube(kept, transform, green.shape, params.tube_m)
        mask = ndimage.binary_fill_holes(close(green & band, params.close_m) & band)
        out += canopy_polygons(mask, transform, plot.angle_deg, params)
    return out


VARIANTS = [("defaults", P, 0.0)] + [(f"row_contrast {c}", replace(P, row_contrast=c), 0.0) for c in (1.0, 1.1, 1.2)] + \
           [(f"value contrast >= {c}", P, c) for c in (1.2, 1.3, 1.5)]
for tag, params, vmin in VARIANTS:
    run(tag, lambda n: polygons(tiles[n], images[n], params, vmin))

if "site" in sys.argv[1:]:
    vine = unary_union([shape(v["geometry"]) for v in load_verdicts() if v["kind"] == "plot" and v["label"] == "vineyard"])
    total = {tag: 0.0 for tag, _, _ in VARIANTS}
    outside = dict(total)
    for tile in load_tiles():
        if not any(a.intersects(tile.bounds) for p in predicted_rows for a in p.axes):
            continue
        rgb = read_rgb(tile)
        for tag, params, vmin in VARIANTS:
            found = polygons(tile, rgb, params, vmin)
            total[tag] += sum(p.area for p in found)
            outside[tag] += sum(p.difference(vine).area for p in found)
    for tag in total:
        print(f"site {tag:24s} canopy {total[tag]:7.0f} m2, outside every vineyard outline {outside[tag]:6.0f} m2")
