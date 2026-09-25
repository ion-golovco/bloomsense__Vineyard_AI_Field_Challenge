"""Vine canopy polygons (CVAT label `vineyard`) with no training, at the tiles' native 0.025 m.

Method, per tile and per predicted plot (`marcaj.plots.detect_plots` blocks and rows):
1. ExG = (2g - r - b) / (r + g + b), Gaussian blur `smooth_m`, green = ExG > `exg_min`.
2. Each row axis is re-fitted to the tile's green pixels within `refine_m` (a straight line through the
   median across-row position per metre), and dropped when its +-`tube_m` band is less than `row_contrast`
   times greener than 0.6-1.0 m beside it (grassed inter-rows otherwise pass as rows).
3. Canopy = green inside the +-`tube_m` tube of the kept axes, holes filled (CVAT polygons hold none),
   8-connected components, split across the row at necks narrower than `split_neck` of the thinner
   side's peak width (the rules split touching plants "where it visibly narrows"), components under
   `min_area_m2` dropped (the rules leave out leaf clumps under about 0.2 m2), polygonised and simplified.
Each polygon takes its plot's `vineyard_id`; nothing is predicted outside predicted plots.

Measured by `research/probes/canopy_probe.py` on the two organizer tiles (a sanity check, not a holdout;
the defaults were chosen with these tiles in view): judge canopy score 0.668 (union IoU 0.664, instance
F1 0.674); 0.605 with the predicted rows as given; 0.728 with the reference rows (diagnostic). About 0.7 s
per vineyard tile on an M4 Pro, one core."""

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
    exg_min: float = 0.110     # best global canopy threshold on both reference tiles
    tube_m: float = 0.30       # half-width of the band around each row axis
    smooth_m: float = 0.05     # Gaussian sigma on ExG before the threshold; 0 turns it off
    min_area_m2: float = 0.20  # the rules leave out leaf clumps under about 0.2 m2 (weeds)
    simplify_m: float = 0.02
    split_neck: float = 0.3    # split where the canopy narrows below this share of the thinner side's peak; 0 = off
    split_piece_m: float = 0.3  # ...leaving at least this much canopy along the row on each side
    refine_m: float = 0.5      # re-fit each axis on the tile to green pixels within this distance; 0 = off
    otsu: bool = False         # threshold at Otsu's split of ExG inside the tube instead of `exg_min`
    row_contrast: float = 1.3  # drop an axis whose tube is less than this times greener than its flanks; 0 = off


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


def fit_axis(axis: LineString, green: np.ndarray, transform: Affine, params: CanopyParams, bin_m: float = 1.0) -> tuple[LineString, float]:
    """The axis moved onto the tile's canopy, and its row contrast.

    The move is a straight line through the median across-row position of the green pixels within `refine_m`,
    per `bin_m` along the row, capped at `refine_m`; with fewer than 3 bins only the offset is refitted, with
    none the axis is kept. The contrast is the green share within `tube_m` of the moved axis over the share
    0.6-1.0 m to either side: a vine row is greener on its axis than beside it, a grassed inter-row is not."""
    rows, cols = np.nonzero(tube([axis], transform, green.shape, FLANK_M[1]))
    (x0, y0), (x1, y1) = axis.coords[0], axis.coords[-1]
    length = np.hypot(x1 - x0, y1 - y0)
    if not len(rows) or not length:
        return axis, 0.0
    d = np.array([x1 - x0, y1 - y0]) / length
    dx, dy = transform.c + (cols + 0.5) * transform.a - x0, transform.f + (rows + 0.5) * transform.e - y0
    u, v, g = dx * d[0] + dy * d[1], -dx * d[1] + dy * d[0], green[rows, cols]
    slope = offset = 0.0
    near = g & (np.abs(v) <= params.refine_m)
    bins = np.floor(u[near] / bin_m).astype(int)
    keys, counts = np.unique(bins, return_counts=True)
    keys = keys[counts >= 20]  # a bin needs 125 cm2 of green
    if params.refine_m and len(keys):
        centres = np.array([np.median(v[near][bins == k]) for k in keys])
        slope, offset = np.polyfit((keys + 0.5) * bin_m, centres, 1) if len(keys) >= 3 else (0.0, float(np.median(centres)))
    shift = np.clip(offset + slope * np.array([0.0, length]), -params.refine_m, params.refine_m)
    v = np.abs(v - np.interp(u, [0.0, length], shift))
    flank = g[(v >= FLANK_M[0]) & (v <= FLANK_M[1])]
    contrast = g[v <= params.tube_m].mean() / max(flank.mean() if len(flank) else 0.0, 1e-3)
    moved = LineString([(x0 - d[1] * shift[0], y0 + d[0] * shift[0]), (x1 - d[1] * shift[1], y1 + d[0] * shift[1])])
    return moved, float(contrast)


def canopy_mask(rgb: np.ndarray, transform: Affine, axes: list[LineString], params: CanopyParams) -> np.ndarray:
    excess, valid = exg(rgb)
    if params.smooth_m:
        excess = ndimage.gaussian_filter(excess, params.smooth_m / PIXEL_M)
    green = (excess > params.exg_min) & valid
    bounds = box(*array_bounds(*green.shape, transform))
    clipped = [axis.intersection(bounds) for axis in axes]
    fitted = [fit_axis(LineString(c.coords), green, transform, params) for c in clipped if c.geom_type == "LineString" and c.length > 0]
    band = tube([axis for axis, contrast in fitted if contrast >= params.row_contrast], transform, green.shape, params.tube_m)
    if params.otsu and band.any():
        green = (excess > otsu(excess[band & valid])) & valid
    return ndimage.binary_fill_holes(green & band)


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
    polygons = []
    for pieces in parts.values():
        fixed = make_valid(max(pieces, key=lambda p: p.area).simplify(params.simplify_m))
        best = max((p for p in getattr(fixed, "geoms", [fixed]) if p.geom_type == "Polygon"), key=lambda p: p.area, default=None)
        if best is not None and best.area >= params.min_area_m2:
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
                  rgb: tuple[np.ndarray, Affine] | None = None) -> list[dict[str, Any]]:
    """Canopy features on one tile from every plot whose axes cross it; a tile with none is not read."""
    crossing = [(plot, [axis for axis in plot.axes if axis.intersects(tile.bounds)]) for plot in rows]
    crossing = [(plot, axes) for plot, axes in crossing if axes]
    if not crossing:
        return []
    image, transform = rgb or read_rgb(tile)
    return [{"type": "Feature", "geometry": mapping(polygon),
             "properties": {"label": "vineyard", "source": "prediction", "vineyard_id": plot.vineyard_id}}
            for plot, axes in crossing
            for polygon in canopy_polygons(canopy_mask(image, transform, axes, params), transform, plot.angle_deg, params)]
