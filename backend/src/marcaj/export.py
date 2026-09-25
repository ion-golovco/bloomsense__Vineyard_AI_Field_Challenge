"""Export the current projected scene as challenge-shaped demo artifacts."""

import argparse
import csv
import json
from pathlib import Path

from shapely.geometry import shape

from marcaj.scene import load_projected_scene, measurement_rows

FIELDS = [
    "level", "vineyard_id", "row_id", "block_count", "row_count", "row_length_m",
    "canopy_area_m2", "canopy_area_ha", "interrow_area_m2", "interrow_area_ha",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export route and measurements from a projected scene")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    scene = load_projected_scene()
    routes = [feature for feature in scene["features"] if feature.get("properties", {}).get("label") == "route"]
    if len(routes) != 1:
        raise ValueError(f"Expected one route, found {len(routes)}")
    route = routes[0]
    geometry = shape(route["geometry"])
    if geometry.geom_type != "LineString" or not geometry.is_ring:
        raise ValueError("The route must be a closed LineString")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    route_output = {
        "type": "Feature", "geometry": route["geometry"],
        "properties": {"length_m": round(geometry.length, 3)},
    }
    (args.output_dir / "route.geojson").write_text(json.dumps(route_output, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "measurements.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        for row in measurement_rows(scene):
            writer.writerow({key: round(value, 3) if isinstance(value, float) else value for key, value in row.items()})
    print(f"Exported {scene.get('source', 'scene')} to {args.output_dir}")


if __name__ == "__main__":
    main()
