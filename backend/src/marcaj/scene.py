"""Read the projected scene and prepare measured browser data."""

import base64
import json
import os
import warnings
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer
from rasterio.errors import NotGeoreferencedWarning
from rasterio.features import rasterize
from rasterio.io import MemoryFile
from rasterio.transform import array_bounds, from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds
from shapely import STRtree
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union

from marcaj import cadastre, sentinel
from marcaj.tiles import DATA_DIR, REPO_ROOT

DEFAULT_SCENE = REPO_ROOT / "data" / "generated" / "scene.json"
PREDICTION = "prediction"
# Derived layers drawn over the scene: inspection points and Sentinel-2 zones (marcaj.poi), and the routes per
# confidence cutoff (marcaj.route). Missing files are skipped; they are rebuilt from the scene on Sunday.
OVERLAYS = [REPO_ROOT / "data" / "generated" / "work" / "poi" / "poi.geojson",
            REPO_ROOT / "data" / "generated" / "work" / "poi" / "sentinel_zones.geojson",
            REPO_ROOT / "data" / "generated" / "routes.geojson"]
_TO_DISPLAY = Transformer.from_crs("EPSG:32635", "EPSG:4326", always_xy=True)


def scene_path() -> Path:
    return Path(os.environ.get("MARCAJ_SCENE_PATH", DEFAULT_SCENE)).expanduser()


def load_projected_scene() -> dict[str, Any]:
    path = scene_path()
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist; build it with marcaj-scene (see README)")
    return _read_scene(str(path), path.stat().st_mtime)


@lru_cache(maxsize=2)
def _read_scene(path: str, modified: float) -> dict[str, Any]:
    with open(path, encoding="utf-8") as source:
        scene = json.load(source)
    if not isinstance(scene, dict) or scene.get("crs") != "EPSG:32635":
        raise ValueError(f"{path} must be a projected EPSG:32635 scene")
    if scene.get("type") != "FeatureCollection" or not isinstance(scene.get("features"), list):
        raise ValueError(f"{path} must contain a GeoJSON FeatureCollection")
    return scene


def is_scored(feature: dict[str, Any]) -> bool:
    """Model predictions are for review and judging only; everything else is the annotated world."""
    return feature.get("properties", {}).get("source") != PREDICTION


def waste_id(feature: dict[str, Any]) -> str:
    """A waste box has no id of its own (CVAT boxes carry none), so it is keyed by field and centroid in decimetres,
    like marcaj.poi keys its points: the same box gets the same id from the prediction and from the Marcaj export."""
    centre = shape(feature["geometry"]).centroid
    return f"WASTE-{feature['properties'].get('vineyard_id') or 'NA'}-{round(centre.x * 10) % 100000:05d}-{round(centre.y * 10) % 100000:05d}"


def _features_with_label(scene: dict[str, Any], label: str) -> list[dict[str, Any]]:
    return [feature for feature in scene["features"] if feature.get("properties", {}).get("label") == label and is_scored(feature)]


def measurement_rows(scene: dict[str, Any]) -> list[dict[str, Any]]:
    features = [feature for feature in scene["features"] if is_scored(feature)]
    vineyard_ids = sorted({
        feature.get("properties", {}).get("vineyard_id")
        for feature in features
        if feature.get("properties", {}).get("label") in {"vineyard", "row", "interrow_area"}
        and feature.get("properties", {}).get("vineyard_id")
    })
    rows = _features_with_label(scene, "row")
    canopies = _features_with_label(scene, "vineyard")
    interrows = _features_with_label(scene, "interrow_area")
    result: list[dict[str, Any]] = []

    def union_area(items: list[dict[str, Any]]) -> float:
        return float(unary_union([shape(item["geometry"]) for item in items]).area) if items else 0.0

    for vineyard_id in vineyard_ids:
        block_rows = [item for item in rows if item["properties"].get("vineyard_id") == vineyard_id]
        block_canopies = [item for item in canopies if item["properties"].get("vineyard_id") == vineyard_id]
        block_interrows = [item for item in interrows if item["properties"].get("vineyard_id") == vineyard_id]
        canopy_area = union_area(block_canopies)
        interrow_area = union_area(block_interrows)
        result.append({
            "level": "block", "vineyard_id": vineyard_id, "row_id": "", "block_count": "",
            "row_count": len({item["properties"].get("row_id", "") for item in block_rows}),
            "row_length_m": sum(shape(item["geometry"]).length for item in block_rows),
            "canopy_area_m2": canopy_area, "canopy_area_ha": canopy_area / 10_000,
            "interrow_area_m2": interrow_area, "interrow_area_ha": interrow_area / 10_000,
        })
        length_by_row: dict[str, float] = defaultdict(float)
        for item in block_rows:
            length_by_row[item["properties"].get("row_id", "")] += shape(item["geometry"]).length
        for row_id, length in sorted(length_by_row.items()):
            result.append({
                "level": "row", "vineyard_id": vineyard_id, "row_id": row_id,
                "block_count": "", "row_count": "", "row_length_m": length,
                "canopy_area_m2": "", "canopy_area_ha": "",
                "interrow_area_m2": "", "interrow_area_ha": "",
            })

    total_canopy = union_area(canopies)
    total_interrow = union_area(interrows)
    result.insert(0, {
        "level": "total", "vineyard_id": "", "row_id": "",
        "block_count": len(vineyard_ids),
        "row_count": len({item["properties"].get("row_id", "") for item in rows}),
        "row_length_m": sum(shape(item["geometry"]).length for item in rows),
        "canopy_area_m2": total_canopy, "canopy_area_ha": total_canopy / 10_000,
        "interrow_area_m2": total_interrow, "interrow_area_ha": total_interrow / 10_000,
    })
    return result


MEASURED = {"vineyard", "row", "interrow_area"}
_MEASUREMENT_KEYS = ("row_count", "row_length_m", "canopy_area_m2", "canopy_area_ha", "interrow_area_m2", "interrow_area_ha")
_last_measured: list[Any] = [None, None]  # the scene last measured and its tables: /api/scene serves one cached scene


def browser_measurements(scene: dict[str, Any]) -> dict[str, Any]:
    """The client's measurement tables (site total, per field, per row), from `measurement_rows` and rounded as
    marcaj-export writes measurements.csv, so the scored numbers are the CSV's. A field with no scored object (not yet
    annotated in Marcaj) is measured from the model's predictions instead and flagged `estimate`; when the Marcaj
    export is loaded, its fields measure from the corrected annotations with no other change."""
    if _last_measured[0] is scene:
        return _last_measured[1]
    features = [f for f in scene["features"] if f.get("properties", {}).get("label") in MEASURED]
    annotated = {f["properties"].get("vineyard_id") for f in features if is_scored(f)} | {"", None}
    estimated = [f for f in features if not is_scored(f) and f["properties"].get("vineyard_id") not in annotated]
    # the fallback predictions are measured by the same code under another source, which is_scored accepts
    measured = [f for f in features if is_scored(f)] + [{**f, "properties": {**f["properties"], "source": "estimate"}} for f in estimated]
    rows = [{key: round(value, 3) if isinstance(value, float) else value for key, value in row.items()}
            for row in measurement_rows({"features": measured})]
    estimate_ids = {f["properties"]["vineyard_id"] for f in estimated}
    structures: dict[tuple[str, str], set[str]] = defaultdict(set)
    for f in measured:
        if f["properties"]["label"] == "row" and f["properties"].get("row_structure"):
            structures[(f["properties"].get("vineyard_id"), f["properties"].get("row_id", ""))].add(f["properties"]["row_structure"])
    result = {
        "estimate_blocks": len(estimate_ids),
        "total": {"block_count": rows[0]["block_count"], **{key: rows[0][key] for key in _MEASUREMENT_KEYS}},
        "blocks": [{"vineyard_id": row["vineyard_id"], "estimate": row["vineyard_id"] in estimate_ids, **{key: row[key] for key in _MEASUREMENT_KEYS}}
                   for row in rows if row["level"] == "block"],
        "rows": [{"vineyard_id": row["vineyard_id"], "row_id": row["row_id"], "length_m": row["row_length_m"],
                  "row_structure": ", ".join(sorted(structures[(row["vineyard_id"], row["row_id"])]))}
                 for row in rows if row["level"] == "row"],
    }
    _last_measured[:] = [scene, result]
    return result


def overlay_features(scene: dict[str, Any], paths: list[Path] = OVERLAYS) -> list[dict[str, Any]]:
    """Features of `paths` whose label the scene doesn't already hold. Of the inspection points, only route targets
    (`challenge`) and Sentinel-2 points are kept; the other candidates are review material."""
    present = {feature["properties"].get("label") for feature in scene["features"]}
    out = []
    for path in paths:
        if not path.is_file():
            continue
        layer = json.loads(path.read_text(encoding="utf-8"))
        if layer.get("crs") != "EPSG:32635":
            raise ValueError(f"{path} must be a projected EPSG:32635 FeatureCollection")
        out += [feature for feature in layer["features"] if feature["properties"].get("label") not in present
                and (feature["properties"].get("label") != "inspection" or feature["properties"].get("challenge")
                     or str(feature["properties"].get("reason", "")).startswith("sentinel"))]
    return out


CADASTRE_CREDIT = "Cadastru: I.P. Cadastrul Bunurilor Imobile"
SENTINEL_CREDIT = "Contains modified Copernicus Sentinel data 2025"
# One hue light -> dark per index, over fixed ranges so dates compare (NDVI p1-p99 on the site is 0.16-0.83, NDMI -0.30-0.34).
SENTINEL_LEGEND = {
    "ndvi": {"min": 0.1, "max": 0.8, "pixel_m": 10, "colors": ["#eef6d8", "#c3e19a", "#86c35a", "#4c9a33", "#1f5f1a"]},
    "ndmi": {"min": -0.3, "max": 0.3, "pixel_m": 20, "colors": ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]},
}
SENTINEL_SUPERSAMPLE = 4  # web-mercator pixels per 10 m pixel side, so nearest resampling keeps the 10 m squares


@lru_cache(maxsize=1)
def _parcels() -> tuple[list[Any], str]:
    return cadastre.load_parcels(), date.fromtimestamp(cadastre.PARCELS.stat().st_mtime).isoformat()


def cadastre_features(scene: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Cached cadastral parcels (marcaj.cadastre; never re-fetched here) that intersect the study area, each with the
    `vineyard_id` of the model field it overlaps most. `parcel_id` is the parcel's index in the cache: the WMS render
    carries no cadastral number."""
    if not cadastre.PARCELS.is_file():
        return [], None
    parcels, snapshot = _parcels()
    study = [shape(f["geometry"]) for f in scene["features"] if f["properties"].get("label") == "study_area"]
    blocks = [f for f in scene["features"] if f["properties"].get("label") == "block" and f["properties"].get("vineyard_id")]
    block_shapes = [shape(f["geometry"]) for f in blocks]
    tree = STRtree(block_shapes)
    out = []
    for index, parcel in enumerate(parcels, start=1):
        if study and not parcel.intersects(study[0]):
            continue
        overlaps = [(parcel.intersection(block_shapes[k]).area, k) for k in tree.query(parcel, predicate="intersects")]
        area, best = max(overlaps, default=(0.0, None))
        properties = {"label": "cadastre", "source": "cadastre", "parcel_id": str(index), "area_m2": round(parcel.area)}
        # ponytail: a boundary sliver (overlap under 10% of the smaller shape) is a neighbour, not the field's parcel
        if best is not None and area >= 0.1 * min(parcel.area, block_shapes[best].area):
            properties |= {"vineyard_id": blocks[best]["properties"]["vineyard_id"], "field_share": round(area / parcel.area, 2)}
        out.append({"type": "Feature", "geometry": mapping(parcel), "properties": properties})
    meta = {"credit": CADASTRE_CREDIT, "snapshot": snapshot, "count": len(out),
            "in_fields": sum("vineyard_id" in f["properties"] for f in out)}
    return out, meta


def _png_data_url(rgba: np.ndarray) -> str:
    with warnings.catch_warnings(), MemoryFile() as memory:
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with memory.open(driver="PNG", width=rgba.shape[2], height=rgba.shape[1], count=4, dtype="uint8") as png:
            png.write(rgba)
        return "data:image/png;base64," + base64.b64encode(memory.read()).decode("ascii")


def _colour(values: np.ndarray, legend: dict[str, Any]) -> np.ndarray:
    t = np.clip((np.nan_to_num(values, nan=legend["min"]) - legend["min"]) / (legend["max"] - legend["min"]), 0, 1)
    stops = np.array([[int(colour[i:i + 2], 16) for i in (1, 3, 5)] for colour in legend["colors"]], dtype=float)
    positions = np.linspace(0, 1, len(stops))
    rgb = np.stack([np.interp(t, positions, stops[:, channel]) for channel in range(3)])
    return np.concatenate([rgb, np.where(np.isnan(values), 0, 255)[None]]).round().astype(np.uint8)


@lru_cache(maxsize=1)
def sentinel_layers() -> dict[str, Any] | None:
    """NDVI and NDMI of the cached Sentinel-2 scenes before the flight (marcaj.sentinel), per date and as the per-pixel
    median, as PNG data URLs on a web-mercator grid (so a Leaflet image overlay is exact) clipped to the study area.
    None when the Sentinel cache is absent: the request path never downloads."""
    if not (sentinel.CACHE / f"search_{sentinel.FLIGHT}_45.json").is_file():
        return None
    chosen = sentinel.scenes()
    if not chosen:
        return None
    grid, (height, width) = sentinel.grid()
    study = shape(json.loads((DATA_DIR / "02_route" / "study_area.geojson").read_text())["features"][0]["geometry"])
    outside = ~rasterize([study], out_shape=(height, width), transform=grid, all_touched=True).astype(bool)
    web = transform_bounds("EPSG:32635", "EPSG:3857", *array_bounds(height, width, grid))
    web_width = width * SENTINEL_SUPERSAMPLE
    web_height = round(web_width * (web[3] - web[1]) / (web[2] - web[0]))
    west, south, east, north = transform_bounds("EPSG:3857", "EPSG:4326", *web)

    def image(values: np.ndarray, index: str) -> str:
        out = np.full((web_height, web_width), np.nan, dtype=np.float32)
        reproject(np.where(outside, np.nan, values).astype(np.float32), out, src_transform=grid, src_crs="EPSG:32635",
                  src_nodata=np.nan, dst_transform=from_bounds(*web, web_width, web_height), dst_crs="EPSG:3857",
                  dst_nodata=np.nan, resampling=Resampling.nearest)
        return _png_data_url(_colour(out, SENTINEL_LEGEND[index]))

    images = []
    for index in ("ndvi", "ndmi"):
        stack = np.stack([getattr(scene, index) for scene in chosen])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels (water, cloud) stay NaN
            median = np.nanmedian(stack, axis=0)
        for key, values, item in [("median", median, None)] + [(scene.date, stack[k], scene) for k, scene in enumerate(chosen)]:
            images.append({"index": index, "date": key, "item_id": item.item_id if item else None,
                           "clear": round(item.clear, 3) if item else None,
                           "site_median": round(float(np.nanmedian(np.where(outside, np.nan, values))), 3),
                           "url": image(values, index)})
    return {"credit": SENTINEL_CREDIT, "flight": sentinel.FLIGHT, "dates": [scene.date for scene in chosen],
            "collection": "sentinel-2-c1-l2a", "bounds": [[south, west], [north, east]], "legend": SENTINEL_LEGEND,
            "images": images}


def browser_scene(scene: dict[str, Any]) -> dict[str, Any]:
    rows = measurement_rows(scene)
    totals = rows[0]
    parcels, cadastre_meta = cadastre_features(scene)
    overlays = overlay_features(scene) + parcels
    routes = _features_with_label(scene, "route") or [f for f in overlays if f["properties"]["label"] == "route"]
    official = [item for item in routes if item["properties"].get("min_confidence") is None and item["properties"].get("scope", "site") == "site"]
    targets = [f for f in _features_with_label(scene, "inspection") + overlays
               if f["properties"]["label"] == "inspection" and f["properties"].get("challenge", True)]
    display_features = [{
        "type": "Feature",
        "geometry": mapping(transform(_TO_DISPLAY.transform, shape(feature["geometry"]))),
        "properties": {**feature["properties"], "id": waste_id(feature)} if feature["properties"].get("label") == "waste" else feature["properties"],
    } for feature in scene["features"] + overlays if feature["properties"].get("label") != "tile"]
    return {
        "source": scene.get("source", "generated scene"),
        "crs": "EPSG:4326",
        "features": {"type": "FeatureCollection", "features": display_features},
        "cadastre": cadastre_meta,
        "sentinel": sentinel_layers(),
        "measurements": browser_measurements(scene),
        "metrics": {
            "block_count": totals["block_count"],
            "row_count": totals["row_count"],
            "row_length_m": totals["row_length_m"],
            "canopy_area_m2": totals["canopy_area_m2"],
            "interrow_area_m2": totals["interrow_area_m2"],
            "route_length_m": sum(shape(item["geometry"]).length for item in official or routes),
            "target_count": len(targets) + len(_features_with_label(scene, "waste")),
            # per confidence cutoff: the site route (scope site, min_confidence None is the official route.geojson) and one per field
            "routes": [{key: item["properties"].get(key) for key in ("scope", "vineyard_id", "min_confidence", "length_m", "targets", "visited", "unreachable", "legal")}
                       for item in routes],
        },
    }
