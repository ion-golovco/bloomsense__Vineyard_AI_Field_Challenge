"""Read the projected scene and prepare measured browser data."""

import json
import os
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union

from marcaj.tiles import REPO_ROOT

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


def browser_scene(scene: dict[str, Any]) -> dict[str, Any]:
    rows = measurement_rows(scene)
    totals = rows[0]
    overlays = overlay_features(scene)
    routes = _features_with_label(scene, "route") or [f for f in overlays if f["properties"]["label"] == "route"]
    official = [item for item in routes if item["properties"].get("min_confidence") is None and item["properties"].get("scope", "site") == "site"]
    targets = [f for f in _features_with_label(scene, "inspection") + overlays
               if f["properties"]["label"] == "inspection" and f["properties"].get("challenge", True)]
    display_features = [{
        "type": "Feature",
        "geometry": mapping(transform(_TO_DISPLAY.transform, shape(feature["geometry"]))),
        "properties": feature["properties"],
    } for feature in scene["features"] + overlays if feature["properties"].get("label") != "tile"]
    return {
        "source": scene.get("source", "generated scene"),
        "crs": "EPSG:4326",
        "features": {"type": "FeatureCollection", "features": display_features},
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
            "rows": [item for item in rows if item["level"] == "row"],
        },
    }
