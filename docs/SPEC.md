# Vineyard AI Field Challenge — implementation specification

Status: updated 25 September 2026 after the organizer release (data package, annotation rules v1.0, Marcaj quick start). Section 8 records what the release settled. Deadline: Sunday 27 September 2026, 15:00 Europe/Chisinau, for both the repository and the Marcaj project.

This is the **canonical system and delivery specification**. [Architecture research](ARCHITECTURE_RESEARCH_2026-09-25.md) contains the paper review and imagery findings; the [inspection product contract](INSPECTION_PRODUCT.md) expands the farmer workflow. Sections 10–17 specify how both fit the challenge deliverables. They describe planned behavior, not a claim that the models, route solver, or satellite service already run.

## 1. Outcome and priorities

Turn the supplied Sireț3 RGB GeoTIFF tiles into corrected vineyard annotations, measured block/row areas and lengths, a legal closed inspection route, and a working map interface. Prioritize by judging weight and failure risk:

| Area | Weight | Critical outcome |
| --- | ---: | --- |
| Individual canopy segmentation | 25% | Separate touching vines; avoid false canopies outside vineyards. |
| Walking route | 25% | Visit reachable targets, stay inside passable space, return to start. |
| Row axes, attributes, block IDs | 15% | Continuous row identity, correct axes and allowed classes. |
| Engineering quality | 15% | Reproducible full-tile run, robust exports, measured runtime. |
| Counts and measurements | 10% | Values derived from the corrected georeferenced annotations. |
| Waste boxes | 10% | Separate visible items; avoid misses, false positives and duplicates. |

Scoring details that should guide local review: canopy combines class IoU (60%) and one-to-one instance F1 at IoU ≥ 0.5 (40%); false canopies on non-vineyard tiles are penalised. Row-axis matches require at least 80% of each axis within 0.4 m of the other. Waste boxes match at IoU ≥ 0.3. Route efficiency is awarded only from 90% target coverage. Counts/areas have a 15% relative-error tolerance and row length has 10%. Attributes use accuracy and macro-F1; missing objects count as errors.

## 2. Inputs and coordinate contract

- Read all organizer-supplied GeoTIFF tiles unchanged, preserving original filenames and georeferencing. Expected count is **311 files across five parts**; confirm against the actual release manifest before import.
- Read the starting-point GeoJSON and authorised passage/forbidden-zone polygons. Validate geometry, CRS, units, and the `type` property on arrival.
- Use EPSG:32635 (metres) for all geometry processing, measurements, route generation, and submitted coordinate arrays. State explicitly in the README that the challenge requires projected coordinates in `.geojson` files.
- Convert a separate display copy to longitude/latitude for a conventional browser map. Never compute metre lengths or areas from that display copy.
- Process tiles or windows incrementally; do not require loading the full 81.5 ha released study area into memory at once. The 145 ha figure in the brief is not the extent of the supplied 311 tiles.

## 3. Annotation contract

| Label | Geometry | Required attributes | Rule |
| --- | --- | --- | --- |
| `vineyard` | Polygon | `vineyard_id` | One polygon per distinguishable vine canopy; exclude inter-row ground. |
| `waste` | Bounding box | `vineyard_id` | One box per distinguishable item, or one for an inseparable cluster; box area is not physical waste area. |
| `row` | Polyline | `vineyard_id`, `row_id`, `row_structure` | Axis of the vine row, not of the inter-row. A visible gap does not create another row ID. |
| `interrow_area` | Polygon | `vineyard_id`, `interrow_cover` | Ground between adjacent vine rows in one block; exclude canopies, roads, headlands and exterior land. |

Allowed `row_structure`: `regular`, `disrupted`, `unassessable`. Allowed `interrow_cover`: `bare_soil`, `vegetation`, `mixed`, `unassessable`. Use `unassessable` only where the image cannot support a class; it is scored as a class, not ignored.

A `vineyard_id` names one connected planting block. IDs are stable across tiles. `row_id` should be globally unique and stable across tiles (for example, a block prefix plus row number); exact ID text need not match the reference. Crossing a tile edge may create clipped geometry fragments, but must not create a new physical object or row identity. Resolve overlap duplicates before measurements and export.

Record inspection locations with an ID, EPSG:32635 coordinates, reason, `vineyard_id`, and `row_id` when applicable. Route targets comprise those locations and detected waste. The organizer's annotation rules must settle ambiguous cases, especially block association of waste in the surrounding zone, partial canopies at tile edges, and what constitutes a disrupted row.

## 4. Processing flow

1. **Ingest:** inventory the exact tile filenames, raster dimensions, transforms, CRS and bounds; read organiser route constraints; reject mismatched/missing inputs.
2. **Pre-annotate:** run a neural-network canopy instance-segmentation baseline, a waste detector, and row/cover extraction. Preserve model versions, weights source, parameters, and run time. Classical image processing may refine shapes and axes.
3. **Reconcile geography:** merge same-block and same-row identities across tiles; split adjacent canopy instances; remove duplicate objects; derive non-overlapping inter-row polygons and gap inspection points.
4. **Import once:** package `annotations.xml` plus every unchanged GeoTIFF under `images/` as CVAT for images 1.1. Validate labels, attributes, image names, shape coordinates and file count before uploading all five parts. Confirm 311 files in Marcaj, then publish. Pre-annotations cannot be imported after publication.
5. **Correct in Marcaj:** assign neighbouring tiles together where practical, agree IDs before splitting work, manually correct geometry/attributes only in Marcaj, review, and submit every job. The exported corrected annotations are the source for final measurements and routing.
6. **Measure and route:** convert corrected pixel shapes to projected geometry; compute block/row counts, row lengths, canopy union area and inter-row union area. Build the passable network from inter-row polygons and authorised passages, remove forbidden areas and canopy crossings, visit every reachable target, and return to start.
7. **Display and submit:** show imagery, objects, IDs, targets, route and measurement tables in the web interface; produce final repository artifacts and reproduction instructions.

If corrections continue after a route run, rerun measurements and routing from the latest Marcaj export so the app and files agree with the reviewed annotations.

## 5. Output contracts

### `route.geojson`

- One `Feature` with one `LineString` and numeric `length_m`, coordinates in EPSG:32635.
- Start and end within 5 m of the supplied starting point; aim for the exact coordinate.
- Stay within passable inter-row areas and authorised passages, with forbidden areas excluded. The official route criterion scores zero if more than 2% of length lies outside passable space.
- Visit every reachable inspection/waste target; a target counts when the line passes within 2 m. Report targets that cannot be legally reached separately rather than drawing through forbidden space.
- Verify `length_m` against the projected geometry. Make the route continuous and valid, with no accidental jumps across disconnected components.

### `measurements.csv`

Use one explicit `level` column (`total`, `block`, `row`) to avoid repeating counts/areas in ambiguous rows. Planned fields: `level,vineyard_id,row_id,block_count,row_count,row_length_m,canopy_area_m2,canopy_area_ha,interrow_area_m2,interrow_area_ha`. Leave non-applicable cells empty. Row records contain individual lengths; block records contain per-block totals; one total record contains distinct global counts and unions. Round only when serializing, after all sums. Confirm this schema against any organizer template on release.

### Marcaj upload

`team_upload.zip` contains `annotations.xml` and `images/<original-tile-name>.tif` for all supplied tiles. The XML uses CVAT for images **1.1** with pixel coordinates, while submitted geospatial outputs use EPSG:32635. Validation must catch missing/renamed tiles, duplicate image entries, out-of-bounds coordinates, invalid geometry, unknown labels/attributes, unstable IDs and canopy/inter-row overlap. CVAT's format specification supports boxes, polygons, polylines and attributes: https://docs.cvat.ai/docs/manual/advanced/formats/format-cvat/

### README and application

Provide install/run instructions from supplied inputs to `route.geojson` and `measurements.csv`; pin dependencies; identify model weights or how to obtain them; disclose paid APIs/LLMs; report full 311-tile processing time and measured hardware; link the working interface. The interface must show route length, vineyard/row IDs, individual and total row lengths, canopy/inter-row area, and block/row counts. Demonstrate from a team laptop if deployment is unavailable.

## 6. Acceptance checks

Before Marcaj publication:

- Exactly the released tile set is present, unchanged by filename and content; all five parts are uploaded and Marcaj shows 311 files.
- XML is parseable CVAT images 1.1; each image entry matches a supplied tile; all shapes use permitted labels, geometry types and attribute values; sampled pixel-to-world placement agrees with the source raster.
- Tile-border IDs are consistent; obvious duplicate shapes and canopy/inter-row overlaps are removed; pre-annotations on example tiles are visually inspected against the organizer's rules.

Before final submission:

- Every Marcaj job is submitted, and final export is archived locally for reproducibility.
- Counts, lengths and areas are regenerated from the latest corrected export; canopy and inter-row union areas do not overlap; hectares equal square metres / 10,000.
- Route is a single closed LineString in EPSG:32635, has correct `length_m`, visits every legally reachable target within 2 m, and has no more than 2% of length outside legal space. Inspect the route visually over imagery and passages.
- Web map and tables agree with the files; the README's full-run command completes on the stated hardware, or any limitation is clearly reported.

## 7. Remaining delivery sequence

1. **Freeze inputs and evidence:** record the 311-tile manifest, organizer constraints and example annotation version. The two organizer examples are development references, never an independent test set.
2. **Get a complete pre-annotation through Marcaj:** prioritize conservative block/row identity and useful canopy candidates across every tile. Validate and upload before publication; reserve correction time for hard green inter-rows, boundaries and false vineyard detections.
3. **Close the scored outputs:** correct in Marcaj, export its latest snapshot, regenerate measurements and the target route, run validators, then check map/files against that same snapshot. Keep an archived export and a measured reproduction command.
4. **Add farmer tooling in dependency order:** app-only POIs, point route, all/selected-row coverage, then on-demand satellite views. Challenge outputs must remain reproducible if satellite is unavailable.

The deadline puts the Marcaj publication gate and scored route ahead of satellite integration. Sections 10–17 are the complete contract for both the challenge slice and the subsequent farmer workflow.

## 8. Settled by the release (25 September)

- **Tiles:** 311 GeoTIFFs, EPSG:32635, 2048 × 2048 px at **0.025 m/px** (51.2 m), 3-band uint8, JPEG-compressed, no overviews. They lie on one grid: left = 628992.0 + 51.2·c, top = 5221222.4 − 51.2·r for `siret3_r<r>_c<c>`. `tiles.load_tiles` reads the tile set from the five part ZIPs, extracts and CRC-checks every file, and rejects any other CRS or grid.
- **Pixel convention:** CVAT points are continuous with the image spanning 0..2048, so world = affine(pixel) with no half-pixel shift. The organizer examples round-trip through world coordinates with zero geometry change.
- **Upload size:** the supplied parts are up to 94.1 MB decimal, yet the organizers call them "under the 90 MB limit", so the limit is 90 MiB. Adding `annotations.xml` would push part 4 over it, so `marcaj-pack` regroups in name order under 88,000,000 bytes and then re-opens every ZIP to verify it.
- **Holes:** CVAT 1.1 polygons are a single ring, so inter-row holes (trees, buildings) cannot be uploaded. `image_elements` fills them and prints a count. Ask on Slack if the count is material.
- **Blocks:** plantings under 5 m apart are one block, and a road or track always separates blocks. Waste takes the block it lies in, or the nearest one within 10 m, and otherwise an empty `vineyard_id`.
- **Attributes:** `disrupted` means a gap of at least 5 m within this tile. `interrow_cover` is bare below 25% cover, mixed at 25–75% and vegetation above 75%. Both are judged per tile.
- **Inspection targets** belong in the application output, not in Marcaj. Route targets are those plus waste. A target counts when the route passes within 2 m; `routing.check_route` measures it against the waste box centroid, which is the conservative reading.
- **`route.geojson`** is a FeatureCollection with the same `crs` member as the organizer GeoJSONs, holding one LineString feature with `length_m`. Export refuses a route that would score 0.
- **Route space:** START lies inside `passages.geojson`. The passages minus the forbidden zones form 2 disconnected components (40,000 m²), so the inter-row areas are needed to join them. The study area is 81.5 ha, the 311 tiles, not the brief's 145 ha.
- **Marcaj export:** CVAT XML or json_simple. `marcaj-scene` reads CVAT XML, as an `.xml` file or inside a ZIP.

## 9. Still open

- How "reachable" is judged for targets in components cut off by forbidden zones.
- Whether Marcaj keeps polygon holes or accepts a separate way to express them.

Neither question prevents pre-annotation, route generation, or a conservative local validity report. Preserve the geometry and report disconnected targets and hole-filling counts; confirm the organizer's interpretation before claiming official compliance.

## 10. Architecture, ownership, and versions

```text
Unchanged GeoTIFFs + organizer constraints
  -> inventory / projected tile grid
  -> vineyard candidate map -> native-resolution objects -> cross-tile reconciliation
  -> CVAT 1.1 upload -> human correction in Marcaj -> versioned corrected export
  -> projected scene -> measurements.csv + mandatory point route.geojson
                     -> app-only POIs, row coverage routes, map
  -> corrected vineyard blocks -> on-demand satellite lot observations -> visit suggestions
```

| Record | Authority | Version/provenance required | Allowed downstream use |
| --- | --- | --- | --- |
| Source imagery and constraints | Original organizer files | File manifest/checksum and CRS | All geometry and challenge checks. |
| Pre-annotations | Local model run | Weights, code revision, parameters, tile IDs, run time | One-time Marcaj import and review queue. |
| Corrected canopies, rows, inter-rows, waste | Latest submitted Marcaj export | Export timestamp/hash plus source tile/shape IDs | Final measurements, POIs, routes and display. |
| Inspection points and status | Application registry | Derivation/export version or user origin; status event time | Field planning only; never a Marcaj label. |
| Satellite observations | Satellite item and processing run | Item ID, acquisition time, band/mask version, block geometry version | Lot-level triage only. |

Every derived artifact records the corrected-export hash. A changed Marcaj export invalidates measurements, gap/waste POIs, route graph, row coverage and cached satellite block masks. User pins and visit history retain their IDs and provenance and are re-associated or flagged for review. The EPSG:32635 scene is the working geometry; WGS84 copies are presentation only. A local single-user file registry is sufficient for the challenge demo. Shared field use later needs authenticated persistence and synchronization and is outside the current release.

The repository already has tile inventory, CVAT conversion/packaging, a projected scene, measurement/export checks, map imagery, and route *validation*. The vineyard models, real gap/POI derivation, route *solver*, point status, row coverage planner and satellite adapter are planned. Do not present the demo scene or existing validator as a completed scorer route.

### 10.1 Internal artifact contracts

- `predictions.geojson` (written by `lab.save()`): projected FeatureCollection using the existing scene labels and global block/row IDs; model-run manifest alongside it records every processed tile, model/weights, confidence and review disposition. This feeds `marcaj-pack`, not the final measurements.
- `scene.json`: projected FeatureCollection reconstructed from the latest Marcaj export, plus organizer route constraints; store its source export hash separately. This is the only annotation snapshot used by final outputs.
- `inspection_points.geojson`: projected FeatureCollection of app-only points with the fields in Section 12. Stable IDs are derived from corrected object IDs and a spatially rounded anchor; preserve a crosswalk when a correction moves or merges an object.
- `route_plan.json`: `mode`, corrected-export hash, start, assumptions (`walking_speed_m_s`, stop time, visibility rule), LineString, `length_m`, per-target actual distance/status, per-row covered/uncovered metres and disconnection reasons. The scored route is serialized separately to the strict `route.geojson` contract in Section 5.
- `lot_observations.json`: one quality/provenance record per requested block and satellite item, with an explicit `unavailable` or `insufficient_evidence` state. Imagery stays in a cache; the app receives only the two chosen clipped views and summary values.

Keep projected data at these processing boundaries. Convert the display payload to WGS84 in the existing API layer. CLI generation and read-only UI delivery can serve the challenge demo; adding write endpoints for field status or a third-party satellite adapter needs the approvals in Section 17.

## 11. Imagery-to-annotation system

### 11.1 Vineyard finding

The first stage produces **candidate regions with confidence and review priority**, not an irreversible whole-tile yes/no. All 311 tiles are inventoried and processed. Use a cheap reduced-resolution texture/row-orientation and context pass to focus native-resolution work; keep a high-recall path for low-confidence or boundary tiles. A truly blank-tile shortcut is allowed only after a spatial holdout shows that it does not discard scored vineyard objects. Tile scores are not exported as vineyard labels.

Differentiate vine plantings from orchards, trees, field crops and roads by combining repeated narrow planting axes, plant-scale canopy instances, spacing regularity, neighbouring block geometry and local texture. Greenness alone is insufficient: a grass-covered inter-row may be greener than the vine. Evaluate maintained bare/mixed inter-rows and overgrown vegetation separately. Dense double-looking rows must retain two physical axes when evidence supports two; do not merge or split solely by expected row width.

### 11.2 Fine geometry and topology

- Infer canopy masks/instances at native 0.025 m/px with overlapping windows, including tile-edge context. Small proposals can use a coarse context model, but a full 2048-pixel tile must not be compressed to a size that erases sub-metre vines.
- Separate touching canopies; keep a review state where individual crowns are not visually resolvable. Fit vine-row axes through plant centres and texture evidence; use multi-tile geometry to connect clipped fragments with the same global `row_id`.
- Identify one `vineyard_id` per planting block under the organizer's road/spacing rules. Produce inter-row polygons from the *space between adjacent rows*, clipping out canopy, roads, headlands, buildings and forbidden/exterior space. Assign cover from the visible ground, not the vine canopy colour.
- Detect waste as boxes, with conservative duplicate removal across windows/tiles. Flag visible ≥5 m row gaps as candidate inspection points; this does not split the row ID. Do not invent a gap where canopy is obscured.
- Retain per-object confidence and source tile/window for review, even though the Marcaj label contract only carries the permitted classes/attributes. Low confidence means review priority, not silent omission.

Train and compare a simple baseline with the strongest feasible native-resolution instance pipeline; choose by **spatial holdout**, not an attractive visual sample. Split by geographic block/contiguous area before cropping, prevent adjacent tile leakage, and stratify maintained, green/overgrown, orchard/non-vineyard, small garden plantings, borders, shadows and double-row cases. The organizer examples may guide formatting but cannot estimate hidden-set accuracy. Report class IoU, one-to-one instance F1, non-vineyard false positives, row-axis continuity/identity, attribute macro-F1 and gap/POI recall separately. Fix the confidence threshold from held-out data; keep a human-review queue for uncertain but plausible vineyards. Model choice, weights and batch/window sizes remain benchmark decisions, not unverified requirements.

## 12. Application data and point lifecycle

The [inspection contract](INSPECTION_PRODUCT.md) defines the user journey. The app registry is separate from the four Marcaj labels. Minimal projected records are:

| Type | Required contract |
| --- | --- |
| `inspection_point` | Stable `id`, Point EPSG:32635, `vineyard_id`, optional `row_id`, `reason` (`gap`, `planting`, `user`, `field`), `source`, `source_ref`, `observed_at`, `confidence`, `status`. |
| `waste_visit` | Stable ID keyed to a corrected waste box, centroid Point, box reference, block ID, status. The box itself remains in Marcaj. |
| `row_alert` | IDs, block, optional row IDs, reason, evidence reference, date, uncertainty; a lot-only satellite alert has no fabricated row ID. |
| `lot_observation` | Block ID/geometry version, satellite item/date, valid interior pixel count, clear fraction, statistic/change, quality state and processing version. |

Derive gap POIs from corrected row axes and canopy evidence, deduplicate across tile edges, and keep an explicit `candidate`/`reviewed` origin so a model guess is not described as confirmed. Waste visits come from the reviewed waste boxes. User pins and field notes are additional operational targets. Status changes (`new`, `planned`, `visited`, `confirmed`, `dismissed`) append timestamped events instead of erasing history. A dismissed operational point remains in the audit record; the scored challenge route uses every required reachable gap/waste target regardless of field status. Export a target-coverage report that lists included, visited, unreachable and uncertain targets with distances to the route.

No POI, row alert or satellite observation enters the CVAT upload or Marcaj correction project. The application can show them next to annotations, clearly identified by source and capture date.

## 13. Routing and row coverage

Build walkable geometry from reviewed inter-row areas and authorised passages, subtracting forbidden zones, canopy obstacles and mapped non-walkable interiors such as trees/buildings. Because CVAT 1.1 may fill polygon holes on upload, preserve the known hole/obstacle geometry outside the Marcaj label set and mark any route through an unresolved filled hole for manual review. Use the organizer start as the challenge start/end. Snap target approaches only within legal geometry; never mark a target visited merely because its snapped proxy is close when the actual target is more than 2 m from the final line. Retain disconnected components and a reason for each unserviceable target. Make one continuous closed LineString; do not insert straight jumps between graph nodes. Validate the **final simplified/exported geometry**, since smoothing or coordinate rounding can change coverage and legality.

| Route mode | Required visits | Optimization and output |
| --- | --- | --- |
| Challenge / Visit points | All reachable required gap POIs and waste box centroids within 2 m; optional user POIs only when selected. | Short legal closed tour; export the challenge `route.geojson`, length, target-distance report and unreachable list. |
| Inspect every row | Each physical row's visible length observed from at least one legal adjacent corridor. | Minimum travel time over a covering set of corridors; output per-row covered metres, uncovered metres and route. |
| Inspect selected rows | Selected row IDs or row alerts plus chosen POIs. | Same coverage accounting on the selected subset, with editable selection and an unvisited report. |

The field route planner may combine selected POIs with either row mode. Use walk-network shortest paths between access points, a visit ordering heuristic/exact solver as scale permits, and local improvement; report both route length and an optimality gap only if a valid lower bound was computed. “Fastest” uses stated walking speed and stop time assumptions, while distance remains an independent output. The challenge route must optimize the point task; an all-row detour is not implicitly required by that file.

**One visible side is enough**, as the user decided. A corridor can credit both adjacent rows only where the projected geometry has an unobstructed plausible view of each row. Divide row axes into short segments; associate each with legal adjacent corridors, subtract screened/occluded or unassessable segments, then solve a set-cover/rural-postman-style route on the remaining corridor segments. Do not infer field visibility as certain from overhead imagery: label coverage as `geometric proxy`, list uncertain/uncovered metres, and let a farmer confirm it. Paired planting axes must be counted independently even when one corridor might cover both. Crossing a tile boundary preserves one physical row and one coverage tally.

The scored route acceptance remains the organizer's start/end within 5 m, all reachable required targets within 2 m, and no more than 2% of length outside legal space. Operational row routes show the same legality diagnostics, plus per-row coverage and walking-time assumptions.

## 14. Satellite decision support

Satellite is requested **only for corrected vineyard blocks**, on demand, with at most two usable views: latest clear observation and a comparable same-season reference. Clip statistics to valid *interior* block pixels; fetch only the intersecting source window plus a small processing buffer. Cache by block geometry version, item IDs and processing version. Show source, dates, clear fraction and valid pixel count in the lot panel. A block with too few uncontaminated pixels, cloud/shadow cover, or incomparable phenological dates receives `insufficient evidence` rather than a vegetation judgement. The exact minimum clear fraction/pixel count and change threshold must be calibrated on pilot blocks before alerts are enabled; record them in configuration and evaluation output.

Compare observations from a relevant growing-season stage; the latest calendar image is not automatically the latest usable vegetation view. Prefer the same interior pixel support in both images and report the count after both masks, so a changing cloud footprint cannot masquerade as a canopy change. A comparison without a qualified reference shows the latest view with `insufficient evidence`, not a change alert.

For Sentinel-2 L2A, use surface-reflectance B04/B08 to calculate NDVI and the scene-classification layer for cloud, shadow, snow and no-data exclusion. B04/B08 are 10 m and the classification layer is 20 m per the [official data specification](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S2L2A.html). A 10 m pixel mixes multiple ~2.5 m vine rows and their green inter-rows. Satellite may suggest a **lot or broad zone** visit; it cannot identify a 5 m missing-vine gap, waste item or exact row for the scorer. Show `inspection recommended`, `no new signal`, or `insufficient evidence` with a short reason. `No new signal` describes the compared observations only, not plant health clearance.

The adapter contract is source-independent: find candidate acquisitions for a block/date window, read only required bands/masks, return provenance and quality, and fail to `unavailable` without breaking the drone/route workflow. Choose a provider and check its licence, credentials, quotas and actual Sireț3 coverage before implementation. A pilot must prove at least one real block has enough pure clear pixels; otherwise retain the satellite panel as an unavailable/ineligible state, without invented imagery or alerts.

## 15. Map, interaction and operational boundaries

On selecting a block, show its rows and reviewed annotations, app-only points, satellite evidence, and one `Plan a walk` control. Route preview must show length, assumed duration, POI coverage, per-row coverage, and unreachable/uncertain items before `Start`. A field user can mark arrival and confirm/dismiss a point; each action stores source and time. The challenge route is always tied to the organizer start and corrected-export hash, while an operational route may replan from a user-selected/current location. The map explains why a point cannot be reached and highlights legal corridors rather than displaying a shortcut through canopies.

Use clear labels for `model candidate`, `Marcaj reviewed`, `field confirmed`, and `satellite signal`; show imagery dates. Loading, missing imagery, disconnected route, invalid geometry and stale export are explicit interface states. At 360 px, 768 px and 1280 px, keep map/route controls reachable; labels, keyboard operation, visible focus and sufficient contrast are required. A challenge demo may be local and single-user. Multi-user sync, live GPS tracking, permissions and push notifications require separate product decisions and are not required for the current acceptance gate.

## 16. Verification matrix

| Requirement | Concrete acceptance evidence |
| --- | --- |
| Full imagery coverage | Exact 311-name/checksum inventory; one import entry per image; model run/review disposition for every tile. |
| Vineyard vs other crops | Geographic holdout with separate maintained, green/overgrown and orchard/non-vineyard results; false positives counted on negative tiles. |
| Individual canopies and paired rows | Visual review plus scored instance/axis metrics on held-out blocks; two physical axes and distinct IDs in paired-row cases. |
| Cross-tile integrity | Same block/row IDs across borders, no duplicate objects, projected round-trip and union-area checks. |
| Marcaj source of truth | All jobs submitted; latest export hash stored; downstream artifacts name that hash and regenerate after corrections. |
| Point route | Geometry validator on the exported file: one closed line, CRS, length, start, legal fraction and per-target true distance; map inspection. |
| All/selected-row routes | Legal path and independent per-row covered/uncovered metre report; reviewed occlusion cases and one-side rule. |
| Measurements | Recompute from latest export; distinct counts, projected lengths/union areas and m²-to-ha conversion; CSV/map agreement. |
| Satellite | Real item provenance and dates, cloud/quality mask, interior-pixel count, two-view comparison or explicit ineligible state; no row/plant claim. |
| Farmer interface | Normal input flow for select block, plan/preview, reachability explanation and POI status; functional and visual checks at 360/768/1280 px. |
| Reproducibility | Pinned environment, model weight source, full-data command, measured runtime/hardware and validator results in README. |
| Review loop | `marcaj-judge` / `lab.report()`: the organizer formulas on the reference tiles, plus regressions against the lab verdicts in `data/review/verdicts.json`. Verdicts are evaluation evidence only and never enter predictions or the upload. |

Do not treat static code, a synthetic scene, or the two organizer examples as proof of hidden-set accuracy or a completed 311-tile run. Record actual pass/fail results as work progresses.

## 17. Decisions and external verification gates

The specification is actionable with these defaults: one visible side counts when a row segment is plausibly visible; POIs are app-only; the scored route visits required points; farmer row routes are separate; satellite is limited to two eligible block views; unclear observations produce `insufficient evidence`.

| Gate | Why it matters | Action before claim or implementation |
| --- | --- | --- |
| Organizer rule for unreachable targets | Could change official route coverage accounting. | Ask organizer; meanwhile report each disconnected component and never cross forbidden space. |
| Marcaj handling of polygon holes | Could change inter-row representation. | Confirm accepted encoding; meanwhile record every filled hole and validate the resulting legal-space risk. |
| Satellite provider, licence and Sireț3 usable pixels | Determines whether the optional two-view product can operate. | Verify credentials/terms and pilot one sufficiently large corrected block before alerting. |
| Model accuracy and compute budget | Determines architecture/window size and review workload. | Benchmark on geographic holdout, measure full run and adjust review threshold before publication. |
| Field visibility | Overhead geometry cannot certify a person can see every vine. | Treat row coverage as a geometric proxy and confirm uncertain segments during field use. |

These are verification gates, not missing product decisions. Planned new API routes, shared persistence, satellite integration or other scope changes require the approval called for by the supplied AGENTS.md before implementation. The current spec itself adds no API or dependency.
