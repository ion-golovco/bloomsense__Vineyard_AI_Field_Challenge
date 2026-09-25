"""Vineyard plots and their row axes, with no training (docs/RESEARCH.md, "Plot variables").
Plots are seeded where vine-spacing row energy beats orchard-spacing energy, split where the row direction
changes, and fitted in each plot's own row frame: the sides on the outermost row axes, the ends square to
the rows unless clearly oblique, and edges that face a road moved onto it. Row axes are the across-row ExG
profile peaks, which matched the reference rows to a median 0.04-0.06 m on both reference tiles."""

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
    reach = max(1, int(0.35 * spacing / MOSAIC_PX_M))
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
    fitted = []
    for region in _regions(layers, blocked, params):
        angle, spacing = _row_angle(excess, region)
        if not spacing:
            continue
        result = _quadrilateral(excess, region, angle, spacing, params)
        if result is None:
            continue
        quad, axes = result
        fitted.append((_largest(_onto_roads(quad, roads, params)), angle, spacing, axes))
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
