"""Sentinel-2 L2A before the 21 May 2025 flight: NDVI (B04/B08, 10 m) and NDMI (B8A/B11, 20 m) per vineyard plot, and
zones inside a plot that stay low against the rest of the plot on every clear scene.

Source: Earth Search STAC `sentinel-2-c1-l2a`, MGRS tile 35TPN, which is on EPSG:32635 like the drone, so windows are
read on the native grid with no resampling. Reflectance = DN x 1e-4 - 0.1 (the asset's `raster:bands` scale and
offset; without the offset NDVI is biased). Only a 1.76 x 1.82 km window over the study area is read from each COG over
HTTP, no auth, and cached under data/raw/external/sentinel/ (one .npz per scene, about 0.2 MB). Cloud, shadow, snow and
no-data come from the scene classification layer (SCL): a scene is used when 99% of the study area is free of them,
and a pixel only when it is vegetation (4) or bare soil (5).

What a flag means: a 10 m pixel holds about 4 rows of vines and their inter-rows, and before 21 May the vines are
short shoots, so NDVI there is mostly inter-row cover: grassed inter-rows raise it, fresh tillage lowers it. A low
zone is a place where the plot is less green or drier than the rest of it on every date, which a missing block of
vines, young replanting, bare or eroded soil, or a different floor management all produce. It cannot see a 5 m gap
(about 2.5% of a pixel). It is a prompt for the drone evidence (`marcaj.poi.sentinel_pois`), never a finding alone.
Run: uv run --frozen python -m marcaj.sentinel"""

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.transform import from_origin
from rasterio.windows import from_bounds
from scipy import ndimage
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from marcaj.tiles import DATA_DIR, REPO_ROOT

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
CACHE = REPO_ROOT / "data" / "raw" / "external" / "sentinel"
STAC = "https://earth-search.aws.element84.com/v1/search"
FLIGHT = "2025-05-21"
BANDS = ("red", "nir", "nir08", "swir16", "scl")  # B04, B08 (10 m); B8A, B11, SCL (20 m)
CLEAR_SCL = (4, 5)
CLOUD_SCL = (0, 1, 3, 8, 9, 10, 11)  # no-data, saturated, cloud shadow, cloud (3 levels), snow; 6 is a 3.7% pond
MIN_CLEAR = 0.99      # share of the study area's SCL pixels that must be free of CLOUD_SCL
MIN_DAYS = 4          # between chosen scenes
SHRINK_M = 5.0        # plot edge pixels mix vines with roads and neighbours
LOW_Z = -1.5          # robust z within the plot, on every chosen scene
MIN_PLOT_PX = 12


def _study_window(data_dir: Path = DATA_DIR) -> tuple[float, float, float, float]:
    study = shape(json.loads((data_dir / "02_route" / "study_area.geojson").read_text())["features"][0]["geometry"])
    x0, y0, x1, y1 = study.bounds
    return float(np.floor(x0 / 20) * 20), float(np.floor(y0 / 20) * 20), float(np.ceil(x1 / 20) * 20), float(np.ceil(y1 / 20) * 20)


def search(end: str = FLIGHT, days: int = 45, max_cloud: float = 50.0) -> list[dict[str, Any]]:
    """35TPN items in the `days` before `end`, newest first, cached as search.json."""
    path = CACHE / f"search_{end}_{days}.json"
    if not path.is_file():
        start = str(np.datetime64(end) - np.timedelta64(days, "D"))
        body = json.dumps({"collections": ["sentinel-2-c1-l2a"], "bbox": [28.700, 47.113, 28.724, 47.130], "limit": 100,
                           "datetime": f"{start}T00:00:00Z/{end}T00:00:00Z", "query": {"eo:cloud_cover": {"lt": max_cloud}}}).encode()
        request = urllib.request.Request(STAC, body, {"Content-Type": "application/json"})
        items = json.load(urllib.request.urlopen(request, timeout=60))["features"]
        items = [item for item in items if "35TPN" in item["id"]]
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items), encoding="utf-8")
    return sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda item: item["properties"]["datetime"], reverse=True)


def _read(href: str, bounds: tuple[float, float, float, float]) -> np.ndarray:
    with rasterio.open(href) as source:
        return source.read(1, window=from_bounds(*bounds, source.transform))


def load(item: dict[str, Any], bands: tuple[str, ...] = BANDS) -> dict[str, np.ndarray]:
    """The study-area window of `bands` (raw DN), cached per item and band set."""
    path = CACHE / f"{item['id']}.npz"
    cached = dict(np.load(path)) if path.is_file() else {}
    missing = [band for band in bands if band not in cached]
    if missing:
        bounds = _study_window()
        cached.update({band: _read(item["assets"][band]["href"], bounds) for band in missing})
        CACHE.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, **cached)
    return {band: cached[band] for band in bands}


def _reflectance(item: dict[str, Any], band: str, dn: np.ndarray) -> np.ndarray:
    meta = (item["assets"][band].get("raster:bands") or [{}])[0]
    return dn.astype(np.float32) * meta.get("scale", 1e-4) + meta.get("offset", -0.1)


def clear_share(item: dict[str, Any]) -> float:
    scl = load(item, ("scl",))["scl"]
    return float(1 - np.isin(scl, CLOUD_SCL).mean())


@dataclass(frozen=True)
class Scene:
    date: str
    item_id: str
    cloud_cover: float
    clear: float
    ndvi: np.ndarray  # 10 m, NaN where not clear
    ndmi: np.ndarray  # 20 m values repeated onto the 10 m grid


def scenes(count: int = 3, end: str = FLIGHT) -> list[Scene]:
    """The `count` latest scenes before `end` with at least `MIN_CLEAR` of the study area clear, `MIN_DAYS` apart."""
    chosen: list[Scene] = []
    for item in search(end):
        date = item["properties"]["datetime"][:10]
        if chosen and (np.datetime64(chosen[-1].date) - np.datetime64(date)).astype(int) < MIN_DAYS:
            continue
        share = clear_share(item)
        if share < MIN_CLEAR:
            continue
        dn = load(item)
        red, nir, nir08, swir16 = (_reflectance(item, band, dn[band]) for band in ("red", "nir", "nir08", "swir16"))
        up = lambda a: np.repeat(np.repeat(a, 2, axis=0), 2, axis=1)[: red.shape[0], : red.shape[1]]
        clear = up(np.isin(dn["scl"], CLEAR_SCL)) & (dn["red"] > 0) & (dn["nir"] > 0)
        ndvi = np.where(clear, (nir - red) / np.maximum(nir + red, 1e-6), np.nan)
        ndmi = np.where(clear, up((nir08 - swir16) / np.maximum(nir08 + swir16, 1e-6)), np.nan)
        chosen.append(Scene(date, item["id"], float(item["properties"]["eo:cloud_cover"]), share, ndvi, ndmi))
        if len(chosen) == count:
            break
    return chosen


def grid() -> tuple[Any, tuple[int, int]]:
    x0, y0, x1, y1 = _study_window()
    return from_origin(x0, y1, 10, 10), (round((y1 - y0) / 10), round((x1 - x0) / 10))


def plot_polygons(features: list[dict[str, Any]]) -> dict[str, Any]:
    """Predicted `block` polygons, or (a Marcaj export has no blocks) each vineyard_id's inter-rows closed by 1.5 m."""
    blocks = {f["properties"]["vineyard_id"]: shape(f["geometry"]) for f in features if f["properties"].get("label") == "block"}
    if blocks:
        return blocks
    parts: dict[str, list] = {}
    for f in features:
        if f["properties"].get("label") == "interrow_area" and f["properties"].get("vineyard_id"):
            parts.setdefault(f["properties"]["vineyard_id"], []).append(shape(f["geometry"]))
    return {plot_id: unary_union(polygons).buffer(1.5).buffer(-1.5) for plot_id, polygons in parts.items()}


def _robust_z(values: np.ndarray, reference: np.ndarray, floor: float = 0.01) -> np.ndarray:
    median = np.nanmedian(reference)
    mad = 1.4826 * np.nanmedian(np.abs(reference - median))
    return (values - median) / max(mad, floor)


def zones(features: list[dict[str, Any]], chosen: list[Scene]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(plot statistics, low zones). A zone is 8-connected 10 m pixels of one plot (shrunk by `SHRINK_M`) whose index
    is at or below `LOW_Z` robust z against the plot's own pixels on every chosen scene, for NDVI or NDMI. A plot's
    `plot_z` is its median against the medians of plots with the same main inter-row cover (all plots when fewer
    than 5 share it), so a whole plot that is lower than its peers shows even without an inner zone."""
    transform, shape_ = grid()
    cover: dict[str, dict[str, int]] = {}
    for f in features:
        p = f["properties"]
        if p.get("label") == "interrow_area" and p.get("interrow_cover"):
            cover.setdefault(p.get("vineyard_id", ""), {}).setdefault(p["interrow_cover"], 0)
            cover[p.get("vineyard_id", "")][p["interrow_cover"]] += 1
    stats, out = [], []
    plots = {plot_id: polygon.buffer(-SHRINK_M) for plot_id, polygon in plot_polygons(features).items()}
    masks = {plot_id: rasterize([polygon], out_shape=shape_, transform=transform).astype(bool)
             for plot_id, polygon in plots.items() if not polygon.is_empty}
    for index_name in ("ndvi", "ndmi"):
        stack = np.stack([getattr(scene, index_name) for scene in chosen])
        medians = {plot_id: np.nanmedian(stack[:, mask], axis=1) for plot_id, mask in masks.items() if mask.sum() >= MIN_PLOT_PX}
        main = {plot_id: max(cover.get(plot_id, {"": 1}), key=cover.get(plot_id, {"": 1}).get) for plot_id in medians}
        for plot_id, mask in masks.items():
            if plot_id not in medians:
                continue
            peers = [q for q in medians if main[q] == main[plot_id]]
            peers = peers if len(peers) >= 5 else list(medians)
            plot_z = [float(_robust_z(medians[plot_id][k], np.array([medians[q][k] for q in peers]))) for k in range(len(chosen))]
            z = np.stack([np.where(mask, _robust_z(stack[k], stack[k][mask]), np.nan) for k in range(len(chosen))])
            low = mask & np.all(z <= LOW_Z, axis=0)
            stats.append({"vineyard_id": plot_id, "index": index_name, "pixels": int(mask.sum()), "cover": main[plot_id],
                          "median": [round(float(v), 3) for v in medians[plot_id]], "plot_z": [round(v, 2) for v in plot_z],
                          "low_pixels": int(low.sum())})
            labels, count = ndimage.label(low, structure=np.ones((3, 3), bool))
            for k in range(1, count + 1):
                part = labels == k
                polygon = unary_union([shape(g) for g, v in shapes(part.astype(np.uint8), mask=part, transform=transform) if v])
                out.append({"type": "Feature", "geometry": mapping(polygon), "properties": {
                    "label": "sentinel_zone", "source": "prediction", "index": index_name, "vineyard_id": plot_id,
                    "pixels": int(part.sum()), "area_m2": round(polygon.area), "z": [round(float(np.nanmean(z[j][part])), 2) for j in range(len(chosen))],
                    "value": [round(float(np.nanmean(stack[j][part])), 3) for j in range(len(chosen))],
                    "plot_median": [round(float(v), 3) for v in medians[plot_id]], "dates": [scene.date for scene in chosen]}})
    return stats, out


if __name__ == "__main__":
    from marcaj.poi import PREDICTIONS_PATH

    chosen = scenes()
    for scene in chosen:
        print(f"{scene.date} {scene.item_id} scene cloud {scene.cloud_cover:.1f}% study area clear {scene.clear:.1%} "
              f"NDVI median {np.nanmedian(scene.ndvi):.3f} NDMI median {np.nanmedian(scene.ndmi):.3f}")
    features = json.loads(PREDICTIONS_PATH.read_text(encoding="utf-8"))["features"]
    stats, found = zones(features, chosen)
    for index_name in ("ndvi", "ndmi"):
        rows = [s for s in stats if s["index"] == index_name]
        mine = [z for z in found if z["properties"]["index"] == index_name]
        print(f"{index_name}: {len(rows)} plots, {sum(s['low_pixels'] for s in rows)} low pixels in {len(mine)} zones "
              f"({sum(z['properties']['pixels'] >= 2 for z in mine)} of 2+ pixels); plots low against peers on every date: "
              f"{[s['vineyard_id'] for s in rows if max(s['plot_z']) <= LOW_Z]}")
