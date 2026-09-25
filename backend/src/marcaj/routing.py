"""Organizer route constraints and the route criterion's pass/fail checks, in EPSG:32635."""

import json
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

from marcaj.scene import is_scored

START_TOLERANCE_M = 5.0
VISIT_RADIUS_M = 2.0
MAX_OUTSIDE_SHARE = 0.02
CONSTRAINT_FILES = {"start.geojson": "start", "passages.geojson": "passage", "forbidden.geojson": "forbidden", "study_area.geojson": "study_area"}


def load_constraints(route_dir: Path) -> list[dict[str, Any]]:
    features = []
    for filename, label in CONSTRAINT_FILES.items():
        collection = json.loads((route_dir / filename).read_text(encoding="utf-8"))
        crs = collection.get("crs", {}).get("properties", {}).get("name", "")
        if not crs.endswith("32635"):
            raise ValueError(f"{filename}: CRS {crs!r} is not EPSG:32635")
        for feature in collection["features"]:
            features.append({"type": "Feature", "geometry": feature["geometry"], "properties": {"label": label, "source": "organizer"}})
    return features


def _geometries(features: list[dict[str, Any]], *labels: str) -> list:
    return [shape(feature["geometry"]) for feature in features if feature.get("properties", {}).get("label") in labels and is_scored(feature)]


def passable_space(features: list[dict[str, Any]]):
    return unary_union(_geometries(features, "interrow_area", "passage")).difference(unary_union(_geometries(features, "forbidden")))


def route_targets(features: list[dict[str, Any]]) -> list[Point]:
    return [geometry if geometry.geom_type == "Point" else geometry.centroid for geometry in _geometries(features, "inspection", "waste")]


def check_route(route: LineString, features: list[dict[str, Any]]) -> dict[str, Any]:
    starts = _geometries(features, "start")
    if len(starts) != 1:
        raise ValueError(f"Expected one start point, found {len(starts)}")
    start = starts[0]
    outside_m = route.difference(passable_space(features)).length
    targets = route_targets(features)
    visited = sum(route.distance(target) <= VISIT_RADIUS_M for target in targets)
    report = {
        "length_m": route.length,
        "start_gap_m": start.distance(Point(route.coords[0])),
        "end_gap_m": start.distance(Point(route.coords[-1])),
        "outside_m": outside_m,
        "outside_share": outside_m / route.length if route.length else 1.0,
        "targets": len(targets),
        "visited": visited,
    }
    report["closed"] = report["start_gap_m"] <= START_TOLERANCE_M and report["end_gap_m"] <= START_TOLERANCE_M
    report["legal"] = report["outside_share"] <= MAX_OUTSIDE_SHARE
    return report
