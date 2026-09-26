"""Organizer route constraints and the route criterion's pass/fail checks, in EPSG:32635."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union

from marcaj.scene import is_scored

START_TOLERANCE_M = 5.0
VISIT_RADIUS_M = 2.0
MAX_OUTSIDE_SHARE = 0.02
# A canopy piece smaller than this is a young or low vine that a walker may step over (route hops, second tier);
# `canopy_m` counts only the larger, mature canopies, `young_canopy_m` the rest.
YOUNG_CANOPY_M2 = 0.25
# The brief rules out crossing forbidden zones and mature canopies; `legal` allows this much of each: a route on grid
# cells clear of canopy stays under it (after the 0.2 m simplification), a hop through one vine does not.
CROSSING_TOLERANCE_M = 0.05
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
    canopies = _geometries(features, "vineyard")
    return {
        "passable": passable_space(features), "forbidden": unary_union(_geometries(features, "forbidden")),
        "canopy": unary_union([item for item in canopies if item.area >= YOUNG_CANOPY_M2]),
        "young_canopy": unary_union([item for item in canopies if item.area < YOUNG_CANOPY_M2]),
    }


def check_route(route: LineString, features: list[dict[str, Any]], targets: list[Point] | None = None, spaces: dict[str, Any] | None = None,
                start: Point | None = None, end: Point | None = None) -> dict[str, Any]:
    """The zero-score rules (start within 5 m of `start`, end within 5 m of `end`, both the scene's START by
    default; at most 2% outside inter-rows plus passages) and target visits within 2 m; also metres through
    forbidden zones and mature canopies, which the brief rules out. `scores` is the organizers' zero-score rule
    alone; `legal` also allows at most CROSSING_TOLERANCE_M through forbidden zones and through mature canopies.
    `targets` defaults to the scene's scored inspection and waste features; `spaces` is `check_spaces(features)`."""
    if start is None:
        starts = _geometries(features, "start")
        if len(starts) != 1:
            raise ValueError(f"Expected one start point, found {len(starts)}")
        start = starts[0]
    end = start if end is None else end
    spaces = check_spaces(features) if spaces is None else spaces
    outside_m = max(route.length - _length_in(route, spaces["passable"]), 0.0)
    targets = route_targets(features) if targets is None else targets
    visited = sum(route.distance(target) <= VISIT_RADIUS_M for target in targets)
    report = {
        "length_m": route.length,
        "start_gap_m": start.distance(Point(route.coords[0])),
        "end_gap_m": end.distance(Point(route.coords[-1])),
        "outside_m": outside_m,
        "outside_share": outside_m / route.length if route.length else 1.0,
        "forbidden_m": _length_in(route, spaces["forbidden"]),
        "canopy_m": _length_in(route, spaces["canopy"]),
        "young_canopy_m": _length_in(route, spaces.get("young_canopy", Polygon())),
        "targets": len(targets),
        "visited": visited,
    }
    report["closed"] = report["start_gap_m"] <= START_TOLERANCE_M and report["end_gap_m"] <= START_TOLERANCE_M
    report["scores"] = report["outside_share"] <= MAX_OUTSIDE_SHARE
    report["legal"] = report["scores"] and report["forbidden_m"] <= CROSSING_TOLERANCE_M and report["canopy_m"] <= CROSSING_TOLERANCE_M
    return report
