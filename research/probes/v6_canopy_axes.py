"""v6: how close the canopy refit brings axes to the organizers' drawn rows on the two example tiles, and the judge canopy
with the organizers' own rows (refit on / off). Start axes: the team's reference rows (global). Evaluation only.
Run from backend/: uv run --frozen --group sam python ../research/probes/v6_canopy_axes.py"""

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, shape

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.canopy import CanopyParams, RowSet  # noqa: E402

D = CanopyParams()


def org_sets() -> list[RowSet]:
    rows = [shape(f["geometry"]) for f in L.organizer() if f["properties"]["label"] == "row"]
    by: dict[str, list] = {}
    for f in L.organizer():
        if f["properties"]["label"] == "row":
            by.setdefault(f["properties"]["vineyard_id"], []).append(LineString([shape(f["geometry"]).coords[0], shape(f["geometry"]).coords[-1]]))
    out = []
    for vid, axes in by.items():
        (x0, y0), (x1, y1) = max(axes, key=lambda a: a.length).coords
        out.append(RowSet(vid, float(np.degrees(np.arctan2(y1 - y0, x1 - x0))), axes))
    return out, rows


def distance_to(axes: list[LineString], refs: list[LineString]) -> np.ndarray:
    """Per sample point every 1 m along each axis: distance to the nearest reference row (m), for axes within 0.5 m."""
    out = []
    for a in axes:
        near = min(refs, key=lambda r: r.distance(a.interpolate(0.5, normalized=True)))
        if near.distance(a.interpolate(0.5, normalized=True)) > 0.5:
            continue
        out += [near.distance(a.interpolate(t)) for t in np.arange(0.5, a.length, 1.0)]
    return np.array(out)


if __name__ == "__main__":
    sets, refs = org_sets()
    spacing = {s.vineyard_id: canopy.row_spacing(s.axes) for s in sets}
    for name, params in (("org norefit", replace(D, refine_m=0, refine2_m=0)), ("org refit", D)):
        found = []
        for n in L.NAMES:
            found += L.tile_canopies(n, params, sets, spacing)
        print(name, L.judge_canopy(found), flush=True)
    plots = L.plot_features()
    team = L.rowsets(plots)
    for n in L.NAMES:
        tile = L.TILES[n]
        img, tr = canopy.read_rgb(tile)
        g = canopy.green_mask(img, D)
        tref = [r.intersection(tile.bounds) for r in refs]
        tref = [r for r in tref if r.geom_type == "LineString" and r.length > 1]
        for s in team:
            axes = [a for a in s.axes if a.intersects(tile.bounds)]
            if len(axes) < 5:
                continue
            ex, _ = canopy.excess_green(img, D)
            raw = canopy.kept_axes(axes, g, tr, replace(D, refine_m=0, refine2_m=0), 0, None, None)
            d0 = distance_to(raw, tref)
            print(f"{n[7:16]} {s.vineyard_id} team rows: median {np.median(d0) * 100:.1f} cm p90 {np.percentile(d0, 90) * 100:.1f}", flush=True)
            for tag, p in (("refit 0.5/0.3", D), ("refit 0.5", replace(D, refine2_m=0)), ("refit 0.5/0.2", replace(D, refine2_m=0.2)),
                           ("refit 0.3/0.3", replace(D, refine_m=0.3)), ("refit 0.5/0.35", replace(D, refine2_m=0.35))):
                fitted = canopy.kept_axes(axes, g, tr, replace(p, row_gap=0, row_value=0, grass_ratio=0), 0, None, None)
                d = distance_to(fitted, tref)
                print(f"   {tag:16s} median {np.median(d) * 100:.1f} cm p90 {np.percentile(d, 90) * 100:.1f} mean {d.mean() * 100:.1f}", flush=True)
