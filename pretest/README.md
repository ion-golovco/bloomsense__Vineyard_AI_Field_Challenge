# Organizers' pre-test

The organizers' pre-test start point: X 629663.8, Y 5220195.3 (EPSG:32635), which is 47.1225039° N, 28.7094577° E. It lies 168 m from the official START. The scored `route.geojson` in the repository root still starts and ends at START, as the brief requires. This folder holds the route from the pre-test point.

| File | Holds |
|---|---|
| `route_pretest.geojson` | The route in the official format: one LineString in EPSG:32635 with `length_m` |
| `route_pretest.png` | That route over the drone mosaic. S marks the pre-test start; red stops are missing vines, blue stops are waste, grey ones are skipped |
| `metrics.png`, `metrics.html` | Our metrics for each scoring criterion, plus the pre-test route |
| `app_all-fields.png`, `app_measurements.png` | The web app on the final data |

| Route from the pre-test point | Value |
|---|---|
| Length | 14,400.6 m. Start and end are both 0.65 m from the given point, snapped onto the passage |
| Outside inter-rows and passages | 170.6 m, 1.18% of the length (the limit is 2%) |
| Forbidden zones, mature canopy | 0 m, 0 m |
| Targets within 2 m | 264 of 317 (row gaps, missing planting and waste) |
| Planning time | 3.7 s on the running app |

Checked with `marcaj.routing.check_route`: closed, legal, would score.

To regenerate it, start `scripts/demo.sh`, then run `scripts/pretest.sh 629663.8 5220195.3`. The coordinates are EPSG:32635 by default; pass `3857` or `4326` as a third argument for other systems.
