"""Vine canopy polygons (CVAT label `vineyard`) with no training, at the tiles' native 0.025 m.

Method, per tile and per predicted plot (`marcaj.plots.detect_plots` blocks and rows):
1. Excess green 2g - r - b in 8-bit DN, Gaussian blur `smooth_m`, green = above `green_dn`. Not the
   normalised ExG (2g - r - b) / (r + g + b): dividing by brightness lifts dark shadow and noise and damps
   sunlit leaves, and the rules trace "the leaves, not their shadow". In the +-0.3 m tube of the reference
   rows the DN index has AUC 0.971 / 0.975 against 0.914 / 0.946 for ExG, and its best threshold is the
   same on both tiles (27 / 28, ExG 0.11 / 0.08), so one global value holds on bright and dark soil.
2. Each row axis is re-fitted to the tile's green pixels within `refine_m`, then within `refine2_m`
   (a straight line through the median across-row position per metre; 1.4-1.9 cm median from the
   reference rows). Rows of one plot are evenly spaced, so of two axes closer than `row_gap` times the
   median spacing, the one with less green in its tube is dropped: in grassed plots the row detector
   also puts axes on the inter-rows, halfway between rows. Then an axis is dropped when the mean excess
   green within `tube_m` is under `row_value` times that 0.6-1.0 m beside it: a row of vines stands out
   from grass, an axis run over a grass verge or a weed-covered block does not (true rows in grass
   1.34-2.5, the verge and weed axes looked at 0.79-1.04). The old green-share contrast (`row_contrast`)
   is off: in grass it scored true rows 1.09-1.16 and the inter-row axes 0.97-1.28.
3. Canopy = green inside the +-`tube_m` tube of the kept axes, closed with a disk of `close_m` (the rules
   trace leaves "within about 10 cm", so gaps under 10 cm inside one plant close; the median gap between
   pieces of one reference plant was 7 cm), holes filled (CVAT polygons hold none), 8-connected
   components, split across the row at necks narrower than `split_neck` of the thinner side's peak width
   (the rules split touching plants "where it visibly narrows"), components under `min_area_m2` dropped
   (the rules leave out leaf clumps under about 0.2 m2), polygonised, simplified and moved `inset_m`
   inwards (matched polygons were 10-12% larger than the reference, +1.2-1.5 cm on the outline).
Each polygon takes its plot's `vineyard_id`; nothing is predicted outside predicted plots.

The reference canopies are cut at exactly 0.30 m from their row line (the farthest vertex of 90% / 77% of
them lies within 1 cm of it), which is why `tube_m` is 0.3 and why row placement bounds the score.

Measured by `research/probes/canopy_probe.py` on the two organizer tiles (a sanity check, not a holdout;
`green_dn`, `close_m`, `inset_m`, `row_gap` and `row_value` were chosen with these tiles in view, among 2-4
values each; research/notes/canopy_rules.md): judge canopy score 0.855 (union IoU 0.822, instance F1 0.905),
402 / 259 canopies against 399 / 251, 535 m2 against 536 m2, from 0.668 (0.664, 0.674) with ExG. With the
reference rows 0.897 (diagnostic). About 0.45 s per vineyard tile on an M4 Pro, one core (0.7-0.9 s on the
dense reference tiles)."""

from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine, array_bounds
from scipy import ndimage
from shapely import make_valid
from shapely.geometry import LineString, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry

from marcaj.layers import exg
from marcaj.tiles import PIXEL_M, Tile

EIGHT = np.ones((3, 3), bool)
FLANK_M = (0.6, 1.0)  # beside the canopy, inside the inter-row (rows are 2.0-3.15 m apart)


@dataclass(frozen=True)
class CanopyParams:
    green_dn: float = 25.0     # green = 2g - r - b above this (8-bit DN); 0 = normalised ExG above `exg_min` instead
    exg_min: float = 0.110     # the previous normalised-ExG threshold (canopy 0.668), used when `green_dn` is 0
    tube_m: float = 0.30       # half-width of the band around each row axis; the reference cuts canopies at 0.30 m
    smooth_m: float = 0.05     # Gaussian sigma on the index before the threshold; 0 turns it off
    min_area_m2: float = 0.20  # the rules leave out leaf clumps under about 0.2 m2 (weeds)
    simplify_m: float = 0.02
    split_neck: float = 0.3    # split where the canopy narrows below this share of the thinner side's peak; 0 = off
    split_piece_m: float = 0.3  # ...leaving at least this much canopy along the row on each side
    refine_m: float = 0.5      # re-fit each axis on the tile to green pixels within this distance; 0 = off
    refine2_m: float = 0.3     # ...then again within this distance of the first fit; 0 = off
    otsu: bool = False         # threshold at Otsu's split of the index inside the tube instead
    row_contrast: float = 0.0  # drop an axis whose tube's green share is under this times its flanks'; 0 = off
    row_value: float = 1.2     # drop an axis whose tube's mean excess green is under this times its flanks'; 0 = off
    row_gap: float = 0.7       # of two axes closer than this times the median spacing, drop the less green; 0 = off
    close_m: float = 0.05      # closing radius on the canopy in the tube; 0 = off
    inset_m: float = 0.01      # polygons moved this far inwards


def excess_green(rgb: np.ndarray, params: CanopyParams) -> tuple[np.ndarray, np.ndarray]:
    """The canopy index (2g - r - b in DN, or ExG when `green_dn` is 0), smoothed, and the valid-pixel mask."""
    excess, valid = exg(rgb)
    if params.green_dn:
        red, green, blue = rgb.astype(np.float32)
        excess = 2 * green - red - blue
    if params.smooth_m:
        excess = ndimage.gaussian_filter(excess, params.smooth_m / PIXEL_M)
    return excess, valid


def green_mask(rgb: np.ndarray, params: CanopyParams) -> np.ndarray:
    excess, valid = excess_green(rgb, params)
    return (excess > (params.green_dn or params.exg_min)) & valid


def read_rgb(tile: Tile) -> tuple[np.ndarray, Affine]:
    with rasterio.open(tile.path) as source:
        return source.read(), source.transform


def _along(transform: Affine, shape_: tuple[int, int], angle_deg: float) -> np.ndarray:
    """Pixel-centre coordinate along the rows, in metres from the raster corner (float32 cannot hold UTM)."""
    rows, cols = np.indices(shape_, dtype=np.float32)
    a = np.radians(angle_deg)
    return (cols + 0.5) * transform.a * np.cos(a) + (rows + 0.5) * transform.e * np.sin(a)


def _split(labels: np.ndarray, count: int, along: np.ndarray, params: CanopyParams) -> tuple[np.ndarray, int]:
    """Cuts each component across the row where its along-row width profile has a neck deeper than
    `split_neck` of the smaller neighbouring peak. Deepest neck first, then recursively on each side."""
    step = 2 * PIXEL_M
    out, next_label = np.zeros_like(labels), 1
    for index, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None:
            continue
        mask = labels[window] == index
        u = along[window][mask]
        bins = ((u - u.min()) / step).astype(int)
        width = ndimage.gaussian_filter1d(np.bincount(bins).astype(float), 1.0)
        cuts: list[int] = []
        pending = [(0, len(width))]
        least = int(params.split_piece_m / step)
        while pending:
            lo, hi = pending.pop()
            best = None
            for i in range(lo + least, hi - least):
                if width[i] <= width[i - 1] and width[i] <= width[i + 1]:
                    depth = width[i] / min(width[lo:i].max(), width[i + 1:hi].max())
                    if depth < params.split_neck and (best is None or depth < best[0]):
                        best = (depth, i)
            if best:
                cuts.append(best[1])
                pending += [(lo, best[1]), (best[1], hi)]
        piece = np.searchsorted(np.sort(cuts), bins, side="right")
        region = out[window]
        region[mask] = next_label + piece
        next_label += len(cuts) + 1
    return out, next_label - 1


def tube(axes: list[LineString], transform: Affine, shape_: tuple[int, int], half_width_m: float) -> np.ndarray:
    if not axes:
        return np.zeros(shape_, bool)
    return rasterize([axis.buffer(half_width_m) for axis in axes], out_shape=shape_, transform=transform).astype(bool)


def _pixels(geometry: BaseGeometry, transform: Affine, shape_: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Row and column indices of the pixels whose centres lie in `geometry`, rasterised in its bounding window only."""
    left, bottom, right, top = geometry.bounds
    (c0, r0), (c1, r1) = ~transform * (left, top), ~transform * (right, bottom)
    r0, r1 = max(int(np.floor(min(r0, r1))), 0), min(int(np.ceil(max(r0, r1))) + 1, shape_[0])
    c0, c1 = max(int(np.floor(min(c0, c1))), 0), min(int(np.ceil(max(c0, c1))) + 1, shape_[1])
    if r1 <= r0 or c1 <= c0:
        return np.zeros(0, int), np.zeros(0, int)
    rows, cols = np.nonzero(rasterize([geometry], out_shape=(r1 - r0, c1 - c0), transform=transform * Affine.translation(c0, r0)))
    return rows + r0, cols + c0


def _line_shift(u: np.ndarray, v: np.ndarray, g: np.ndarray, limit: float, length: float, bin_m: float) -> np.ndarray:
    """Across-row shift at both ends of a straight line through the median `v` of the green pixels within `limit`,
    per `bin_m` along the row, capped at `limit`; with fewer than 3 bins only an offset, with none zero."""
    near = g & (np.abs(v) <= limit)
    bins = np.floor(u[near] / bin_m).astype(int)
    keys, counts = np.unique(bins, return_counts=True)
    keys = keys[counts >= 20]  # a bin needs 125 cm2 of green
    if not len(keys):
        return np.zeros(2)
    centres = np.array([np.median(v[near][bins == k]) for k in keys])
    slope, offset = np.polyfit((keys + 0.5) * bin_m, centres, 1) if len(keys) >= 3 else (0.0, float(np.median(centres)))
    return np.clip(offset + slope * np.array([0.0, length]), -limit, limit)


def fit_axis(axis: LineString, green: np.ndarray, transform: Affine, params: CanopyParams, bin_m: float = 1.0) -> tuple[LineString, float]:
    """The axis moved onto the tile's canopy, and its row contrast.

    The move is a straight line through the median across-row position of the green pixels within `refine_m`,
    per `bin_m` along the row, capped at `refine_m`, then again within `refine2_m` of that line; with fewer than
    3 bins only the offset is refitted, with none the axis is kept. The contrast is the green share within
    `tube_m` of the moved axis over the share 0.6-1.0 m to either side (of the pixels within 1 m of the input)."""
    rows, cols = _pixels(axis.buffer(FLANK_M[1]), transform, green.shape)
    (x0, y0), (x1, y1) = axis.coords[0], axis.coords[-1]
    length = np.hypot(x1 - x0, y1 - y0)
    if not len(rows) or not length:
        return axis, 0.0
    d = np.array([x1 - x0, y1 - y0]) / length
    dx, dy = transform.c + (cols + 0.5) * transform.a - x0, transform.f + (rows + 0.5) * transform.e - y0
    u, v, g = dx * d[0] + dy * d[1], -dx * d[1] + dy * d[0], green[rows, cols]
    shift = _line_shift(u, v, g, params.refine_m, length, bin_m) if params.refine_m else np.zeros(2)
    if params.refine_m and params.refine2_m:
        shift = shift + _line_shift(u, v - np.interp(u, [0.0, length], shift), g, params.refine2_m, length, bin_m)
    v = np.abs(v - np.interp(u, [0.0, length], shift))
    flank = g[(v >= FLANK_M[0]) & (v <= FLANK_M[1])]
    contrast = g[v <= params.tube_m].mean() / max(flank.mean() if len(flank) else 0.0, 1e-3)
    moved = LineString([(x0 - d[1] * shift[0], y0 + d[0] * shift[0]), (x1 - d[1] * shift[1], y1 + d[0] * shift[1])])
    return moved, float(contrast)


def _across(axes: list[LineString]) -> list[tuple[float, LineString]]:
    """Each axis with its midpoint's offset across the rows (along the normal of the longest), sorted."""
    longest = max(axes, key=lambda axis: axis.length)
    (x0, y0), (x1, y1) = longest.coords[0], longest.coords[-1]
    normal = np.array([y0 - y1, x1 - x0]) / longest.length
    return sorted(((float(np.asarray(axis.interpolate(0.5, normalized=True).coords[0]) @ normal), axis) for axis in axes), key=lambda p: p[0])


def row_spacing(axes: list[LineString]) -> float:
    """Median distance between neighbouring axes, across the rows; 0 with fewer than 2."""
    return float(np.median(np.diff([offset for offset, _ in _across(axes)]))) if len(axes) >= 2 else 0.0


def _drop_interrows(axes: list[LineString], green: np.ndarray, transform: Affine, params: CanopyParams,
                    spacing_m: float = 0.0) -> list[LineString]:
    """Of two axes closer than `row_gap` times the row spacing (`spacing_m`, else the median over `axes`), drops the
    one with less green in its tube, closest pair first, until none is left."""
    spacing = spacing_m or (row_spacing(axes) if len(axes) >= 3 else 0.0)
    if len(axes) < 2 or not spacing:
        return axes
    placed = _across(axes)
    share = lambda axis: float(green[_pixels(axis.buffer(params.tube_m), transform, green.shape)].mean())
    while len(placed) >= 2:
        gaps = np.diff([offset for offset, _ in placed])
        k = int(np.argmin(gaps))
        if gaps[k] >= params.row_gap * spacing:
            break
        placed.pop(k if share(placed[k][1]) < share(placed[k + 1][1]) else k + 1)
    return [axis for _, axis in placed]


def value_contrast(axis: LineString, excess: np.ndarray, transform: Affine, params: CanopyParams) -> float:
    """Mean excess green (clipped at 0) within `tube_m` of the axis over that 0.6-1.0 m to either side."""
    rows, cols = _pixels(axis.buffer(FLANK_M[1]), transform, excess.shape)
    (x0, y0), (x1, y1) = axis.coords[0], axis.coords[-1]
    if not len(rows) or not axis.length:
        return 0.0
    d = np.array([x1 - x0, y1 - y0]) / axis.length
    dx, dy = transform.c + (cols + 0.5) * transform.a - x0, transform.f + (rows + 0.5) * transform.e - y0
    v, value = np.abs(-dx * d[1] + dy * d[0]), np.clip(excess[rows, cols], 0, None)
    flank = value[(v >= FLANK_M[0]) & (v <= FLANK_M[1])]
    return float(value[v <= params.tube_m].mean() / max(flank.mean() if len(flank) else 0.0, 1e-3))


def kept_axes(axes: list[LineString], green: np.ndarray, transform: Affine, params: CanopyParams,
              spacing_m: float = 0.0, excess: np.ndarray | None = None) -> list[LineString]:
    """The axes of one plot on one tile, clipped to it, re-fitted on `green` and filtered: the canopy tube's centre lines.
    `spacing_m` is the plot's row spacing (`row_spacing` of all its axes); 0 takes the median over these axes.
    `excess` (`excess_green`) enables the `row_value` test."""
    bounds = box(*array_bounds(*green.shape, transform))
    clipped = [axis.intersection(bounds) for axis in axes]
    fitted = [fit_axis(LineString(c.coords), green, transform, params) for c in clipped if c.geom_type == "LineString" and c.length > 0]
    kept = [axis for axis, contrast in fitted if contrast >= params.row_contrast]
    kept = _drop_interrows(kept, green, transform, params, spacing_m) if params.row_gap else kept
    if params.row_value and excess is not None:
        kept = [axis for axis in kept if value_contrast(axis, excess, transform, params) >= params.row_value]
    return kept


def close(mask: np.ndarray, radius_m: float) -> np.ndarray:
    """Morphological closing with a disk. Outside the raster counts as canopy, so a plant cut by the tile edge
    still reaches it (the rules trace such a plant up to the edge)."""
    r = int(round(radius_m / PIXEL_M))
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    disk = x * x + y * y <= r * r
    return ndimage.binary_erosion(ndimage.binary_dilation(mask, disk), disk, border_value=1)


def canopy_mask(rgb: np.ndarray, transform: Affine, axes: list[LineString], params: CanopyParams,
                green: np.ndarray | None = None, spacing_m: float = 0.0) -> np.ndarray:
    """`green` replaces the colour threshold with another per-pixel canopy mask (a network's), same shape as a band;
    `spacing_m` goes to `kept_axes`."""
    excess, valid = excess_green(rgb, params)
    green = ((excess > (params.green_dn or params.exg_min)) if green is None else green) & valid
    band = tube(kept_axes(axes, green, transform, params, spacing_m, excess), transform, green.shape, params.tube_m)
    if params.otsu and band.any():
        green = (excess > otsu(excess[band & valid])) & valid
    mask = green & band
    if params.close_m:
        mask = close(mask, params.close_m) & band
    return ndimage.binary_fill_holes(mask)


def otsu(values: np.ndarray, bins: int = 256) -> float:
    """The threshold that maximises the between-class variance of `values`."""
    counts, edges = np.histogram(values, bins)
    centres = (edges[:-1] + edges[1:]) / 2
    weight = np.cumsum(counts)
    mean = np.cumsum(counts * centres)
    total, grand = weight[-1], mean[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (grand * weight - total * mean) ** 2 / (weight * (total - weight))
    return float(centres[np.nanargmax(between[:-1])])


def canopy_polygons(mask: np.ndarray, transform: Affine, angle_deg: float, params: CanopyParams) -> list[Polygon]:
    labels, count = ndimage.label(mask, EIGHT)
    if params.split_neck and count:
        labels, count = _split(labels, count, _along(transform, mask.shape, angle_deg), params)
    labels[np.bincount(labels.ravel())[labels] * PIXEL_M**2 < params.min_area_m2] = 0
    parts: dict[int, list[BaseGeometry]] = {}
    for geometry, value in shapes(labels.astype(np.int32), mask=labels > 0, connectivity=8, transform=transform):
        parts.setdefault(int(value), []).append(shape(geometry))
    largest = lambda g: max((p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"), key=lambda p: p.area, default=None)
    polygons = []
    for pieces in parts.values():
        best = largest(make_valid(max(pieces, key=lambda p: p.area).simplify(params.simplify_m)))
        if best is not None and best.area >= params.min_area_m2:
            best = largest(make_valid(Polygon(best.exterior).buffer(-params.inset_m, join_style="mitre"))) if params.inset_m else best
            if best is not None:
                polygons.append(Polygon(best.exterior))
    return polygons


@dataclass(frozen=True)
class RowSet:
    """One plot's rows: its id, row angle (degrees from east) and axis lines in EPSG:32635."""
    vineyard_id: str
    angle_deg: float
    axes: list[LineString]


def plot_rows(plots: list[dict[str, Any]]) -> list[RowSet]:
    """Each plot's rows from the `block` and `row` features of `marcaj.plots.detect_plots`."""
    axes: dict[str, list[LineString]] = {}
    for feature in plots:
        if feature["properties"]["label"] == "row":
            axes.setdefault(feature["properties"]["vineyard_id"], []).append(shape(feature["geometry"]))
    return [RowSet(f["properties"]["vineyard_id"], f["properties"]["row_angle"], axes.get(f["properties"]["vineyard_id"], []))
            for f in plots if f["properties"]["label"] == "block"]


def tile_canopies(tile: Tile, rows: list[RowSet], params: CanopyParams = CanopyParams(),
                  rgb: tuple[np.ndarray, Affine] | None = None, green: np.ndarray | None = None) -> list[dict[str, Any]]:
    """Canopy features on one tile from every plot whose axes cross it; a tile with none is not read.
    `green` is passed through to `canopy_mask`."""
    crossing = [(plot, [axis for axis in plot.axes if axis.intersects(tile.bounds)]) for plot in rows]
    crossing = [(plot, axes) for plot, axes in crossing if axes]
    if not crossing:
        return []
    image, transform = rgb or read_rgb(tile)
    return [{"type": "Feature", "geometry": mapping(polygon),
             "properties": {"label": "vineyard", "source": "prediction", "vineyard_id": plot.vineyard_id}}
            for plot, axes in crossing
            for polygon in canopy_polygons(canopy_mask(image, transform, axes, params, green, row_spacing(plot.axes)), transform, plot.angle_deg, params)]
