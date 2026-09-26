"""For gap stretches on kept axes with vine colour (colour25 > 0.15 in gaps_diag_<tag>.json): which canopy step loses
the pixels (tube+colour, closing/fill, grass-strip rule, split + minimum area). Evaluation only.
Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_recall_steps.py [tag]"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from shapely.geometry import LineString

sys.path.insert(0, str(Path(__file__).parent))
from canopy_recall_lib import PROB, WORK, frozen_plots  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.canopy import EIGHT, CanopyParams, plot_rows, row_spacing  # noqa: E402
from marcaj.tiles import PIXEL_M, load_tiles  # noqa: E402
from scipy import ndimage  # noqa: E402

P = CanopyParams()
tag = sys.argv[1] if len(sys.argv) > 1 else "uploaded"
diag = [d for d in json.loads((WORK / f"gaps_diag_{tag}.json").read_text()) if d["status"] == "kept" and d["colour25"] > 0.15]
rows = plot_rows(frozen_plots())
tiles = {t.name: t for t in load_tiles()}
by_tile = defaultdict(list)
for d in diag:
    by_tile[d["tile"]].append(d)
for name, items in by_tile.items():
    tile = tiles[name]
    rgb, transform = canopy.read_rgb(tile)
    prob = np.load(PROB / f"{name[:-4]}.npy").astype(np.float32) / 255
    excess, valid = canopy.excess_green(rgb, P)
    green = (excess > P.green_dn) & valid & (prob > 0.2)
    for d in items:
        plot = next(p for p in rows if p.vineyard_id == d["row_id"][:3])
        axes = [a for a in plot.axes if a.intersects(tile.bounds)]
        kept = canopy.kept_axes(axes, green, transform, P, row_spacing(plot.axes), excess)
        band = canopy.tube(kept, transform, green.shape, P.tube_m)
        gap = rasterize([LineString([d["start"], d["end"]]).buffer(0.9, cap_style="flat")], out_shape=green.shape, transform=transform).astype(bool)
        m0 = green & band
        m1 = ndimage.binary_fill_holes(canopy.close(m0, P.close_m) & band)
        m2 = canopy._drop_grass_strips(m1, rgb, transform, axes, P)
        labels, count = ndimage.label(m2, EIGHT)
        labels, count = canopy._split(labels, count, canopy._along(transform, m2.shape, plot.angle_deg), P)
        minimum = canopy.min_area(labels, P)
        sizes = np.bincount(labels.ravel()) * PIXEL_M**2
        m3 = (labels > 0) & (sizes[labels] >= minimum)
        a = lambda m: round(float((m & gap).sum() * PIXEL_M**2), 2)
        print(f"{d['row_id']} {d['gap_m']:5.1f} m {name[7:16]} c{d['colour25']:.2f} shift {d['axis_shift_m']:.2f} | tube&colour {a(m0)} closed {a(m1)} strips {a(m2)} min-area {a(m3)} m2 (min {minimum})")
