"""Organizer route constraints and the route criterion's pass/fail checks, in EPSG:32635."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import shapely
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


def _length_in(route: LineString, geometry) -> float:
    """Route metres inside `geometry`, summed per segment: an overlay of the whole line dissolves a route that
    retraces itself, so an out-and-back spur would count once."""
    if geometry.is_empty:
        return 0.0
    coords = np.asarray(route.coords)
    segments = shapely.linestrings(np.stack([coords[:-1], coords[1:]], axis=1))
    shapely.prepare(geometry)
    inside = shapely.covered_by(segments, geometry)
    partial = segments[~inside & shapely.intersects(segments, geometry)]
    return float(shapely.length(segments[inside]).sum() + shapely.length(shapely.intersection(partial, geometry)).sum())


def check_spaces(features: list[dict[str, Any]]) -> dict[str, Any]:
    """The unions check_route measures against, to compute once when checking many routes over one scene."""
    return {
        "passable": passable_space(features), "forbidden": unary_union(_geometries(features, "forbidden")),
        "canopy": unary_union(_geometries(features, "vineyard")),
    }


def check_route(route: LineString, features: list[dict[str, Any]], targets: list[Point] | None = None, spaces: dict[str, Any] | None = None) -> dict[str, Any]:
    """The zero-score rules (start/end within 5 m of START, at most 2% outside inter-rows plus passages) and
    target visits within 2 m; also metres through forbidden zones and canopies, which the brief rules out.
    `targets` defaults to the scene's scored inspection and waste features; `spaces` is `check_spaces(features)`."""
    starts = _geometries(features, "start")
    if len(starts) != 1:
        raise ValueError(f"Expected one start point, found {len(starts)}")
    start = starts[0]
    spaces = check_spaces(features) if spaces is None else spaces
    outside_m = max(route.length - _length_in(route, spaces["passable"]), 0.0)
    targets = route_targets(features) if targets is None else targets
    visited = sum(route.distance(target) <= VISIT_RADIUS_M for target in targets)
    report = {
        "length_m": route.length,
        "start_gap_m": start.distance(Point(route.coords[0])),
        "end_gap_m": start.distance(Point(route.coords[-1])),
        "outside_m": outside_m,
        "outside_share": outside_m / route.length if route.length else 1.0,
        "forbidden_m": _length_in(route, spaces["forbidden"]),
        "canopy_m": _length_in(route, spaces["canopy"]),
        "targets": len(targets),
        "visited": visited,
    }
    report["closed"] = report["start_gap_m"] <= START_TOLERANCE_M and report["end_gap_m"] <= START_TOLERANCE_M
    report["legal"] = report["outside_share"] <= MAX_OUTSIDE_SHARE
    return report
