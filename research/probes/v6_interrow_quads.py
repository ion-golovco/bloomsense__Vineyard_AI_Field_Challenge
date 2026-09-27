"""v6: inter-rows that follow non-parallel rows. Two prototype changes, monkeypatched, against the current code:
(1) `rows.interrow_areas` builds each inter-row as the quadrilateral between its two rows' own lines (each row's across
    position interpolated along the pattern), not a box at their centroid offsets along the first row's direction;
(2) the carry path's inter-row axes (`predict.carry_plots`, parallel to the pattern) re-fitted to the given rows' own
    slope (a straight line through the drawn vertices within 0.6 m and the axis's span).
Scored: overlap of inter-rows with the +-0.30 m band of the reference row pieces (the canopy tube; the organizers'
inter-rows never enter it), total inter-row area, V35-25, and the judge on the example tiles. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/v6_interrow_quads.py"""

import json
import sys
from pathlib import Path
from unittest import mock

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, Polygon, mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import rows  # noqa: E402
from marcaj.plots import exclusions  # noqa: E402
from marcaj.predict import carry_plots  # noqa: E402

ORIGINAL = rows.interrow_areas


def interrow_quads(features, exclusions_, obstacles=()):
    """rows.interrow_areas with each side on its own row line (see module doc)."""
    block = {rows._pattern(f["properties"]): f["properties"]["vineyard_id"] for f in features if f["properties"]["label"] == "row"}
    blockers = unary_union([shape(f["geometry"]) for f in obstacles]) if obstacles else None
    areas = []
    inset = rows.INTERROW_INSET_M
    for pattern_id, kept in rows.kept_rows(features).items():
        along = rows._direction(kept[0][1])
        across = np.array([-along[1], along[0]])
        frame = lambda u, w: along * u + across * w

        def at(line):
            (t0, w0), (t1, w1) = ((float(np.asarray(p) @ along), float(np.asarray(p) @ across)) for p in (line.coords[0], line.coords[-1]))
            return lambda u: w0 + (w1 - w0) * (u - t0) / (t1 - t0) if t1 != t0 else w0

        for (_, a), (_, b) in zip(kept[:-1], kept[1:]):
            (ua0, ua1), (ub0, ub1) = (sorted(np.asarray(line.coords) @ along) for line in (a, b))
            u0, u1 = max(ua0, ub0), min(ua1, ub1)
            if u1 <= u0:
                continue
            fa, fb = at(a), at(b)
            if fa((u0 + u1) / 2) > fb((u0 + u1) / 2):
                fa, fb = fb, fa
            if min(fb(u0) - fa(u0), fb(u1) - fa(u1)) <= 2 * inset:
                continue
            box = lambda lo, hi: Polygon([frame(lo, fa(lo) + inset), frame(hi, fa(hi) + inset), frame(hi, fb(hi) - inset), frame(lo, fb(lo) - inset)])
            if rows.EXTEND_M:
                u0 -= rows.EXTEND_M * box(u0 - rows.EXTEND_M, u0).intersects(exclusions_)
                u1 += rows.EXTEND_M * box(u1, u1 + rows.EXTEND_M).intersects(exclusions_)
            polygon = box(u0, u1).difference(exclusions_)
            parts = [part for part in getattr(polygon, "geoms", [polygon]) if part.geom_type == "Polygon"]
            if parts:
                largest = max(parts, key=lambda part: part.area)
                pieces = rows.cut_obstacles(largest, blockers, across) if blockers is not None and largest.intersects(blockers) else [largest]
                areas += [{"type": "Feature", "geometry": mapping(piece), "properties": {
                    "label": "interrow_area", "vineyard_id": block[pattern_id], "pattern_id": pattern_id, "interrow_cover": "bare_soil"}} for piece in pieces]
    return areas


def refit_axes(axes, given):
    """Each carry axis re-fitted to the given rows' own slope: v(u) through the drawn vertices (densified every 1 m) within
    0.6 m across of the axis and inside its span."""
    lines = [shape(f["geometry"]) for f in given]
    tree = STRtree(lines)
    out = []
    for f in axes:
        axis = shape(f["geometry"])
        d = rows._direction(axis)
        n = np.array([-d[1], d[0]])
        (x0, y0) = axis.coords[0]
        t0, t1 = sorted(float(np.asarray(p) @ d) for p in axis.coords)
        w = float(np.asarray(axis.coords[0]) @ n)
        pts = []
        for i in tree.query(axis.buffer(0.6)):
            g = lines[i]
            pts += [np.asarray(g.interpolate(s).coords[0]) for s in np.arange(0, g.length, 1.0)] + [np.asarray(g.coords[-1])]
        pts = np.array(pts) if pts else np.zeros((0, 2))
        if len(pts):
            u, v = pts @ d, pts @ n
            keep = (np.abs(v - w) <= 0.6) & (u >= t0 - 0.5) & (u <= t1 + 0.5)
            u, v = u[keep], v[keep]
        if len(pts) and len(u) >= 3 and np.ptp(u) > 2:
            slope, offset = np.polyfit(u, v, 1)
            ends = [d * t + n * (offset + slope * t) for t in (t0, t1)]
            f = {**f, "geometry": mapping(LineString(ends))}
        out.append(f)
    return out


def measure(tag, inter, band, v35):
    geoms = [shape(f["geometry"]) for f in inter]
    total = sum(g.area for g in geoms)
    over = sum(g.intersection(band).area for g in geoms)
    v = [g for g, f in zip(geoms, inter) if f["properties"]["vineyard_id"].startswith("V35-25")]
    vo = sum(g.intersection(band).area for g in v)
    print(f"{tag:34s} inter-rows {len(geoms)} area {total:.0f} m2 | inside the row band {over:.0f} m2 ({100 * over / total:.2f}%) | "
          f"V35-25 {sum(g.area for g in v):.0f} m2, in band {vo:.0f} ({100 * vo / max(sum(g.area for g in v), 1):.1f}%)", flush=True)


if __name__ == "__main__":
    given = json.loads((L.REF / "rows.geojson").read_text())["features"]
    pieces = L.ref_pieces()
    band = unary_union([shape(f["geometry"]).buffer(0.30, cap_style="flat") for f in pieces])
    found, axes = carry_plots(given)
    ex = exclusions()
    base = [f for f in found if f["properties"]["label"] != "row"]
    with mock.patch.object(rows, "STRAY_SHARE", 0.0):
        measure("current (parallel axes, boxes)", ORIGINAL(base + axes, ex, []), band, None)
        measure("quads on parallel axes", interrow_quads(base + axes, ex, []), band, None)
        fitted = refit_axes(axes, given)
        measure("boxes on refit axes", ORIGINAL(base + fitted, ex, []), band, None)
        measure("quads on refit axes", interrow_quads(base + fitted, ex, []), band, None)
