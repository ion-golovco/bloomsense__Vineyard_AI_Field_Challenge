# Vineyard AI Field Challenge — implementation specification

Status: initial specification, 25 September 2026. Source: organizer challenge statement supplied by the team. Organizer annotation rules, example tiles, actual tile files, and Marcaj guide have not yet been inspected; they take precedence where they add detail. Deadline: Sunday 27 September 2026, 15:00 Europe/Chisinau, for both the repository and the Marcaj project.

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
- Process tiles or windows incrementally; do not require loading the full ~145 ha mosaic into memory at once.

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

## 7. Work that can start before the organizer data

1. Prepare a small synthetic georeferenced vineyard tile and corresponding polygons/rows/targets/passages for end-to-end format and routing checks. This is a development fixture, not Sireț3 annotation.
2. Build the raster/vector coordinate conversion, CVAT 1.1 writer/reader, input inventory, and output validators around that fixture.
3. Train or adapt a canopy instance-segmentation baseline on [Riseholme](https://zenodo.org/records/19234907) and assess a waste baseline using [DroneWaste](https://github.com/lucamora/dronewaste). Verify each dataset and model licence, record weights and hardware, and expect domain shift on Sireț3.
4. Implement a legal-space route prototype and a simple map using synthetic data. Keep display reprojection separate from submitted projected files.
5. Agree team ownership for model, GIS/route, and Marcaj/export/UI. Reserve time for review and job submission, not only inference.

## 8. Release-day decisions and unresolved details

- Inspect the organizer's annotation PDF and sample tile/template first. Resolve any difference from this initial spec before generating a real Marcaj ZIP.
- Check actual tile extent, overlap, colour/season, vineyard density, memory footprint, and inference throughput. Tune the baseline on the released, unscored examples before processing all tiles.
- Confirm how Marcaj represents pre-annotation IDs across tiles and whether waste in the surrounding zone is assigned to the nearest block or handled differently.
- Confirm whether the starting point lies in a legal passage and how the official rules define reachability at disconnected components.
- Freeze the model and pre-annotation ZIP before publishing Marcaj. After publication, work through corrections, route, measurements and final validation from the corrected export.
