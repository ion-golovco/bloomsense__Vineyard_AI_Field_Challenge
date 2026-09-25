# Marcaj inspection planner: product and tooling contract

25 September 2026. This is the proposed implementation contract for the next product slice. It extends the [challenge specification](SPEC.md) and [research brief](ARCHITECTURE_RESEARCH_2026-09-25.md). It does not claim that route solving, satellite triage, or live point management is implemented.

## The farmer's three questions

1. **What needs a visit?** Show concrete points of interest (POIs) and row/lot alerts with source, date, evidence, and uncertainty.
2. **What is the shortest legal walk?** Provide separate routes for required POIs, all vine rows, and selected rows. Combine required POIs with selected rows when asked.
3. **Can I avoid a routine walk today?** Show the latest usable satellite observation and one comparable reference view for *mapped vineyard lots only*, then say `inspection recommended`, `no new signal`, or `insufficient evidence`. Never turn a cloudy or coarse observation into an all-clear.

## Separation of records

Marcaj annotation export is authoritative for canopy polygons, row axes, inter-row polygons, waste boxes, IDs, and their challenge attributes. The inspection planner maintains a separate application GeoJSON in EPSG:32635. These product records are **not** imported as new Marcaj labels:

| Record | Minimum fields | Created from | Routing role |
|---|---|---|---|
| `inspection_point` | `id`, point, `vineyard_id`, optional `row_id`, `reason`, `source`, `observed_at`, `confidence`, `status` | visible gap candidate, missing planting candidate, user pin, or field report | A POI target if selected; confirmed challenge gap targets are mandatory in the challenge route. |
| `waste_visit` | stable waste ID, box centroid, box reference, `vineyard_id`, `status` | reviewed Marcaj waste box | Mandatory challenge target. Keep the box as annotation and the point as its visit location. |
| `lot_observation` | `vineyard_id`, acquisition date, source, clear fraction, valid pixel count, aggregate value/change, quality state | on-demand satellite window clipped to that vineyard block | Can recommend a lot-level visit; never locate a single vine or 5 m gap. |
| `row_alert` | `vineyard_id`, one or more `row_id`s, reason, evidence link, uncertainty | canopy/row evidence or a satellite lot/zone alert | Can select rows for an inspection route. Satellite alerts remain lot/zone level if rows cannot be isolated. |

`status` should distinguish `new`, `planned`, `visited`, `confirmed`, and `dismissed`; each transition records time and origin. Do not delete a challenge POI when a farmer dismisses it: the challenge route still needs every reachable hidden-reference target. The UI should show a separate view of challenge-required targets and operational recommendations.

## Route modes and objective

| Mode in the app | Required coverage | Optimization | Validation |
|---|---|---|---|
| **Visit points** | Every selected reachable gap, waste, or user POI within the configured visit radius. Challenge export uses all reachable gap/waste targets and the organizer's 2 m radius. | Shortest closed legal tour from start over walking-network distances. | One continuous LineString, start/end within 5 m, no more than 2% outside legal space for the challenge, every reachable target reported. |
| **Inspect every row** | Every physical `row_id` is seen from at least one side along its visible length. A corridor may count for the rows on both sides only where the imagery and corridor geometry support visibility; obscured portions remain uncovered. | Shortest closed walk through a covering set of inter-row corridors and passages. | Coverage report lists each row, covered length, walked metres, and uncovered or unreachable segments. |
| **Inspect selected rows** | Chosen rows or lot alerts, plus any selected POIs. | Same legal network, fewer required corridors. | Coverage and unvisited-target report; selection stays editable on the map. |

Walkable space is the union of reviewed `interrow_area` and authorised `passage` polygons, minus `forbidden` polygons and canopy obstacles. Row axes are **planting geometry**, not walking paths. Build a navigation graph along safe inter-row corridors with legal connectors at their ends and through authorised passages. A single map click should identify the nearest row/corridor/POI and explain why a route can or cannot reach it.

The challenge's `route.geojson` uses **Visit points** with all reachable required targets. All-row and selected-row routes are additional app outputs, not a replacement for that file. If legal connectivity is absent, display the unreachable targets and the disconnected components rather than drawing a shortcut through vines or a forbidden zone. “Fastest” means minimise expected walking time and known stop time; report distance and assumed walking speed separately. An optimality claim needs a lower bound or exact solver result.

## Two satellite views per eligible vineyard lot

Use the corrected vineyard block polygon, not the tile boundary, as the request and display unit. Request only the raster window intersecting that lot and a small processing buffer; compute statistics only inside the lot's eligible pixels. Cache by block geometry version, satellite item ID, and processing version.

1. **Latest usable view:** cloud-masked Sentinel-2 L2A true colour plus a simple vegetation overlay and acquisition date. Show clear coverage and the number of valid 10 m pixels.
2. **Comparison view:** closest comparable clear observation from the same seasonal window, or a within-season baseline if available. Show the change over the same interior pixels, the two dates, and a short plain-language interpretation.

Use B04/B08 surface reflectance for NDVI and the L2A scene-classification layer to remove cloud, cirrus, shadow, snow, and no-data pixels. Red and NIR are 10 m; the scene-classification layer is 20 m in the [official data documentation](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S2L2A.html). Do not label a 10 m pixel as one row: at the ~2.5 m row spacing seen in the organizer examples, one pixel spans several rows and also mixes canopy with inter-row vegetation. The existing [local Sentinel probe](RESEARCH.md) is exploratory and does not validate row-level alerts.

An observation is **ineligible** when the lot is too narrow/fragmented for enough interior valid pixels, cloud-free coverage is inadequate, or the comparison dates are not comparable. The UI then says `insufficient evidence`. `No new signal` means only that these two usable lot-level observations do not show a detected change; it is not a claim that every vine is healthy. Satellite change may create a lot/zone review prompt, not a scored missing-vine POI. A 5 m gap and waste still come from drone imagery or field observation.

The two-view interface should be on-demand from the lot detail panel: `Drone map | Latest satellite | Change`. There is no all-tile satellite layer or full-scene download in the normal farmer workflow. The drone capture date and satellite capture date must always be visible, since they can be from different seasons or years.

## Concrete UI flow

1. Select a vineyard block. See `Rows`, `Points to visit`, `Satellite signal`, and `Plan a walk` in one panel.
2. Choose `Visit points`, `Every row`, or `Selected rows`; optionally add `Waste` and `Gaps` to a row route. Row inspection uses one visible side as agreed, and flags segments with no defensible view.
3. Preview route length, estimated walk time, row coverage, target coverage, and inaccessible items **before** starting. Map highlights the exact legal corridor used.
4. In the field, tap `Arrived`, `Confirmed`, or `Dismissed` on a POI. Keep timestamp and provenance. Replan from the current location only as an operational mode; the challenge export remains a closed loop from the organizer start.
5. Satellite panel shows at most two usable, lot-clipped views and a quality badge. It may suggest `Inspect this lot`, but it never quietly creates an exact row/plant POI from 10 m data.

## Implementation slices and acceptance

1. **POI registry and map:** derive stable app-only POIs from reviewed gaps and waste, keep user pins/status separately, show a clickable list and map markers. Check that Marcaj export/import still contains only four challenge labels.
2. **Point route solver:** build legal graph from inter-row polygons/passages; route all reachable required POIs; write route plus a machine-readable coverage report. Verify the organizer start/2 m/2% constraints and compare distance with a simple baseline.
3. **Row coverage planner:** add corridor-to-row visibility mapping and `all`/`selected` row modes. Demonstrate paired rows and tile-crossing IDs without collapsing them; verify visible covered length by row ID and flag obscured parts.
4. **Satellite adapter:** fetch two on-demand, cloud-masked observations per eligible vineyard lot; no observation is shown without date, clear fraction, valid pixel count, and source. Pilot on one sufficiently large Sireț3 lot before enabling recommendations.
5. **Field workflow:** add arrival/status tracking, route summary, offline-friendly saved plan, and regenerate plan when corrected Marcaj geometry changes.

The current repository has projected scenes, an `inspection` demo feature, a map renderer, and route legality checks. It does **not** yet generate POIs from the real mosaic, solve routes, manage point status, or fetch satellite views. New API endpoints and a satellite provider are architecture/integration changes that the user-provided AGENTS.md instructions require us to approve before implementation.
