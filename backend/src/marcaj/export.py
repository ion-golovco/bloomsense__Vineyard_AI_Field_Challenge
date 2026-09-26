"""Export the submission files from a projected scene: route.geojson (solved by `marcaj.route` from the scene's
scored features and the --poi inspection points, refused if it would score 0), route_targets.csv (every target:
visited, unreachable or over the outside budget, with its distance to the route and the reason), measurements.csv,
and for the client data/generated/routes.geojson: site-wide and per-field routes per POI confidence cutoff
(all, >= 0.5, >= 0.7)."""

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

from shapely.geometry import mapping

from marcaj.route import OUTSIDE_BUDGET, poi_targets, route_target_features, routes_by_confidence
from marcaj.scene import load_projected_scene, measurement_rows
from marcaj.tiles import REPO_ROOT

FIELDS = [
    "level", "vineyard_id", "row_id", "block_count", "row_count", "row_length_m",
    "canopy_area_m2", "canopy_area_ha", "interrow_area_m2", "interrow_area_ha",
]
ORGANIZER_CRS = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}}


def _write_route(scene: dict[str, Any], output_dir: Path, poi: Path | None, confidence_over: float | None, budget: float, routes_path: Path) -> None:
    """route.geojson and route_targets.csv for the site-wide all-POI route (the submission), and routes.geojson for
    the client: the site-wide route per POI confidence cutoff (all, >= 0.5, >= 0.7) and one route per field and
    cutoff; a route that would score 0 is reported and left out."""
    started = time.perf_counter()
    targets = route_target_features(scene["features"]) + (poi_targets(poi, confidence_over) if poi else [])
    cutoffs = (None, 0.5, 0.7) if poi else (None,)
    solved = routes_by_confidence(scene["features"], targets, cutoffs, budget)
    for feature, line, rows, report in solved[:len(cutoffs)]:
        properties = feature["properties"]
        print(
            f"site route for POI confidence >= {properties['min_confidence']}: {properties['length_m']:.1f} m, start/end gap {properties['start_gap_m']:.2f}/"
            f"{properties['end_gap_m']:.2f} m, {properties['outside_share']:.2%} outside passable space ({properties['robust_outside_share']:.2%} of it eroded 0.3 m), "
            f"{report['forbidden_m']:.2f} m forbidden, {report['canopy_m']:.2f} m canopy, {properties['visited']}/{properties['targets']} targets within 2 m"
        )
    fields = [feature["properties"] for feature, *_ in solved[len(cutoffs):]]
    print(f"{len(fields)} field routes over {len({item['vineyard_id'] for item in fields})} fields, "
          f"{sum(item['visited'] == item['targets'] for item in fields)} visiting all their targets; {time.perf_counter() - started:.0f} s in all")
    official, line, rows, _ = solved[0]
    if not official["properties"]["closed"] or not official["properties"]["legal"]:
        raise ValueError("The route would score 0: it must start and end within 5 m of START and stay 98% inside passable space")
    collection = {
        "type": "FeatureCollection", "crs": ORGANIZER_CRS,
        "features": [{"type": "Feature", "geometry": mapping(line), "properties": {"length_m": round(line.length, 3)}}],
    }
    (output_dir / "route.geojson").write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "route_targets.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]) if rows else ["id"])
        writer.writeheader()
        writer.writerows(rows)
    for item in rows:
        if item["status"] != "visited":
            print(f"  {item['status']}: {item['label']} {item['id']} at {item['distance_m']:.1f} m, {item['reason']}")
    legal = [feature for feature, *_ in solved if feature["properties"]["closed"] and feature["properties"]["legal"]]
    for feature, *_ in solved:
        if feature not in legal:
            print(f"left out of routes.geojson, it would score 0: {feature['properties']['scope']} {feature['properties']['vineyard_id']} route for POI confidence >= {feature['properties']['min_confidence']}")
    routes_path.parent.mkdir(parents=True, exist_ok=True)
    routes_path.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": legal}) + "\n", encoding="utf-8")
    print(f"Wrote {len(legal)} routes to {routes_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export route.geojson and measurements.csv from a projected scene")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--no-route", action="store_true", help="write measurements.csv only")
    parser.add_argument("--poi", type=Path, help="inspection points GeoJSON (marcaj.poi output); only challenge: true points are route targets")
    parser.add_argument("--poi-confidence-over", type=float, help="keep only POIs with confidence above this (default: all)")
    parser.add_argument("--routes", type=Path, default=REPO_ROOT / "data" / "generated" / "routes.geojson", help="the client's routes: site-wide and per field, per POI confidence cutoff")
    parser.add_argument("--outside-budget", type=float, default=OUTSIDE_BUDGET, help=f"share of the route allowed outside passable space eroded by 0.3 m (default {OUTSIDE_BUDGET}; the zero-score limit is 0.02)")
    args = parser.parse_args()
    scene = load_projected_scene()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_route:
        _write_route(scene, args.output_dir, args.poi, args.poi_confidence_over, args.outside_budget, args.routes)
    with (args.output_dir / "measurements.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        for row in measurement_rows(scene):
            writer.writerow({key: round(value, 3) if isinstance(value, float) else value for key, value in row.items()})
    print(f"Exported {scene.get('source', 'scene')} to {args.output_dir}")


if __name__ == "__main__":
    main()
