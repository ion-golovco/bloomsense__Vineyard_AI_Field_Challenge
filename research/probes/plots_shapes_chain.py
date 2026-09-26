"""Plots shapes: why the parcels beside the recovered overgrown plots fail the overgrown test (per check).
Run from backend/: uv run --frozen python ../research/probes/plots_shapes_chain.py"""

import json

import numpy as np
from shapely.geometry import shape
from shapely.ops import unary_union

from marcaj import plots
from marcaj.cadastre import load_parcels
from marcaj.tiles import REPO_ROOT

W = REPO_ROOT / "data" / "generated" / "work" / "plots_shapes"
import sys
feats = json.loads((W / f"{sys.argv[1] if len(sys.argv) > 1 else 'base'}.geojson").read_text())["features"]
blocks = [(shape(f["geometry"]), f["properties"]) for f in feats if f["properties"]["label"] == "block"]
parcels = load_parcels()
excess = plots.load_excess()
roads = plots.exclusions()
params = plots.PlotParams()
clipped = plots._clipped(excess, params.overgrown_clip)
covered = unary_union([g for g, _ in blocks]).buffer(params.strip_clear_m)
weedy = [(g, p) for g, p in blocks if p.get("overgrown")]
for k in (1501, 1497, 1496, 1495, 1494, 1493, 1491, 1509, 1570):
    parcel = parcels[k]
    region = plots._largest(parcel.difference(roads.buffer(params.road_setback_m)).difference(covered))
    g0, p0 = min(weedy, key=lambda b: b[0].distance(region) if not region.is_empty else 1e9)
    near = min(g.distance(region) for g, _ in blocks) if not region.is_empty else -1
    a0, s0 = p0["row_angle"], p0["row_spacing_m"]
    if region.is_empty:
        print(k, "no region"); continue
    rows = []
    for angle in a0 + np.arange(-3, 3.1, 1.0):
        snr, sp, vo = plots._band_snr(clipped, region, angle)
        rows.append((round(snr, 1), round(angle, 1), round(sp, 2), round(vo, 2)))
    best = max(rows)
    null = plots._band_snr(clipped, region, best[1] + 90)[0]
    wave = plots._wave(clipped, region, best[1], best[2])
    q = plots._quadrilateral(clipped, region, best[1], best[2], params)
    print(k, f"parcel {parcel.area:.0f} region {region.area:.0f} ({region.area / parcel.area:.2f}) nearest weedy {p0['vineyard_id']} {g0.distance(parcel):.1f} m a0 {a0} s0 {s0} nearest plot {near:.1f} m",
          f"best snr/angle/sp/vo {best} null {null:.1f} (x{best[0] / max(null, 1e-9):.2f}) wave {wave:.4f} quad rows {len(q[1]) if q else None}")
    # also the free spectrum (any spacing)
