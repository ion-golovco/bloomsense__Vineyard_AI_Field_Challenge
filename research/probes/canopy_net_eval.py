"""Scores canopy variants on the two organizer reference tiles with the organizer formulas (`marcaj.judge`), using the rows
frozen in data/generated/work/canopy_net/plots.json so every variant and the rule baseline see the same axes.
Evaluation only (reads the organizer examples, never data/review/ for prediction). Imported by the other canopy_net
probes; run alone for the rule baseline: uv run --frozen --group sam python ../research/probes/canopy_net_eval.py [live]
(`live` re-runs detect_plots with the current plots.py instead of the frozen rows)."""

import json
import sys
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from rasterio.features import rasterize
from shapely.geometry import shape

from marcaj.canopy import plot_rows, read_rgb, tile_canopies
from marcaj.cvat import build_scene
from marcaj.judge import judge
from marcaj.tiles import DATA_DIR, REPO_ROOT, load_tiles

WORK = REPO_ROOT / "data" / "generated" / "work" / "canopy_net"
NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
tiles = {tile.name: tile for tile in load_tiles()}
base = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(tiles.values()))["features"]
reference = [f for f in base if f["properties"]["source"] == "reference"]
rows = plot_rows(json.loads((WORK / "plots.json").read_text()))
images = {name: read_rgb(tiles[name]) for name in NAMES}


def ref_polygons(name: str, label: str = "vineyard") -> list:
    return [shape(f["geometry"]) for f in reference if f["properties"]["tile"] == name and f["properties"]["label"] == label]


def raster(name: str, polygons: list) -> np.ndarray:
    return rasterize(polygons, out_shape=(2048, 2048), transform=images[name][1]).astype(bool) if polygons else np.zeros((2048, 2048), bool)


def score(tag: str, per_tile: dict[str, list[dict[str, Any]]], seconds: float = 0.0, explain: bool = False) -> dict[str, Any]:
    """Judge canopy score on both tiles, and per tile: score, union IoU, F1, predicted/reference count, predicted/reference area."""
    report = judge({"type": "FeatureCollection", "crs": "EPSG:32635", "features": base + [f for found in per_tile.values() for f in found]})
    by_tile = {t["tile"]: t for t in report["tiles"]}
    out = {"tag": tag, "canopy": report["scores"]["canopy"], "iou": report["canopy_iou"], "f1": report["canopy_f1"], "tiles": {}, "seconds": seconds}
    line = f"{tag:44s} canopy {out['canopy']:.3f} (IoU {out['iou']:.3f}, F1 {out['f1']:.3f})"
    for name in NAMES:
        t = by_tile[name]
        pred, ref = t["counts"]["vineyard"]
        area = sum(shape(f["geometry"]).area for f in per_tile.get(name, []))
        out["tiles"][name] = {"score": 0.6 * t["canopy_iou"] + 0.4 * t["canopy_f1"], "iou": t["canopy_iou"], "f1": t["canopy_f1"],
                              "n": [pred, ref], "area_m2": [round(area, 1), round(sum(p.area for p in ref_polygons(name)), 1)]}
        line += f" | {name[7:16]} {out['tiles'][name]['score']:.3f} IoU {t['canopy_iou']:.3f} F1 {t['canopy_f1']:.3f} n {pred}/{ref} m2 {area:.0f}/{out['tiles'][name]['area_m2'][1]:.0f}"
    print(line + (f" | {seconds:.1f} s" if seconds else ""), flush=True)
    if explain:
        for name in NAMES:
            out["tiles"][name]["errors"] = errors(name, per_tile.get(name, []))
            print(f"    {name[7:16]} {out['tiles'][name]['errors']}", flush=True)
    return out


def errors(name: str, found: list[dict[str, Any]]) -> dict[str, float]:
    """Why reference canopies are not matched at IoU 0.5: missed (no prediction over 10% of it), merged (a prediction over
    10% of it also covers 10% of another), split (2+ predictions), else boundary (one prediction, IoU < 0.5); predictions
    covering no reference canopy (false, e.g. weeds or shadow); pixel precision and recall."""
    from shapely import STRtree

    from marcaj.judge import _iou, _match

    refs, preds = ref_polygons(name), [shape(f["geometry"]) for f in found]
    matched = {j for _, j in _match([(p, {}) for p in preds], [(r, {}) for r in refs], _iou, 0.5)}
    tree_p, tree_r = STRtree(preds), STRtree(refs)
    covers = lambda p, r: p.intersection(r).area > 0.1 * r.area
    out = {"matched": len(matched), "missed": 0, "merged": 0, "split": 0, "boundary": 0}
    for j, r in enumerate(refs):
        if j in matched:
            continue
        over = [preds[i] for i in tree_p.query(r) if covers(preds[i], r)]
        if not over:
            out["missed"] += 1
        elif any(sum(covers(p, refs[k]) for k in tree_r.query(p)) >= 2 for p in over):
            out["merged"] += 1
        elif len(over) >= 2:
            out["split"] += 1
        else:
            out["boundary"] += 1
    out["false"] = sum(1 for p in preds if not any(p.intersection(refs[k]).area > 0.1 * p.area for k in tree_r.query(p)))
    truth, guess = raster(name, refs), raster(name, preds)
    out["px_precision"] = round(float((truth & guess).sum() / max(guess.sum(), 1)), 3)
    out["px_recall"] = round(float((truth & guess).sum() / max(truth.sum(), 1)), 3)
    return out


def run(tag: str, detect: Callable[[str], list[dict[str, Any]]], explain: bool = True) -> dict[str, Any]:
    started = time.perf_counter()
    found = {name: detect(name) for name in NAMES}
    return score(tag, found, time.perf_counter() - started, explain)


def baseline(rowsets=None) -> dict[str, Any]:
    rowsets = rowsets or rows
    return run("rule baseline (marcaj.canopy, live)", lambda name: tile_canopies(tiles[name], rowsets, rgb=images[name]))


if __name__ == "__main__":
    baseline()
    if "live" in sys.argv[1:]:
        from marcaj.plots import detect_plots

        rowsets = plot_rows(detect_plots())
        run("rule baseline, live plots.py rows", lambda name: tile_canopies(tiles[name], rowsets, rgb=images[name]))
