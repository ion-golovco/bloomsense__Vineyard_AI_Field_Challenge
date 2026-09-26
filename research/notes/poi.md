# Inspection points: drone gaps and Sentinel-2 zones (26 September)

Code: `backend/src/marcaj/poi.py` (drone POIs, Sentinel check, `python -m marcaj.poi`) and `backend/src/marcaj/sentinel.py` (scenes, NDVI/NDMI, low zones). Output: `data/generated/work/poi/poi.geojson`, with `sentinel_zones.geojson` and `sentinel_report.json` beside it. Probes: `research/probes/poi_examples.py` (example tiles), `poi_site.py` (site counts, caches `rows.pkl`), `poi_render.py` (sheets), `poi_sentinel_control.py` (control). Run from `backend/`: `uv run --frozen python -m marcaj.poi [--scene S] [--no-satellite]`. It takes 13 s (12 s of that is reading the tiles), peaks at 0.6 GB, and uses the cached Sentinel windows.

## POI definition

Every feature is `label: inspection` and `source: prediction`. Each carries the SPEC §12 fields: `id`, `reason`, `vineyard_id`, `row_id`, `source_ref` (tiles or scenes), `observed_at`, `confidence` and `status: new`. It also carries `origin: candidate`, `challenge`, `gap_m`, `gap_start` / `gap_end` (the canopy-free stretch on the row axis), `green_share` and `row_planted`. IDs are `GAP-<row_id>-<x mod 10⁴>-<y mod 10⁴>`, from the row ID plus a rounded anchor, as SPEC asks.

- **`gap`**: a canopy-free stretch of at least 5 m along a row, between two planted parts of the same global row. This is the organizers' `disrupted` threshold. Rows are merged across tiles by `row_id` and sampled every 5 cm. A sample counts as planted when a canopy polygon lies within ±0.3 m of the axis, the tube the reference cuts canopies at. The POI sits at the midpoint of the stretch. A stretch is dropped when more than 20% of it is hidden: off the row pieces, on no-data, or in deep shadow (mean RGB under 40). On the site this drops 9 gaps.
  - **Why canopy and not pixel green:** on the example tiles, pixel green (ExG > 0.11, runs ≥ 0.55 m) breaks true reference gaps, so recall falls from 4/8 to 3/8. Even on the reference canopies it shrinks 2 of the 8 reference gaps below 5 m. Vine green is kept as `green_share` instead.
  - **`challenge`:** true when `green_share < 0.75`. A gap that is green all along is a row the canopy model missed. In view were a 58 m gap on P11-R001 over a garden hedge, and a 21 m one on P15.
- **`planting`**: missing planting at a row end. The row's own axis runs on unplanted past its last vine, while the median of its 2 + 2 nearest planted neighbours is planted there. A staggered block end cancels out in the median.
  - **Low precision:** of 16 random ones, 3 looked plausible. Most were grassed or young rows where the canopy model misses vines, such as P01 rows of 31–44 m with vines visible along them.
  - **`challenge`:** true only with no vine green (`green_share < 0.1`) and ≤ 15 m. About half of those 16 looked plausible, and the longer ones are misplaced rows. `confidence` is halved.
- **`sentinel_low_ndvi` / `sentinel_low_ndmi`**: a Sentinel low zone confirmed by the drone (below). Always `challenge: false`.
- **`confidence`:** 1 − hidden share, halved once for each doubt: green gap (`green_share ≥ 0.375`), row under 25% planted, or a row end.
- **Marcaj export (Sunday):** `--scene` accepts a Marcaj-export scene from `marcaj-scene`. When rows have `source: reference`, only scored features are used. Checked on the example CVAT, built with `build_scene` and with the predictions mixed in: 51 rows, 8 gaps and 2 row ends. That is all 5 reference `disrupted` rows (R06, R08, R09 by interior gap; R07, R23 by edge stretch), plus V02-R15, whose gap is 5.15 m by our sampling and which the reference marks regular (the rows note measured it at 5.01 m).

## Checks on the two example tiles (a sanity check, not a holdout)

The reference targets are the reference canopy-free stretches of at least 5 m on the reference rows: 8 interior gaps and 2 tile-edge stretches, all on r006 block V02. Two tiles only, so row ends are tile edges.

| POI set | POIs | Reference gaps with a POI within 2 m | Edge stretches | POIs on a reference stretch ≥ 3 m |
|---|---|---|---|---|
| challenge `gap` | 5 | 4/8 | 0/2 | 5/5 |
| + `planting` with no green (current challenge set) | 7 | 5/8 | 1/2 | 7/7 |
| + all `planting` | 8 | 5/8 | 2/2 | 8/8 |

Why reference gaps are missed:
- **Predicted rows start too late.** Two gaps (V02-R08 7.2 m, R09 12.8 m) lie where the predicted row starts 1–5 m inside the tile, the oblique plot edge noted in `interrows.md`. There the gap becomes a row end.
- **A weed piece of predicted canopy splits a gap.** R09 7.9 m becomes 4.75 + 3.05, and R15 5.15 m becomes 3.2 + 3.25.
- **One false gap.** On r021, P02-R044 is 6.8 m against a 3.9 m reference gap: one missed plant.

Render: `gaps_examples.jpg`. White is reference gaps, red is predicted, magenta is the POI and its 2 m radius.

## Site-wide (predictions of 26 September, 593 global rows)

| | Count |
|---|---|
| `gap`, challenge | 178 (p50 6.8 m, p90 15.9 m; 120 rows) |
| `gap`, not challenge (green) | 4 |
| `planting`, challenge | 13 |
| `planting`, not challenge | 41 |
| Challenge total | **191**, 51 of them with confidence ≤ 0.5 |

Challenge POIs by plot: P09 39, P05 28, P01 19, P12 19, P02 12, P24 11, P03 10, P04 9, P06 9, P08 9, P10 6, P22 5, P34 4, P29 2, P30 2, and 1 each in P07, P11, P15, P18, P21, P32 and P35.

Renders: `gaps_random.jpg` (16 random), `gaps_p09_p12.jpg`, `gaps_lowrow.jpg`, `gaps_green.jpg`, `planting_random.jpg`, `planting_nogreen.jpg`. Most random gaps are visible missing-vine stretches.

Weak spots:
- **P09**, 39 POIs: a young block whose tiny vines the canopy model misses. Many of its "gaps" contain small plants, so they are low confidence.
- **Plots that may not be vineyards**, flagged in the plots audit: P34, and outer rows along hedges and gardens.

## Sentinel-2 scenes and fetch

- **Catalogue:** Earth Search `sentinel-2-c1-l2a`, tile 35TPN (EPSG:32635, the drone grid), baseline 05.11.
- **Reflectance:** DN × 1e-4 − 0.1, from the assets' `raster:bands`.
- **Candidates:** 12 scenes between 6 April and 21 May under 50% scene cloud. The SCL was screened over the study area:
  - 19 May was 92% cloud (class 10).
  - 4 May, 3 May, 1 May, 29 Apr, 24 Apr, 21 Apr and 19 Apr were 100% free of cloud and shadow. The pond (class 6) is 3.7% of the area.
- **Used:** the 3 latest clear scenes at least 4 days apart: **2025-05-04** (scene cloud 21.1%), **2025-04-29** (9.8%) and **2025-04-24** (1.7%). All have 100% of the study area clear.
- **Fetched:** a 1.76 × 1.82 km window. SCL was read for the 12 candidates; B04, B08, B8A and B11 for the 3 used ones. That is 0.7 MB cached in `data/raw/external/sentinel/`, plus the search JSON.
- **Computed:** NDVI (B04/B08) at 10 m and NDMI (B8A/B11) at 20 m, repeated onto 10 m. Only SCL 4/5 pixels are used. Plots are the `block` polygons shrunk by 5 m. A Marcaj export has none, so there each `vineyard_id`'s inter-rows are closed by 1.5 m.
- **Coverage:** only **14 of 36 plots have ≥ 12 interior pixels**. The rest are too narrow, and SPEC calls that "insufficient evidence". `sentinel_ndvi.jpg` shows the 4 May NDVI with the plots and zones.

**Per plot (4 May NDVI):** medians are 0.23–0.45. P02, P03, P06 and P07 sit at 0.23–0.26 against 0.35–0.45 for the rest. Their robust z against the other plots is −0.9 to −1.3, which is not flagged (threshold −1.5 on every date). P02 is the r021 example block: young vines on pale, tilled soil. A low plot here means bare inter-rows and small canopies, not a problem. Every plot's main `interrow_cover` is `bare_soil`, so the "similar plots" peer group is all plots.

**Zones:** a zone is a connected group of 10 m pixels whose robust z against its own plot is ≤ −1.5 on all 3 dates.
- NDVI: 17 pixels in 6 zones, in P03 (2), P04 (2), P05 and P10.
- NDMI: 3 pixels in 2 zones, both in P04, overlapping the NDVI ones.

**What a flag means, and what it doesn't:**
- It means that part of the plot is less green or drier than the rest of the plot on 3 dates in 10 days.
- A 10 m pixel holds about 4 rows and their inter-rows. Before 21 May the vines are short shoots, so the signal is mostly the inter-row floor: grass raises NDVI and fresh tillage lowers it. A block of missing vines, young replanting, bare or eroded soil, or a different floor management all look the same.
- A 5 m gap is about 2.5% of a pixel, so the satellite cannot find the scored targets.
- NDMI is 20 m, so a 1-pixel NDMI zone is a clipped quarter of one 20 m pixel.

## Drone check of the zones (`poi.sentinel_pois`)

Inside a zone, the plot's rows give three measures:
- canopy share along the axis;
- canopy-free stretches ≥ 2 m per 100 m of row (one or more missing plants at 1.0–1.5 m planting distance);
- the longest such stretch.

Each is compared with the same plot's rows. A zone is kept when its canopy share is under 0.75 × the plot's, when its short-gap rate is at least 2 × the plot's (with 2 or more gaps), or when it holds a 5 m gap. The point goes at the middle of the longest canopy-free stretch, on a row axis. Otherwise the zone is dropped as "drone shows nothing unusual".

| Zone | z (4 May / 29 Apr / 24 Apr) | Drone | Result |
|---|---|---|---|
| NDVI P03, 5 px | −2.0 / −2.0 / −1.9 | canopy 0.75 vs plot 0.70; 2.3 vs 1.9 gaps/100 m | dropped |
| NDVI P03, 4 px | −1.7 / −1.9 / −1.9 | canopy 0.71 vs 0.70; 3.7 vs 1.9 gaps/100 m, longest 4.4 m | dropped |
| NDVI P04, 2 px | −2.4 / −1.7 / −1.7 | 4.7 vs 1.0 gaps/100 m; 6.7 m gap | **kept** (same spot as a gap POI) |
| NDVI P04, 1 px | −3.1 / −1.8 / −1.6 | 3.6 vs 1.0 gaps/100 m, but only 1 gap in 28 m | dropped |
| NDVI P05, 4 px | −1.7 / −1.8 / −1.8 | canopy 0.67 vs 0.76; 6.05 m gap | **kept** (2.9 m from a gap POI) |
| NDVI P10, 1 px | −2.8 / −1.9 / −2.0 | canopy 0.74 vs 0.89; 6.0 vs 1.2 gaps/100 m | **kept** (new spot, 33 m from any gap POI) |
| NDMI P04, 1 px | −1.9 / −1.5 / −1.5 | 6.0 vs 1.0 gaps/100 m | **kept** (1.3 m from a gap POI) |
| NDMI P04, 2 px | −2.8 / −2.1 / −2.2 | 5.1 vs 1.0 gaps/100 m | **kept** (new spot, 20.7 m away) |

**Control** (`poi_sentinel_control.py`): the same zones were moved to random places, in whole pixels, inside the same shrunk plot. The drone rule keeps 7 of 120 moved zones (6%) against 5 of 8 real ones (62%). Low Sentinel zones do sit where the drone rows have more missing plants than the rest of the plot. The sample is small: 8 zones in 4 plots. Render: `sentinel_zones.jpg` (10 m pixels in white, canopy-free stretches ≥ 2 m in red, kept points in magenta).

## For the route agent

- **Filtering:** POIs are `source: prediction`, so `scene.is_scored` and the default `routing.route_targets` / `route.py` targets skip them. Pass them explicitly, filtered on `challenge == true`.
- **Covering a gap:** a gap is covered for any organizer point inside it when the route walks an adjacent inter-row along `gap_start` → `gap_end`. The axis is about 1.25 m from the inter-row centre, inside the 2 m radius. The midpoint alone guarantees only the middle 4 m.

## Decisions for the user

1. **How many targets go into the scored route.** Coverage (15%) counts first, and efficiency (10%) only from 90% coverage. So the default is recall-first: all 191 challenge POIs. A tighter set is `confidence > 0.5`, which is 140 POIs. It drops all 13 row ends, 17 of P09's 39, 8 each in P05 and P08, and all 4 in P34. The organizers' list is hidden, and the example tiles cannot settle this.
2. **Row-end `planting` in the scored route:** 13 now. Dropping them loses 1 of 2 reference edge stretches on the example tiles.
3. **Sentinel POIs** are `challenge: false` by default (5 points, 3 of them near existing gap POIs). Choose between showing them only as a product layer, or also running a separate "scout" route.
4. **Sunday:** re-run `python -m marcaj.poi --scene <Marcaj-export scene>`, so the gaps come from corrected canopies. On the reference rows it reproduced all 5 disrupted rows.
