"""Read projected scene artifacts and prepare measured browser data."""

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union

from marcaj.demo import build_demo_scene

_TO_DISPLAY = Transformer.from_crs("EPSG:32635", "EPSG:4326", always_xy=True)


def load_projected_scene() -> dict[str, Any]:
    configured_path = os.environ.get("MARCAJ_SCENE_PATH")
    if not configured_path:
        return build_demo_scene()
    path = Path(configured_path).expanduser()
    with path.open(encoding="utf-8") as source:
        scene = json.load(source)
    if not isinstance(scene, dict) or scene.get("crs") != "EPSG:32635":
        raise ValueError(f"{path} must be a projected EPSG:32635 scene")
    if scene.get("type") != "FeatureCollection" or not isinstance(scene.get("features"), list):
        raise ValueError(f"{path} must contain a GeoJSON FeatureCollection")
    return scene


def _features_with_label(scene: dict[str, Any], label: str) -> list[dict[str, Any]]:
    return [feature for feature in scene["features"] if feature.get("properties", {}).get("label") == label]


def measurement_rows(scene: dict[str, Any]) -> list[dict[str, Any]]:
    features = scene["features"]
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
            "row_count": len({item["properties"]["row_id"] for item in block_rows}),
            "row_length_m": sum(shape(item["geometry"]).length for item in block_rows),
            "canopy_area_m2": canopy_area, "canopy_area_ha": canopy_area / 10_000,
            "interrow_area_m2": interrow_area, "interrow_area_ha": interrow_area / 10_000,
        })
        length_by_row: dict[str, float] = defaultdict(float)
        for item in block_rows:
            length_by_row[item["properties"]["row_id"]] += shape(item["geometry"]).length
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
        "row_count": len({item["properties"]["row_id"] for item in rows}),
        "row_length_m": sum(shape(item["geometry"]).length for item in rows),
        "canopy_area_m2": total_canopy, "canopy_area_ha": total_canopy / 10_000,
        "interrow_area_m2": total_interrow, "interrow_area_ha": total_interrow / 10_000,
    })
    return result


def browser_scene(scene: dict[str, Any]) -> dict[str, Any]:
    rows = measurement_rows(scene)
    totals = rows[0]
    routes = _features_with_label(scene, "route")
    display_features = []
    for feature in scene["features"]:
        projected = shape(feature["geometry"])
        display_features.append({
            "type": "Feature",
            "geometry": mapping(transform(_TO_DISPLAY.transform, projected)),
            "properties": feature["properties"],
        })
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
            "route_length_m": sum(shape(item["geometry"]).length for item in routes),
            "target_count": len(_features_with_label(scene, "inspection")) + len(_features_with_label(scene, "waste")),
            "rows": [item for item in rows if item["level"] == "row"],
        },
    }
