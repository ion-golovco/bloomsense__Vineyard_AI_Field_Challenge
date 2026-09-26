"""Vineyard plots and their row axes, with no training (docs/RESEARCH.md, "Plot variables"; research/notes/plots.md).
Plots are seeded where vine-spacing row energy beats orchard-spacing energy, split where the row direction changes,
dropped when the across-row ExG wave is too weak to be green vines (tractor lines on bare fields), and fitted in each
plot's own row frame: the sides on the outermost row axes, the ends square to the rows unless clearly oblique. The seed's
fit is then walked outward row by row and along each row while the row stands out from its inter-rows, so a bare-soil
core takes in its grassy continuation; a new outer row also counts when its tube is green and its flanks much less so
(young or weak vines the tube-minus-flank contrast misses). Fits of one planting that a tree split are joined, and
edges that face a road are moved onto it. Row axes are the across-row ExG profile peaks, which matched the reference
rows to a median 0.04-0.06 m on both reference tiles. The row spacing is the strongest local maximum inside the vine
band (a maximum on the band edge is a strip's edges leaking in), and plots with rows over 3.2 m apart are dropped (fruit
trees). A second pass seeds on the top-hat layer (layers.py, `tophat_m` 1.6, ratio > `strip_ratio`) away from the
first pass's plots, so it only adds plots: narrow 10-14 m strips of young vines on bare soil, whose soil-against-grass
edge puts energy in the orchard band of the default layer, and weak grassy parts of plots; its plots need 4 rows and
200 m2. Then (`parcel_share` 0.7), a plot takes in the rest of a cadastral parcel it already covers 70% of when that
rest shows the plot's own rows, or (`parcel_along` 0.3) of a parcel it covers 30% of when the rest only carries its rows
on along them (a strip's own parcel); a parcel never creates a plot. Parcels come from the public cadastre WMS of I.P.
Cadastrul Bunurilor Imobile (https://map.cadastru.md/geoserver/ows, layer w_cbi:cad_terenuri, fetched 26 Sep 2026 by
`marcaj.cadastre`; no reuse licence found, research/notes/fields.md) and are read from
data/raw/external/cadastre/parcels_32635.geojson; a missing file raises FileNotFoundError, and `parcel_share=0,
parcel_along=0, track_m=0` runs without it.

A plot is one row pattern (one lattice: angle, spacing, phase); a block is the organizers' vineyard_id: plots that touch
or lie under 5 m apart, unless a road or a track (a strip outside every parcel) lies between (`_blocks`). detect_plots
keys every feature by its pattern (vineyard_id = pattern_id, plus block_id), so canopies, inter-rows and attributes are
fitted per lattice; `assign_blocks`, run last on the whole prediction, gives everything its block's vineyard_id and
joins a block's plots into one `block` feature. IDs are positional, so a change elsewhere renumbers nothing: a block is
`V<r>-<c>` after the tile its centroid lies in, its patterns `V<r>-<c>a`, `b`... when it has several, and rows
`<pattern>-R001`... across the rows.

`verify_plots` (predict.py, after the canopy, before inter-rows and waste) runs the vine detection inside every plot and
drops a pattern whose canopy does not look like vine rows (`vine_evidence`, `looks_like_vineyard`): canopy along under
`verify_cover` of the row length, rows no greener than halfway between them (`verify_contrast`), or a median piece longer
than a vine with patchy cover (`verify_vine_m`, `verify_hedge`); a weak pattern stays when another pattern of its block
passes (the weedy part of a vineyard). On the v3 prediction it drops 4 blocks (scrub, a weed strip, grass by a road;
1,303 m2, none on any outline) and keeps the young strips #8, #9, #26 and the unoutlined vine strips V22-15 and V37-22
(research/probes/plots_verify_*.py).

Against the 35 hand-drawn vineyard outlines (`judge.plot_scores`, north tunes, south validates;
research/probes/plots_recall_eval.py): vineyard area recall 0.86 (0.82 before the second pass, parcels and spacing
rules), area IoU 0.83 north and 0.73 south; 140 m2 on orchard outlines (1,914 before). Per block (assign_blocks,
research/probes/plots_v3_eval.py) F1 at IoU 0.5 / 0.75 is 0.90 / 0.65 north and 0.82 / 0.41 south (per plot 0.82 / 0.55
and 0.76 / 0.43): fragments of one outline now count once. The F1 counts second-pass plots on vineyards nobody outlined
as false. About 26 s."""

import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine
from scipy import ndimage, stats
from shapely import contains_xy
from shapely.geometry import LineString, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from marcaj.cadastre import load_parcels
from marcaj.layers import LAYER_PX_M, Layers, exg, load_layers
from marcaj.mosaic import MOSAIC_PX_M, load_mosaic
from marcaj.tiles import DATA_DIR

VINE_BAND_M = (2.0, 3.6)
GRID_LEFT, GRID_TOP, TILE_M = 628992.0, 5221222.4, 51.2  # the tile grid (CLAUDE.md): siret3_r<r>_c<c>
STRIP_TOPHAT_M = 1.6  # top-hat width of the second-pass seed layer (layers.py)


@dataclass(frozen=True)
class PlotParams:
    ratio_min: float = 2.0        # vine-spacing over orchard-spacing row energy
    opening_m: float = 0.8        # cuts bridges narrower than this between seeds
    min_seed_m2: float = 150.0
    mode_share: float = 0.2       # a second row direction needs this share of a seed to split it off
    split_deg: float = 45.0       # ...and must differ by at least this much
    square_deg: float = 10.0      # row ends within this of square to the rows are squared off
    road_reach_m: float = 5.0     # an edge this close to a road is moved onto it
    road_setback_m: float = 1.0   # ...stopping this far short of the road boundary
    min_area_m2: float = 150.0
    min_row_m: float = 2.0
    peak_reach: float = 0.6       # a row axis is the highest across-row ExG peak within this many spacings (0.35 kept grass strips 1.1-1.35 m from a row)
    max_spacing_m: float = 3.2    # wider rows are fruit trees or tracks (orchard #38 3.26 m; every vineyard plot <= 3.02 m); vine band ends at 3.6
    min_wave_exg: float = 0.005   # seeds with a weaker across-row ExG wave are dropped: tractor lines on bare fields 0.0005-0.0015, vineyards 0.011+
    walk: float = 0.5             # a row continues where its tube-minus-flank ExG is at least this share of the plot's median; 0 = off
    walk_reach_m: float = 40.0    # how far beyond its seed a plot may be walked
    gap_m: float = 3.0            # a row may lapse this long (missing vines); a track, headland or tree clump stops it
    new_row_share: float = 0.6    # a new outer row needs evidence along this share of its neighbour
    side_green: float = 0.3       # ...or, vine green on the lattice, this share of its +-0.3 m tube green (mosaic ExG > 0.08); 0 = off
    side_flank: float = 0.7       # ...with the tubes half a spacing to either side at most this share as green (not a verge)
    merge_m: float = 5.0          # fits of one planting under this far apart join (the 5 m block rule); 0 = off
    strip_ratio: float = 4.0      # second-pass seeds where the top-hat layer's vine/orchard ratio beats this, away from first-pass plots; 0 = off
    strip_clear_m: float = 1.0    # ...and at least this far from them
    strip_min_rows: int = 4       # ...and such a plot needs this many rows
    strip_min_m2: float = 200.0   # ...and this area
    parcel_share: float = 0.7     # a cadastral parcel the plot already covers this share of is filled...; 0 = off
    parcel_gate: float = 0.4      # ...where the rest shows the plot's rows: across-row ExG wave at its angle and spacing >= this share of the plot's
    parcel_phase: float = 0.35    # ...on the plot's own row lattice, within this share of a spacing
    parcel_along: float = 0.3     # ...or a parcel it covers this share of, where the rest only carries its rows on along them; 0 = off
    block_m: float = 5.0          # plantings under this far apart are one block (the organizers' rule)...
    track_m: float = 1.5          # ...unless a road lies between, or a track: this much of the link outside every cadastral parcel; 0 = no parcels
    lattice_m: float = 8.0        # ...and two plots of one row lattice this close (2 rows lost) are one block when the ground between carries their rows; 0 = off
    snap_deg: float = 6.0         # a second-pass plot under block_m from a first-pass plot within this angle (and 8% spacing) takes its lattice; 0 = off
    # verify_plots, after the canopy (looks_like_vineyard); each check is off at 0
    verify_cover: float = 0.25    # canopy along at least this share of the row length (young strips 0.34-0.56, scrub 0-0.19)
    verify_contrast: float = 0.015  # mosaic ExG on the rows minus halfway between them (outlined 0.033+, a weed strip 0.010)
    verify_vine_m: float = 4.0    # a median canopy piece longer than this (a vine is at most ~2.5 m; outlined up to 3.6, scrub 4.1-5.1) makes the rows hedges...
    verify_hedge: float = 0.5     # ...and a vine hedge covers at least this share of its rows (outlined hedges 0.50-0.72, scrub 0.38-0.40)


class _Excess:
    """ExG of the 0.2 m mosaic, sampled along arbitrary world coordinates."""

    def __init__(self, data_dir: Path) -> None:
        rgb, self.transform = load_mosaic(data_dir)
        self.values, _ = exg(rgb)

    def at(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        rows, cols = (self.transform.f - y) / MOSAIC_PX_M - 0.5, (x - self.transform.c) / MOSAIC_PX_M - 0.5
        return ndimage.map_coordinates(self.values, [rows, cols], order=1)


class _Frame:
    """A raster aligned with the rows: axis 1 (u) runs along them, axis 0 (v) across."""

    def __init__(self, polygon: Polygon, angle_deg: float, step: float, pad: float = 0.0) -> None:
        self.origin, self.angle = np.asarray(polygon.centroid.coords[0]), np.radians(angle_deg)
        xy = np.asarray(polygon.exterior.coords) - self.origin
        u, v = xy @ self._axes[0], xy @ self._axes[1]
        self.u = np.arange(u.min() - pad, u.max() + pad, step)
        self.v = np.arange(v.min() - pad, v.max() + pad, step)
        self.x, self.y = self.world(*np.meshgrid(self.u, self.v))
        self.inside = contains_xy(polygon, self.x, self.y)

    @property
    def _axes(self) -> tuple[np.ndarray, np.ndarray]:
        c, s = np.cos(self.angle), np.sin(self.angle)
        return np.array([c, s]), np.array([-s, c])

    def world(self, u: Any, v: Any) -> tuple[np.ndarray, np.ndarray]:
        (c, s), _ = self._axes
        u, v = np.asarray(u), np.asarray(v)
        return self.origin[0] + u * c - v * s, self.origin[1] + u * s + v * c


@lru_cache(maxsize=1)
def load_excess(data_dir: Path = DATA_DIR) -> _Excess:
    return _Excess(data_dir)


def exclusions(data_dir: Path = DATA_DIR):
    """Passages and forbidden zones: a road always separates plots, and nothing is annotated on them."""
    route = data_dir / "02_route"
    return unary_union([shape(f["geometry"]) for name in ("passages.geojson", "forbidden.geojson")
                        for f in json.loads((route / name).read_text(encoding="utf-8"))["features"]])


def _row_power(excess: _Excess, polygon: Polygon, angle: float, step: float) -> tuple[float, float]:
    """Vine-band power of the across-row ExG profile at this angle, and the spacing of its peak."""
    frame = _Frame(polygon, angle, step)
    counts = frame.inside.sum(1)
    use = counts > max(3, 4 / step)
    if use.sum() < 8:
        return 0.0, 0.0
    profile = ((excess.at(frame.x, frame.y) * frame.inside).sum(1) / np.maximum(counts, 1))[use]
    power = np.abs(np.fft.rfft((profile - profile.mean()) * np.hanning(len(profile)), 4096)) ** 2
    frequency = np.fft.rfftfreq(4096, step)
    band = np.flatnonzero((frequency >= 1 / VINE_BAND_M[1]) & (frequency <= 1 / VINE_BAND_M[0]))
    # the peak must be a local maximum inside the band: a maximum on its edge is leakage (a strip's edges, orchard rows)
    inner = band[1:-1][(power[band[1:-1]] >= power[band[:-2]]) & (power[band[1:-1]] >= power[band[2:]])]
    if not len(inner):
        return 0.0, 0.0
    k = int(inner[np.argmax(power[inner])])
    return float(power[k] / len(profile)), float(1 / frequency[k])


def _row_angle(excess: _Excess, polygon: Polygon) -> tuple[float, float]:
    """Row angle (degrees from east) and spacing: a full search every 3 degrees at 0.4 m, refined at 0.2 m.
    The layer's own angle is not trusted as a seed: it is perpendicular on 3 of the 35 outlined plots."""
    best = max(((_row_power(excess, polygon, a, 2 * MOSAIC_PX_M)[0], a) for a in np.arange(0, 180, 3.0)))[1]
    for span, step in ((3.0, 0.5), (0.5, 0.1)):
        best = max((_row_power(excess, polygon, a, MOSAIC_PX_M)[0], a) for a in best + np.arange(-span, span + 1e-9, step))[1]
    return best % 180, _row_power(excess, polygon, best, MOSAIC_PX_M)[1]


def _end_line(u: np.ndarray, v: np.ndarray, square_deg: float) -> tuple[float, float]:
    """Row ends as u = intercept + slope * v; squared off when within `square_deg` of square."""
    slope, intercept, _, _ = stats.theilslopes(u, v)
    if np.degrees(np.arctan(abs(slope))) <= square_deg:
        return 0.0, float(np.median(u))
    return float(slope), float(intercept)


def _quadrilateral(excess: _Excess, region: Polygon, angle: float, spacing: float, params: PlotParams) -> tuple[Polygon, list[LineString]] | None:
    """The region's rows in its row frame: the sides on the outermost row axes, the ends fitted through each
    row's extent in the region. Returns the quadrilateral and the row axes as lines across the whole frame."""
    frame = _Frame(region, angle, MOSAIC_PX_M, pad=spacing)
    inside = frame.inside
    columns = np.flatnonzero(inside.any(0))
    if len(columns) < 10:
        return None
    middle = slice(columns[0] + len(columns) // 5, columns[-1] - len(columns) // 5 + 1)
    profile = excess.at(frame.x, frame.y)[:, middle].mean(1)
    width = inside.sum(1) * MOSAIC_PX_M
    covered = np.flatnonzero(width >= 0.5 * np.median(width[width > 0]))
    if len(covered) < 2:
        return None
    lo, hi = frame.v[covered[0]], frame.v[covered[-1]]
    reach = max(1, int(params.peak_reach * spacing / MOSAIC_PX_M))
    floor = np.percentile(profile[covered[0]:covered[-1] + 1], 60)
    axes = frame.v[[i for i in range(reach, len(profile) - reach)
                    if profile[i] == profile[i - reach:i + reach + 1].max() and profile[i] > floor]]
    axes = axes[(axes >= lo - spacing / 2) & (axes <= hi + spacing / 2)]
    if len(axes) < 2:
        return None
    # rows are regular: a missing peak between two rows is a weak row, not a gap in the planting
    filled = [axes[0]]
    for a, b in zip(axes[:-1], axes[1:]):
        missing = int(round((b - a) / spacing)) - 1
        filled += [a + (b - a) * (k + 1) / (missing + 1) for k in range(max(missing, 0))] + [b]
    axes = np.array(filled)
    lo, hi = axes.min(), axes.max()
    ends = []
    for v in axes:
        cols = np.flatnonzero(inside[int(round((v - frame.v[0]) / MOSAIC_PX_M))])
        if len(cols):
            ends.append((v, frame.u[cols[0]], frame.u[cols[-1]]))
    if len(ends) < 2:
        return None
    v, start, stop = np.array(ends).T
    (s1, i1), (s2, i2) = _end_line(start, v, params.square_deg), _end_line(stop, v, params.square_deg)
    corners = [(i1 + s1 * lo, lo), (i2 + s2 * lo, lo), (i2 + s2 * hi, hi), (i1 + s1 * hi, hi)]
    quad = Polygon(list(zip(*frame.world(*zip(*corners)))))
    if not quad.is_valid or not quad.area:
        return None
    return quad, [LineString(list(zip(*frame.world([frame.u[0], frame.u[-1]], [v, v])))) for v in axes]


def _wave(excess: _Excess, polygon: Polygon, angle: float, spacing: float) -> float:
    """Amplitude of the across-row ExG wave at the plot's spacing: green vine rows, not tractor lines on bare soil."""
    return abs(_phasor(excess, polygon, angle, spacing, np.asarray(polygon.centroid.coords[0])))


def _phasor(excess: _Excess, polygon, angle: float, spacing: float, origin: np.ndarray) -> complex:
    """The across-row ExG wave at the plot's spacing as a complex amplitude, its phase measured from `origin`, so two
    areas on one row lattice have the same phase."""
    frame = _Frame(polygon, angle, MOSAIC_PX_M)
    counts = frame.inside.sum(1)
    use = counts > 10
    if use.sum() < 8:
        return 0j
    profile = ((excess.at(frame.x, frame.y) * frame.inside).sum(1) / np.maximum(counts, 1))[use]
    v = frame.v[use] + (frame.origin - origin) @ np.array([-np.sin(frame.angle), np.cos(frame.angle)])
    return complex(2 * (profile * np.exp(-2j * np.pi * v / spacing)).mean())


def _walk(excess: _Excess, region: Polygon, angle: float, spacing: float, usable_at, params: PlotParams) -> tuple[Polygon, list[LineString]] | None:
    """The seed's quadrilateral walked outward: a new outer row where the lattice predicts one and it has evidence along
    most of its neighbour, then each row's ends along the row while the evidence lasts. Evidence is the ExG of a
    +-0.3 m tube on the row minus the tubes half a spacing to either side, smoothed 1 m along the row, against
    `walk` times the plot's own median, so grassy continuations of a bare-soil core still count. Roads and no-data stop it."""
    result = _quadrilateral(excess, region, angle, spacing, params)
    if result is None or not params.walk:
        return result
    quad, axes = result
    frame = _Frame(region, angle, MOSAIC_PX_M, pad=params.walk_reach_m)
    usable = usable_at(frame.x, frame.y)
    tube = 2 * round(0.3 / MOSAIC_PX_M) + 1
    values = excess.at(frame.x, frame.y)
    mean = ndimage.uniform_filter1d(np.where(usable, values, 0), tube, axis=0) / np.maximum(
        ndimage.uniform_filter1d(usable.astype(np.float32), tube, axis=0), 1e-3)
    green = ndimage.uniform_filter1d(((values > 0.08) & usable).astype(np.float32), tube, axis=0)
    half = round(spacing / 2 / MOSAIC_PX_M)
    evidence = np.zeros(mean.shape, np.float32)
    evidence[half:-half] = mean[half:-half] - 0.5 * (mean[:-2 * half] + mean[2 * half:])
    ok = usable.copy()
    ok[:half] = ok[-half:] = False
    ok[half:-half] &= usable[:-2 * half] & usable[2 * half:]
    sigma = 1.0 / MOSAIC_PX_M
    weight = ndimage.gaussian_filter1d(ok.astype(np.float32), sigma, axis=1)
    evidence = ndimage.gaussian_filter1d(np.where(ok, evidence, 0), sigma, axis=1) / np.maximum(weight, 1e-3)
    ok &= weight > 0.5
    normal = np.array([-np.sin(frame.angle), np.cos(frame.angle)])
    inside = contains_xy(quad, frame.x, frame.y)
    rows: dict[int, list[int]] = {}
    for axis in axes:
        iv = int(round(((np.asarray(axis.coords[0]) - frame.origin) @ normal - frame.v[0]) / MOSAIC_PX_M))
        cols = np.flatnonzero(inside[iv]) if 0 <= iv < len(frame.v) else []
        if len(cols):
            rows[iv] = [cols[0], cols[-1]]
    levels = [np.median(evidence[iv, a:b + 1][ok[iv, a:b + 1]]) for iv, (a, b) in rows.items() if ok[iv, a:b + 1].any()]
    if len(rows) < 2 or not np.median(levels) > 0:
        return result
    threshold = params.walk * np.median(levels)
    reach, step = max(1, round(0.25 * spacing / MOSAIC_PX_M)), spacing / MOSAIC_PX_M
    for side in (-1, 1):
        edge = min(rows) if side < 0 else max(rows)
        while True:
            a, b = rows[edge]
            centre = int(round(edge + side * step))
            candidates = [iv for iv in range(centre - reach, centre + reach + 1) if half <= iv < len(frame.v) - half]
            if not candidates:
                break
            best = max(candidates, key=lambda iv: np.median(np.where(ok[iv, a:b + 1], evidence[iv, a:b + 1], -np.inf)))
            if (ok[best, a:b + 1] & (evidence[best, a:b + 1] > threshold)).mean() < params.new_row_share:
                # or vine green on the lattice: green along the row, much less half a spacing to either side (not a verge)
                best = max(candidates, key=lambda iv: green[iv, a:b + 1].mean())
                row, flank = green[best, a:b + 1].mean(), 0.5 * (green[best - half, a:b + 1] + green[best + half, a:b + 1]).mean()
                if not params.side_green or row < params.side_green or flank > params.side_flank * row or ok[best, a:b + 1].mean() < params.new_row_share:
                    break
            rows[best] = [a, b]
            edge = best
    gap = params.gap_m / MOSAIC_PX_M
    for iv, extent in rows.items():
        good, passable = ok[iv] & (evidence[iv] > threshold), ok[iv] | usable[iv]
        for end, direction in ((1, 1), (0, -1)):
            last = j = extent[end]
            while 0 <= j + direction < len(frame.u) and abs(j + direction - last) <= gap and passable[j + direction]:
                j += direction
                if good[j]:
                    last = j
            extent[end] = last
    order = sorted(rows)
    v = frame.v[order]
    (s1, i1), (s2, i2) = (_end_line(frame.u[[rows[iv][end] for iv in order]], v, params.square_deg) for end in (0, 1))
    lo, hi = v.min(), v.max()
    corners = [(i1 + s1 * lo, lo), (i2 + s2 * lo, lo), (i2 + s2 * hi, hi), (i1 + s1 * hi, hi)]
    walked = Polygon(list(zip(*frame.world(*zip(*corners)))))
    if not walked.is_valid or walked.area < quad.area:
        return result
    return walked, [LineString(list(zip(*frame.world([frame.u[0], frame.u[-1]], [c, c])))) for c in v]


def _fill_parcels(fitted: list, excess: _Excess, roads, parcels: list, params: PlotParams) -> list:
    """Takes in the rest of each cadastral parcel a plot already mostly covers when that rest carries the plot's own
    rows (same angle and spacing, on the same row lattice), and continues the lattice into it, each new outer row on its
    ExG peak.
    A parcel is ownership, not planting, so it never creates a plot and only extends one where the rows agree."""
    tree, out = STRtree(parcels), []
    for polygon, angle, spacing, axes in fitted:
        origin = np.asarray(polygon.centroid.coords[0])
        own = _phasor(excess, polygon, angle, spacing, origin)
        normal = np.array([-np.sin(np.radians(angle)), np.cos(np.radians(angle))])
        across = np.asarray(polygon.exterior.coords) @ normal
        rests = []
        for k in tree.query(polygon):
            parcel = parcels[k]
            share = parcel.intersection(polygon).area / parcel.area
            if share < min(params.parcel_share, params.parcel_along or 1.0):
                continue
            for rest in getattr(parcel.difference(polygon), "geoms", [parcel.difference(polygon)]):
                if rest.geom_type != "Polygon" or rest.area < 20:
                    continue
                # below parcel_share, only the plot's own rows carried on along the rows (a strip's parcel), not new rows
                side = np.asarray(rest.exterior.coords) @ normal
                if share < params.parcel_share and (side.min() < across.min() - 0.5 * spacing or side.max() > across.max() + 0.5 * spacing):
                    continue
                wave = _phasor(excess, rest, angle, spacing, origin)
                shift = abs((np.angle(wave) - np.angle(own) + np.pi) % (2 * np.pi) - np.pi) / (2 * np.pi)
                if abs(wave) >= max(params.parcel_gate * abs(own), params.min_wave_exg) and shift <= params.parcel_phase:
                    rests.append(rest)
        if rests:
            grown = _largest(unary_union([polygon, *rests]).buffer(0.01).buffer(-0.01).difference(roads.buffer(params.road_setback_m)))
            if grown.area > polygon.area:
                along, normal = np.array([np.cos(np.radians(angle)), np.sin(np.radians(angle))]), np.array([-np.sin(np.radians(angle)), np.cos(np.radians(angle))])
                v = sorted(np.asarray(a.coords[0]) @ normal for a in axes)
                ring = np.asarray(grown.exterior.coords) @ normal
                centre, reach = np.asarray(grown.centroid.coords[0]), np.hypot(*np.subtract(grown.bounds[2:], grown.bounds[:2]))
                line = lambda c: LineString([centre + (c - centre @ normal) * normal - reach * along, centre + (c - centre @ normal) * normal + reach * along])
                for side, limit in ((-1, ring.min()), (1, ring.max())):
                    c = v[0] if side < 0 else v[-1]
                    # each new outer row is the ExG peak within 0.3 spacings of where the lattice predicts it
                    while side * (limit - c) > spacing:
                        inside = _longest(line(c + side * spacing).intersection(grown))
                        if inside.length < params.min_row_m:
                            break
                        points = np.array([inside.interpolate(d).coords[0] for d in np.arange(0, inside.length, 0.2)])
                        offsets = np.arange(-0.3, 0.3 + 1e-9, 0.05) * spacing
                        c += side * spacing + offsets[int(np.argmax([excess.at(*(points + o * normal).T).mean() for o in offsets]))]
                        v = [c] + v if side < 0 else v + [c]
                axes = [line(c) for c in v]
                polygon = grown
        out.append((polygon, angle, spacing, axes))
    return out


def _merge(fitted: list, excess: _Excess, roads, params: PlotParams) -> list:
    """Joins two fits of one planting that a tree or a weak stretch split: same angle and spacing, rows on one lattice
    (a half-spacing shift is another planting), under `merge_m` apart with no road between, and filling at least 80%
    of their joint quadrilateral (so an L-shaped pair stays two plots)."""
    fitted = list(fitted)
    for i in range(len(fitted)):
        j = i + 1
        while i < len(fitted) and j < len(fitted):
            (pa, aa, sa, xa), (pb, ab, sb, xb) = fitted[i], fitted[j]
            result = None
            if abs((aa - ab + 90) % 180 - 90) <= 1.5 and abs(sa - sb) <= 0.05 * sa and pa.distance(pb) <= params.merge_m:
                normal = np.array([-np.sin(np.radians(aa)), np.cos(np.radians(aa))])
                offset = (np.median([np.asarray(x.coords[0]) @ normal for x in xb]) - np.asarray(xa[0].coords[0]) @ normal) % sa
                union = unary_union([pa, pb])
                bridge = union.convex_hull.difference(union)
                if min(offset, sa - offset) <= 0.2 * sa and not bridge.intersection(roads).area > 0.1 * bridge.area:
                    result = _quadrilateral(excess, union.convex_hull, aa, sa, params)
            if result is None or pa.area + pb.area < 0.8 * result[0].area:
                j += 1
                continue
            fitted[i] = (_largest(_onto_roads(result[0], roads, params)), aa, sa, result[1])
            del fitted[j]
            j = i + 1
    return fitted


def _onto_roads(quad: Polygon, roads, params: PlotParams) -> Polygon:
    """Moves each edge that has a road within `road_reach_m` outward, then clips at the road less the setback."""
    ring = list(shapely.remove_repeated_points(quad, 1e-6).exterior.coords)[:-1]
    if len(ring) < 3:
        return quad.difference(roads.buffer(params.road_setback_m))
    if quad.exterior.is_ccw is False:
        ring = ring[::-1]
    lines = []
    for a, b in zip(ring, ring[1:] + ring[:1]):
        a, b = np.asarray(a), np.asarray(b)
        normal = np.array([b[1] - a[1], a[0] - b[0]]) / np.hypot(*(b - a))  # outward for a counter-clockwise ring
        strip = Polygon([a, b, b + normal * params.road_reach_m, a + normal * params.road_reach_m])
        shift = params.road_reach_m if strip.intersection(roads).area > 0.1 * strip.area else 0.0
        lines.append((a + normal * shift, b - a))
    corners = []
    for (p1, d1), (p2, d2) in zip(lines[-1:] + lines[:-1], lines):
        det = d1[0] * -d2[1] + d2[0] * d1[1]
        if abs(det) < 1e-9:
            return quad.difference(roads.buffer(params.road_setback_m))
        t = ((p2 - p1)[0] * -d2[1] + d2[0] * (p2 - p1)[1]) / det
        corners.append(p1 + t * d1)
    moved = Polygon(corners)
    if not moved.is_valid:
        moved = quad
    return moved.difference(roads.buffer(params.road_setback_m))


def _longest(geometry) -> LineString:
    parts = [part for part in getattr(geometry, "geoms", [geometry]) if part.geom_type == "LineString"]
    return max(parts, key=lambda part: part.length) if parts else LineString()


def _largest(geometry):
    parts = [part for part in getattr(geometry, "geoms", [geometry]) if part.geom_type == "Polygon"]
    return max(parts, key=lambda part: part.area) if parts else Polygon()


def _bin_gap(bins: np.ndarray, k: int) -> np.ndarray:
    gap = np.abs(bins - k) % 12
    return np.minimum(gap, 12 - gap)


def _regions(layers: Layers, blocked: np.ndarray, params: PlotParams) -> list[Polygon]:
    """Seed regions of one row direction each, as polygons on the layer grid."""
    usable = layers.valid & ~blocked
    seeds = (layers.vine_over_orchard > params.ratio_min) & usable
    seeds = ndimage.binary_opening(seeds, iterations=max(1, round(params.opening_m / LAYER_PX_M)))
    labels, count = ndimage.label(seeds)
    sizes = ndimage.sum(seeds, labels, range(1, count + 1)) * LAYER_PX_M**2
    bins = np.round(np.nan_to_num(layers.row_angle) / 15).astype(int) % 12

    boxes, regions = ndimage.find_objects(labels), []
    for label in np.flatnonzero(sizes >= params.min_seed_m2) + 1:
        rows, cols = boxes[label - 1]
        seed = labels[rows, cols] == label
        histogram = np.bincount(bins[rows, cols][seed], minlength=12)
        smooth = histogram + np.roll(histogram, 1) + np.roll(histogram, -1)
        modes = [k for k in np.argsort(-smooth) if smooth[k] >= params.mode_share * seed.sum()]
        kept: list[int] = []
        for k in modes:
            if all(min(abs(k - j) % 12, 12 - abs(k - j) % 12) * 15 >= params.split_deg for j in kept):
                kept.append(k)
        distance = np.stack([_bin_gap(bins[rows, cols], k) for k in kept])
        for i, k in enumerate(kept):
            pieces, n = ndimage.label(seed & (distance.argmin(0) == i))
            for piece in range(1, n + 1):
                mask = pieces == piece
                if mask.sum() * LAYER_PX_M**2 < params.min_seed_m2:
                    continue
                window = layers.transform * Affine.translation(cols.start, rows.start)
                polygons = [shape(g) for g, value in shapes(mask.astype(np.uint8), mask=mask, transform=window) if value]
                regions.append(_largest(unary_union(polygons)))
    return regions


def detect_plots(params: PlotParams = PlotParams(), data_dir: Path = DATA_DIR, layers: Layers | None = None,
                 excess: _Excess | None = None) -> list[dict[str, Any]]:
    layers = layers or load_layers(data_dir)
    excess = excess or load_excess(data_dir)
    roads = exclusions(data_dir)
    blocked = rasterize([roads], out_shape=layers.valid.shape, transform=layers.transform).astype(bool)
    usable, t = (layers.valid & ~blocked).astype(np.uint8), layers.transform
    usable_at = lambda x, y: ndimage.map_coordinates(usable, [(t.f - y) / LAYER_PX_M - 0.5, (x - t.c) / LAYER_PX_M - 0.5], order=0, cval=0).astype(bool)

    def fit(regions: list[Polygon], strict: bool = False, near: list | None = None) -> list:
        out = []
        for region in regions:
            angle, spacing = _row_angle(excess, region)
            # a weak piece beside a plot of nearly its lattice is that plot's weedy part: fit it on the plot's lattice
            for polygon, a, s, _ in near or []:
                if spacing and region.distance(polygon) < params.block_m and abs((a - angle + 90) % 180 - 90) <= params.snap_deg and abs(s - spacing) <= 0.08 * s:
                    angle, spacing = a, s
                    break
            if not spacing or spacing > params.max_spacing_m or _wave(excess, region, angle, spacing) < params.min_wave_exg:
                continue
            result = _walk(excess, region, angle, spacing, usable_at, params)
            if result is None:
                continue
            polygon = _largest(_onto_roads(result[0], roads, params))
            # weaker seeds need a few rows and some area
            if strict and (len(result[1]) < params.strip_min_rows or polygon.area < params.strip_min_m2):
                continue
            out.append((polygon, angle, spacing, result[1]))
        return out

    fitted = fit(_regions(layers, blocked, params))
    if params.strip_ratio:
        # second pass, only away from the first pass's plots, so it adds plots and never reshapes one (merges aside)
        clear = [polygon.buffer(params.strip_clear_m) for polygon, *_ in fitted if not polygon.is_empty]
        taken = rasterize(clear, out_shape=layers.valid.shape, transform=layers.transform).astype(bool) if clear else np.zeros_like(blocked)
        fitted += fit(_regions(load_layers(data_dir, tophat_m=STRIP_TOPHAT_M), blocked | taken, replace(params, ratio_min=params.strip_ratio)), strict=True, near=fitted)
    fitted = _merge(fitted, excess, roads, params) if params.merge_m else fitted
    if params.parcel_share:
        fitted = _fill_parcels(fitted, excess, roads, load_parcels(), params)
    # a bigger plot keeps an overlap, and a fit mostly inside an accepted plot is the same plot found twice
    plots, taken = [], Polygon()
    for polygon, angle, spacing, axes in sorted(fitted, key=lambda item: -item[0].area):
        if polygon.intersection(taken).area > 0.5 * polygon.area:
            continue
        polygon = _largest(polygon.difference(taken))
        if polygon.area < params.min_area_m2:
            continue
        taken = taken.union(polygon)
        # the outer axes lie on the plot's sides, so clip with a 5 cm tolerance to keep them
        rows = [_longest(axis.intersection(polygon.buffer(0.05, join_style="mitre"))) for axis in axes]
        plots.append((polygon, angle, spacing, [row for row in rows if row.length >= params.min_row_m]))
    blocks = _blocks(plots, excess, roads, load_parcels() if params.track_m else [], params)
    features = []
    for (polygon, angle, spacing, rows), (block_id, pattern_id) in zip(plots, _ids([polygon for polygon, *_ in plots], blocks)):
        # vineyard_id is the pattern here, so rows, inter-rows and canopies work per row lattice; assign_blocks sets the block's
        features.append({"type": "Feature", "geometry": mapping(polygon),
                         "properties": {"label": "block", "vineyard_id": pattern_id, "pattern_id": pattern_id, "block_id": block_id,
                                        "area_m2": round(polygon.area), "row_angle": round(angle, 1), "row_spacing_m": round(spacing, 2),
                                        "rows": len(rows), "params": asdict(params)}})
        features += [{"type": "Feature", "geometry": mapping(row),
                      "properties": {"label": "row", "vineyard_id": pattern_id, "pattern_id": pattern_id, "block_id": block_id,
                                     "row_id": f"{pattern_id}-R{k:03d}", "row_structure": "regular", "length_m": round(row.length, 2)}}
                     for k, row in enumerate(rows, start=1)]
    return features


def _blocks(plots: list, excess: _Excess, roads, parcels: list[Polygon], params: PlotParams) -> list[int]:
    """The organizers' block rule: plantings that touch or lie under `block_m` apart are one block, unless their
    shortest link crosses a road (passages, forbidden zones) or a track, where `track_m` of the link lies outside every
    cadastral parcel (an unregistered strip between parcels; #28/#29's track is not in the passages). Two plots of one
    row lattice (angle within 3 degrees, spacing within 8%) up to `lattice_m` apart are one block too when the ground
    between them carries their rows (across-row ExG wave 0.4-1.5 times the larger plot's, in phase within 0.2 spacing):
    weedy rows are vineyard ground, not a gap (#6); a stronger wave is a tree or shrub line (#15/#16, 2-8 times). A
    parcel only separates, it never joins. Returns a block index per plot (the index of one of its members)."""
    polygons = [polygon for polygon, *_ in plots]
    parent = list(range(len(polygons)))
    root = lambda i: i if parent[i] == i else root(parent[i])
    covered = unary_union(parcels) if parcels else None
    tree = STRtree(polygons)
    for i, a in enumerate(polygons):
        for j in tree.query(a.buffer(max(params.block_m, params.lattice_m))):
            b, distance = polygons[j], a.distance(polygons[j])
            if j <= i or distance >= max(params.block_m, params.lattice_m):
                continue
            link = shapely.shortest_line(a, b)
            if link.length > 0.01 and (link.buffer(0.3).intersects(roads) or covered is not None and link.difference(covered).length >= params.track_m):
                continue
            if distance >= params.block_m and not _one_lattice(plots[i], plots[j], excess, roads):
                continue
            parent[root(j)] = root(i)
    return [root(i) for i in range(len(polygons))]


def _one_lattice(a: tuple, b: tuple, excess: _Excess, roads, gate: float = 0.4) -> bool:
    """Two plots on one row lattice whose rows run on over the ground between them."""
    (pa, aa, sa, _), (pb, ab, sb, _) = sorted((a, b), key=lambda plot: -plot[0].area)
    if abs((aa - ab + 90) % 180 - 90) > 3 or abs(sa - sb) > 0.08 * sa:
        return False
    between = _largest(unary_union([pa, pb]).convex_hull.difference(pa.buffer(0.5)).difference(pb.buffer(0.5)).difference(roads))
    if between.area < 20:
        return False
    origin = np.asarray(pa.centroid.coords[0])
    own, wave = _phasor(excess, pa, aa, sa, origin), _phasor(excess, between, aa, sa, origin)
    shift = abs((np.angle(wave) - np.angle(own) + np.pi) % (2 * np.pi) - np.pi) / (2 * np.pi)
    return gate * abs(own) <= abs(wave) <= 1.5 * abs(own) and shift <= 0.2


def vine_evidence(features: list[dict[str, Any]], excess: _Excess) -> dict[str, dict[str, Any]]:
    """Per row pattern, how much its rows look like vine rows, from the pipeline's own `row` and canopy (`vineyard`)
    features: canopy pieces per 10 m of row, share of row length under canopy, share of rows with any canopy, canopy
    share of the plot, piece length along the row (median, p90, share over 2.5 m: a vine is at most about 2.5 m) and
    width, spread of the spacing between piece centres along a row (IQR / median), spread of the row spacing, and
    the mosaic ExG on the rows against halfway between them. Works on detect_plots output (keyed by pattern) and on a
    finished prediction (rows split per tile, blocks with `patterns`)."""
    patterns = {}
    for f in features:
        p = f["properties"]
        if p["label"] == "block":
            for q in p.get("patterns") or [p]:
                patterns[q.get("pattern_id", p["vineyard_id"])] = (p.get("block_id", p["vineyard_id"]), q["row_angle"], q["row_spacing_m"], q["area_m2"])
    key = lambda p: p.get("pattern_id") or p.get("vineyard_id", "")
    lines: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    canopies: dict[str, list] = defaultdict(list)
    for f in features:
        p = f["properties"]
        if p["label"] == "row":
            lines[key(p)][p["row_id"]].append(np.asarray(f["geometry"]["coordinates"], float))
        elif p["label"] == "vineyard":
            canopies[key(p)].append(shape(f["geometry"]))
    out = {}
    for pattern, (block_id, angle, spacing, area) in patterns.items():
        d = np.array([np.cos(np.radians(angle)), np.sin(np.radians(angle))])
        n = np.array([-d[1], d[0]])
        rows = np.array([[(xy @ d).min(), (xy @ d).max(), (xy @ n).mean()] for xy in (np.vstack(p) for p in lines[pattern].values())]).reshape(-1, 3)
        pieces = np.array(canopies[pattern], dtype=object)
        local = shapely.transform(pieces, lambda xy: np.column_stack([xy @ d, xy @ n])) if len(pieces) else pieces
        b = shapely.bounds(local).reshape(-1, 4)
        cu, cv, length, width = (b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2, b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]
        near = np.abs(cv[:, None] - rows[:, 2]) + np.where((cu[:, None] >= rows[:, 0] - 1) & (cu[:, None] <= rows[:, 1] + 1), 0, np.inf)
        owner = np.where(near.min(1) <= 0.6, near.argmin(1), -1) if len(rows) and len(cu) else np.full(len(cu), -1)
        covered, gaps, on, off = np.zeros(len(rows)), [], [], []
        for k, (u0, u1, v) in enumerate(rows):
            mine = owner == k
            spans = sorted(zip(np.maximum(b[mine, 0], u0), np.minimum(b[mine, 2], u1)))
            end = -np.inf
            for s, e in spans:
                covered[k] += max(0.0, e - max(s, end))
                end = max(end, e)
            centres = np.sort(cu[mine & (length <= 2.5)])
            gaps += [g for g in np.diff(centres) if g <= 6.0]
            u = np.arange(u0, u1, 0.4)
            at = lambda offset: excess.at(*(u[:, None] * d + (v + offset) * n).T)
            on.append(at(0.0))
            off += [at(spacing / 2), at(-spacing / 2)]
        row_m = float((rows[:, 1] - rows[:, 0]).sum())
        across = np.diff(np.sort(rows[:, 2]))
        steps = np.maximum(np.round(across / spacing), 1)
        on_exg, off_exg = (float(np.mean(np.concatenate(x))) if x and len(np.concatenate(x)) else 0.0 for x in (on, off))
        q = lambda values, p: float(np.percentile(values, p)) if len(values) else 0.0
        out[pattern] = {
            "block_id": block_id, "area_m2": float(area), "spacing_m": float(spacing), "rows": len(rows), "row_m": row_m,
            "canopies": len(pieces), "per_10m": 10 * len(pieces) / max(row_m, 1.0), "cover": float(covered.sum() / max(row_m, 1.0)),
            "rows_any": float((covered >= 0.05 * np.maximum(rows[:, 1] - rows[:, 0], 1.0)).mean()) if len(rows) else 0.0,
            "canopy_share": float(shapely.area(pieces).sum() / max(area, 1.0)) if len(pieces) else 0.0,
            "len_med": q(length, 50), "len_p90": q(length, 90), "long_share": float((length > 2.5).mean()) if len(length) else 0.0,
            "width_med": q(width, 50), "gap_med": q(gaps, 50), "gap_cv": (q(gaps, 75) - q(gaps, 25)) / max(q(gaps, 50), 1e-3),
            "spacing_cv": float(np.std(across / steps) / spacing) if len(across) > 1 else 0.0,
            "on_exg": on_exg, "off_exg": off_exg, "contrast": on_exg - off_exg,
        }
    return out


def looks_like_vineyard(evidence: dict[str, Any], params: PlotParams = PlotParams()) -> tuple[bool, str]:
    """The verification rule on `vine_evidence`: vine rows carry canopy along a good share of their length and are
    greener than the ground halfway between them; where the median canopy piece is longer than a vine the rows are
    hedges, and a vine hedge is continuous (scrub leaves long, patchy pieces). Returns (keep, reason)."""
    if params.verify_cover and evidence["cover"] < params.verify_cover:
        return False, f"canopy on {evidence['cover']:.0%} of the row length"
    if params.verify_contrast and evidence["contrast"] < params.verify_contrast:
        return False, f"rows no greener than between them (ExG {evidence['contrast']:+.3f})"
    if params.verify_vine_m and evidence["len_med"] > params.verify_vine_m and evidence["cover"] < params.verify_hedge:
        return False, f"patchy pieces longer than a vine (median {evidence['len_med']:.1f} m, cover {evidence['cover']:.0%})"
    return True, ""


def verify_plots(found: list[dict[str, Any]], canopies: list[dict[str, Any]], params: PlotParams = PlotParams(), data_dir: Path = DATA_DIR,
                 excess: _Excess | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Drops the blocks of `detect_plots` output none of whose row patterns has canopy that looks like vine rows
    (`looks_like_vineyard`), with their rows and canopies, then regroups the rest into blocks and renames them (`_blocks`,
    `_ids`), so a dropped plot leaves no gap in the names. Run it after the canopy and before the
    inter-rows and waste, which then never see a dropped plot. Returns (plots and rows, canopies, dropped `block`
    features with their evidence and reason); every kept `block` carries its `vine_evidence`."""
    excess = excess or load_excess(data_dir)
    evidence = vine_evidence(found + canopies, excess)
    checked = {pattern: looks_like_vineyard(e, params) for pattern, e in evidence.items()}
    # a weak pattern in a block with a vine-like one is that vineyard's weedy part (#10's grassy north-west rows), not a false plot
    verified = {e["block_id"] for pattern, e in evidence.items() if checked[pattern][0]}
    checked = {pattern: (True, "") if evidence[pattern]["block_id"] in verified else result for pattern, result in checked.items()}
    key = lambda p: p.get("pattern_id") or p.get("vineyard_id", "")
    rounded = lambda e: {k: round(v, 3) if isinstance(v, float) else v for k, v in e.items() if k != "block_id"}
    blocks = [f for f in found if f["properties"]["label"] == "block"]
    kept = [f for f in blocks if checked[key(f["properties"])][0]]
    dropped = [{**f, "properties": {**f["properties"], "vine_evidence": rounded(evidence[key(f["properties"])]), "dropped": checked[key(f["properties"])][1]}}
               for f in blocks if not checked[key(f["properties"])][0]]
    plots = [(shape(f["geometry"]), f["properties"]["row_angle"], f["properties"]["row_spacing_m"], []) for f in kept]
    groups = _blocks(plots, excess, exclusions(data_dir), load_parcels() if params.track_m else [], params)
    names = dict(zip((key(f["properties"]) for f in kept), _ids([polygon for polygon, *_ in plots], groups)))
    out: tuple[list, list] = ([], [])
    for side, features in enumerate((found, canopies)):
        for f in features:
            p, old = f["properties"], key(f["properties"])
            if old not in names:
                if old not in checked:
                    out[side].append(f)
                continue
            block, pattern = names[old]
            q = {**p, "vineyard_id": pattern, "pattern_id": pattern, **({"block_id": block} if "block_id" in p else {})}
            if "row_id" in p:
                q["row_id"] = pattern + p["row_id"][len(old):]
            if p["label"] == "block":
                q["vine_evidence"] = rounded(evidence[old])
            out[side].append({**f, "properties": q})
    return out[0], out[1], dropped


def _tile_id(point) -> str:
    return f"V{int((GRID_TOP - point.y) // TILE_M):02d}-{int((point.x - GRID_LEFT) // TILE_M):02d}"


def _ids(polygons: list[Polygon], blocks: list[int]) -> list[tuple[str, str]]:
    """(block id, pattern id) per plot, by position so that a change elsewhere renumbers nothing: a block is named by
    the tile its centroid lies in (`V<r>-<c>`, `-2`... west to east when two share a tile), and a block of several
    row patterns names them a, b... north to south. A single pattern is named as its block."""
    members: dict[int, list[int]] = defaultdict(list)
    for i, block in enumerate(blocks):
        members[block].append(i)
    anchors = {block: unary_union([polygons[i] for i in group]).centroid for block, group in members.items()}
    by_tile: dict[str, list[int]] = defaultdict(list)
    for block in sorted(anchors, key=lambda block: anchors[block].x):
        by_tile[_tile_id(anchors[block])].append(block)
    names = {block: name if k == 0 else f"{name}-{k + 1}" for name, group in by_tile.items() for k, block in enumerate(group)}
    out = {}
    for block, group in members.items():
        group = sorted(group, key=lambda i: (-polygons[i].centroid.y, polygons[i].centroid.x))
        for k, i in enumerate(group):
            out[i] = (names[block], names[block] if len(group) == 1 else names[block] + "abcdefghijklmnopqrstuvwxyz"[k])
    return [out[i] for i in range(len(polygons))]


def assign_blocks(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gives every feature of a row pattern its block's vineyard_id (the pattern stays in `pattern_id`, which rows and
    inter-rows group by) and joins a block's pattern polygons into one `block` feature, with each pattern's angle,
    spacing and rows under `patterns`. Run it last, on the whole prediction: detect_plots keys everything by pattern so
    that canopies, inter-rows and attributes are fitted per row lattice. Idempotent."""
    block_of = {f["properties"]["vineyard_id"]: f["properties"]["block_id"] for f in features if f["properties"]["label"] == "block"}
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    out = []
    for feature in features:
        properties = feature["properties"]
        if properties["label"] == "block":
            members[properties["block_id"]].append(feature)
        elif properties.get("vineyard_id") in block_of:
            out.append({**feature, "properties": {**properties, "vineyard_id": block_of[properties["vineyard_id"]],
                                                  "pattern_id": properties.get("pattern_id", properties["vineyard_id"])}})
        else:
            out.append(feature)
    blocks = []
    for block_id, group in sorted(members.items()):
        patterns = [pattern for f in group for pattern in f["properties"].get("patterns") or [
            {key: f["properties"][key] for key in ("pattern_id", "row_angle", "row_spacing_m", "rows", "area_m2", "vine_evidence") if key in f["properties"]}]]
        main = max(patterns, key=lambda pattern: pattern["area_m2"])
        geometry = unary_union([shape(f["geometry"]) for f in group])
        properties = {key: value for key, value in group[0]["properties"].items() if key != "pattern_id"}
        blocks.append({"type": "Feature", "geometry": group[0]["geometry"] if len(group) == 1 else mapping(geometry), "properties": {
            **properties, "vineyard_id": block_id, "block_id": block_id, "area_m2": round(geometry.area), "row_angle": main["row_angle"],
            "row_spacing_m": main["row_spacing_m"], "rows": sum(pattern["rows"] for pattern in patterns), "patterns": patterns,
            **({"vine_evidence": main["vine_evidence"]} if "vine_evidence" in main else {})}})
    return blocks + out


if __name__ == "__main__":
    started = time.perf_counter()
    found = detect_plots()
    blocks = [f for f in found if f["properties"]["label"] == "block"]
    print(f"{len(blocks)} plots, {sum(f['properties']['area_m2'] for f in blocks) / 1e4:.2f} ha, {len(found) - len(blocks)} rows in {time.perf_counter() - started:.1f} s")
