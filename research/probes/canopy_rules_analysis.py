"""Error analysis of `marcaj.canopy` on the two organizer tiles: counts, match breakdown, false positive / negative
classes, area bias, polygon style, reference plant spacing, pixel error by distance to the boundary, and oracle bounds.
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_rules_analysis.py [predictions.json]
(a file saved by `canopy_rules_lib.save`; without one, the `marcaj.canopy` defaults are run and saved as defaults.json)."""

import json
import sys

from collections import Counter

import numpy as np
from scipy import ndimage
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

from canopy_rules_lib import NAMES, images, predicted_rows, raster, ref_objects, reference, run, score, tiles, base
from marcaj.canopy import CanopyParams, canopy_mask, canopy_polygons, tile_canopies
from marcaj.judge import _iou, _match, judge
from marcaj.layers import exg
from marcaj.tiles import PIXEL_M

P = CanopyParams()
if len(sys.argv) > 1:
    found = {n: [shape(g) for g in gs] for n, gs in json.loads(open(sys.argv[1]).read()).items()}
    S = score(found)
    print(S.line(sys.argv[1]))
else:
    S, found = run("marcaj.canopy defaults", lambda n: [shape(f["geometry"]) for f in tile_canopies(tiles[n], predicted_rows, P, images[n])])
features = [{"type": "Feature", "geometry": p.__geo_interface__, "properties": {"label": "vineyard", "source": "prediction", "vineyard_id": "X"}}
            for n in NAMES for p in found[n]]
official = judge({"type": "FeatureCollection", "crs": "EPSG:32635", "features": base + features})
print(f"judge on the same polygons: canopy {official['scores']['canopy']:.4f} vs harness {S.canopy:.4f}")


def row_of(geometry, rows: list) -> tuple[int, float]:
    c = geometry.centroid
    d = [r.distance(c) for r in rows]
    return int(np.argmin(d)), float(min(d))


def along(rows: list) -> np.ndarray:
    (x0, y0), (x1, y1) = max(rows, key=lambda r: r.length).coords[:2]
    return np.array([x1 - x0, y1 - y0]) / np.hypot(x1 - x0, y1 - y0)


def extent(geometry, d: np.ndarray) -> tuple[float, float]:
    xy = np.asarray(geometry.exterior.coords)
    u, v = xy @ d, xy @ np.array([-d[1], d[0]])
    return float(np.ptp(u)), float(np.ptp(v))


for name in NAMES:
    pred, ref = found[name], ref_objects(name, "vineyard")
    rows = ref_objects(name, "row")
    d = along(rows)
    bounds = tiles[name].bounds
    print(f"\n=== {name}: predicted {len(pred)}, reference {len(ref)}, reference rows {len(rows)}")
    pairs = _match([(p, {}) for p in pred], [(r, {}) for r in ref], _iou, 0.5)
    mp, mr = {i for i, _ in pairs}, {j for _, j in pairs}
    print(f"matched {len(pairs)}; unmatched predicted {len(pred) - len(mp)}, unmatched reference {len(ref) - len(mr)}")

    # overlap table: share of each ref covered by each pred
    cover = np.zeros((len(pred), len(ref)))
    for i, p in enumerate(pred):
        for j, r in enumerate(ref):
            if p.intersects(r):
                cover[i, j] = p.intersection(r).area
    ref_area = np.array([r.area for r in ref])
    pred_area = np.array([p.area for p in pred])
    share_of_ref = cover / ref_area[None, :]
    share_of_pred = cover / pred_area[:, None]

    # false positives
    fp: Counter = Counter()
    fp_area: Counter = Counter()
    for i, p in enumerate(pred):
        if i in mp:
            continue
        refs = np.nonzero(share_of_ref[i] > 0)[0]
        edge = p.distance(bounds.exterior) < 0.05
        if share_of_pred[i].sum() < 0.1:
            _, dist = row_of(p, rows)
            kind = "no overlap, off reference rows (>0.4 m)" if dist > 0.4 else ("no overlap, on a row, tile edge" if edge else "no overlap, on a row (weed / unannotated / shadowed plant)")
        elif (share_of_ref[i] > 0.3).sum() >= 2:
            kind = "merged: covers >30% of 2+ references"
        elif len(refs) and max(share_of_ref[i]) < 0.5 and (cover[:, refs[np.argmax(share_of_ref[i][refs])]] > 0).sum() >= 2:
            kind = "fragment: part of an over-split reference"
        else:
            j = refs[np.argmax(cover[i, refs])]
            kind = "one reference, IoU<0.5, too big" if p.area > ref[j].area else "one reference, IoU<0.5, too small"
        fp[kind] += 1
        fp_area[kind] += p.area
    print("unmatched predicted:")
    for kind, n in fp.most_common():
        print(f"  {n:4d}  {fp_area[kind]:6.1f} m2  {kind}")

    # false negatives
    fn: Counter = Counter()
    for j, r in enumerate(ref):
        if j in mr:
            continue
        preds = np.nonzero(cover[:, j] > 0)[0]
        axes = [a for plot in predicted_rows for a in plot.axes if a.intersects(bounds)]
        if share_of_ref[:, j].sum() < 0.1:
            dist = min(a.distance(r.centroid) for a in axes) if axes else 9.9
            kind = "missed, no predicted row within 0.4 m" if dist > 0.4 else "missed, row present (colour / min-area / contrast drop)"
        elif any((share_of_ref[i] > 0.3).sum() >= 2 for i in preds):
            kind = "merged into a neighbour"
        elif (share_of_ref[preds, j] > 0.2).sum() >= 2:
            kind = "split into 2+ pieces"
        else:
            i = preds[np.argmax(cover[preds, j])]
            kind = "one prediction, IoU<0.5, prediction bigger" if pred[i].area > r.area else "one prediction, IoU<0.5, prediction smaller"
        fn[kind] += 1
    print("unmatched reference:")
    for kind, n in fn.most_common():
        print(f"  {n:4d}  {kind}")

    # matched pairs
    ious = np.array([_iou(pred[i], ref[j]) for i, j in pairs])
    ratio = np.array([pred[i].area / ref[j].area for i, j in pairs])
    offset = np.array([(pred[i].area - ref[j].area) / ((pred[i].length + ref[j].length) / 2) for i, j in pairs])
    print(f"matched IoU median {np.median(ious):.3f} (p25 {np.percentile(ious, 25):.3f}); area ratio pred/ref median {np.median(ratio):.3f} "
          f"(p25 {np.percentile(ratio, 25):.2f}, p75 {np.percentile(ratio, 75):.2f}); signed boundary offset median {100 * np.median(offset):+.1f} cm")
    ious_all = np.array([max((_iou(p, r) for p in pred if p.intersects(r)), default=0.0) for r in ref])
    print(f"best IoU per reference: {np.mean(ious_all >= 0.5):.1%} >= 0.5, {np.mean((ious_all >= 0.4) & (ious_all < 0.5)):.1%} in 0.4-0.5, "
          f"{np.mean((ious_all > 0) & (ious_all < 0.4)):.1%} in (0,0.4), {np.mean(ious_all == 0):.1%} zero")

    # polygon style
    def style(polys):
        verts = np.array([len(p.exterior.coords) - 1 for p in polys])
        convex = np.array([p.area / p.convex_hull.area for p in polys])
        compact = np.array([p.length / np.sqrt(p.area) for p in polys])
        area = np.array([p.area for p in polys])
        ext = np.array([extent(p, d) for p in polys])
        return (f"n {len(polys)}, area median {np.median(area):.3f} m2 (p10 {np.percentile(area, 10):.2f}, p90 {np.percentile(area, 90):.2f}), vertices median {np.median(verts):.0f}, "
                f"convexity median {np.median(convex):.2f}, perimeter/sqrt(area) median {np.median(compact):.2f}, along x across median {np.median(ext[:, 0]):.2f} x {np.median(ext[:, 1]):.2f} m")
    print(f"reference style: {style(ref)}")
    print(f"predicted style: {style(pred)}")

    # plant spacing along each reference row
    gaps, spacing, touching, per_row = [], [], 0, []
    ref_row = [row_of(r, rows)[0] for r in ref]
    pred_row = [row_of(p, rows) for p in pred]
    for k, row in enumerate(rows):
        members = sorted((ref[j] for j in range(len(ref)) if ref_row[j] == k), key=lambda g: np.asarray(g.centroid.coords[0]) @ d)
        u = np.array([np.asarray(g.centroid.coords[0]) @ d for g in members])
        spacing += list(np.diff(u))
        for a, b in zip(members, members[1:]):
            gap = a.distance(b)
            gaps.append(gap)
            touching += gap < 0.03
        per_row.append((k, len(members), sum(1 for i, (rk, dist) in enumerate(pred_row) if rk == k and dist <= 0.4), row.length))
    spacing, gaps = np.array(spacing), np.array(gaps)
    print(f"reference centroid spacing along the row: median {np.median(spacing):.2f} m (p10 {np.percentile(spacing, 10):.2f}, p25 {np.percentile(spacing, 25):.2f}, "
          f"p75 {np.percentile(spacing, 75):.2f}); neighbour polygons touching (<3 cm) {touching}/{len(gaps)}; gap between neighbours median {np.median(gaps):.2f} m; "
          f"{np.mean(gaps < 0.15):.0%} under 15 cm")
    diff = np.array([n_pred - n_ref for _, n_ref, n_pred, _ in per_row])
    print(f"per reference row, predicted minus reference count: {Counter(diff.tolist()).most_common()}")
    print("  rows with |diff| >= 3: " + ", ".join(f"row {k} ({length:.0f} m) {n_ref}->{n_pred}" for k, n_ref, n_pred, length in per_row if abs(n_pred - n_ref) >= 3))
    off_rows = sum(1 for _, dist in pred_row if dist > 0.4)
    print(f"predicted canopies more than 0.4 m from any reference row: {off_rows}")

    # pixels
    image, transform = images[name]
    excess, valid = exg(image)
    truth, guess = raster(name, ref), raster(name, pred)
    brightness = image.astype(np.float32).mean(axis=0)
    dist_to_truth = ndimage.distance_transform_edt(~truth) * PIXEL_M
    dist_to_guess = ndimage.distance_transform_edt(~guess) * PIXEL_M
    fp_px, fn_px, tp_px = guess & ~truth, truth & ~guess, truth & guess
    print(f"pixels: tp {tp_px.sum() * PIXEL_M**2:.1f} m2, fp {fp_px.sum() * PIXEL_M**2:.1f} m2, fn {fn_px.sum() * PIXEL_M**2:.1f} m2")
    for tag, mask, dist in (("fp", fp_px, dist_to_truth), ("fn", fn_px, dist_to_guess)):
        dd = dist[mask]
        print(f"  {tag} pixels by distance to the other outline: <=5 cm {np.mean(dd <= 0.05):.0%}, 5-10 cm {np.mean((dd > 0.05) & (dd <= 0.10)):.0%}, "
              f"10-25 cm {np.mean((dd > 0.10) & (dd <= 0.25)):.0%}, >25 cm {np.mean(dd > 0.25):.0%}; ExG median {np.median(excess[mask]):.3f}, brightness median {np.median(brightness[mask]):.0f}")
    print(f"  tp pixels ExG median {np.median(excess[tp_px]):.3f}, brightness median {np.median(brightness[tp_px]):.0f}")

# oracles
print("\n=== oracle bounds (diagnostic, use the reference)")


def perfect_split(name: str, polygons: list) -> list:
    """Our union mask, each pixel given to the nearest reference canopy within 0.3 m; the rest keep their components."""
    ref = ref_objects(name, "vineyard")
    labels = np.zeros((2048, 2048), np.int32)
    from rasterio.features import rasterize, shapes
    _, transform = images[name]
    labels = rasterize([(r, k + 1) for k, r in enumerate(ref)], out_shape=(2048, 2048), transform=transform).astype(np.int32)
    dist, (ii, jj) = ndimage.distance_transform_edt(labels == 0, return_indices=True)
    nearest = labels[ii, jj]
    mask = raster(name, polygons)
    out = np.where(mask & (dist * PIXEL_M <= 0.3), nearest, 0)
    rest, count = ndimage.label(mask & (out == 0), np.ones((3, 3), bool))
    out = np.where(rest > 0, rest + len(ref) + 1, out)
    polys = []
    for geometry, value in shapes(out, mask=out > 0, connectivity=8, transform=transform):
        polys.append(shape(geometry))
    merged: dict = {}
    for geometry, value in shapes(out, mask=out > 0, connectivity=8, transform=transform):
        merged.setdefault(int(value), []).append(shape(geometry))
    return [unary_union(pieces) for pieces in merged.values()]


def our_split_on_truth(name: str) -> list:
    _, transform = images[name]
    angle = next(plot.angle_deg for plot in predicted_rows if any(a.intersects(tiles[name].bounds) for a in plot.axes))
    return canopy_polygons(raster(name, ref_objects(name, "vineyard")), transform, angle, P)


def matched_only(name: str) -> list:
    pred, ref = found[name], ref_objects(name, "vineyard")
    return [p for p in pred if any(p.intersects(r) for r in ref)]


def plus_missed(name: str) -> list:
    pred, ref = found[name], ref_objects(name, "vineyard")
    union = unary_union(pred)
    return pred + [r for r in ref if not r.intersects(union)]


for tag, fn_ in (("our mask, perfect instance split", lambda n: perfect_split(n, found[n])),
                 ("reference union mask, our split + min area", our_split_on_truth),
                 ("drop predictions touching no reference", matched_only),
                 ("add references touching no prediction", plus_missed),
                 ("both of the above", lambda n: [p for p in plus_missed(n) if any(p.intersects(r) for r in ref_objects(n, 'vineyard'))])):
    print(score({n: fn_(n) for n in NAMES}).line(tag))

if len(sys.argv) == 1:
    from canopy_rules_lib import WORK, save
    save(WORK / "defaults.json", found)
