"""Plot recall against the lab's hand-drawn outlines: per-outline coverage, best IoU and the stage where each outline is
lost, plus the half scores (judge.plot_scores), vineyard-area recall and area on orchards. Evaluation only (reads
data/review/). Run from backend/:
uv run --frozen python ../research/probes/plots_recall_eval.py [name=value ...] [out=name.geojson] [src=file.geojson] [diag=1]"""

import json
import sys
import time
from functools import lru_cache
from dataclasses import fields, replace

import numpy as np
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import shape
from shapely.ops import unary_union

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from marcaj import plots
from marcaj.judge import PLOT_SPLIT_NORTHING, _iou, plot_scores
from marcaj.layers import LAYER_PX_M, load_layers
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots_recall"


@lru_cache(maxsize=1)
def _footprint():
    from plot_experiment import tile_footprint
    return tile_footprint()


def outlines() -> list[tuple[int, str, object]]:
    return [(i, v["label"], shape(v["geometry"])) for i, v in enumerate(v for v in load_verdicts() if v["kind"] == "plot")]


def summary(features: list[dict]) -> dict:
    verdicts = load_verdicts()
    s = plot_scores(features, verdicts)
    blocks = unary_union([shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"])
    rows = unary_union([shape(f["geometry"]) for f in features if f["properties"]["label"] == "row"])
    by = {label: unary_union([g for _, l, g in outlines() if l == label]) for label in ("vineyard", "orchard", "overgrown")}
    s["recall"] = blocks.intersection(by["vineyard"]).area / by["vineyard"].area
    # what a detector can reach: inside the imagery, off passages and forbidden zones (1 m margin)
    reachable = by["vineyard"].intersection(_footprint()).difference(plots.exclusions().buffer(1.0))
    s["recall_reachable"] = blocks.intersection(reachable).area / reachable.area
    s["missed_reachable_m2"] = reachable.difference(blocks).area
    s["orchard_m2"] = blocks.intersection(by["orchard"]).area
    s["overgrown_cover"] = blocks.intersection(by["overgrown"]).area / by["overgrown"].area
    s["false_m2"] = blocks.difference(unary_union(list(by.values()))).area
    s["rows_m"] = rows.length
    s["rows_out_m"] = rows.difference(unary_union(list(by.values()))).length
    s["plots"] = sum(f["properties"]["label"] == "block" for f in features)
    return s


def line(s: dict) -> str:
    n, so = s["north"], s["south"]
    return (f"N {n['f1_50']:.3f}/{n['f1_75']:.3f} S {so['f1_50']:.3f}/{so['f1_75']:.3f} | area IoU N {n['area_iou']:.3f} S {so['area_iou']:.3f} | "
            f"recall {s['recall']:.3f} (reachable {s['recall_reachable']:.3f}, missed {s['missed_reachable_m2']:,.0f} m2) | orchard {s['orchard_m2']:,.0f} m2 | false(outside all) {s['false_m2']:,.0f} m2 | "
            f"overgrown cover {s['overgrown_cover']:.2f} | plots {s['plots']} | rows {s['rows_m']:,.0f} m, outside {s['rows_out_m']:,.0f} m")


def per_outline(features: list[dict], params: plots.PlotParams, diag: bool) -> list[dict]:
    blocks = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"]
    union = unary_union(blocks)
    rows_out = []
    if diag:
        layers, excess, roads = load_layers(), plots.load_excess(), plots.exclusions()
        blocked = rasterize([roads], out_shape=layers.valid.shape, transform=layers.transform).astype(bool)
        usable = layers.valid & ~blocked
        seeds = (layers.vine_over_orchard > params.ratio_min) & usable
        opened = ndimage.binary_opening(seeds, iterations=max(1, round(params.opening_m / LAYER_PX_M)))
        region_union = unary_union(plots._regions(layers, blocked, params))
    for i, label, g in outlines():
        best = max((_iou(g, b) for b in blocks if b.intersects(g)), default=0.0)
        row = {"i": i, "label": label, "half": "N" if g.centroid.y >= PLOT_SPLIT_NORTHING else "S", "m2": round(g.area),
               "cover": round(union.intersection(g).area / g.area, 3), "iou": round(best, 3),
               "x": round(g.centroid.x), "y": round(g.centroid.y)}
        if diag:
            inside = rasterize([g], out_shape=layers.valid.shape, transform=layers.transform).astype(bool)
            ratio = layers.vine_over_orchard[inside & usable]
            angle, spacing = plots._row_angle(excess, g)
            mrr = np.asarray(g.minimum_rotated_rectangle.exterior.coords)
            row |= {"width": round(float(min(np.hypot(*np.diff(mrr, axis=0).T)[:2])), 1),
                    "ratio_q50": round(float(np.median(ratio)), 2) if ratio.size else 0, "ratio_q90": round(float(np.percentile(ratio, 90)), 2) if ratio.size else 0,
                    "seed": round(float(seeds[inside].mean()), 3), "opened": round(float(opened[inside].mean()), 3),
                    "region": round(region_union.intersection(g).area / g.area, 3),
                    "road": round(g.intersection(roads).area / g.area, 3),
                    "angle": round(angle, 1), "spacing": round(spacing, 2),
                    "wave": round(plots._wave(excess, g, angle, spacing), 4) if spacing else 0,
                    "power": round(plots._row_power(excess, g, angle, 0.2)[0], 2),
                    "green": round(float(np.nanmedian(layers.green_share[inside])), 2),
                    "orch": round(float(np.median(layers.orchard_energy[inside & usable])) * 1e4, 2) if ratio.size else 0}
        rows_out.append(row)
    return rows_out


if __name__ == "__main__":
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    out, src, diag = args.pop("out", ""), args.pop("src", ""), args.pop("diag", "") == "1"
    base = plots.PlotParams()
    kinds = {f.name: type(getattr(base, f.name)) for f in fields(base)}
    params = replace(base, **{k: kinds[k](v) for k, v in args.items()})
    started = time.perf_counter()
    features = json.loads((WORK / src).read_text())["features"] if src else plots.detect_plots(params)
    elapsed = time.perf_counter() - started
    WORK.mkdir(parents=True, exist_ok=True)
    s = summary(features)
    print(f"{src or args or 'defaults'} ({elapsed:.0f} s): {line(s)}")
    table = per_outline(features, params, diag)
    if diag or out:
        name = (out or src or "run").replace(".geojson", "")
        (WORK / f"{name}_outlines.json").write_text(json.dumps({"summary": s, "outlines": table}, indent=1))
    for r in sorted(table, key=lambda r: r["iou"]):
        if r["label"] == "vineyard" and r["iou"] < 0.6:
            print("  ", {k: v for k, v in r.items() if k not in ("x", "y")})
    if out:
        (WORK / out).write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}))
