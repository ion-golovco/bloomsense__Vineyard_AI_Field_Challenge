"""Per-outline error analysis of `marcaj.plots.detect_plots` against the lab's hand-drawn vineyard outlines.
Evaluation only: it reads data/review/, which prediction code must never do.
For each outline: the best predicted plot, IoU, the pipeline stage where the outline was lost (seed, region,
fit, overlap), row-angle error, and per-edge offsets in the outline's own row frame (sides = outermost rows,
ends = row ends; positive = the prediction reaches beyond the drawn edge), each edge tagged road or open.
Run from backend/: uv run --frozen python ../research/probes/plot_errors.py [predictions.geojson] [--params k=v ...]"""

import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

from marcaj import plots
from marcaj.judge import PLOT_SPLIT_NORTHING, _iou, plot_scores
from marcaj.layers import LAYER_PX_M, load_layers
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots"
ROAD_M = 6.0      # an edge with road within this distance outside it is a road edge
EDGE_OFF_M = 3.0  # an edge further off than this is "off"


def params_from(args: list[str]) -> plots.PlotParams:
    changes = dict(a.split("=") for a in args)
    base = plots.PlotParams()
    return replace(base, **{k: type(getattr(base, k))(v) for k, v in changes.items()})


def edge_offsets(outline: Polygon, predicted, angle: float, roads) -> dict[str, tuple[float, bool]]:
    """Median signed offset of the prediction's edge from the drawn edge on each of the 4 sides, in the row frame."""
    frame = plots._Frame(outline, angle, 0.2, pad=20)
    from shapely import contains_xy
    o = frame.inside
    p = contains_xy(predicted, frame.x, frame.y) if not predicted.is_empty else np.zeros_like(o)
    r = contains_xy(roads, frame.x, frame.y)
    out = {}
    for axis, names in ((0, ("side_lo", "side_hi")), (1, ("end_lo", "end_hi"))):
        coord = frame.v if axis == 0 else frame.u
        # scan lines across the edge: columns (fixed u) for the sides, rows (fixed v) for the ends
        o_lines, p_lines, r_lines = (o.T, p.T, r.T) if axis == 0 else (o, p, r)
        present = np.flatnonzero(o_lines.any(1))
        if len(present) < 5:
            continue
        middle = present[len(present) // 5: len(present) - len(present) // 5 + 1]
        lo_d, hi_d, lo_road, hi_road = [], [], [], []
        for i in middle:
            oi, pi = np.flatnonzero(o_lines[i]), np.flatnonzero(p_lines[i])
            a, b = coord[oi[0]], coord[oi[-1]]
            reach = int(ROAD_M / 0.2)
            lo_road.append(r_lines[i][max(0, oi[0] - reach):oi[0]].any())
            hi_road.append(r_lines[i][oi[-1] + 1:oi[-1] + 1 + reach].any())
            if len(pi):
                lo_d.append(a - coord[pi[0]])
                hi_d.append(coord[pi[-1]] - b)
        if lo_d:
            out[names[0]] = (float(np.median(lo_d)), float(np.mean(lo_road)) > 0.3)
            out[names[1]] = (float(np.median(hi_d)), float(np.mean(hi_road)) > 0.3)
    return out


def main() -> None:
    args = [a for a in sys.argv[1:] if "=" in a]
    source = next((Path(a) for a in sys.argv[1:] if "=" not in a), None)
    params = params_from(args)
    layers, excess, roads = load_layers(), plots.load_excess(), plots.exclusions()
    blocked = rasterize([roads], out_shape=layers.valid.shape, transform=layers.transform).astype(bool)
    features = json.loads(source.read_text())["features"] if source else plots.detect_plots(params, layers=layers, excess=excess)
    blocks = [(shape(f["geometry"]), f["properties"]) for f in features if f["properties"]["label"] == "block"]
    verdicts = load_verdicts()
    outlines = [(i, v, shape(v["geometry"])) for i, v in enumerate(v for v in verdicts if v["kind"] == "plot")]
    vine = [(i, v, g) for i, v, g in outlines if v["label"] == "vineyard"]
    others = {label: unary_union([g for _, v, g in outlines if v["label"] == label]) for label in ("orchard", "overgrown")}

    # pipeline stages, mirrored from detect_plots
    usable = layers.valid & ~blocked
    seeds = (layers.vine_over_orchard > params.ratio_min) & usable
    opened = ndimage.binary_opening(seeds, iterations=max(1, round(params.opening_m / LAYER_PX_M)))
    regions = plots._regions(layers, blocked, params)
    region_union = unary_union(regions)
    fits = []
    for region in regions:
        angle, spacing = plots._row_angle(excess, region)
        result = plots._quadrilateral(excess, region, angle, spacing, params) if spacing else None
        fits.append(None if result is None else plots._largest(plots._onto_roads(result[0], roads, params)))

    def cover(mask: np.ndarray, polygon: Polygon) -> float:
        inside = rasterize([polygon], out_shape=mask.shape, transform=layers.transform).astype(bool)
        return float(mask[inside].mean()) if inside.any() else 0.0

    rows, classes = [], Counter()
    area_at_stake = Counter()
    for i, v, outline in vine:
        half = "N" if outline.centroid.y >= PLOT_SPLIT_NORTHING else "S"
        hits = sorted(((_iou(outline, b), b, p) for b, p in blocks if b.intersects(outline)), key=lambda t: -t[0])
        best_iou, best, props = hits[0] if hits else (0.0, Polygon(), {})
        covering = [(b, p) for _, b, p in hits if b.intersection(outline).area > 0.15 * outline.area]
        angle, spacing = plots._row_angle(excess, outline)
        mrr = np.asarray(outline.minimum_rotated_rectangle.exterior.coords)
        width = float(min(np.hypot(*np.diff(mrr, axis=0).T)[:2]))
        seed_cover, open_cover = cover(seeds, outline), cover(opened, outline)
        region_cover = region_union.intersection(outline).area / outline.area
        fit_best = max((_iou(outline, f) for f in fits if f is not None and f.intersects(outline)), default=0.0)
        angle_error = abs((props.get("row_angle", angle) - angle + 90) % 180 - 90) if props else None
        edges = edge_offsets(outline, best, angle, roads) if props else {}
        beyond = best.difference(outline).area if props else 0.0
        on_other = sum(best.intersection(g).area for j, w, g in vine if j != i) if props else 0.0
        on_orchard = best.intersection(others["orchard"]).area if props else 0.0

        # failure class, most severe first
        if best_iou >= 0.75:
            kind = "ok"
        elif best.intersection(outline).area < 0.2 * outline.area and not covering:
            kind = ("missed: no seed" if open_cover < 0.05 else "missed: seed too small/split" if region_cover < 0.2
                    else "missed: fit failed" if fit_best < 0.2 else "missed: lost in overlap")
        elif angle_error is not None and angle_error > 20:
            kind = "wrong row direction"
        elif len(covering) >= 2:
            kind = "split"
        elif on_other > 0.2 * best.area:
            kind = "merged with neighbour"
        elif on_orchard > 0.2 * best.area:
            kind = "merged with orchard"
        else:
            off = [(name, d, road) for name, (d, road) in edges.items() if abs(d) > EDGE_OFF_M]
            kind = "edge off: " + (", ".join(f"{name}{'(road)' if road else '(open)'} {d:+.0f} m" for name, d, road in off) if off else "several small")
        family = kind.split(":")[0] if not kind.startswith("edge") else "edge off"
        classes[family] += 1
        area_at_stake[family] += outline.area * (1 - best_iou)
        rows.append({"n": i, "id": v["id"], "half": half, "area": outline.area, "width": width, "iou": best_iou, "plot": props.get("vineyard_id", ""),
                     "angle": angle, "spacing": spacing, "angle_error": angle_error, "seed": seed_cover, "opened": open_cover, "region": region_cover,
                     "fit_best": fit_best, "beyond_m2": beyond, "edges": edges, "kind": kind, "centroid": (outline.centroid.x, outline.centroid.y)})

    print(f"{'#':>3} {'id':10} h {'m2':>6} {'w m':>5} {'IoU':>5} {'plot':4} {'ang':>5} {'sp':>4} {'dA':>5} {'seed':>4} {'open':>4} {'reg':>4} {'fit':>4}  edges (side_lo side_hi end_lo end_hi, + = beyond)  class")
    for r in sorted(rows, key=lambda r: r["iou"]):
        edges = " ".join(f"{name[0]}{name[-2:]}{d:+5.1f}{'R' if road else ' '}" for name, (d, road) in r["edges"].items())
        print(f"{r['n']:3d} {r['id']} {r['half']} {r['area']:6.0f} {r['width']:5.1f} {r['iou']:5.2f} {r['plot']:4} {r['angle']:5.1f} {r['spacing']:4.2f} "
              f"{r['angle_error'] if r['angle_error'] is not None else float('nan'):5.1f} {r['seed']:4.2f} {r['opened']:4.2f} {r['region']:4.2f} {r['fit_best']:4.2f}  {edges:52s} {r['kind']}")
    print("\nclasses:", dict(classes))
    print("outline area not matched (m2 x (1 - IoU)) by class:", {k: round(a) for k, a in area_at_stake.items()})
    offsets = Counter()
    for r in rows:
        for name, (d, road) in r["edges"].items():
            if abs(d) > EDGE_OFF_M:
                offsets[f"{name.split('_')[0]} {'road' if road else 'open'} {'short' if d < 0 else 'long'}"] += 1
    print("edges off by more than", EDGE_OFF_M, "m:", dict(offsets))
    all_d = [(name.split("_")[0], road, d) for r in rows for name, (d, road) in r["edges"].items()]
    for part in ("side", "end"):
        for road in (False, True):
            ds = [d for p, rd, d in all_d if p == part and rd == road]
            if ds:
                print(f"  {part:4s} {'road' if road else 'open'}: n {len(ds)}, median {np.median(ds):+.1f} m, IQR {np.percentile(ds, 25):+.1f}..{np.percentile(ds, 75):+.1f}")
    # predicted plots that match no vineyard outline
    anything = unary_union([g for _, _, g in outlines])
    print("\npredicted plots mostly outside every outline:")
    for b, p in blocks:
        if b.intersection(anything).area < 0.5 * b.area:
            print(f"  {p['vineyard_id']} {b.area:6.0f} m2 at ({b.centroid.x:.0f}, {b.centroid.y:.0f}), angle {p['row_angle']}, spacing {p['row_spacing_m']}, "
                  f"{b.intersection(others['orchard']).area:.0f} m2 on orchards, {b.intersection(others['overgrown']).area:.0f} m2 on overgrown")
    for half, s in plot_scores(features, verdicts).items():
        print(half, {k: round(x, 3) if isinstance(x, float) else x for k, x in s.items()})
    (WORK / "errors.json").write_text(json.dumps(rows, indent=1, default=float))


if __name__ == "__main__":
    main()
