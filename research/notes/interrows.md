# Inter-rows and row / inter-row attributes (26 September)

`marcaj.rows` against the two organizer reference tiles, plus a read-only sweep of the whole site. The reference tiles are a sanity check, not a holdout. Probes: `research/probes/interrow_probe.py` (per-object breakdown), `interrow_ablate.py` (one change at a time, same plots and canopies), `interrow_sweep.py` (site sweep and renders). The input is fixed to the cached `detect_plots()` output at `data/generated/work/interrows/plots.json`, which matches the plots in `predictions.geojson`: 40 plots and 648 rows.

## Result

Judge on the two reference tiles, run in one process with the same canopies for every variant. The canopy score is 0.855 because canopy.py changed under other work while this ran.

| Variant | inter-row F1 r021 / r006 | axes r021 / r006 | attributes | counts | points |
|---|---|---|---|---|---|
| A: committed `rows.py` (inset 0.35) | 0.958 / 0.800 | 0.980 / 0.909 | 0.850 | 0.723 | 42.40 |
| B: A + inset 0.30 | 0.958 / 0.800 | 0.980 / 0.909 | 0.850 | 0.790 | 43.07 |
| C: B + new `row_structure`, square ends, visible area only, 0.1 m² minimum piece | 0.979 / 0.778 | 0.980 / 0.909 | 0.916 | 0.776 | 43.26 |
| D: C + stray rows dropped | 0.979 / 0.941 | 0.980 / 0.962 | 0.974 | 0.894 | 44.95 |
| **E: D + ends carried onto passages (current)** | **0.979 / 0.941** | **0.980 / 0.962** | **0.974** | **0.896** | **44.98** |
| F: E with trapezoid ends (not kept) | 0.979 / 0.962 | 0.980 / 0.962 | 0.981 | 0.911 | 45.15 |

In an earlier run, with the canopy score still at 0.668, C split into two parts: `row_structure` alone gave +0.36 (attributes 0.850 → 0.923), and square ends gave −0.17. The total gain is **+2.58 points**, all of it in components that do not depend on canopy. The measures in E:

- rows 50 / 51 → 0.869
- inter-row area 3,959 / 4,064 m² → 0.827
- row length 1,902 / 1,942 m → 0.797

The two remaining ones were 0.739, 0.408 and 0.478 before.

## Reference-tile analysis (committed code)

**How the reference is built.** All 49 reference inter-rows have every vertex at 0.298–0.302 m from its row axis. The ones with 5 vertices take in a tile corner. The organizers therefore generated them as axis ± 0.300 m. The earlier note of "a median 0.30–0.38 m" does not hold against the reference axes. Both tiles are interior, so every row and inter-row ends on the tile edge; they show no real row ends. Each tile has rows − 1 inter-rows: 24 on r021 and 25 on r006. None lies on the outer side of the outermost in-tile row, at all 4 corners. The corner triangle that our clip creates there (0.0, 0.9 and 2.9 m²) is absent from the reference.

**r006 inter-row F1 0.800** (30 predicted, 25 reference, 22 matched):

- **3 stray predicted rows** (P05-R020, R022, R028), 1.14–1.35 m from the nearest reference row, run along grass strips. They split reference inter-rows 15, 16 and 21 into 6 slivers 0.5–0.9 m wide, with best IoU 0.27–0.35. The same 3 rows are false axes (axes 0.909), +3 on the row count and +166 m of row length.
- **2 corner triangles** beyond the outermost in-tile rows: 0.9 and 2.9 m².
- **Rows stop short:** on reference inter-rows 0–10 the predicted start is 1.1–5.2 m inside the tile edge, because the plot edge crosses the tile obliquely. IoU there is 0.66–0.94.

**r021 inter-row F1 0.958** (24 / 24, 23 matched): the corner row, reference V01-R25 at 6.1 m, is missing, so its 16.7 m² inter-row is missing too. There is also one 0.0 m² sliver.

**Matched IoU**, before → after:

| Tile | Before (min / median / max) | After (min / median / max) |
|---|---|---|
| r021 | 0.865 / 0.934 / 0.977 | 0.869 / 0.936 / 0.973 |
| r006 | 0.658 / 0.870 / 0.959 | 0.642 / 0.913 / 0.957 |

The sides were 0.05 m narrower per side (the inset) plus the axis error (median 0.04–0.06 m, up to 0.2 m). The ends on r021 match within ±0.4 m, which is the tile-edge cut.

**Counts 0.720:**

| Measure | Predicted / reference | Score | Cause |
|---|---|---|---|
| blocks | | 1.000 | |
| rows | 53 / 51 | 0.739 | +3 stray, −1 corner row |
| canopy area | | 0.978 | |
| **inter-row area** | 3,703 / 4,064 m² (−8.9%) | **0.408** | the 0.35 m inset alone cost 204 m²; the stray and short rows the rest |
| **row length** | 2,043 / 1,942 m (+5.2%) | **0.478** | the stray rows |

**Attributes 0.850:**

- `row_structure`: 2 of 5 disrupted rows found, 3 predicted regular, and 2 reference rows missing.
- `interrow_cover`: all 45 matched pieces correct. The 4 errors are missing objects: 3 stray-row splits and 1 missing corner row.

**Why `row_structure` missed.** The reference counts a gap between two canopies *and* the stretch from a tile edge to the first canopy. V02-R07 has an edge gap of 11.3 m and R23 one of 6.8 m, both disrupted. Applying "longest canopy-free stretch ≥ 5 m, including tile-edge stretches" to the reference canopies reproduces 50 of 51 reference labels; the exception, R15, has a 5.01 m gap and is marked regular.

The old pixel test counted interior gaps only, and weed green bridged them. Gaps between our *predicted* canopies caught all 5 disrupted rows but called 4 regular rows disrupted, because missed plants lengthen the gaps (R16: 7.49 m predicted against 3.83 m in the reference). So I did not use canopy gaps. Pixel green with runs under 0.55 m removed, plus the tile-edge stretches, gives 5/5 disrupted and 0/44 false. That holds on a plateau of 0.45–0.65 m; at 0.15 m, or without the edge stretches, it finds only 2–4 of 5.

## Changes in `backend/src/marcaj/rows.py`

1. `INTERROW_INSET_M` 0.35 → **0.30**, the measured reference rule: +0.67 points (inter-row area 0.408 → 0.742).
2. **`row_structure`**: the longest stretch without vine green (ExG > 0.11 in ±0.3 m, runs ≥ 0.55 m), counting stretches that run into a tile edge or the no-data edge. `disrupted` at ≥ 5 m. A piece ≥ 5 m with no vine green at all is `unassessable` ("the row cannot be made out"). There are 73 such pieces, mostly the plastic-mulch plot P15 and the bare field P32 (`rows_without_vine_green.jpg`). The old code called them regular. +0.36 points (attributes 0.850 → 0.923).
3. **Square ends at the shorter row**, as the rules say ("If one row is shorter, end at the shorter one"; the checklist says inter-rows never extend past the row ends). This costs −0.17 on these tiles, only because r006's predicted plot edge cuts the rows short obliquely. The trapezoid (variant F) is +0.17 and +1.5% of site inter-row area. I kept the rule.
4. **Stray rows dropped** (`rows.kept_rows`): a row closer than 0.7 spacing to both neighbours, where those neighbours are 0.7–1.4 spacings apart. `interrow_areas` skips them and `per_tile` drops them from the output. 14 of 648 rows on the site are affected (`stray_rows.jpg`, stray rows in magenta, mostly grass strips); on r006 they are exactly the 3 found above. **+1.69 points**: axes r006 0.909 → 0.962, inter-row F1 r006 → 0.941, attributes → 0.974, rows 0.869, length 0.797.
5. **Ends carried onto passages**: when a passage or forbidden zone lies within 1.5 m of an inter-row end, the end is extended and then clipped at it. `plots.py`'s 1.0 m road setback stops 547 of the 640 row ends near a passage at exactly 0.95 m. On P02 and elsewhere the vines and end posts visibly run into the passage polygon (`row_ends_at_passages.jpg`). Inter-rows touching a passage go from 0% to 85% of those within 4 m of one (0% to 52% of all merged strips). +0.03 points.
6. **Visible area only**: rows and inter-rows are clipped to the tile minus the orthomosaic's no-data margin (hole-filled valid mask, opened at 0.2 m, so shadows don't count). Before, 656 m² of inter-row and 586 m of row lay on black. Inter-row pieces under 0.1 m² are dropped; the smallest reference inter-row is 7.5 m².

`interrow_areas(features, exclusions)` and `per_tile(features, tiles)` keep their signatures. `interrow_areas` + `per_tile` over all tiles takes **19–25 s**; `per_tile` alone was about 16 s before. `predict.predict(tiles=[3 tiles])` runs end to end with a 3.45 GB peak, from `detect_plots`.

Site packing: the rebuilt predictions (`data/generated/work/interrows/predictions_rows.geojson`) give `check_cvat` 0 problems on 311 tiles and 0 holes filled.

- **Rows**: 168 disrupted, 1,519 regular, 73 unassessable. Before: 64 disrupted and 1,729 regular.
- **Inter-rows**: 1,446 bare_soil, 228 mixed, 47 vegetation.

## Whole-site sweep (`predictions.geojson` → after the changes)

| Anomaly | Before | After | Example tiles |
|---|---|---|---|
| inter-row overlaps a canopy (> 0.01 m²) | 1,062 pieces, 595 m² total (median 0.26 m²) | 1,300 pieces, 1,257 m² (median 0.56, max 9.2) | r019_c013, r020_c013, r021_c012 |
| narrow (< half the expected width): stray-row slivers | 86 pieces, 1,105 m² | 11, 141 m² | r006_c004, r007_c004 → r026_c032, r037_c024 |
| corner piece (only one of its two rows enters the tile) | 148, 446 m² | 146, 332 m² | r010_c001, r019_c010, r020_c012 |
| sliver < 1 m² | 67, 26 m² | 58, 24 m² | r006_c002, r007_c003 |
| outside its block > 0.5 m² | 19 pieces, 23 m² outside | 520 pieces, 1,035 m² outside (median 2.0 m²): the passage extension, by design | r009_c001, r008_c005 |
| giant (> 1.5 × (spacing − 2 × inset)) | 6, 491 m² | 6, 482 m²: genuinely wide lanes (the reference r021 has 3.6–3.7 m spacings too) | r035_c025, r013_c003 |
| on no-data | 656 m² / 586 m of row | 254 m² / 322 m (residual at the ragged edge; no piece mostly on no-data) | r011_c001, r038_c022 |
| holes (CVAT fills them) | 0 | 0 | |
| invalid, overlapping another inter-row, row axis inside, on a passage or forbidden zone | 0 | 0 | |
| plots with < 2 rows or no inter-rows | 0 | 0 | |
| inter-rows over a building not in forbidden.geojson | not counted (seen in a render) | not fixed | r020_c013 (shed roof inside P02) |

Attribute mixes per plot look plausible. Tilled plots are all bare_soil, for example P02 197/5/0 and P03 188/2/0. The grassed plots carry mixed and vegetation: P17 7/15/9, P19 6/7/7, P28 2/6/3.

Renders are in `data/generated/work/interrows/`: `before/*.jpg` and `after/*.jpg` for the anomaly examples, `stray_rows.jpg`, `row_ends_at_passages.jpg`, `rows_without_vine_green.jpg` and `bridge_P17_P20.jpg`.

## Route implications (report only)

- **Before**: no predicted inter-row touched a passage (median gap 0.94 m), and the passable space was 613 parts; the largest was the passage itself. Every entry into an inter-row would have been about 1 m outside.
- **After**: 279 parts. The largest is 96,988 m², the big passage plus the inter-rows now attached to it.
- **The two passage components** (2,137 and 37,863 m², 81 m apart) are **not bridged by any inter-row**. P17's inter-rows touch the small one. Their far ends are 3.0–11 m from the big one, across a bare headland with a tree (`bridge_P17_P20.jpg`). Going there takes about 3 m outside passable space each way, which fits easily within 2%, or the targets there count as unreachable.
- Neighbouring inter-rows never touch: a 0.6 m canopy strip lies between them. Moving between inter-rows goes through a passage at the row ends or crosses a row, which counts as outside. 48% of strips have no passage at either end: dead ends against scrub or other plots.
- Corner pieces are kept for continuity along a strip that crosses a tile corner. The reference omits them, so if the organizers check the route against *their* inter-rows, walking through a tile corner counts as outside. Each such crossing is a few metres.

## Not done, and why

- **Canopies not subtracted from inter-rows.** It removes all overlap and lifts the r021 median IoU from 0.936 to 0.942. But it costs 45 m² (1.1%) of inter-row area on the reference tiles, −0.15 points, because the reference bands are plain axis ± 0.3 m. The cause is canopy.py re-fitting each axis per tile by up to 0.5 m, while the inter-rows use the plot axes.
- **Corner pieces kept.** They score nothing in the judge (only 0.4% of area), and dropping them breaks strips at tile corners.
- **Trapezoid ends not used** (see change 3).
- **Buildings and trees inside inter-rows are not cut out.** CVAT fills holes anyway, so a cut-out would mean splitting the polygon.

## For the other owners (evidence, not edited)

- **plots.py** (plots owner):
  1. The peak NMS in `_quadrilateral` uses a reach of 0.35 × spacing (0.8 m), so grass-strip peaks 1.0–1.4 m from a vine row survive. These are the 14 stray rows listed by `rows.kept_rows`. A separation of about 0.6 × spacing at the source would make the filter in `rows.kept_rows` a no-op.
  2. `road_setback_m = 1.0` stops rows 0.95 m short of passages where the vines continue.
  3. The r021 corner row V01-R25 is missing, and r006's plot edge cuts rows 1–5 m short inside the tile.
- **canopy.py** (canopy owners): `canopy.plot_rows` still includes the stray rows, and 58 predicted canopies (44 m²) lie within 0.3 m of them. `rows.kept_rows(features)` gives the cleaned set.
- **Route owner**: see route implications above. The passage extension relies on `plots.exclusions()`, which is passages plus forbidden zones.

## Files

- `backend/src/marcaj/rows.py` (changed)
- `research/probes/interrow_probe.py`, `interrow_ablate.py`, `interrow_sweep.py` (new)
- `data/generated/work/interrows/` (cache, rebuilt predictions, renders)

`predictions.geojson` and `scene.json` were not touched. To use the changes, re-run `python -m marcaj.predict`.
