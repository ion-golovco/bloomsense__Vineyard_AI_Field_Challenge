"""Vineyard plots and their row axes, with no training (docs/RESEARCH.md, "Plot variables"; research/notes/plots.md).
Plots are seeded where vine-spacing row energy beats orchard-spacing energy, split where the row direction changes,
dropped when the across-row ExG wave is too weak to be green vines (tractor lines on bare fields), and fitted in each
plot's own row frame: the sides on the outermost row axes, the ends square to the rows unless clearly oblique. The seed's
fit is then walked outward row by row and along each row while the row stands out from its inter-rows, so a bare-soil
core takes in its grassy continuation; a new outer row also counts when its tube is green and its flanks much less so
(young or weak vines the tube-minus-flank contrast misses). Fits of one planting that a tree split are joined, and
edges that face a road are moved onto it. Row axes are the across-row ExG profile peaks, which matched the reference
rows to a median 0.04-0.06 m on both reference tiles. Optionally (`parcel_share` 0.7; off by default until the
cadastre licence is settled, research/notes/fields.md), a plot then takes in the rest of a cadastral parcel it already
covers 70% of when that rest shows the plot's own rows. Parcels come from the public cadastre WMS of I.P. Cadastrul
Bunurilor Imobile (https://map.cadastru.md/geoserver/ows, layer w_cbi:cad_terenuri, fetched 26 Sep 2026 by
`marcaj.cadastre`) and are read from data/raw/external/cadastre/parcels_32635.geojson; with `parcel_share` set, a
missing file raises FileNotFoundError.

Against the 35 hand-drawn vineyard outlines (`judge.plot_scores`, north tunes, south validates): north F1 0.81 at
IoU 0.5, 0.60 at 0.75, median best IoU 0.76, area IoU 0.81; south 0.65 / 0.35 / 0.70 / 0.68. Without the green
outer-row test 0.81 / 0.54 / 0.76 / 0.81 and 0.65 / 0.35 / 0.70 / 0.66 (research/notes/fields.md); the parcel fill was
measured on that base at 0.81 / 0.54 / 0.76 / 0.82 and 0.76 / 0.41 / 0.70 / 0.70. The frozen detector before the walk
scored 0.59 / 0.44 / 0.65 / 0.79 and 0.47 / 0.12 / 0.52 / 0.55. About 22 s."""

import json
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
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
    min_wave_exg: float = 0.005   # seeds with a weaker across-row ExG wave are dropped: tractor lines on bare fields 0.0005-0.0015, vineyards 0.011+
    walk: float = 0.5             # a row continues where its tube-minus-flank ExG is at least this share of the plot's median; 0 = off
    walk_reach_m: float = 40.0    # how far beyond its seed a plot may be walked
    gap_m: float = 3.0            # a row may lapse this long (missing vines); a track, headland or tree clump stops it
    new_row_share: float = 0.6    # a new outer row needs evidence along this share of its neighbour
    side_green: float = 0.3       # ...or, vine green on the lattice, this share of its +-0.3 m tube green (mosaic ExG > 0.08); 0 = off
    side_flank: float = 0.7       # ...with the tubes half a spacing to either side at most this share as green (not a verge)
    merge_m: float = 5.0          # fits of one planting under this far apart join (the 5 m block rule); 0 = off
    parcel_share: float = 0.0     # a cadastral parcel the plot already covers this share of is filled... (0.7 measured; 0 = off until the cadastre licence is settled)
    parcel_gate: float = 0.4      # ...where the rest shows the plot's rows: across-row ExG wave at its angle and spacing >= this share of the plot's
    parcel_phase: float = 0.35    # ...on the plot's own row lattice, within this share of a spacing


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
    power[(frequency < 1 / VINE_BAND_M[1]) | (frequency > 1 / VINE_BAND_M[0])] = 0
    k = int(np.argmax(power))
    return float(power[k] / len(profile)), float(1 / frequency[k]) if frequency[k] else 0.0


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
        rests = []
        for k in tree.query(polygon):
            parcel = parcels[k]
            if parcel.intersection(polygon).area < params.parcel_share * parcel.area:
                continue
            rest = _largest(parcel.difference(polygon))
            if rest.area < 20:
                continue
            wave = _phasor(excess, rest, angle, spacing, origin)
            shift = abs((np.angle(wave) - np.angle(own) + np.pi) % (2 * np.pi) - np.pi) / (2 * np.pi)
            if abs(wave) >= params.parcel_gate * abs(own) and shift <= params.parcel_phase:
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
    ring = list(quad.exterior.coords)[:-1]
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
    fitted = []
    for region in _regions(layers, blocked, params):
        angle, spacing = _row_angle(excess, region)
        if not spacing or _wave(excess, region, angle, spacing) < params.min_wave_exg:
            continue
        result = _walk(excess, region, angle, spacing, usable_at, params)
        if result is None:
            continue
        quad, axes = result
        fitted.append((_largest(_onto_roads(quad, roads, params)), angle, spacing, axes))
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
    features = []
    for number, (polygon, angle, spacing, rows) in enumerate(plots, start=1):
        plot_id = f"P{number:02d}"
        features.append({"type": "Feature", "geometry": mapping(polygon),
                         "properties": {"label": "block", "vineyard_id": plot_id, "area_m2": round(polygon.area), "row_angle": round(angle, 1),
                                        "row_spacing_m": round(spacing, 2), "rows": len(rows), "params": asdict(params)}})
        features += [{"type": "Feature", "geometry": mapping(row),
                      "properties": {"label": "row", "vineyard_id": plot_id, "row_id": f"{plot_id}-R{k:03d}", "row_structure": "regular",
                                     "length_m": round(row.length, 2)}}
                     for k, row in enumerate(rows, start=1)]
    return features


if __name__ == "__main__":
    started = time.perf_counter()
    found = detect_plots()
    blocks = [f for f in found if f["properties"]["label"] == "block"]
    print(f"{len(blocks)} plots, {sum(f['properties']['area_m2'] for f in blocks) / 1e4:.2f} ha, {len(found) - len(blocks)} rows in {time.perf_counter() - started:.1f} s")
