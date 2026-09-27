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
3. Canopy = green inside the +-`tube_m` tube of the kept axes. Along each kept axis, a `weak_window_m` stretch whose
   tube is under `weak_share` green also takes 2g - r - b above `weak_dn`: young, weak or dark-leaved vines fall under
   the full threshold, and most >= 5 m gap candidates on rows with visible vines were such stretches (a piece mostly of
   these pixels longer than `weak_max_m` is grass or soil and loses them; vines are at most about 2.5 m). Then closed
   with a disk of `close_m` (the rules
   trace leaves "within about 10 cm", so gaps under 10 cm inside one plant close; the median gap between
   pieces of one reference plant was 7 cm), holes filled (CVAT polygons hold none), 8-connected
   components; a piece longer than `strip_m` along the row whose mean g - r is above `strip_gr` is dropped
   (a tube filled with grass along a verge, a weedy block or a grassed row: vine leaves here are yellow-
   green, mean g - r 1.3-9.6 DN over the reference canopies including the 56 m strips, grass 16.6-27.9).
   Components are split across the row at necks narrower than `split_neck` of the thinner side's peak
   width (the rules split touching plants "where it visibly narrows"), pieces under `min_area_m2` dropped
   (the rules leave out leaf clumps under about 0.2 m2), except in a young block: where the median piece
   of at least `young_m2` (one plot on one tile, at least `young_pieces` of them) is under `min_area_m2`,
   the pieces are the plants ("each plant is its own polygon, however small") and are kept down to
   `young_m2`; the same test per row (`row_young`) catches weak or replanted rows in mature blocks. Polygonised,
   simplified and moved `inset_m` inwards (matched polygons were 10-12% larger than the reference, +1.2-1.5 cm on
   the outline), keeping every part a self-touching pixel ring or the inset splits off (`all_parts`).
Each polygon takes its plot's `vineyard_id`; nothing is predicted outside predicted plots.

The reference canopies are cut at exactly 0.30 m from their row line (the farthest vertex of 90% / 77% of
them lies within 1 cm of it), which is why `tube_m` is 0.3 and why row placement bounds the score.

Measured by `research/probes/canopy_probe.py` on the two organizer tiles (a sanity check, not a holdout;
`green_dn`, `close_m`, `inset_m`, `row_gap` and `row_value` were chosen with these tiles in view, among 2-4
values each; `strip_gr` and `young_m2` from site views, and neither changes these tiles; research/notes/
canopy_rules.md): judge canopy score 0.855 (union IoU 0.821, instance F1 0.905), 402 / 260 canopies against
399 / 251, from 0.668 (0.664, 0.674) with ExG; 0.854 through `canopy_net` "and". With the reference rows
0.897 (diagnostic). Site-wide (131 tiles, as predict.py calls it) the strip rule removes 35 canopies / 380 m2
and the young-block rule adds 275 (238 in P09). About 0.5 s per vineyard tile on an M4 Pro, one core
(0.7-0.9 s on the dense reference tiles), plus the network.

Recall rules of 26 September (`all_parts`, `weak_*`, `row_young`; research/probes/canopy_recall_*.py, rows frozen from
the uploaded predictions, through `canopy_net` "and"): judge canopy 0.8538 -> 0.8512 (IoU 0.8193 -> 0.8177, F1 0.9056
-> 0.9015; judge points 44.88 -> 44.86 of 50, the canopy-area count rising 0.956 -> 0.982); site canopies 13,609 ->
14,235; >= 5 m gap candidates on predicted rows 175 -> 116 (1,672 -> 1,043 m); the organizers' own gaps on the two tiles
still 7/10 found. About 0.6 s per tile.

`refit_rows` (predict.py, after the canopy) moves the exported row axes onto these canopies. On the pattern-keyed plots
of 26 September 16:00 (679 rows, 606 moved), the lattice sat a median 7 cm from the canopy (p95 0.18 m at the row centre,
0.28 m at an end; 0.08 m from the reference rows, now 0.06 m). Canopy outside +-0.3 m of the rows 1,992 -> 1,082 of
13,928 m2, canopy inside inter-rows 1,792 -> 972 m2, matched inter-row IoU median 0.935 / 0.896 -> 0.957 / 0.938 on
r021 / r006, >= 5 m gap route targets 114 -> 103; judge 44.86 -> 44.87 (research/probes/rows_refit_*.py). About 4 s for the site.

Round three, 26 September evening (research/probes/canopy_v3_*.py, frozen v4 rows, full user labels; research/notes/
canopy_rules.md): `grass_ratio` (an outermost axis whose canopy colour is paler and smoother than the plot's median axis,
standing in green flanks, is a verge of grass or weeds) removes 75 pieces / 88 m2 on 12 tiles, all verge grass in the
sheets; judge unchanged (0.8563 on the refit rows), user "not a vine" 10 -> 12 of 20 removed, 0 of 130 vines lost. Dark
foliage: CIELAB a* < -8 is a strict subset of 2g - r - b > 25 here, and the pixels the index misses (2g - r - b 15-25,
a* -8..-2) overlap shadow and soil; the organizers' own row cover on r021_c012 (field V21-13) is 0.72, ours 0.73. Every
recall variant (`grow_*` hysteresis, finer weak stretches, `tuft_m2`, Otsu) costs judge points and fills about as many
real gaps as vines-present gaps, so they stay off.

v6, 27 September (the team's corrected Marcaj rows as input; research/probes/v6_canopy_*.py, work/v6/canopy/STATUS.md):
the refit weighs each metre by its green pixel count (`refine_weighted`): axes 1.44 -> 0.90 cm (median) from the
organizers' rows, judge canopy 0.861 -> 0.865 on the team's rows, 0.857 -> 0.862 on the v5 rows (the organizers' own rows
without refit give 0.892). Rows drawn in pieces joined at tile edges clip to slivers along the edge, which `kept_axes`
now drops (it dropped the whole clipped row before). Every other default is still the best single value on these rows."""

from dataclasses import dataclass
from typing import Any

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine, array_bounds
from scipy import ndimage
import shapely
from shapely import make_valid
from shapely.geometry import LineString, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from marcaj.layers import exg
from marcaj.tiles import PIXEL_M, Tile

EIGHT = np.ones((3, 3), bool)
SLIVER_M2 = 0.01  # polygon parts under this (16 px) are outline slivers
FLANK_M = (0.6, 1.0)  # beside the canopy, inside the inter-row (rows are 2.0-3.15 m apart)
VEG_RING_M = (0.35, 0.9)  # the ground around a piece that `in_vegetation` looks at


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
    refine_weighted: bool = True  # ...each metre's median weighted by its green pixel count
    otsu: bool = False         # threshold at Otsu's split of the index inside the tube instead
    row_contrast: float = 0.0  # drop an axis whose tube's green share is under this times its flanks'; 0 = off
    row_value: float = 1.2     # drop an axis whose tube's mean excess green is under this times its flanks'; 0 = off
    row_gap: float = 0.7       # of two axes closer than this times the median spacing, drop the less green; 0 = off
    close_m: float = 0.05      # closing radius on the canopy in the tube; 0 = off
    inset_m: float = 0.01      # polygons moved this far inwards
    young_m2: float = 0.05     # where the median piece of at least this size is under `min_area_m2`, the plants are
    young_pieces: int = 10     # ...young and small (with at least this many pieces): keep pieces down to `young_m2`; 0 = off
    strip_m: float = 8.0       # a canopy piece longer than this along the row...
    strip_gr: float = 15.0     # ...whose mean g - r (DN) is above this is grass or weeds, not vines; 0 = off
    weak_dn: float = 15.0      # along a kept axis, a stretch whose tube is under `weak_share` colour takes 2g - r - b above this; 0 = off
    weak_share: float = 0.15   # ...(share of the tube's pixels above `green_dn`, per stretch of `weak_window_m`)
    weak_window_m: float = 4.0 # about two plants at the ~2 m planting distance
    weak_max_m: float = 4.0    # ...but a piece mostly of such pixels longer than this along the row is grass; 0 = off
    row_young: int = 6         # the young rule per row: a row with at least this many pieces whose median is small; 0 = off
    all_parts: bool = True     # keep every polygon part of a label (not only the largest) and don't let the inset split one
    long_m: float = 0.0        # a piece longer than this along the row is split at necks under `long_neck` instead; 0 = off
    long_neck: float = 0.5
    veg_m: float = 3.0         # a piece longer than this whose mean g - r is above `veg_gr`, standing in ground that is over
    veg_gr: float = 10.0       # ...`veg_ring` green (2g - r - b above `green_dn`, 0.35-0.9 m around it) and bluish-green too
    veg_ring: float = 0.4      # ...(mean g - r of that green above `veg_ring_gr`), is cut from a tree, shrub or lush weeds; 0 = off
    veg_ring_gr: float = 8.5
    tuft_m2: float = 0.0       # a piece mostly of weak-stretch pixels (`weak_dn`) is kept down to this; 0 = off
    lab_a: float = 0.0         # also count as colour a pixel whose CIELAB a* (smoothed) is under this (green hue, any darkness); 0 = off
    grow_a: float = 0.0        # grow the canopy into tube pixels 8-connected to it whose a* is under this...; 0 = off
    grow_dn: float = 5.0       # ...and 2g - r - b above this (grey-brown shadow on soil has a* near 0 and 2g - r - b near 0)
    grow_core_m: float = 0.0   # ...within this distance of the axis only (bridges plants along the row, not their shaded flank); 0 = the tube
    grass_ratio: float = 0.9   # drop an axis whose canopy colour is both paler (mean 2g - r - b) and smoother (mean |grad g|) than
    grass_axes: int = 4        # ...this share of the median axis of its plot on the tile (at least `grass_axes` axes); 0 = off
    grass_flank: float = 1.5   # ...when it is an outermost axis and its flanks are over this times as green as the median's
    grass_flank_min: float = 0.15  # ...and over this share green (2g - r - b above `green_dn`)


def excess_green(rgb: np.ndarray, params: CanopyParams) -> tuple[np.ndarray, np.ndarray]:
    """The canopy index (2g - r - b in DN, or ExG when `green_dn` is 0), smoothed, and the valid-pixel mask."""
    excess, valid = exg(rgb)
    if params.green_dn:
        red, green, blue = rgb.astype(np.float32)
        excess = 2 * green - red - blue
    if params.smooth_m:
        excess = ndimage.gaussian_filter(excess, params.smooth_m / PIXEL_M)
    return excess, valid


def lab_a_star(rgb: np.ndarray, params: CanopyParams) -> np.ndarray:
    """CIELAB a* (D65) of 8-bit sRGB, smoothed like the index: green is negative, grey-brown shadow near 0."""
    c = rgb.astype(np.float32) / 255
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    x = (0.4124 * c[0] + 0.3576 * c[1] + 0.1805 * c[2]) / 0.9505
    y = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    f = lambda t: np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16 / 116)
    a = 500 * (f(x) - f(y))
    return ndimage.gaussian_filter(a, params.smooth_m / PIXEL_M) if params.smooth_m else a


def green_mask(rgb: np.ndarray, params: CanopyParams) -> np.ndarray:
    excess, valid = excess_green(rgb, params)
    mask = excess > (params.green_dn or params.exg_min)
    if params.lab_a:
        mask |= lab_a_star(rgb, params) < params.lab_a
    return mask & valid


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
            neck = params.long_neck if params.long_m and (hi - lo) * step > params.long_m else params.split_neck
            for i in range(lo + least, hi - least):
                if width[i] <= width[i - 1] and width[i] <= width[i + 1]:
                    depth = width[i] / min(width[lo:i].max(), width[i + 1:hi].max())
                    if depth < neck and (best is None or depth < best[0]):
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


def _line_shift(u: np.ndarray, v: np.ndarray, g: np.ndarray, limit: float, length: float, bin_m: float,
                weighted: bool = False) -> np.ndarray:
    """Across-row shift at both ends of a straight line through the median `v` of the green pixels within `limit`,
    per `bin_m` along the row, capped at `limit`; with fewer than 3 bins only an offset, with none zero. `weighted`
    weighs each bin by its green pixel count (a full plant counts more than a leaf tip or a weed at the band's edge)."""
    near = g & (np.abs(v) <= limit)
    bins = np.floor(u[near] / bin_m).astype(int)
    keys, counts = np.unique(bins, return_counts=True)
    keys, counts = keys[counts >= 20], counts[counts >= 20]  # a bin needs 125 cm2 of green
    if not len(keys):
        return np.zeros(2)
    centres = np.array([np.median(v[near][bins == k]) for k in keys])
    w = np.sqrt(counts) if weighted else None  # polyfit weighs residuals: sqrt(count) is a count-weighted least squares
    slope, offset = np.polyfit((keys + 0.5) * bin_m, centres, 1, w=w) if len(keys) >= 3 else (0.0, float(np.median(centres)))
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
    shift = _line_shift(u, v, g, params.refine_m, length, bin_m, params.refine_weighted) if params.refine_m else np.zeros(2)
    if params.refine_m and params.refine2_m:
        shift = shift + _line_shift(u, v - np.interp(u, [0.0, length], shift), g, params.refine2_m, length, bin_m, params.refine_weighted)
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
              spacing_m: float = 0.0, excess: np.ndarray | None = None, rgb: np.ndarray | None = None) -> list[LineString]:
    """The axes of one plot on one tile, clipped to it, re-fitted on `green` and filtered: the canopy tube's centre lines.
    `spacing_m` is the plot's row spacing (`row_spacing` of all its axes); 0 takes the median over these axes.
    `excess` (`excess_green`) enables the `row_value` test, with `rgb` also the `grass_ratio` test."""
    bounds = box(*array_bounds(*green.shape, transform))
    clipped = [axis.intersection(bounds) for axis in axes]
    # a row drawn in pieces joined at the tile edge clips to its line plus slivers along the edge: those go, the rest are chords
    edge = bounds.boundary.buffer(0.01)
    chords = [LineString([p.coords[0], p.coords[-1]]) for c in clipped for p in getattr(c, "geoms", [c])
              if p.geom_type == "LineString" and p.length > 0 and not p.within(edge)]
    fitted = [fit_axis(chord, green, transform, params) for chord in chords if chord.length > 0]
    kept = [axis for axis, contrast in fitted if contrast >= params.row_contrast]
    kept = _drop_interrows(kept, green, transform, params, spacing_m) if params.row_gap else kept
    if params.row_value and excess is not None:
        kept = [axis for axis in kept if value_contrast(axis, excess, transform, params) >= params.row_value]
    if params.grass_ratio and excess is not None and rgb is not None and len(kept) >= params.grass_axes:
        kept = _drop_grass_rows(kept, green, excess, rgb, transform, params)
    return kept


def _drop_grass_rows(axes: list[LineString], green: np.ndarray, excess: np.ndarray, rgb: np.ndarray, transform: Affine,
                     params: CanopyParams) -> list[LineString]:
    """Drops an outermost axis (first or last across the rows) whose canopy-colour pixels in the tube are both paler (mean
    2g - r - b) and smoother (mean |grad g|) than `grass_ratio` of the plot's median axis on the tile, and whose flanks
    (0.6-1.0 m) are over `grass_flank` times as green as the median axis's and over `grass_flank_min` green: the lattice ran on over a grassy or weedy
    verge. Vine canopy is bright yellow-green leaves and their shade; verge grass and weeds are pale and even, and green
    all around. In V19-11, V21-13 and V22-13 the two verge rows scored 0.72 / 0.68-0.76 of their block's median with
    flanks 2.0 / 2.9 times as green; the other 133 rows 0.91-1.06."""
    gy, gx = np.gradient(rgb[1].astype(np.float32))
    stats = []
    for axis in axes:
        rows, cols = _pixels(axis.buffer(FLANK_M[1], cap_style="flat"), transform, green.shape)
        (x0, y0), (x1, y1) = axis.coords[0], axis.coords[-1]
        d = np.array([x1 - x0, y1 - y0]) / axis.length
        v = np.abs(-(transform.c + (cols + 0.5) * transform.a - x0) * d[1] + (transform.f + (rows + 0.5) * transform.e - y0) * d[0])
        on = green[rows, cols] & (v <= params.tube_m)
        flank = green[rows, cols][(v >= FLANK_M[0]) & (v <= FLANK_M[1])]
        r, c = rows[on], cols[on]
        stats.append((float(excess[r, c].mean()), float(np.hypot(gx[r, c], gy[r, c]).mean()), float(flank.mean()) if len(flank) else 0.0)
                     if len(r) >= 320 else None)
    known = [s for s in stats if s]
    if len(known) < params.grass_axes:
        return axes
    ex, grad, side = (float(np.median([s[k] for s in known])) for k in range(3))
    order = [axis for _, axis in _across(axes)]
    edges = {id(order[0]), id(order[-1])}
    grass = lambda a, s: (s is not None and id(a) in edges and s[0] < params.grass_ratio * ex and s[1] < params.grass_ratio * grad
                          and s[2] > max(params.grass_flank * side, params.grass_flank_min))
    return [a for a, s in zip(axes, stats) if not grass(a, s)]


def close(mask: np.ndarray, radius_m: float) -> np.ndarray:
    """Morphological closing with a disk. Outside the raster counts as canopy, so a plant cut by the tile edge
    still reaches it (the rules trace such a plant up to the edge)."""
    r = int(round(radius_m / PIXEL_M))
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    disk = x * x + y * y <= r * r
    return ndimage.binary_erosion(ndimage.binary_dilation(mask, disk), disk, border_value=1)


def canopy_mask(rgb: np.ndarray, transform: Affine, axes: list[LineString], params: CanopyParams,
                green: np.ndarray | None = None, spacing_m: float = 0.0, with_weak: bool = False) -> Any:
    """`green` replaces the colour threshold with another per-pixel canopy mask (a network's), same shape as a band;
    `spacing_m` goes to `kept_axes`. `with_weak` also returns the weak-stretch pixels (`_weak`) for `canopy_polygons`."""
    excess, valid = excess_green(rgb, params)
    green = (green_mask(rgb, params) if green is None else green) & valid
    kept = kept_axes(axes, green, transform, params, spacing_m, excess, rgb)
    band = tube(kept, transform, green.shape, params.tube_m)
    if params.otsu and band.any():
        green = (excess > otsu(excess[band & valid])) & valid
    mask = green & band
    if params.grow_a:
        hue = lab_a_star(rgb, params)
        core = tube(kept, transform, green.shape, params.grow_core_m) if params.grow_core_m else band
        mask = ndimage.binary_propagation(mask, EIGHT, mask | (core & valid & (hue < params.grow_a) & (excess > params.grow_dn)))
    weak = _weak(mask, kept, green, excess, valid, transform, params) if params.weak_dn and params.green_dn else None
    if weak is not None:
        mask |= weak
    if params.close_m:
        mask = close(mask, params.close_m) & band
    mask = ndimage.binary_fill_holes(mask)
    if weak is not None and params.weak_max_m and kept:
        mask = _drop_weak_strips(mask, weak, kept, params)
    mask = _drop_grass_strips(mask, rgb, transform, axes, params) if params.strip_gr and axes else mask
    return (mask, weak if weak is not None else np.zeros_like(mask)) if with_weak else mask


def _weak(mask: np.ndarray, axes: list[LineString], green: np.ndarray, excess: np.ndarray, valid: np.ndarray,
          transform: Affine, params: CanopyParams) -> np.ndarray:
    """The pixels to add along each axis: per `weak_window_m` stretch whose tube is under `weak_share` canopy colour,
    those whose 2g - r - b is above `weak_dn` (a weak, young or dark-leaved stretch of row; the full threshold keeps
    shadow and weeds out elsewhere)."""
    out = np.zeros_like(mask)
    for axis in axes:
        rows, cols = _pixels(axis.buffer(params.tube_m, cap_style="flat"), transform, mask.shape)
        if not len(rows):
            continue
        (x0, y0), (x1, y1) = axis.coords[0], axis.coords[-1]
        d = np.array([x1 - x0, y1 - y0]) / axis.length
        u = (transform.c + (cols + 0.5) * transform.a - x0) * d[0] + (transform.f + (rows + 0.5) * transform.e - y0) * d[1]
        window = np.floor(u / params.weak_window_m).astype(int)
        window -= window.min()
        share = np.bincount(window, weights=green[rows, cols]) / np.maximum(np.bincount(window), 1)
        low = (share[window] < params.weak_share) & (excess[rows, cols] > params.weak_dn) & valid[rows, cols]
        out[rows[low], cols[low]] = True
    return out & ~mask


def _drop_weak_strips(mask: np.ndarray, weak: np.ndarray, axes: list[LineString], params: CanopyParams) -> np.ndarray:
    """Removes the weak pixels of 8-connected pieces that are mostly weak and longer than `weak_max_m` along the row:
    young or weak vines are separate small plants (at most about 2.5 m), a long faint strip is grass or soil along the tube."""
    labels, count = ndimage.label(mask, EIGHT)
    if not count:
        return mask
    longest = max(axes, key=lambda axis: axis.length)
    (x0, y0), (x1, y1) = longest.coords[0], longest.coords[-1]
    dx, dy = (x1 - x0) / longest.length, (y1 - y0) / longest.length
    rows, cols = np.nonzero(mask)
    lab = labels[rows, cols]
    index = np.arange(1, count + 1)
    u = (cols * dx - rows * dy) * PIXEL_M
    length = np.asarray(ndimage.maximum(u, lab, index)) - np.asarray(ndimage.minimum(u, lab, index))
    share = np.bincount(lab, weights=weak[rows, cols], minlength=count + 1)[1:] / np.maximum(np.bincount(lab, minlength=count + 1)[1:], 1)
    drop = np.concatenate([[False], (share > 0.5) & (length > params.weak_max_m)])
    return mask & ~(drop[labels] & weak)


def _drop_grass_strips(mask: np.ndarray, rgb: np.ndarray, transform: Affine, axes: list[LineString], params: CanopyParams) -> np.ndarray:
    """Removes 8-connected pieces longer than `strip_m` along the row whose mean g - r exceeds `strip_gr`: vine leaves
    here are yellow-green (mean g - r of the reference canopies 1.3-9.6 DN, strips up to 56 m included), while the
    tube filled with grass along a verge, a weedy block or a grassed row is bluish-green (16.6-27.9)."""
    labels, count = ndimage.label(mask, EIGHT)
    longest = max(axes, key=lambda axis: axis.length)
    (x0, y0), (x1, y1) = longest.coords[0], longest.coords[-1]
    direction = np.array([x1 - x0, y1 - y0]) / longest.length
    red, green = rgb[0], rgb[1]
    drop = np.zeros(count + 1, bool)
    for index, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None or np.hypot(window[0].stop - window[0].start, window[1].stop - window[1].start) * PIXEL_M <= params.strip_m:
            continue
        rows, cols = np.nonzero(labels[window] == index)
        along = ((cols + window[1].start) * direction[0] - (rows + window[0].start) * direction[1]) * PIXEL_M
        if np.ptp(along) > params.strip_m:
            rr, cc = rows + window[0].start, cols + window[1].start
            drop[index] = float(np.mean(green[rr, cc].astype(np.float32) - red[rr, cc])) > params.strip_gr
    return mask & ~drop[labels]


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


def min_area(labels: np.ndarray, params: CanopyParams) -> float:
    """`min_area_m2`, or `young_m2` in a young block: when the median of the pieces of at least `young_m2` (of one plot
    on one tile) is under `min_area_m2`, the pieces are the plants themselves ("each plant is its own polygon, however
    small"), not leaf clumps beside bigger plants."""
    if not params.young_pieces:
        return params.min_area_m2
    sizes = np.bincount(labels.ravel())[1:] * PIXEL_M**2
    sizes = sizes[sizes >= params.young_m2]
    young = len(sizes) >= params.young_pieces and float(np.median(sizes)) < params.min_area_m2
    return params.young_m2 if young else params.min_area_m2


def row_minimum(labels: np.ndarray, transform: Affine, angle_deg: float, params: CanopyParams, minimum: float) -> np.ndarray:
    """Per-label minimum area: `minimum`, or `young_m2` for the pieces of a row whose median piece (of at least
    `young_m2`, at least `row_young` of them) is under `min_area_m2`: the plot-level young rule, per row. A weak or
    replanted row in a mature block holds plants of 0.05-0.2 m2 that the plot's median doesn't see. Rows are the pieces'
    across-row positions, split where consecutive positions are over 0.5 m apart (rows are at least 2 m apart)."""
    count = int(labels.max())
    out = np.full(count + 1, minimum)
    if not params.row_young or minimum <= params.young_m2 or not count:
        return out
    rows, cols = np.nonzero(labels)
    index = labels[rows, cols]
    pixels = np.bincount(index, minlength=count + 1).astype(float)
    a = np.radians(angle_deg)
    across = ((cols + 0.5) * transform.a * -np.sin(a) + (rows + 0.5) * transform.e * np.cos(a)).astype(float)
    across = np.bincount(index, weights=across, minlength=count + 1) / np.maximum(pixels, 1)
    sizes = pixels * PIXEL_M**2
    ids = np.flatnonzero(sizes >= params.young_m2)
    ids = ids[np.argsort(across[ids])]
    for group in np.split(ids, np.flatnonzero(np.diff(across[ids]) > 0.5) + 1):
        if len(group) >= params.row_young and float(np.median(sizes[group])) < params.min_area_m2:
            out[group] = params.young_m2
    return out


def canopy_polygons(mask: np.ndarray, transform: Affine, angle_deg: float, params: CanopyParams,
                    weak: np.ndarray | None = None) -> list[Polygon]:
    labels, count = ndimage.label(mask, EIGHT)
    if params.split_neck and count:
        labels, count = _split(labels, count, _along(transform, mask.shape, angle_deg), params)
    minimum = row_minimum(labels, transform, angle_deg, params, min_area(labels, params))
    if params.tuft_m2 and weak is not None and count:
        share = np.bincount(labels.ravel(), weights=weak.ravel(), minlength=count + 1) / np.maximum(np.bincount(labels.ravel(), minlength=count + 1), 1)
        minimum = np.where(share > 0.5, np.minimum(minimum, params.tuft_m2), minimum)
    labels[np.bincount(labels.ravel())[labels] * PIXEL_M**2 < minimum[labels]] = 0
    parts: dict[int, list[BaseGeometry]] = {}
    for geometry, value in shapes(labels.astype(np.int32), mask=labels > 0, connectivity=8, transform=transform):
        parts.setdefault(int(value), []).append(shape(geometry))
    largest = lambda g: max((p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"), key=lambda p: p.area, default=None)
    polygons = []
    for value, pieces in parts.items():
        if params.all_parts:
            polygons += _parts(pieces, float(minimum[value]), params)
            continue
        best = largest(make_valid(max(pieces, key=lambda p: p.area).simplify(params.simplify_m)))
        if best is not None and best.area >= minimum[value]:
            best = largest(make_valid(Polygon(best.exterior).buffer(-params.inset_m, join_style="mitre"))) if params.inset_m else best
            if best is not None:
                polygons.append(Polygon(best.exterior))
    return polygons


def _parts(pieces: list[Polygon], minimum: float, params: CanopyParams) -> list[Polygon]:
    """One label's outline, simplified and inset. A ring traced from 8-connected pixels touches itself at diagonal steps,
    so make_valid splits it into parts, and the inset cuts it at necks under 2 cm; keeping only the largest part dropped
    whole stretches (10 of 28 m2 of one hedge on r033_c019). Parts are re-joined by 1 mm; when the inset still splits
    the polygon it stays un-inset, one instance; parts that stay apart are kept if at least `minimum`."""
    parts = [p for p in _polygons(make_valid(unary_union([make_valid(p) for p in pieces]).simplify(params.simplify_m))) if p.area >= SLIVER_M2]
    if len(parts) > 1:
        joined = _polygons(unary_union(parts).buffer(0.001, join_style="mitre"))
        parts = joined if len(joined) == 1 else parts
    out = []
    for part in parts:
        part = Polygon(part.exterior)
        if part.area < minimum:
            continue
        inset = [p for p in _polygons(make_valid(part.buffer(-params.inset_m, join_style="mitre"))) if p.area >= SLIVER_M2] if params.inset_m else [part]
        out.append(Polygon(inset[0].exterior) if len(inset) == 1 else part)
    return out


def length_m(geometry: BaseGeometry) -> float:
    """Long side of the minimum rotated rectangle."""
    corners = np.asarray(geometry.minimum_rotated_rectangle.exterior.coords)
    return float(max(np.hypot(*(corners[1] - corners[0])), np.hypot(*(corners[2] - corners[1]))))


def in_vegetation(polygon: Polygon, rgb: np.ndarray, transform: Affine, params: CanopyParams) -> bool:
    """True for a piece longer than `veg_m` that is bluish-green itself (mean g - r over `veg_gr` DN) and stands in green
    (over `veg_ring` of the pixels 0.35-0.9 m around it have 2g - r - b over `green_dn`) that is bluish-green too (their
    mean g - r over `veg_ring_gr`): a strip the tube cut out of a tree or shrub crown or lush weeds. Vine leaves in this
    flight are yellow-green and the rows stand in soil or dry grass."""
    if not params.veg_gr or length_m(polygon) <= params.veg_m:
        return False
    rows, cols = _pixels(polygon, transform, rgb.shape[1:])
    if not len(rows) or float(np.mean(rgb[1, rows, cols].astype(np.float32) - rgb[0, rows, cols])) <= params.veg_gr:
        return False
    rows, cols = _pixels(polygon.buffer(VEG_RING_M[1]).difference(polygon.buffer(VEG_RING_M[0])), transform, rgb.shape[1:])
    red, green, blue = rgb[:, rows, cols].astype(np.float32)
    lush = 2 * green - red - blue > (params.green_dn or 25.0)
    return bool(lush.mean() > params.veg_ring and float(np.mean(green[lush] - red[lush])) > params.veg_ring_gr) if lush.any() else False


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    return [p for p in getattr(geometry, "geoms", [geometry]) if p.geom_type == "Polygon" and not p.is_empty]


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
    out = []
    for plot, axes in crossing:
        mask, weak = canopy_mask(image, transform, axes, params, green, row_spacing(plot.axes), with_weak=True)
        out += [{"type": "Feature", "geometry": mapping(polygon),
                 "properties": {"label": "vineyard", "source": "prediction", "vineyard_id": plot.vineyard_id}}
                for polygon in canopy_polygons(mask, transform, plot.angle_deg, params, weak) if not in_vegetation(polygon, image, transform, params)]
    return out


def _canopy_points(polygons: list[BaseGeometry], origin: np.ndarray, d: np.ndarray, bin_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The polygons cut into `bin_m` slices along the rows: each slice's centroid (u along `d`, v across, in metres from
    `origin`) and area."""
    if not polygons:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    n = np.array([-d[1], d[0]])
    local = shapely.transform(np.array(polygons, dtype=object), lambda xy: np.column_stack([(xy - origin) @ d, (xy - origin) @ n]))
    bounds = shapely.bounds(local)
    k0, k1 = np.floor(bounds[:, 0] / bin_m).astype(int), np.floor(bounds[:, 2] / bin_m).astype(int)
    count = k1 - k0 + 1
    index = np.repeat(np.arange(len(local)), count)
    k = k0[index] + np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
    slices = shapely.intersection(local[index], shapely.box(k * bin_m, bounds[index, 1] - 1, (k + 1) * bin_m, bounds[index, 3] + 1))
    area = shapely.area(slices)
    keep = area > 0
    centre = shapely.centroid(slices[keep])
    return shapely.get_x(centre), shapely.get_y(centre), area[keep]


def refit_rows(features: list[dict[str, Any]], canopies: list[dict[str, Any]], reach_m: float = 0.8, bin_m: float = 0.5,
               min_cover_m: float = 3.0, min_share: float = 0.2, clip_m: float = 0.15, gap_share: float = 0.4,
               per_row: bool = False, min_span_m: float = 10.0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The `row` features of `marcaj.plots.detect_plots` moved onto the canopy polygons, and a record per row.

    Per row pattern (`pattern_id`, else `vineyard_id`): its lattice rows are parallel, so the pattern gets one angle
    correction and each row its own offset, a weighted least-squares fit of the across-row position of the canopy slices
    (`_canopy_points`, `bin_m` along the row, weighted by area) assigned to the nearest lattice row within `reach_m`,
    re-fitted on the slices within 0.3 m and then `clip_m` of the fit. A row with canopy-covered bins over less than
    `min_cover_m` or `min_share` of its length keeps its lattice offset (turned with the pattern). A row whose move would
    bring it closer than (1 - `gap_share`) or farther than (1 + `gap_share`) of its lattice distance to a neighbour goes
    back to its lattice offset, largest move first. `per_row` also fits each row's own angle (where its canopy spans
    `min_span_m`): closer to the vines, but `rows.interrow_areas` builds all inter-rows of a pattern along one direction,
    so it needs parallel rows.
    The rows keep their extent, direction, IDs and properties; everything else passes through."""
    pattern = lambda properties: properties.get("pattern_id") or properties.get("vineyard_id", "")  # one row lattice (plots.py)
    by_plot: dict[str, list[int]] = {}
    for i, feature in enumerate(features):
        if feature["properties"]["label"] == "row":
            by_plot.setdefault(pattern(feature["properties"]), []).append(i)
    polygons: dict[str, list[BaseGeometry]] = {}
    for feature in canopies:
        polygons.setdefault(pattern(feature["properties"]), []).append(shape(feature["geometry"]))
    out, records = list(features), []
    for plot_id, indices in by_plot.items():
        lines = [np.asarray(features[i]["geometry"]["coordinates"], float)[[0, -1]] for i in indices]
        origin = lines[0][0]
        d = (lines[0][1] - lines[0][0]) / np.linalg.norm(lines[0][1] - lines[0][0])
        n = np.array([-d[1], d[0]])
        ends = np.array([np.sort((line - origin) @ d) for line in lines])
        v_row = np.array([float(np.mean((line - origin) @ n)) for line in lines])
        u, v, w = _canopy_points(polygons.get(plot_id, []), origin, d, bin_m)
        inside = (u[:, None] >= ends[:, 0] - bin_m) & (u[:, None] <= ends[:, 1] + bin_m)
        distance = np.where(inside, np.abs(v[:, None] - v_row), np.inf)
        nearest = np.argmin(distance, axis=1) if len(u) else np.zeros(0, int)
        near = distance[np.arange(len(u)), nearest] <= reach_m if len(u) else np.zeros(0, bool)
        r = v - v_row[nearest] if len(u) else v
        cover = np.array([len(np.unique(np.floor(u[near & (nearest == k)] / bin_m))) * bin_m for k in range(len(lines))])
        supported = cover >= np.maximum(min_cover_m, min_share * (ends[:, 1] - ends[:, 0]))
        offset, centre, slope = np.zeros(len(lines)), ends.mean(axis=1), np.zeros(len(lines))
        use = near & supported[nearest] if len(u) else near
        for limit in (reach_m, 0.3, clip_m, clip_m):
            inlier = use & (np.abs(r - offset[nearest] - slope[nearest] * (u - centre[nearest])) <= limit) if len(u) else use
            k, weight = nearest[inlier], w[inlier]
            total = np.bincount(k, weights=weight, minlength=len(lines))
            ok = total > 0
            centre = np.where(ok, np.bincount(k, weights=weight * u[inlier], minlength=len(lines)) / np.maximum(total, 1e-9), centre)
            mean_r = np.bincount(k, weights=weight * r[inlier], minlength=len(lines)) / np.maximum(total, 1e-9)
            du = u[inlier] - centre[k]
            sxx = np.bincount(k, weights=weight * du * du, minlength=len(lines))
            sxy = np.bincount(k, weights=weight * du * (r[inlier] - mean_r[k]), minlength=len(lines))
            shared = float(sxy.sum() / sxx.sum()) if sxx.sum() > 1e-6 else 0.0
            span = np.zeros(len(lines))
            if inlier.any():
                np.maximum.at(span, k, u[inlier])
                low = np.full(len(lines), np.inf)
                np.minimum.at(low, k, u[inlier])
                span = np.where(ok, span - low, 0.0)
            slope = np.where((span >= min_span_m) & (sxx > 1e-6), sxy / np.maximum(sxx, 1e-9), shared) if per_row else np.full(len(lines), shared)
            offset = np.where(ok & supported, mean_r, 0.0)
        moved = supported & (total > 0)
        shift = lambda k, at: offset[k] + slope[k] * (at - centre[k]) if moved[k] else shared * (at - centre[k])
        order = np.argsort(v_row)
        reverted = np.zeros(len(lines), bool)
        for _ in range(len(lines)):
            bad = []
            for a, b in zip(order[:-1], order[1:]):
                lattice = v_row[b] - v_row[a]
                lo, hi = max(ends[a, 0], ends[b, 0]), min(ends[a, 1], ends[b, 1])
                if hi <= lo or lattice <= 0:
                    continue
                gaps = [lattice + shift(b, at) - shift(a, at) for at in (lo, hi)]
                if min(gaps) < (1 - gap_share) * lattice or max(gaps) > (1 + gap_share) * lattice:
                    bad.append(max((a, b), key=lambda k: abs(offset[k]) if moved[k] else -1.0))
            bad = [k for k in bad if moved[k]]
            if not bad:
                break
            worst = max(bad, key=lambda k: abs(offset[k]))
            moved[worst], reverted[worst] = False, True
        for k, i in enumerate(indices):
            s0, s1 = shift(k, ends[k, 0]), shift(k, ends[k, 1])
            a, b = origin + d * ends[k, 0] + n * (v_row[k] + s0), origin + d * ends[k, 1] + n * (v_row[k] + s1)
            out[i] = {**features[i], "geometry": mapping(LineString([a, b] if (lines[k][1] - lines[k][0]) @ d > 0 else [b, a]))}
            records.append({"row_id": features[i]["properties"]["row_id"], "vineyard_id": plot_id, "length_m": float(ends[k, 1] - ends[k, 0]),
                            "cover_m": float(cover[k]), "moved": bool(moved[k]), "reverted": bool(reverted[k]),
                            "shift_start_m": float(s0), "shift_end_m": float(s1),
                            "turn_deg": float(np.degrees(np.arctan(slope[k] if moved[k] else shared)))})
    return out, records
