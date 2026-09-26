"""Shared harness for the canopy-rules probes: the two organizer tiles, their reference objects, cached predicted
rows, and a fast canopy scorer with the judge's own formulas (`marcaj.judge._match`, `_iou`). Evaluation only.
The scorer skips the judge's CVAT round trip (clip + 0.01 px snap), which moves the score by < 0.001."""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from rasterio.features import rasterize
from shapely.geometry import shape
from shapely.ops import unary_union

from marcaj.canopy import RowSet, plot_rows, read_rgb
from marcaj.cvat import build_scene
from marcaj.judge import _f1, _iou, _match
from marcaj.tiles import DATA_DIR, REPO_ROOT, load_tiles

NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
WORK = REPO_ROOT / "data" / "generated" / "work" / "canopy_rules"
# one frozen `detect_plots()` output, so trials don't move with the plots agent's edits; CANOPY_PLOTS picks another
PLOTS = Path(os.environ.get("CANOPY_PLOTS", WORK / "plots.json"))

tiles = {tile.name: tile for tile in load_tiles()}
base = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(tiles.values()))["features"]
reference = [f for f in base if f["properties"]["source"] == "reference"]
plots = json.loads(PLOTS.read_text())
predicted_rows = plot_rows(plots)
images = {name: read_rgb(tiles[name]) for name in NAMES}


def ref_objects(name: str, label: str) -> list:
    return [shape(f["geometry"]) for f in reference if f["properties"]["tile"] == name and f["properties"]["label"] == label]


def reference_rows(name: str) -> list[RowSet]:
    """Diagnostic only: the hand-drawn rows of the tile as one plot."""
    rows = ref_objects(name, "row")
    (x0, y0), (x1, y1) = max(rows, key=lambda r: r.length).coords[:2]
    return [RowSet("REF", float(np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 180), rows)]


def raster(name: str, polygons: list) -> np.ndarray:
    _, transform = images[name]
    return rasterize(polygons, out_shape=(2048, 2048), transform=transform).astype(bool) if polygons else np.zeros((2048, 2048), bool)


@dataclass
class Score:
    canopy: float
    iou: float
    f1: float
    tiles: dict[str, dict[str, Any]]

    def line(self, tag: str, seconds: float | None = None) -> str:
        parts = [f"{tag:44s} {self.canopy:.3f} (IoU {self.iou:.3f}, F1 {self.f1:.3f})"]
        for name, t in self.tiles.items():
            parts.append(f"{name[7:16]} {t['score']:.3f} IoU {t['iou']:.3f} F1 {t['f1']:.3f} n {t['pred']}/{t['ref']}")
        if seconds is not None:
            parts.append(f"{seconds:.2f} s/tile")
        return " | ".join(parts)


def score(polygons_by_tile: dict[str, list]) -> Score:
    """0.6 x union IoU + 0.4 x one-to-one F1 at IoU 0.5, pooled over both tiles as the judge pools them."""
    inter = union = tp = n_pred = n_ref = 0.0
    per = {}
    for name in NAMES:
        pred = [(p, {}) for p in polygons_by_tile.get(name, [])]
        ref = [(r, {}) for r in ref_objects(name, "vineyard")]
        pairs = _match(pred, ref, _iou, 0.5)
        pu, ru = unary_union([g for g, _ in pred]), unary_union([g for g, _ in ref])
        i, u = pu.intersection(ru).area, pu.union(ru).area
        inter, union, tp, n_pred, n_ref = inter + i, union + u, tp + len(pairs), n_pred + len(pred), n_ref + len(ref)
        f1 = _f1(len(pairs), len(pred), len(ref)) or 0.0
        per[name] = {"iou": i / u, "f1": f1, "score": 0.6 * i / u + 0.4 * f1, "pred": len(pred), "ref": len(ref), "tp": len(pairs)}
    iou, f1 = inter / union, _f1(int(tp), int(n_pred), int(n_ref)) or 0.0
    return Score(0.6 * iou + 0.4 * f1, iou, f1, per)


def run(tag: str, predict: Callable[[str], list], quiet: bool = False) -> tuple[Score, dict[str, list]]:
    """`predict(name)` returns the polygons of one tile; prints the judge line with per-tile seconds."""
    found, spent = {}, []
    for name in NAMES:
        started = time.perf_counter()
        found[name] = predict(name)
        spent.append(time.perf_counter() - started)
    result = score(found)
    if not quiet:
        print(result.line(tag, float(np.mean(spent))), flush=True)
    return result, found


def save(path: Path, found: dict[str, list]) -> None:
    from shapely.geometry import mapping
    path.write_text(json.dumps({name: [mapping(p) for p in polygons] for name, polygons in found.items()}))
