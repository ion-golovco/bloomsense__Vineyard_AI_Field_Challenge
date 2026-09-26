"""Where canopies are missing site-wide, and why. Evaluation only (reads the plot outlines in data/review/verdicts.json).

For every tile a predicted row crosses, the vine-green pixels (2g - r - b > `green_dn`) within +-`tube_m` of every
re-fitted predicted axis are assigned to the first cause that explains why no canopy covers them:
  axis dropped by the inter-row rule (`row_gap`) or by the mean-green row test (`row_value`);
  U-Net "and" filter (in the rule canopy, not in the network-filtered canopy predict.py writes);
  small piece (in the rule mask, in no rule polygon: pieces under `min_area_m2`, plus the 1 cm inset ring).
Also measured: green within +-`tube_m` of each axis's extension 0-5 m beyond its ends and inside a vineyard outline
(rows that stop short), pale leaves (DN 12-25 touching the canopy mask) and vineyard outlines no predicted block covers.
Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_rules_missing.py [plots.json] [--limit N]"""

import json
import sys
import time
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import LineString, box, shape
from shapely.ops import unary_union

from marcaj import canopy, canopy_net
from marcaj.canopy import CanopyParams, _drop_interrows, excess_green, fit_axis, plot_rows, row_spacing, tube, value_contrast
from marcaj.plots import detect_plots
from marcaj.review import load_verdicts
from marcaj.tiles import PIXEL_M, REPO_ROOT, load_tiles

WORK = REPO_ROOT / "data" / "generated" / "work" / "canopy_rules"
P = CanopyParams()
A = PIXEL_M ** 2


def load_plots(path: Path) -> list:
    if path.is_file():
        return json.loads(path.read_text())
    found = detect_plots()
    path.write_text(json.dumps(found))
    return found


def analyse(tile, plots_, rowsets, outline, model, keep: bool = False) -> dict:
    image, transform = canopy.read_rgb(tile)
    excess, valid = excess_green(image, P)
    green = (excess > P.green_dn) & valid
    shape_ = green.shape
    bounds = box(*tile.bounds.bounds)
    net_green = canopy_net.mask(image, model, P, threshold=0.2, combine="and", flips=False)
    fitted_all, dropped_gap, dropped_value, kept_all = [], [], [], []
    rule_polys, net_polys, rule_mask = [], [], np.zeros(shape_, bool)
    for plot in rowsets:
        axes = [a for a in plot.axes if a.intersects(tile.bounds)]
        if not axes:
            continue
        clipped = [a.intersection(bounds) for a in axes]
        fitted = [fit_axis(LineString(c.coords), green, transform, P)[0] for c in clipped if c.geom_type == "LineString" and c.length > 0]
        after_gap = _drop_interrows(fitted, green, transform, P, row_spacing(plot.axes))
        after_value = [a for a in after_gap if value_contrast(a, excess, transform, P) >= P.row_value]
        fitted_all += fitted
        dropped_gap += [a for a in fitted if a not in after_gap]
        dropped_value += [a for a in after_gap if a not in after_value]
        kept_all += after_value
        m = canopy.canopy_mask(image, transform, axes, P, None, row_spacing(plot.axes))
        rule_mask |= m
        rule_polys += canopy.canopy_polygons(m, transform, plot.angle_deg, P)
        nm = canopy.canopy_mask(image, transform, axes, P, net_green, row_spacing(plot.axes))
        net_polys += canopy.canopy_polygons(nm, transform, plot.angle_deg, P)
    burn = lambda geoms: rasterize(geoms, out_shape=shape_, transform=transform).astype(bool) if geoms else np.zeros(shape_, bool)
    all_tube = tube(fitted_all, transform, shape_, P.tube_m) & green
    net_cov = burn(net_polys)
    rule_cov = burn(rule_polys)
    rule_cov_wide = ndimage.binary_dilation(rule_cov, iterations=2)
    net_cov_wide = ndimage.binary_dilation(net_cov, iterations=2)
    left = all_tube & ~net_cov_wide
    causes = {}
    t = tube(dropped_gap, transform, shape_, P.tube_m) & left
    causes["axis dropped: inter-row rule"] = t
    left &= ~t
    t = tube(dropped_value, transform, shape_, P.tube_m) & left
    causes["axis dropped: mean-green test"] = t
    left &= ~t
    t = rule_cov_wide & left
    causes["U-Net 'and' filter"] = t
    left &= ~t
    t = rule_mask & left
    causes["small piece (< 0.2 m2) or split remnant"] = t
    left &= ~t
    causes["other in tube"] = left
    # rows that stop short: extension tubes, inside the vineyard outlines, not already in a tube
    ext = []
    for a in kept_all:
        (x0, y0), (x1, y1) = a.coords[0], a.coords[-1]
        d = np.array([x1 - x0, y1 - y0]) / a.length
        ext += [LineString([(x0, y0), (x0 - 5 * d[0], y0 - 5 * d[1])]), LineString([(x1, y1), (x1 + 5 * d[0], y1 + 5 * d[1])])]
    inside = burn([outline.intersection(bounds)]) if not outline.intersection(bounds).is_empty else np.zeros(shape_, bool)
    ext_green = tube(ext, transform, shape_, P.tube_m) & green & ~tube(fitted_all, transform, shape_, P.tube_m) & inside & ~net_cov_wide
    # pale leaves: DN 12-25 touching the rule canopy
    pale = (excess > 12) & (excess <= P.green_dn) & valid & tube(kept_all, transform, shape_, P.tube_m)
    pale_touch = pale & ndimage.binary_dilation(rule_mask, iterations=2)
    # pieces under the minimum area in the final (net) canopy mask: count and area
    small_n = 0
    lab, n = ndimage.label(causes["small piece (< 0.2 m2) or split remnant"], canopy.EIGHT)
    if n:
        sizes = np.bincount(lab.ravel())[1:] * A
        small_n = int((sizes >= 0.05).sum())
    extra = {"masks": dict(causes, **{"beyond row ends": ext_green}), "net_polys": net_polys, "kept_lines": kept_all,
             "dropped": dropped_gap + dropped_value} if keep else {}
    return {
        **extra,
        "tile": tile.name,
        "green_in_tubes_m2": float(all_tube.sum() * A),
        "covered_m2": float((all_tube & net_cov_wide).sum() * A),
        "causes_m2": {k: float(v.sum() * A) for k, v in causes.items()},
        "beyond_row_ends_m2": float(ext_green.sum() * A),
        "pale_touching_m2": float(pale_touch.sum() * A),
        "small_pieces_0.05_to_0.2": small_n,
        "rule_canopies": len(rule_polys), "net_canopies": len(net_polys),
        "rule_area_m2": float(sum(p.area for p in rule_polys)), "net_area_m2": float(sum(p.area for p in net_polys)),
        "axes": len(fitted_all), "dropped_gap": len(dropped_gap), "dropped_value": len(dropped_value),
        "vine_outline_share": float(inside.mean()),
    }


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    plots_path = Path(args[0]) if args else WORK / "plots_now.json"
    started = time.perf_counter()
    plots_ = load_plots(plots_path)
    print(f"plots {time.perf_counter() - started:.0f} s, {sum(f['properties']['label'] == 'block' for f in plots_)} blocks", flush=True)
    rowsets = plot_rows(plots_)
    verdicts = load_verdicts()
    outlines = [shape(v["geometry"]) for v in verdicts if v["kind"] == "plot" and v["label"] == "vineyard"]
    outline = unary_union(outlines)
    blocks = unary_union([shape(f["geometry"]) for f in plots_ if f["properties"]["label"] == "block"])
    print("vineyard outlines with little predicted block cover:")
    for k, o in enumerate(outlines):
        share = o.intersection(blocks).area / o.area
        if share < 0.7:
            c = o.centroid
            print(f"  outline {k}: {o.area:7.0f} m2, block cover {share:.0%}, centroid {c.x:.0f} {c.y:.0f}")
    print(f"outline area without a predicted block: {outline.difference(blocks).area:.0f} of {outline.area:.0f} m2", flush=True)
    model = canopy_net.load()
    tiles = [t for t in load_tiles() if any(a.intersects(t.bounds) for p in rowsets for a in p.axes)]
    records = []
    for tile in tiles[:limit]:
        t0 = time.perf_counter()
        records.append(analyse(tile, plots_, rowsets, outline, model))
        records[-1]["seconds"] = time.perf_counter() - t0
    (WORK / "missing.json").write_text(json.dumps(records, indent=1))
    total = lambda key: sum(r[key] for r in records)
    print(f"{len(records)} tiles, {np.mean([r['seconds'] for r in records]):.1f} s each")
    print(f"green in tubes {total('green_in_tubes_m2'):.0f} m2, covered {total('covered_m2'):.0f} m2")
    for cause in records[0]["causes_m2"]:
        worst = sorted(records, key=lambda r: -r["causes_m2"][cause])[:5]
        print(f"  {cause:42s} {sum(r['causes_m2'][cause] for r in records):7.0f} m2 | worst " + ", ".join(f"{r['tile'][7:16]} {r['causes_m2'][cause]:.0f}" for r in worst))
    for key in ("beyond_row_ends_m2", "pale_touching_m2"):
        worst = sorted(records, key=lambda r: -r[key])[:5]
        print(f"  {key:42s} {total(key):7.0f} m2 | worst " + ", ".join(f"{r['tile'][7:16]} {r[key]:.0f}" for r in worst))
    print(f"rule canopies {total('rule_canopies')} ({total('rule_area_m2'):.0f} m2), after the U-Net 'and' filter {total('net_canopies')} ({total('net_area_m2'):.0f} m2); "
          f"small pieces 0.05-0.2 m2 left out: {total('small_pieces_0.05_to_0.2')}; axes {total('axes')}, dropped by inter-row rule {total('dropped_gap')}, by mean-green test {total('dropped_value')}")


def sheets(per_cause: int = 6) -> None:
    """Contact sheets of the worst tiles per cause from missing.json: canopy red, the cause's missing green magenta,
    kept axes yellow (dropped axes too, on the axis sheet)."""
    from canopy_rules_sheet import sheet
    records = json.loads((WORK / "missing.json").read_text())
    rowsets = plot_rows(load_plots(WORK / "plots_now.json"))
    outline = unary_union([shape(v["geometry"]) for v in load_verdicts() if v["kind"] == "plot" and v["label"] == "vineyard"])
    tiles = {t.name: t for t in load_tiles()}
    model = canopy_net.load()
    for cause, key, tag in (("axis dropped: mean-green test", None, "axis_dropped"), ("small piece (< 0.2 m2) or split remnant", None, "small_pieces"),
                            ("U-Net 'and' filter", None, "unet_filter"), ("beyond row ends", "beyond_row_ends_m2", "row_ends")):
        worst = sorted(records, key=lambda r: -(r[key] if key else r["causes_m2"][cause]))[:per_cause]
        items = []
        for r in worst:
            a = analyse(tiles[r["tile"]], None, rowsets, outline, model, keep=True)
            m = a["masks"][cause]
            lab, n = ndimage.label(ndimage.binary_dilation(m, iterations=8))
            if not n:
                continue
            biggest = np.argmax(np.bincount(lab.ravel())[1:]) + 1
            rr, cc = np.nonzero(lab == biggest)
            _, transform = canopy.read_rgb(tiles[r["tile"]])
            x, y = transform * (cc.mean(), rr.mean())
            from shapely.geometry import Point
            value = r[key] if key else r["causes_m2"][cause]
            extra = a["kept_lines"] + (a["dropped"] if tag == "axis_dropped" else [])
            items.append((r["tile"], Point(x, y), f"{r['tile'][7:16]} {tag} {value:.0f} m2", a["net_polys"], extra, m))
        print(sheet(items, WORK / f"missing_{tag}.jpg", columns=3, size=420))


if __name__ == "__main__":
    sheets() if "--sheets" in sys.argv else (None if "--outlines" in sys.argv else main())


def outline_sheet() -> None:
    """Vineyard outlines (cyan-ish yellow) where no predicted block (red) lies: a crop at the uncovered part of each."""
    from canopy_rules_sheet import sheet
    plots_ = load_plots(WORK / "plots_now.json")
    blocks = [shape(f["geometry"]) for f in plots_ if f["properties"]["label"] == "block"]
    union = unary_union(blocks)
    outlines = [shape(v["geometry"]) for v in load_verdicts() if v["kind"] == "plot" and v["label"] == "vineyard"]
    tiles = load_tiles()
    items = []
    for k, o in sorted(enumerate(outlines), key=lambda x: -x[1].difference(union).area)[:12]:
        gap = o.difference(union)
        point = gap.representative_point()
        tile = next((t for t in tiles if t.bounds.contains(point)), None)
        if tile is None or len(items) >= 9:
            continue
        items.append((tile.name, point, f"outline {k} {tile.name[7:16]} no block on {gap.area:.0f} of {o.area:.0f} m2", blocks, [o], None))
    print(sheet(items, WORK / "missing_no_plot.jpg", columns=3, size=600))


if __name__ == "__main__" and "--outlines" in sys.argv:
    outline_sheet()
