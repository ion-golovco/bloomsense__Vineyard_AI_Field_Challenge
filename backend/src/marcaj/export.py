"""Export the submission files, route.geojson and measurements.csv, from a projected scene."""

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from marcaj.routing import check_route
from marcaj.scene import load_projected_scene, measurement_rows

FIELDS = [
    "level", "vineyard_id", "row_id", "block_count", "row_count", "row_length_m",
    "canopy_area_m2", "canopy_area_ha", "interrow_area_m2", "interrow_area_ha",
]
ORGANIZER_CRS = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}}


def _write_route(scene: dict[str, Any], output_dir: Path) -> None:
    routes = [feature for feature in scene["features"] if feature.get("properties", {}).get("label") == "route"]
    if len(routes) != 1:
        raise ValueError(f"Expected one route, found {len(routes)}; pass --no-route to export measurements only")
    geometry = shape(routes[0]["geometry"])
    if geometry.geom_type != "LineString":
        raise ValueError(f"The route must be a LineString, not {geometry.geom_type}")
    report = check_route(geometry, scene["features"])
    print(
        f"route {report['length_m']:.1f} m, start/end gap {report['start_gap_m']:.2f}/{report['end_gap_m']:.2f} m, "
        f"{report['outside_share']:.2%} outside passable space, {report['visited']}/{report['targets']} targets within 2 m"
    )
    if not report["closed"] or not report["legal"]:
        raise ValueError("The route would score 0: it must start and end within 5 m of START and stay 98% inside passable space")
    collection = {
        "type": "FeatureCollection", "crs": ORGANIZER_CRS,
        "features": [{"type": "Feature", "geometry": routes[0]["geometry"], "properties": {"length_m": round(geometry.length, 3)}}],
    }
    (output_dir / "route.geojson").write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export route.geojson and measurements.csv from a projected scene")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--no-route", action="store_true", help="write measurements.csv only")
    args = parser.parse_args()
    scene = load_projected_scene()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_route:
        _write_route(scene, args.output_dir)
    with (args.output_dir / "measurements.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        for row in measurement_rows(scene):
            writer.writerow({key: round(value, 3) if isinstance(value, float) else value for key, value in row.items()})
    print(f"Exported {scene.get('source', 'scene')} to {args.output_dir}")


if __name__ == "__main__":
    main()
