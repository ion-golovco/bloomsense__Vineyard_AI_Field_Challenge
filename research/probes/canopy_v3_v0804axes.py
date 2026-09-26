"""V08-04: which canopy.kept_axes test removes the block's axes per tile (colour green, frozen v4 rows), with each
dropped axis's length, tube green share and value contrast. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v3_v0804axes.py"""

import sys
from pathlib import Path

import numpy as np
from rasterio.transform import array_bounds
from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import frozen_plots  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.canopy import CanopyParams, plot_rows  # noqa: E402
from marcaj.tiles import load_tiles  # noqa: E402

BLOCK = sys.argv[1] if len(sys.argv) > 1 else "V08-04"
P = CanopyParams()
feats = frozen_plots()
ids = {f["properties"]["vineyard_id"] for f in feats if f["properties"]["label"] == "block" and f["properties"]["block_id"] == BLOCK}
rows = [p for p in plot_rows(feats) if p.vineyard_id in ids]
print(BLOCK, "patterns", sorted(ids), "axes", sum(len(p.axes) for p in rows))
tot = {"clip": 0.0, "contrast": 0.0, "row_gap": 0.0, "row_value": 0.0, "grass": 0.0}
for tile in load_tiles():
    for plot in rows:
        axes = [a for a in plot.axes if a.intersects(tile.bounds)]
        if not axes:
            continue
        rgb, tr = canopy.read_rgb(tile)
        excess, valid = canopy.excess_green(rgb, P)
        green = canopy.green_mask(rgb, P) & valid
        bounds = box(*array_bounds(*green.shape, tr))
        clipped = [a.intersection(bounds) for a in axes]
        fitted = [canopy.fit_axis(LineString(c.coords), green, tr, P) for c in clipped if c.geom_type == "LineString" and c.length > 0]
        s1 = [a for a, c in fitted if c >= P.row_contrast]
        s2 = canopy._drop_interrows(s1, green, tr, P, canopy.row_spacing(plot.axes))
        vc = {id(a): canopy.value_contrast(a, excess, tr, P) for a in s2}
        s3 = [a for a in s2 if vc[id(a)] >= P.row_value]
        s4 = canopy._drop_grass_rows(s3, green, excess, rgb, tr, P) if len(s3) >= P.grass_axes else s3
        L = lambda xs: sum(a.length for a in xs)
        share = lambda a: float(green[canopy._pixels(a.buffer(P.tube_m), tr, green.shape)].mean())
        tot["clip"] += L(a for a, _ in fitted); tot["row_gap"] += L(s1) - L(s2); tot["row_value"] += L(s2) - L(s3); tot["grass"] += L(s3) - L(s4)
        drops = [(round(a.length), round(share(a), 3), round(vc[id(a)], 2)) for a in s2 if vc[id(a)] < P.row_value]
        gapd = [(round(a.length), round(share(a), 3)) for a in s1 if a not in s2]
        print(f"{tile.name} {plot.vineyard_id}: axes {len(fitted)} {L(a for a, _ in fitted):.0f} m -> row_gap -{len(s1)-len(s2)} -> row_value -{len(s2)-len(s3)} -> grass -{len(s3)-len(s4)} | "
              f"value drops (len, share, vc) {drops} | gap drops {gapd} | kept share median {np.median([share(a) for a in s4]) if s4 else 0:.3f}", flush=True)
print({k: round(v) for k, v in tot.items()})
