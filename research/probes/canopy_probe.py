"""Canopy detector (`marcaj.canopy`) on the two organizer reference tiles, scored with the organizer formulas
(`marcaj.judge`: 0.6 x union IoU + 0.4 x one-to-one F1 at IoU 0.5), plus pixel IoU, row-axis error and a preview.
Evaluation only. Variant U tubes the reference rows: a diagnostic that separates row error from canopy error.
Run from backend/: uv run --frozen python ../research/probes/canopy_probe.py [plots.json]
(an optional cached `detect_plots()` output skips its ~20 s)."""

import json
import sys
import time
import warnings
from dataclasses import replace
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely.geometry import shape

from marcaj.canopy import CanopyParams, RowSet, plot_rows, read_rgb, tile_canopies, tube
from marcaj.cvat import build_scene
from marcaj.judge import judge
from marcaj.plots import detect_plots
from marcaj.tiles import DATA_DIR, REPO_ROOT, load_tiles

NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
tiles = {tile.name: tile for tile in load_tiles()}
base = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(tiles.values()))["features"]
reference = [f for f in base if f["properties"]["source"] == "reference"]
cached = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if cached and cached.is_file():
    plots = json.loads(cached.read_text())
else:
    started = time.perf_counter()
    plots = detect_plots()
    print(f"detect_plots {time.perf_counter() - started:.1f} s")
    if cached:
        cached.write_text(json.dumps(plots))
predicted_rows = plot_rows(plots)
images = {name: read_rgb(tiles[name]) for name in NAMES}


def ref_objects(name: str, label: str) -> list:
    return [shape(f["geometry"]) for f in reference if f["properties"]["tile"] == name and f["properties"]["label"] == label]


def reference_rows(name: str) -> list[RowSet]:
    """Diagnostic only: the hand-drawn rows of the tile as one plot."""
    rows = ref_objects(name, "row")
    (x0, y0), (x1, y1) = max(rows, key=lambda r: r.length).coords[:2]
    return [RowSet("REF", float(np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 180), rows)]


def axis_error(name: str) -> str:
    """Lateral distance from each reference row's midpoint to the nearest predicted row."""
    axes = [a for plot in predicted_rows for a in plot.axes if a.intersects(tiles[name].bounds)]
    gaps = np.array([min(a.distance(r.interpolate(0.5, normalized=True)) for a in axes) for r in ref_objects(name, "row")])
    return f"{len(axes)} predicted rows cross the tile; ref-row offset median {np.median(gaps):.3f} m, within 0.3 m {np.mean(gaps <= 0.3):.0%}"


def pixel_iou(name: str, polygons: list) -> float:
    _, transform = images[name]
    truth = rasterize(ref_objects(name, "vineyard"), out_shape=(2048, 2048), transform=transform).astype(bool)
    guess = rasterize(polygons, out_shape=(2048, 2048), transform=transform).astype(bool) if polygons else np.zeros_like(truth)
    return float((truth & guess).sum() / (truth | guess).sum())


def run(tag: str, params: CanopyParams, rows_for=lambda name: predicted_rows) -> dict:
    predictions, seconds = [], []
    for name in NAMES:
        started = time.perf_counter()
        found = tile_canopies(tiles[name], rows_for(name), params)  # reads the tile itself, as a full run would
        seconds.append(time.perf_counter() - started)
        predictions.append(found)
    report = judge({"type": "FeatureCollection", "crs": "EPSG:32635", "features": base + [f for found in predictions for f in found]})
    by_tile = {t["tile"]: t for t in report["tiles"]}
    print(f"{tag:34s} canopy {report['scores']['canopy']:.3f} (IoU {report['canopy_iou']:.3f}, F1 {report['canopy_f1']:.3f})", end="")
    for name, found, spent in zip(NAMES, predictions, seconds):
        t = by_tile[name]
        pred, ref = t["counts"]["vineyard"]
        print(f" | {name[7:16]} {0.6 * t['canopy_iou'] + 0.4 * t['canopy_f1']:.3f} IoU {t['canopy_iou']:.3f} F1 {t['canopy_f1']:.3f}"
              f" n {pred}/{ref} px {pixel_iou(name, [shape(f['geometry']) for f in found]):.3f} {spent:.1f}s", end="")
    print()
    return {"report": report, "predictions": predictions, "seconds": seconds}


def outline(image: np.ndarray, transform, polygons: list, colour: tuple[int, int, int]) -> np.ndarray:
    out = image.copy()
    edge = rasterize([p.boundary for p in polygons], out_shape=image.shape[1:], transform=transform, all_touched=True).astype(bool) if polygons else None
    if edge is not None:
        for band, value in enumerate(colour):
            out[band][edge] = value
    return out


def preview(name: str, found: list, size: int = 480) -> Path:
    """Reference outlines (magenta, left) beside predicted outlines (yellow, right), a 12 m crop at the tile centre."""
    image, transform = images[name]
    crop = slice(1024 - size // 2, 1024 + size // 2)
    left = outline(image, transform, ref_objects(name, "vineyard"), (255, 0, 255))[:, crop, crop]
    right = outline(image, transform, [shape(f["geometry"]) for f in found], (255, 255, 0))[:, crop, crop]
    both = np.concatenate([left, np.full((3, size, 8), 255, np.uint8), right], axis=2)
    path = REPO_ROOT / "data" / "generated" / f"canopy_preview_{name[:-4]}.jpg"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with rasterio.open(path, "w", driver="JPEG", width=both.shape[2], height=both.shape[1], count=3, dtype="uint8", quality=90) as out:
            out.write(both)
    return path


for name in NAMES:
    print(f"{name}: {axis_error(name)}")
    for tag, rows in (("predicted (as given)", predicted_rows), ("reference", reference_rows(name))):
        inside = tube([a for plot in rows for a in plot.axes], images[name][1], (2048, 2048), CanopyParams().tube_m)
        truth = rasterize(ref_objects(name, "vineyard"), out_shape=(2048, 2048), transform=images[name][1]).astype(bool)
        print(f"  reference canopy inside the +-0.3 m tube of the {tag} rows: {(truth & inside).sum() / truth.sum():.1%}")

default = CanopyParams()
print(f"\ndefaults {default}; A is the defaults without the neck split\nvariant | both tiles | per tile: score, union IoU, instance F1, predicted/reference, pixel IoU, seconds")
plain = replace(default, split_neck=0.0)
raw = replace(plain, refine_m=0, row_contrast=0)
results = {"A": run("A predicted rows, no split", plain)}
run("A0 predicted rows as given", raw)
run("A1 A without the contrast filter", replace(plain, row_contrast=0))
run("U reference rows (diagnostic)", raw, reference_rows)
results["S"] = run("S A + split neck 0.3 (defaults)", default)
run("S5 A + split neck 0.5", replace(default, split_neck=0.5))
run("U + split neck 0.3 (diagnostic)", replace(raw, split_neck=0.3), reference_rows)
run("A + Otsu threshold in the tube", replace(plain, otsu=True))
print("\none-at-a-time sensitivity on A (every value tried is listed)")
for field, values in (("exg_min", (0.08, 0.14)), ("tube_m", (0.2, 0.4)), ("smooth_m", (0.0, 0.1)),
                      ("min_area_m2", (0.1, 0.3)), ("refine_m", (0.3, 0.7)), ("row_contrast", (2.0,))):
    for value in values:
        run(f"A {field}={value}", replace(plain, **{field: value}))

per_tile = np.mean(results["S"]["seconds"])
print(f"\nS runtime {per_tile:.2f} s per vineyard tile (read + detect + polygonise) -> about {per_tile * 311 / 60:.1f} min for all 311 tiles on one core, "
      f"an upper bound since tiles without rows are skipped")
for name, found in zip(NAMES, results["S"]["predictions"]):
    print("preview:", preview(name, found))
