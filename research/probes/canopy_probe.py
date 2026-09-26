"""Canopy detector (`marcaj.canopy`) on the two organizer reference tiles, scored with the organizer formulas
(`marcaj.judge`: 0.6 x union IoU + 0.4 x one-to-one F1 at IoU 0.5), plus pixel IoU, row-axis error and a preview.
Evaluation only. Variant U tubes the reference rows: a diagnostic that separates row error from canopy error; O is
the previous method (normalised ExG, contrast test). research/probes/canopy_rules_*.py hold the error analysis and trials.
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
    print(f"{tag:44s} canopy {report['scores']['canopy']:.3f} (IoU {report['canopy_iou']:.3f}, F1 {report['canopy_f1']:.3f})", end="")
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
print(f"\ndefaults {default}\nvariant | both tiles | per tile: score, union IoU, instance F1, predicted/reference, pixel IoU, seconds")
old = replace(default, green_dn=0, refine2_m=0, row_contrast=1.3, row_gap=0, row_value=0, close_m=0, inset_m=0, young_pieces=0, strip_gr=0)
results = {"S": run("S defaults", default)}
run("O previous defaults (ExG > 0.11, contrast 1.3)", old)
run("U reference rows (diagnostic)", replace(default, refine_m=0, row_gap=0), reference_rows)
print("\neach rule off, one at a time")
for tag, change in (("normalised ExG > 0.11 instead of DN", dict(green_dn=0)), ("no inter-row drop", dict(row_gap=0)),
                    ("no inter-row drop, contrast 1.3", dict(row_gap=0, row_contrast=1.3)), ("inter-row drop + contrast 1.3", dict(row_contrast=1.3)), ("no value-contrast test", dict(row_value=0)), ("no young-block minimum", dict(young_pieces=0)),
                    ("no grass-strip rule", dict(strip_gr=0)),
                    ("no second refit", dict(refine2_m=0)), ("no refit (rows as given)", dict(refine_m=0)), ("no closing", dict(close_m=0)),
                    ("no inset", dict(inset_m=0)), ("no neck split", dict(split_neck=0)), ("Otsu threshold in the tube", dict(otsu=True))):
    run(f"S {tag}", replace(default, **change))
print("\none-at-a-time sensitivity on S (every value tried is listed)")
for field, values in (("green_dn", (22.0, 27.0, 30.0)), ("smooth_m", (0.025, 0.075)), ("close_m", (0.025, 0.075)), ("row_gap", (0.5, 0.85)),
                      ("inset_m", (0.005, 0.02)), ("tube_m", (0.25, 0.35)), ("min_area_m2", (0.15, 0.25)), ("split_neck", (0.2, 0.4)),
                      ("refine2_m", (0.2,)), ("row_value", (1.3, 1.5))):
    for value in values:
        run(f"S {field}={value}", replace(default, **{field: value}))

per_tile = np.mean(results["S"]["seconds"])
print(f"\nS runtime {per_tile:.2f} s per vineyard tile (read + detect + polygonise) -> about {per_tile * 311 / 60:.1f} min for all 311 tiles on one core, "
      f"an upper bound since tiles without rows are skipped")
for name, found in zip(NAMES, results["S"]["predictions"]):
    print("preview:", preview(name, found))
