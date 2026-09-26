# Code review — 26 September 2026

Reviewed the working tree, including existing uncommitted changes. This review changes documentation only; it does not fix detector, routing or UI behavior, commit changes, upload to Marcaj or deploy the app. Additional research/source edits appeared during the review, so the full-run result is a run snapshot rather than a guarantee about every later edit.

The complete implementation walkthrough and mentor explanation are in [README.md](../README.md). The main result is: **the hybrid detector runs and packages successfully, but the corrected-annotation → derived artifacts → client-map chain is not yet ready to present as a consistent final submission.**

## Findings, in priority order

### 1. [P1] Route acceptance permits forbidden/canopy crossings

**Evidence:** [`routing.check_route`](../backend/src/marcaj/routing.py#L74) calculates `forbidden_m` and `canopy_m`, but line 80 sets `legal` solely from `outside_share <= 0.02`. [`export._write_route`](../backend/src/marcaj/export.py#L44) accepts the official route when `closed` and `legal` are true. The graph also retains canopy/row obstacles overlapping passable cells as expensive walkable cells ([route.py](../backend/src/marcaj/route.py#L106)); simplifying a path can introduce crossings even when grid nodes avoid them.

**Reproduced:** a closed 198 m out-and-back route across a 0.5 m forbidden strip and a 0.5 m canopy strip returned `forbidden_m=1.0`, `canopy_m=1.0`, `outside_share=0.00505`, `closed=true`, **`legal=true`**. This is a validator counterexample, not a claim that the stored site route crosses forbidden zones. Stored all-target route geometry does cross approximately 1.55 m of reference canopy when checked against the current scene.

**Impact:** a route that violates the no-crossing requirement can pass the final export gate. The 2% allowance does not make a forbidden crossing acceptable.

**Fix direction:** keep the outside-share result separate from full compliance; require forbidden/canopy clearance on the final simplified and rounded line and prevent hard obstacles from remaining walkable. Validate target distances after those repairs. Any tolerances should represent numeric precision, not permit physical crossings.

### 2. [P1] Artifacts are mixed across runs and lack a correction-version check

**Evidence:** [`scene.overlay_features`](../backend/src/marcaj/scene.py#L110) reads fixed POI/zone/route files if they exist. It checks CRS, not their source-scene version or route validity. [`mosaic.load_mosaic`](../backend/src/marcaj/mosaic.py#L42) and [`layers.load_layers`](../backend/src/marcaj/layers.py#L104) similarly reuse caches by existence. The planned corrected-export hash in SPEC §10 is not enforced.

At review time:

| Artifact | Observed contents |
|---|---|
| Default scene and predictions | 13,279 canopy pieces and 29 old waste boxes |
| Separate waste output | 2 waste boxes |
| Saved all-target route | 193 targets, including 2 development waste targets; 125 visited |
| Browser overview | 225 points: 29 old waste + 191 challenge POIs + 5 satellite points |
| Browser measured annotation world | Only 2 reference blocks, 51 rows; full-site predictions are excluded |
| Fresh detector review run | 13,518 canopy pieces and 2 waste boxes |

[`research/probes/route_probe.py`](../research/probes/route_probe.py#L37) deliberately retags predictions `source=dev` to plan as if corrected, replaces waste with a separate file, and writes route overlays into the same path read by the app. That is a useful development probe, but its assumptions are not carried into a verifiable scene-version contract.

**Reproduced:** saved routes report `legal=true` against their development geometry. Rechecking them against the current scene's actual scored annotation world gives **48.51%, 47.40% and 45.75% outside passable space**, all failing the 2% rule. This mismatch is expected because only the two examples are reference annotations; it proves the app is combining different worlds, not that the full predicted-site graph has those outside percentages.

A second provenance issue is [`scene.is_scored`](../backend/src/marcaj/scene.py#L48): anything except explicit `source=prediction` counts as authoritative. Raw predictions omit source on block, row and inter-row features; loading that file directly as a scene can count predicted rows/ground while excluding predicted canopy/waste. `cvat.build_scene` tags predictions correctly, so use that boundary.

**Impact:** correcting Marcaj or changing the detector can leave a visually plausible route and POIs from old geometry. Metrics, points and displayed routes need not agree.

**Fix direction:** version artifacts by the corrected-export hash, input/parameter hashes and weights hash; reject mismatches at load/export; keep development probe artifacts distinct from final outputs; tag every prediction explicitly and accept only declared authoritative sources for final measurements. `lab.save()` also needs to remain a development action: it rebuilds the default scene from examples.

### 3. [P1] Corrected scenes and global routes do not fit the frontend contract

**Evidence and reproduction:**

- [`main.ts`](../web/src/main.ts#L300) populates fields only from `label=block` polygons. [`cvat.build_scene`](../backend/src/marcaj/cvat.py#L210) imports four challenge classes and constraints; corrected CVAT has no app `block` label. A corrected-only scene made from all 750 example objects has **zero selectable field polygons**, despite correct block/row measurements.
- [`route.routes_by_confidence`](../backend/src/marcaj/route.py#L510) emits whole-site routes with no `vineyard_id`. [`main.ts`](../web/src/main.ts#L66) requires field ownership in a multi-field scene; line 249 skips the route and START in per-field mode. The loaded browser showed **“36 fields in scene · field route pending”**, with a disabled route button, while the API returned three routes.
- The all-fields overview draws all three confidence alternatives together; there is no selection control for the variants.
- Detailed measured block/row counts, canopy/inter-row areas and individual row lengths are supplied by the API but have no client measurement table. Only candidate boundary area is shown.
- Reference IDs (`V01`, `V02`) differ from predicted field IDs (`Pxx`), so selecting predicted fields can omit the corresponding reference objects.

**Impact:** the intended post-correction demo loses its field navigation. The existing demo cannot show a per-field route or calculate the intended route-based savings, and cannot demonstrate all required measurements through the UI.

**Fix direction:** derive app-only block geometry from corrected IDs, use those same IDs throughout the display scene, treat the official route as a site-level route, explicitly select one alternative, and render authoritative measurements. Separate any later per-field route plan from the global submission tour rather than inventing a field ID for the whole-site line.

### 4. [P2] Fragment merging does not fully implement block grouping or rerun-stable identity

**Evidence:** [`plots._merge`](../backend/src/marcaj/plots.py#L326) joins fits only with nearly equal direction (within 1.5°), spacing (within 5%), compatible row phase and sufficient quadrilateral fill. Those constraints help reconstruct one planting, but they are narrower than the organizer rule that plantings under 5 m apart share a block unless a road/track separates them. No later grouping pass resolves that difference. [`plots.detect_plots`](../backend/src/marcaj/plots.py#L460) assigns `Pxx` IDs in descending accepted area order.

**Impact:** nearby differently oriented plantings can retain different `vineyard_id` values. The perfect grouping score on the two examples does not establish site-wide compliance. A change to one plot can renumber other plot IDs, breaking downstream row/POI/harvest associations across reruns.

**Fix direction:** separate detection fragment geometry from challenge block identity; apply the proximity/road grouping rule after detection, then maintain a spatial ID crosswalk between versions. Preserve distinct physical row axes even when blocks share an ID.

### 5. [P2] Reported scores do not establish hidden-set model quality

**Evidence:** the example tiles guided colour, geometry and network choices. The checkpoint correctly records `diagnostic=false`, no external teacher/init weights, 118 pseudo-labelled training tiles, and two-example neighbourhood exclusion. That prevents direct training-label leakage from the examples, but the teacher and model-selection process were tuned against them. The network is trained to approximate automatic rule labels, with shared geometric post-processing; its recorded hybrid result is roughly tied with the teacher baseline.

Plot evaluation uses 19 north and 16 south team outlines. [`research/notes/fields.md`](../research/notes/fields.md) explicitly records a phase choice after inspecting a south result; other trial tables also repeatedly compare both halves. Treat south as a geographic development comparison, not an untouched final holdout.

Waste is unscored locally because the examples contain none. The inter-row-only detector has narrower coverage than the full challenge waste scope. Plot misses automatically suppress downstream canopy/row/POI coverage; there is no independent rescue pass for those regions.

[`judge.py`](../backend/src/marcaj/judge.py#L222) calculates non-vineyard false-canopy penalties and returns them separately, but line 240 does not subtract them from reported points. There were no such verdict penalties in this snapshot. This becomes misleading if no-vineyard verdicts are later added and the printed estimate is interpreted as a complete organizer score.

**Impact:** “44.88/50” is a useful local regression number, not a predicted competition score, 89.8% unseen-site accuracy, or proof of waste/route readiness. A confidence cutoff is not calibrated precision.

**Fix direction:** freeze a model version before evaluating new geographic annotations; report plot recall, canopy instance/union metrics, gap recall, waste precision/recall and route coverage separately. Label tuned examples, team visual judgements and official corrected geometry separately. Account for any applicable penalties explicitly.

## Verification evidence

| Check | Result | Scope |
|---|---|---|
| Python compile check | PASS | Backend and research probes; no training rerun |
| Frontend build/typecheck | PASS | `npm run build` |
| Input integrity | PASS | All 311 tiles match original size/CRC, CRS and raster grid |
| Reference CVAT roundtrip | PASS | 750 → 750 objects, same label/tile/IDs, maximum nearest Hausdorff difference 0.0 m |
| Existing upload ZIPs | PASS | Five ZIPs, no local verifier problems |
| Fresh upload ZIPs | PASS | Five ZIPs containing all 311 original tiles; zero verifier problems; 8.1 seconds |
| Full automatic detector | PASS | 36 plots, 13,518 canopy pieces, 1,776 row fragments, 1,727 inter-row fragments, 2 waste boxes |
| Fresh detector local judge | PASS as regression | 44.88/50 available points on two tuned examples; canopy composite 0.854, union IoU 0.8191, instance F1 0.9063, axis F1 0.9703 |
| Corrected-example POIs | PASS | 51 unique reference rows produce 8 gap and 2 planting candidates; satellite disabled; 1.3 seconds |
| Existing route controls | PASS | Outside-distance rejection, repeated traversal distance and unclosed-route rejection |
| Saved route vs current reference scene | FAIL | 45.75–48.51% outside annotated passable geometry; incompatible development artifact |
| Crossing acceptance counterexample | FAIL | Forbidden and canopy crossings both present while `legal=true` |
| Corrected-only field selection | FAIL | Corrected example scene supplies zero app block features |
| Browser per-field route | FAIL | Route pending despite route features returned by API |
| Browser navigation/rendering | PASS for inspected states | Per-field, all-fields, satellite empty state and harvest empty state render; route and measurement integration gaps remain |
| Final root submission files | NOT PRESENT | Neither `route.geojson` nor `measurements.csv` existed; no final Marcaj export/import was verified |

The full review run took **622.5 seconds** on macOS arm64, Python 3.11, Torch 2.14 CPU, with four Torch threads and existing mosaic/layer caches. That timing includes prediction, constructing an example-plus-prediction scene and judging it; peak memory was not measured. MPS was unavailable to this run. Historical M4 Pro/MPS timings in older notes are not interchangeable with it.

Fresh review outputs were written under `/private/tmp/marcaj-review-*`, leaving the saved default scene, predictions and upload set intact. Browser review used a separate localhost port (8011), avoiding the user's normal port 8000. The UI was inspected at 360, 768 and 1280 px widths, including dense map and empty satellite/harvest states. All error states and every expanded control were not exercised.

## Other implementation boundaries

- Default inference has no LLM calls, paid inference APIs, SAM refinement, RT-DETR model or cadastral dependency. A U-Net does perform real filtering; whether that meets every organizer interpretation is not settled by local execution.
- Satellite processing currently selects up to three historical scenes and produces only flagged-zone features. It does not provide the planned per-lot one/two-view service or explicit unavailable/insufficient-evidence record for every field; `zones(..., chosen=[])` fails at stacking.
- Geometry-cache provenance and explicit no-data quality reports remain incomplete. Satellite bands repeated onto a finer grid do not gain spatial resolution. Source inspection does not establish a disease or water-stress diagnosis.
- Measurements union areas but sum row lengths; overlapping duplicated row fragments would overcount length. CVAT import itself does not run the packer's complete validation against externally corrected exports.
- Route optimization deliberately drops some reachable but budget-expensive targets. Saved coverage is 125/193 locally generated targets, **64.8%**, below the documented 90% coverage gate for the efficiency component. Neither coverage of hidden targets nor the efficiency score is verified.
- Gap endpoints are approached through optional out-and-back walks. There is no complete continuous-gap visibility or every-row coverage validator; “Inspect every row” and “Inspect selected rows” are plans.
- Harvest records are manual browser-local entries. Yield inference, automatic harvest completion, server storage and multi-device synchronization are absent. Labour savings are conditional user-input estimates.
- The packer performs substantial local checks, but a local passing ZIP does not prove platform import or publication. The final project export and a publicly hosted UI were not available for verification.
- The training code contains a diagnostic mode that uses hand annotations. The shipped checkpoint metadata says it was not used; keep those diagnostic weights out of submitted outputs. Exact historical training reproduction also requires the ignored teacher snapshot and frozen row geometry.
- `research/probes/` contains trial-specific scripts and visual review aids, some writing shared caches. They are not a second mandatory production pipeline. Keep their results tied to their stated source/version; do not run the entire directory indiscriminately.

## Review coverage and remaining delivery work

The runtime review covers all `backend/src/marcaj/` modules: tile/mosaic/layer ingestion; plots/rows/canopy/U-Net/waste inference; CVAT/package conversion; scene/measurement/judge/review/lab; POI/Sentinel/cadastre; route solver/validator/export; API/imagery/SAM research adapter. It also covers all current TypeScript modules, HTML/CSS layout, runtime locks/configuration, notebook flow, and the training/evaluation/research-probe relationships. Research scripts were inventoried and parsed; selected relevant probes were inspected/run. Every historical parameter experiment and external publication was not independently rerun or reverified.

Only README and this report were authored by this review. Existing dirty files and research edits were preserved. No dependencies were added and no production behavior was changed.

The required completion sequence is: resolve the acceptance/artifact/UI findings, import and review the final annotation project, regenerate all measurements/POIs/routes from its latest submitted export, verify crossing and target coverage on that exact geometry, and demonstrate the same data in the client with the final root artifacts present.
