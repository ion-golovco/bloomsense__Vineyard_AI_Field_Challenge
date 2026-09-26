"""Canopy trials on the two organizer tiles, each scored with the judge's formulas next to the baseline. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_rules_trials.py [group ...]"""

import sys
from dataclasses import replace

import numpy as np
from scipy import ndimage
from shapely.geometry import shape

from canopy_rules_lib import NAMES, WORK, images, predicted_rows, reference_rows, run, save, tiles
from marcaj.canopy import CanopyParams, tile_canopies
from marcaj.layers import exg
from marcaj.tiles import PIXEL_M

P = CanopyParams()


def green_dn(rgb: np.ndarray, threshold: float = 27.0, smooth_m: float = 0.05) -> np.ndarray:
    r, g, b = rgb.astype(np.float32)
    value = 2 * g - r - b
    if smooth_m:
        value = ndimage.gaussian_filter(value, smooth_m / PIXEL_M)
    return value > threshold


def polygons(name: str, params: CanopyParams = P, green=None, rows=None) -> list:
    rgb = images[name]
    return [shape(f["geometry"]) for f in tile_canopies(tiles[name], rows or predicted_rows, params, rgb, green)]


groups = (sys.argv[1:] or ["colour"]) if __name__ == "__main__" else []
if "colour" in groups:
    run("S baseline (ExG > 0.11)", lambda n: polygons(n))
    for t in (22, 25, 27, 30, 33):
        result, found = run(f"ExG(DN) > {t}", lambda n: polygons(n, P, green_dn(images[n][0], t)))
    for t in (27,):
        run(f"ExG(DN) > {t}, no split", lambda n: polygons(n, replace(P, split_neck=0), green_dn(images[n][0], t)))
        for neck in (0.2, 0.4, 0.5):
            run(f"ExG(DN) > {t}, split neck {neck}", lambda n: polygons(n, replace(P, split_neck=neck), green_dn(images[n][0], t)))
        run(f"ExG(DN) > {t}, reference rows (diagnostic)", lambda n: polygons(n, replace(P, refine_m=0, row_contrast=0), green_dn(images[n][0], t), reference_rows(n)))

if "save" in groups:
    save(WORK / "dn25.json", run("ExG(DN) > 25", lambda n: polygons(n, P, green_dn(images[n][0], 25)))[1])


def hysteresis(value: np.ndarray, low: float, high: float) -> np.ndarray:
    """Pixels above `low` connected to a pixel above `high`."""
    labels, _ = ndimage.label(value > low, np.ones((3, 3), bool))
    keep = np.zeros(labels.max() + 1, bool)
    keep[np.unique(labels[value > high])] = True
    keep[0] = False
    return keep[labels]


def dn(rgb: np.ndarray, smooth_m: float = 0.05) -> np.ndarray:
    r, g, b = rgb.astype(np.float32)
    return ndimage.gaussian_filter(2 * g - r - b, smooth_m / PIXEL_M) if smooth_m else 2 * g - r - b


if "colour2" in groups:
    for smooth in (0.025, 0.075, 0.1):
        for t in (23, 25, 27):
            run(f"ExG(DN) > {t}, smooth {smooth}", lambda n: polygons(n, P, dn(images[n][0], smooth) > t))
    for low, high in ((20, 30), (22, 30), (20, 35), (23, 35), (25, 35), (25, 40)):
        run(f"ExG(DN) hysteresis {low}/{high}", lambda n: polygons(n, P, hysteresis(dn(images[n][0]), low, high)))
    for area in (0.15, 0.25):
        run(f"ExG(DN) > 25, min area {area}", lambda n: polygons(n, replace(P, min_area_m2=area), dn(images[n][0]) > 25))
    for tube_m in (0.25, 0.35, 0.4):
        run(f"ExG(DN) > 25, tube {tube_m}", lambda n: polygons(n, replace(P, tube_m=tube_m), dn(images[n][0]) > 25))
    run("ExG(DN) > 25, no contrast filter", lambda n: polygons(n, replace(P, row_contrast=0), dn(images[n][0]) > 25))
    run("ExG(DN) > 25, no refit", lambda n: polygons(n, replace(P, refine_m=0), dn(images[n][0]) > 25))


def oracle_rows(name: str, extend: bool = False, trim: bool = False) -> list:
    """Diagnostic: predicted axes that lie within 0.4 m of a reference row (a perfect row test); with `extend`,
    each kept axis is stretched to its reference row's ends (perfect row ends)."""
    from canopy_rules_lib import ref_objects
    from marcaj.canopy import RowSet
    from shapely.geometry import LineString
    refrows = ref_objects(name, "row")
    out = []
    for plot in predicted_rows:
        kept = []
        for a in plot.axes:
            c = a.intersection(tiles[name].bounds)
            if c.geom_type != "LineString" or not c.length:
                continue
            mid = c.interpolate(0.5, normalized=True)
            best = min(refrows, key=lambda r: r.distance(mid))
            if best.distance(mid) > 0.4:
                continue
            if extend:
                (x0, y0), (x1, y1) = c.coords[0], c.coords[-1]
                d = np.array([x1 - x0, y1 - y0]) / c.length
                u = [(np.array(p) - (x0, y0)) @ d for p in best.coords]
                lo, hi = min(0.0, min(u)), max(c.length, max(u))
                if trim:
                    lo, hi = min(u), max(u)
                c = LineString([(x0 + d[0] * lo, y0 + d[1] * lo), (x0 + d[0] * hi, y0 + d[1] * hi)])
            kept.append(c)
        out.append(RowSet(plot.vineyard_id, plot.angle_deg, kept))
    return out


if "rows" in groups:
    t = 25
    run("ExG(DN) > 25 (new base)", lambda n: polygons(n, P, dn(images[n][0]) > t))
    run("  oracle row test (no contrast filter)", lambda n: polygons(n, replace(P, row_contrast=0), dn(images[n][0]) > t, oracle_rows(n)))
    run("  oracle row test + oracle row ends", lambda n: polygons(n, replace(P, row_contrast=0), dn(images[n][0]) > t, oracle_rows(n, True)))
    run("  reference rows, refit on", lambda n: polygons(n, replace(P, row_contrast=0), dn(images[n][0]) > t, reference_rows(n)))
    run("  reference rows, no refit", lambda n: polygons(n, replace(P, row_contrast=0, refine_m=0), dn(images[n][0]) > t, reference_rows(n)))


from marcaj.canopy import EIGHT, _along, canopy_polygons, fit_axis, tube
from rasterio.transform import array_bounds
from shapely.geometry import LineString, box, mapping


def disk(radius_m: float) -> np.ndarray:
    r = int(round(radius_m / PIXEL_M))
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return x * x + y * y <= r * r


def lattice_mask(rgb, transform, axes, params, green, value, strength="share", ratio=0.7, contrast_min=None, spacing=None, close_m=0.0,
                 outer_m=0.0, second_refine_m=0.0):
    """canopy_mask with a lattice rule: of two axes closer than `ratio` x the plot's median row spacing, the weaker
    (by `strength`: tube green share, mean excess green in the tube, or contrast) is dropped."""
    from marcaj.layers import exg
    _, valid = exg(rgb)
    green = green & valid
    bounds = box(*array_bounds(*green.shape, transform))
    clipped = [a.intersection(bounds) for a in axes]
    fitted = [fit_axis(LineString(c.coords), green, transform, params) for c in clipped if c.geom_type == "LineString" and c.length > 0]
    if second_refine_m:
        fitted = [fit_axis(a, green, transform, replace(params, refine_m=second_refine_m)) for a, _ in fitted]
    contrast_min = params.row_contrast if contrast_min is None else contrast_min
    kept = [(a, c) for a, c in fitted if c >= contrast_min]
    if len(kept) >= 2 and ratio:
        longest = max(kept, key=lambda f: f[0].length)[0]
        (x0, y0), (x1, y1) = longest.coords[0], longest.coords[-1]
        normal = np.array([-(y1 - y0), x1 - x0]) / longest.length
        def power(a):
            band = tube([a], transform, green.shape, params.tube_m) & valid
            if strength == "share":
                return green[band].mean()
            if strength == "value":
                return value[band].mean()
            return 0.0
        scored = []
        for a, c in kept:
            m = np.asarray(a.interpolate(0.5, normalized=True).coords[0])
            scored.append([float(m @ normal), a, c if strength == "contrast" else power(a)])
        scored.sort(key=lambda s: s[0])
        s = spacing or float(np.median(np.diff([o for o, _, _ in scored])))
        while len(scored) >= 2:
            gaps = np.diff([o for o, _, _ in scored])
            k = int(np.argmin(gaps))
            if gaps[k] >= ratio * s:
                break
            scored.pop(k if scored[k][2] < scored[k + 1][2] else k + 1)
        kept = [(a, c) for _, a, c in scored]
    band = tube([a for a, _ in kept], transform, green.shape, params.tube_m)
    if outer_m:
        core, band = band, tube([a for a, _ in kept], transform, green.shape, outer_m)
    mask = green & band
    if close_m:
        # erosion treats outside the tile as canopy, so plants cut by the tile edge still reach it
        mask = ndimage.binary_erosion(ndimage.binary_dilation(mask, disk(close_m)), disk(close_m), border_value=1) & band
    if outer_m:
        labels, _ = ndimage.label(mask, EIGHT)
        keep = np.zeros(labels.max() + 1, bool)
        keep[np.unique(labels[core & mask])] = True
        keep[0] = False
        mask = keep[labels]
    return ndimage.binary_fill_holes(mask)


def plot_spacing(plot) -> float:
    axes = plot.axes
    if len(axes) < 2:
        return 0.0
    longest = max(axes, key=lambda a: a.length)
    (x0, y0), (x1, y1) = longest.coords[0], longest.coords[-1]
    normal = np.array([-(y1 - y0), x1 - x0]) / longest.length
    offsets = sorted(float(np.asarray(a.interpolate(0.5, normalized=True).coords[0]) @ normal) for a in axes)
    return float(np.median(np.diff(offsets)))


def lattice_polygons(name, params=P, t=25, rows=None, whole_plot=False, **kw):
    rgb, transform = images[name]
    value = dn(rgb)
    green = value > t
    out = []
    for plot in rows or predicted_rows:
        axes = [a for a in plot.axes if a.intersects(tiles[name].bounds)]
        if axes:
            mask = lattice_mask(rgb, transform, axes, params, green, value, spacing=plot_spacing(plot) if whole_plot else None, **kw)
            out += canopy_polygons(mask, transform, plot.angle_deg, params)
    return out


if "lattice" in groups:
    for strength in ("share", "value", "contrast"):
        for contrast_min in (0.0, 1.3):
            for whole in (False, True):
                run(f"lattice 0.7 by {strength}, contrast>={contrast_min}{', plot spacing' if whole else ''}",
                    lambda n: lattice_polygons(n, strength=strength, contrast_min=contrast_min, whole_plot=whole))
    run("lattice off, contrast 0 (control)", lambda n: lattice_polygons(n, ratio=0, contrast_min=0))
    run("lattice off, contrast 1.3 (= new base)", lambda n: lattice_polygons(n, ratio=0))

if "save_lattice" in groups:
    save(WORK / "lattice.json", run("lattice 0.7 by share, contrast 0", lambda n: lattice_polygons(n, contrast_min=0))[1])

if "close" in groups:
    for close in (0.025, 0.05, 0.075, 0.1):
        for neck in (0.3, 0.4):
            run(f"lattice + closing {close} m, neck {neck}", lambda n: lattice_polygons(n, replace(P, split_neck=neck), contrast_min=0, close_m=close))


from rasterio.features import shapes
from shapely import make_valid
from shapely.geometry import Polygon


def split2(labels, along, value, params, d_weak=0.0, v_max=0.0, step=2 * PIXEL_M, long_m=0.0, long_neck=0.0):
    """canopy._split plus a weak-neck rule: also cut where depth < `d_weak` and the mean excess green in the neck
    bin is below `v_max` (a valley in leaf colour, not only in width)."""
    out, next_label = np.zeros_like(labels), 1
    least = int(params.split_piece_m / step)
    for index, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None:
            continue
        mask = labels[window] == index
        u = along[window][mask]
        bins = ((u - u.min()) / step).astype(int)
        counts = np.bincount(bins).astype(float)
        width = ndimage.gaussian_filter1d(counts, 1.0)
        colour = ndimage.gaussian_filter1d(np.bincount(bins, weights=value[window][mask]), 1.0) / np.maximum(width, 1e-6)
        cuts, pending = [], [(0, len(width))]
        while pending:
            lo, hi = pending.pop()
            best = None
            for i in range(lo + least, hi - least):
                if width[i] <= width[i - 1] and width[i] <= width[i + 1]:
                    depth = width[i] / min(width[lo:i].max(), width[i + 1:hi].max())
                    neck = long_neck if long_m and (hi - lo) * step > long_m else params.split_neck
                    ok = depth < neck or (depth < d_weak and colour[i] < v_max)
                    if ok and (best is None or depth < best[0]):
                        best = (depth, i)
            if best:
                cuts.append(best[1])
                pending += [(lo, best[1]), (best[1], hi)]
        piece = np.searchsorted(np.sort(cuts), bins, side="right")
        region = out[window]
        region[mask] = next_label + piece
        next_label += len(cuts) + 1
    return out, next_label - 1


def polygons_of(labels, transform, params):
    labels[np.bincount(labels.ravel())[labels] * PIXEL_M**2 < params.min_area_m2] = 0
    parts = {}
    for geometry, v in shapes(labels.astype(np.int32), mask=labels > 0, connectivity=8, transform=transform):
        parts.setdefault(int(v), []).append(shape(geometry))
    out = []
    for pieces in parts.values():
        fixed = make_valid(max(pieces, key=lambda p: p.area).simplify(params.simplify_m))
        best = max((p for p in getattr(fixed, "geoms", [fixed]) if p.geom_type == "Polygon"), key=lambda p: p.area, default=None)
        if best is not None and best.area >= params.min_area_m2:
            out.append(Polygon(best.exterior))
    return out


def full_polygons(name, params=P, t=25, close_m=0.05, d_weak=0.0, v_max=0.0, rows=None, post=None, long_m=0.0, long_neck=0.0, **kw):
    rgb, transform = images[name]
    value = dn(rgb)
    out = []
    for plot in rows or predicted_rows:
        axes = [a for a in plot.axes if a.intersects(tiles[name].bounds)]
        if axes:
            mask = lattice_mask(rgb, transform, axes, params, value > t, value, contrast_min=kw.pop("contrast_min", 0), close_m=close_m, **kw)
            labels, _ = ndimage.label(mask, EIGHT)
            labels, _ = split2(labels, _along(transform, mask.shape, plot.angle_deg), value, params, d_weak, v_max, long_m=long_m, long_neck=long_neck)
            out += polygons_of(labels, transform, params)
    return [post(p) for p in out] if post else out


if "necks" in groups:
    run("closing 0.05, neck 0.3 (check = 0.845)", lambda n: full_polygons(n))
    for d_weak in (0.5, 0.6, 0.7):
        for v_max in (30, 35, 40, 45):
            run(f"  + weak neck depth<{d_weak} & DN<{v_max}", lambda n: full_polygons(n, d_weak=d_weak, v_max=v_max))

if "tube" in groups:
    run("closing 0.05, neck 0.3 (check = 0.845)", lambda n: full_polygons(n))
    for second in (0.3, 0.2):
        run(f"  + second refit within {second} m", lambda n: full_polygons(n, second_refine_m=second))
    for inner, outer in ((0.3, 0.4), (0.3, 0.5), (0.25, 0.4), (0.2, 0.4), (0.2, 0.5)):
        run(f"  core tube {inner}, outer {outer}", lambda n: full_polygons(n, replace(P, tube_m=inner), outer_m=outer))
    run("  reference rows, no refit", lambda n: full_polygons(n, replace(P, refine_m=0), rows=reference_rows(n)))
    run("  reference rows, refit", lambda n: full_polygons(n, rows=reference_rows(n)))
    run("  reference rows, no refit, core 0.3 outer 0.5", lambda n: full_polygons(n, replace(P, refine_m=0), rows=reference_rows(n), outer_m=0.5))

if "save_close" in groups:
    save(WORK / "close.json", run("closing 0.05, neck 0.3, second refit 0.3", lambda n: full_polygons(n, second_refine_m=0.3))[1])

if "shape" in groups:
    base_kw = dict(second_refine_m=0.3)
    for t in (23, 27, 29):
        run(f"closing 0.05, refit x2, ExG(DN) > {t}", lambda n: full_polygons(n, t=t, **base_kw))
    for b in (-0.005, -0.01, -0.015, -0.02):
        run(f"  ExG(DN) > 25, buffer {b} m", lambda n: full_polygons(n, post=lambda p: p.buffer(b, join_style="mitre"), **base_kw))
    for simp in (0.01, 0.04, 0.06):
        run(f"  simplify {simp} m", lambda n: full_polygons(n, replace(P, simplify_m=simp), **base_kw))
    for long_m in (2.0, 2.5, 3.0):
        for long_neck in (0.45, 0.6):
            run(f"  neck {long_neck} on pieces longer than {long_m} m", lambda n: full_polygons(n, long_m=long_m, long_neck=long_neck, **base_kw))

if "ends" in groups:
    kw = dict(second_refine_m=0.3, post=lambda p: p.buffer(-0.01, join_style="mitre"))
    run("current best (closing, refit x2, buffer -0.01)", lambda n: full_polygons(n, **kw))
    run("  oracle rows kept (no lattice)", lambda n: full_polygons(n, rows=oracle_rows(n), ratio=0, **kw))
    run("  oracle rows + extend", lambda n: full_polygons(n, rows=oracle_rows(n, True), ratio=0, **kw))
    run("  oracle rows + extend + trim", lambda n: full_polygons(n, rows=oracle_rows(n, True, True), ratio=0, **kw))
    run("  reference rows, refit x2", lambda n: full_polygons(n, rows=reference_rows(n), **kw))
    run("  reference rows, no refit", lambda n: full_polygons(n, replace(P, refine_m=0), rows=reference_rows(n), post=kw["post"]))

if "edge" in groups:
    kw = dict(second_refine_m=0.3, post=lambda p: p.buffer(-0.01, join_style="mitre"))
    save(WORK / "edge.json", run("closing kept at the tile edge, refit x2, buffer -0.01", lambda n: full_polygons(n, **kw))[1])
    run("  same, closing 0.075", lambda n: full_polygons(n, close_m=0.075, **kw))
