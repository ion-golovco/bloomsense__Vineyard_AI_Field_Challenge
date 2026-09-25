"""Vineyard plots from row periodicity, with no training: vine rows repeat every 2.0-3.6 m,
orchard trees every 3.8-7 m. Scored in 25.6 m windows over one seamless 0.2 m/px mosaic."""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.features import shapes
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from marcaj.mosaic import MOSAIC_PX_M, load_mosaic
from marcaj.tiles import DATA_DIR, REPO_ROOT

SCORES_PATH = REPO_ROOT / "data" / "generated" / "window_scores.npz"
WINDOW_PX = 128
STEP_PX = 32


@dataclass(frozen=True)
class MaskParams:
    vine_min: float = 60.0
    vine_over_orchard: float = 2.0
    closing: int = 1
    opening: int = 1
    min_area_m2: float = 200.0
    simplify_m: float = 3.0


@dataclass(frozen=True)
class Scores:
    vine: np.ndarray
    orchard: np.ndarray
    transform: Affine


def _mosaic(data_dir: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    rgb, transform = load_mosaic(data_dir)
    red, green, blue = rgb.astype(np.float32)
    total = red + green + blue
    return (2 * green - red - blue) / np.maximum(total, 1), total / 3, total > 30, transform.c, transform.f


def compute_scores(data_dir: Path = DATA_DIR) -> Scores:
    exg, lum, valid, left, top = _mosaic(data_dir)
    frequency = np.fft.fftfreq(WINDOW_PX, d=MOSAIC_PX_M)
    radius = np.hypot(*np.meshgrid(frequency, frequency))
    vine_band = (radius >= 1 / 3.6) & (radius <= 1 / 2.0)
    orchard_band = (radius >= 1 / 7.0) & (radius <= 1 / 3.8)
    taper = np.outer(np.hanning(WINDOW_PX), np.hanning(WINDOW_PX))
    rows = (valid.shape[0] - WINDOW_PX) // STEP_PX + 1
    cols = (valid.shape[1] - WINDOW_PX) // STEP_PX + 1
    vine = np.zeros((rows, cols), np.float32)
    orchard = np.zeros_like(vine)
    for i in range(rows):
        band = slice(i * STEP_PX, i * STEP_PX + WINDOW_PX)
        windows_valid = np.lib.stride_tricks.sliding_window_view(valid[band], (WINDOW_PX, WINDOW_PX))[0, ::STEP_PX]
        usable = windows_valid.mean(axis=(1, 2)) > 0.6
        if not usable.any():
            continue
        mask = windows_valid[usable]
        for channel in (exg, lum):
            windows = np.lib.stride_tricks.sliding_window_view(channel[band], (WINDOW_PX, WINDOW_PX))[0, ::STEP_PX][usable]
            mean = (windows * mask).sum(axis=(1, 2)) / mask.sum(axis=(1, 2))
            power = np.abs(np.fft.fft2((windows - mean[:, None, None]) * mask * taper)) ** 2
            base = np.median(power[:, vine_band | orchard_band], axis=1) + 1e-12
            vine[i, usable] = np.maximum(vine[i, usable], power[:, vine_band].max(axis=1) / base)
            orchard[i, usable] = np.maximum(orchard[i, usable], power[:, orchard_band].max(axis=1) / base)
    cell = STEP_PX * MOSAIC_PX_M
    offset = WINDOW_PX / 2 * MOSAIC_PX_M - cell / 2
    return Scores(vine, orchard, Affine(cell, 0, left + offset, 0, -cell, top - offset))


def load_scores(data_dir: Path = DATA_DIR, path: Path = SCORES_PATH, refresh: bool = False) -> Scores:
    if path.is_file() and not refresh:
        saved = np.load(path)
        return Scores(saved["vine"], saved["orchard"], Affine(*saved["transform"]))
    started = time.perf_counter()
    scores = compute_scores(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, vine=scores.vine, orchard=scores.orchard, transform=np.array(scores.transform[:6]))
    print(f"window scores computed in {time.perf_counter() - started:.1f} s, cached at {path}")
    return scores


def _constraint(route_dir: Path, filename: str):
    return unary_union([shape(feature["geometry"]) for feature in json.loads((route_dir / filename).read_text(encoding="utf-8"))["features"]])


def detect_plots(scores: Scores, params: MaskParams = MaskParams(), data_dir: Path = DATA_DIR) -> list[dict[str, Any]]:
    mask = (scores.vine > params.vine_min) & (scores.vine > params.vine_over_orchard * scores.orchard)
    if params.closing:
        mask = ndimage.binary_closing(mask, iterations=params.closing)
    if params.opening:
        mask = ndimage.binary_opening(mask, iterations=params.opening)
    blobs = unary_union([shape(geometry) for geometry, value in shapes(mask.astype(np.uint8), mask=mask, transform=scores.transform) if value])
    route_dir = data_dir / "02_route"
    blocks = blobs.difference(_constraint(route_dir, "passages.geojson")).difference(_constraint(route_dir, "forbidden.geojson"))
    parts = sorted((part for part in getattr(blocks, "geoms", [blocks]) if part.area >= params.min_area_m2), key=lambda part: -part.area)
    with np.errstate(invalid="ignore", divide="ignore"):
        rectangles = [part.minimum_rotated_rectangle.area for part in parts]
    return [{
        "type": "Feature",
        "geometry": mapping(part.simplify(params.simplify_m, preserve_topology=True)),
        "properties": {"label": "block", "vineyard_id": f"P{number:02d}", "area_m2": round(part.area),
                       "rectangularity": round(part.area / rectangle, 2) if rectangle else None, "params": asdict(params)},
    } for number, (part, rectangle) in enumerate(zip(parts, rectangles), start=1)]
