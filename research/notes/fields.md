# Fields: cadastral parcels for the plot detector (26 September)

Scored by `judge.plot_scores` against the 35 `vineyard` outlines in `data/review/verdicts.json` (north, northing ≥ 5,219,600, tunes; south validates). Scores read N F1@0.5 / F1@0.75 / median best IoU / area IoU / false m², then the same for S, as drawn. The kept detector before this work was 0.811 / 0.541 / 0.755 / 0.811 / 5,380 and 0.647 / 0.353 / 0.703 / 0.660 / 2,391.

**Outcome.** Parcels measurably help one way: filling the rest of a parcel a plot already covers ≥ 70%, when that rest shows the plot's own rows. S F1@0.5 goes from 0.647 to 0.765, F1@0.75 from 0.353 to 0.412, area IoU from 0.660 to 0.701. N area IoU goes from 0.811 to 0.816. Ten outlines improve and none gets worse. The fill is **off by default** (`parcel_share = 0.0`) because the cadastre's reuse licence is unclear (§1). §7 adds a second change, now on by default: new outer rows are also taken on vine green on the row lattice.

## 1. Data sources

| Source | What | Terms found | Fetched | Size |
|---|---|---|---|---|
| Cadastre WMS of I.P. Cadastrul Bunurilor Imobile, `https://map.cadastru.md/geoserver/ows` (GeoServer), layer `w_cbi:cad_terenuri` "2.2. Terenuri cadastrale" | One `GetMap` as `image/svg+xml`, EPSG:4026 (MOLDREF99 / Moldova TM), bbox 222760,219530,224600,221410 (the study area plus about 30 m), 7360 × 7520 px = 0.25 m/px. The render gives each parcel as a closed vector path, so `marcaj.cadastre` converts the paths to polygons with no tracing | Capabilities say `Fees NONE` and `AccessConstraints NONE`. **No licence or reuse terms found.** geoportal.md, the old public viewer, is now a parked domain. ipcbi.gov.md lists no data terms. The INSPIRE record for the parcel view service (`c64ef8f4-9cd6-49cb-a500-0bd12c9ce16c`) redirects to data.europa.eu. A search-engine summary of that record quotes restrictive use conditions: materials used only for the work requested, handled by a limited number of people, no unjustified copying. I could not verify that this text belongs to the Moldovan record | 26 Sep 2026, 09:35 EEST | 2.5 MB SVG → 2,121 polygons, 318 ha, `parcels_32635.geojson` |
| Same service, WFS | — | `GetCapabilities` returns 403 "Request forbidden by administrative rules": an access control, so it was not used and not worked around | — | — |
| OpenStreetMap via Overpass (ODbL) | landuse and highway ways in lat 47.110–47.132, lon 28.697–28.727 | ODbL, attribution required | 26 Sep 2026. overpass-api.de returned 504, so overpass.kumi.systems was used (OSM base 2026-07-28) | 484 KB, 298 elements |

Besides the full request, there were 3 small test requests: WMS capabilities 265 KB, and one 200 m test tile as SVG (122 KB) and PNG (138 KB). Raw files are in `data/raw/external/cadastre/` and `data/raw/external/osm/`, both gitignored. My first full-area request sent the user's email in its User-Agent. `marcaj.cadastre.fetch` sends none.

OSM has **no** vineyard, orchard or farmland polygons here. It has 112 tracks, 90 residential roads, 53 service roads, 4 forest and 3 grass polygons. It was not used.

**Registration.** Shifting the parcels over ±6 m in 0.5 m steps, the best shift (+0.5, +0.5) m raises the outline boundary within 1 m of a parcel edge only from 0.383 to 0.410. There is no systematic offset beyond about 0.5 m.

## 2. How parcels relate to the outlines

Probe: `research/probes/cadastre_outlines.py`, which writes `data/generated/work/fields/cadastre_outlines.json` and `cadastre_sheet.jpg`.

| Outlines | Best single parcel IoU, median | Union of parcels ≥ 50% inside: median IoU; count ≥ 0.75 | Boundary within 1 m / 2 m of a parcel edge (median per outline) |
|---|---|---|---|
| vineyard (35) | 0.44 | 0.67; 16 of 35 | 0.35 / 0.63 |
| orchard (7) | 0.53 | 0.55; 2 of 7 | 0.19 / 0.33 |
| overgrown (7) | 0.38 | 0.68; 3 of 7 | 0.35 / 0.60 |

- Pooled over all vineyard boundary, 38% lies within 1 m of a parcel edge and 67% within 2 m. For outlines shifted at random by up to ±30 m, the figures are 17–22% and 34–38%. So parcel edges carry real information, about twice chance. Still, a third of the outline boundary lies on no parcel edge, and the strips are 10–20 m wide, so any line is within 2 m of a parcel edge a third of the time.
- One vineyard often spans many parcels: #1 covers 20, #2 14 (median 2). Only #13 (0.92) and #34 (0.79) are close to a single parcel.
- The kept detector is more than 3 m off on 39% of the outline boundary. On those stretches the outline lies on a parcel edge only 36% of the time. The share is high for #6 (0.69), #9 (0.84), #13 (1.0), #23 (0.63), #24 (0.68), #25 (0.78) and #34 (0.64), and low (≤ 0.16) for #0, #2, #3, #4, #12, #15, #28 and #32.
- The kept plots' own boundary lies within 1 m of a parcel edge for 31% of its length, and within 2 m for 51%.

## 3. Changes tried

| Change | North | South | Verdict |
|---|---|---|---|
| Baseline (kept detector) | 0.811 / 0.541 / 0.755 / 0.811 / 5,380 | 0.647 / 0.353 / 0.703 / 0.660 / 2,391 | |
| **Snap** each plot edge ≥ 8 m onto the parallel parcel edge with the best coverage, outward up to 3 m (`cadastre_snap.py`) | 0.811 / 0.541 / 0.758 / 0.813 / 5,456 | 0.647 / 0.353 / 0.684 / 0.662 / 2,654 | dropped |
| Same, outward up to 6 m; coverage 0.6 / 0.8 | 0.811 / 0.541 / 0.755 / 0.808 / 6,114–6,149 | 0.647 / 0.353 / 0.638 / 0.676 / 3,646–3,676 | dropped |
| Same, outward up to 6 m, strip wave ≥ 0.5 × plot | 0.811 / 0.541 / 0.755 / 0.809 / 5,687 | 0.647 / 0.353 / 0.703 / 0.673 / 2,904 | dropped |
| Same, outward up to 10 m, wave 0.5, coverage 0.7 | 0.865 / 0.541 / 0.755 / 0.811 / 5,701 | 0.647 / 0.353 / 0.670 / 0.665 / 3,429 | dropped: south median down, false area up |
| Snap inward up to 3 m; up to 6 m at coverage 0.8 | 0.811 / 0.486–0.541 / 0.721–0.752 / 0.800–0.806 / 4,974–5,000 | 0.647 / 0.294 / 0.696 / 0.653–0.657 / 2,114–2,218 | dropped: it cuts plots at owner lines |
| Snap both ways up to 3 m, coverage 0.8 | 0.811 / 0.541 / 0.752 / 0.811 / 5,290 | 0.647 / 0.294 / 0.677 / 0.659 / 2,464 | dropped |
| **Clip** plots to the parcels they mostly cover (`cadastre_fill.py clip=1`) | 0.649 / 0.162–0.270 / 0.513–0.646 / 0.640–0.662 / 11.9k–17.4k | 0.588 / 0.294 / 0.559 / 0.681–0.687 | dropped: plantings span several parcels |
| **Fill** parcels ≥ 50% covered, no row test (post-process) | 0.811 / 0.378 / 0.691 / 0.764 / 13,280 | 0.765 / 0.412 / 0.684 / 0.714 / 3,597 | dropped |
| Fill parcels ≥ 70% covered, rest wave ≥ 0.4 × plot (post-process) | 0.811 / 0.541 / 0.755 / 0.816 / 5,470 | 0.765 / 0.412 / 0.703 / 0.706 / 2,685 | taken into `plots.py` |
| **`_fill_parcels` in `plots.py`**: share 0.7, gate 0.4, phase within 0.35 spacing, rows continued onto ExG peaks | **0.811 / 0.541 / 0.755 / 0.816 / 5,433** | **0.765 / 0.412 / 0.703 / 0.701 / 2,685** | **kept, off by default** |
| gate 0.3 / 0.5 / 0.6 | 0.816 / 5,433; 0.814 / 5,417; 0.813 / 5,401 | 0.765 / 0.412 / 0.701; 0.647 / 0.412 / 0.693; 0.647 / 0.412 / 0.684 | the north picks 0.3–0.4 |
| share 0.5 / 0.6 / 0.8 / 0.9 | 0.811 / 0.486 / 0.740 / 0.805 / 7,181 (0.5 and 0.6); 0.814 / 5,417; 0.813 / 5,396 | 0.765 / 0.412 / 0.670 / 0.702 / 3,596; 0.647 / 0.353 / 0.664; 0.647 / 0.353 / 0.664 | the north picks 0.7. **Cliff at 0.6**, see below |
| phase 0.25 / 0.5 (0.5 = no phase test) | 0.816 / 5,433; 0.816 / 5,435 | 0.765 / 0.353 / 0.680; 0.765 / 0.412 / 0.703 | the north cannot choose; see below |
| Walk look-ahead: take 1 / 2 weak outer rows when the row beyond passes (item 2 of plots.md §5) | 0.811 / 0.486 / 0.718 / 0.809 / 5,876; 0.833 / 0.500 / 0.718 / 0.792 / 5,876 | 0.667 / 0.364 / 0.670 / 0.682 / 2,803; 0.500 / 0.375 / 0.670 / 0.685 / 4,030 | dropped, code removed |

**Per outline**, fill on against off:
- north: #6 0.40 → 0.42, #25 0.56 → 0.69, #29 0.85 → 0.86, #30 0.62 → 0.64
- south: #10 0.47 → 0.51, #11 0.72 → 0.88, #15 0.83 → 0.92, #19 0.40 → 0.50, #20 0.75 → 0.79, #23 0.55 → 0.61

No outline gets worse. The run never creates a plot and still finds 36. The contact sheet is `data/generated/work/fields/parcel_fill.jpg`.

**Rows.** Most of the growth runs along the rows, where the existing axes lengthen. Across the rows, the fill adds 6 rows (358 m). Continuing the lattice at the plot's spacing first put them on inter-rows (median 1.14 m from the ExG peak). Each new row is now the ExG peak within 0.3 spacings of its predicted position: median 0.17 m from the peak, 4 of 6 within 0.35 m (the base rows: 0.06 m, 91%). Probe: `research/probes/plot_rows_on_peaks.py`.

**Caveats.**
- **Share cliff.** At share 0.6, outlines #0, #12, #21, #22 and #29 get worse; for example, #29 0.86 → 0.74 takes in a tilled strip whose tractor lines pass the wave test. North false area jumps to 7,181 m². The wave gate is weak there, and the share threshold does most of the work.
- **Phase tolerance.** The north cannot choose between 0.25 and 0.5. I set 0.35 after seeing that 0.25 drops #11 on the south, so that choice is informed by the south.
- **South false area** rises 2,391 → 2,685 m².

## 4. Downstream check

`plot_pipeline_check.py` runs the rule-based canopy, not the U-Net, so it gives 44.99 where predict gives 44.88. It is identical with the fill off and on:

| | Canopy | Axes | Attributes | Grouping | Counts | Points |
|---|---|---|---|---|---|---|
| Totals | 0.855 | 0.970 | 0.974 | 1.000 | 0.898 | **44.99 / 50** |
| r006_c004 | 0.811 | 0.962 | | | | inter-row 0.941 |
| r021_c012 | 0.834 | 0.980 | | | | inter-row 0.979 |

Rows 50 of 51 and 1,902 of 1,942 m. Neither reference plot takes in a parcel. `detect_plots` runs in 21–22 s either way.

## 5. What is in the code

- **`backend/src/marcaj/cadastre.py` (new).** `fetch` makes the one WMS request, `svg_parcels` converts SVG to EPSG:32635, `load_parcels` raises `FileNotFoundError` with the fetch command when the file is missing, and `edge_distance` samples the distance to the nearest parcel edge. Rebuild with `uv run --frozen python -m marcaj.cadastre [--fetch]`.
- **`backend/src/marcaj/plots.py`.** New `PlotParams` fields `parcel_share` (0.0 = off; 0.7 measured), `parcel_gate` 0.4 and `parcel_phase` 0.35. New `_phasor` (`_wave` now calls it, with the same numbers) and `_fill_parcels`, which runs after `_merge` and before overlaps are resolved. The docstring records the source and the scores. With `parcel_share` 0 (and `side_green` 0, §7), all 629 features equal `data/generated/work/plots/final.geojson` in geometry and properties; only `params` lists the new fields. The current defaults include the §7 change, so they differ.
- **Probes.** `research/probes/cadastre_outlines.py`, `cadastre_snap.py`, `cadastre_fill.py` and `plot_rows_on_peaks.py`.
- **Outputs.** In `data/generated/work/fields/`: `final_off.geojson`, `final_parcels.geojson`, `cadastre_sheet.jpg`, `parcel_fill.jpg` and `plots_before.py` (a backup).

## 6. For the user to decide

1. **The cadastre licence.** Can plots derived from IPCBI parcels go into the Marcaj pre-annotations and the public repo? The service declares no fees and no access constraints but gives no reuse licence, and an unverified summary of its INSPIRE record reads as restrictive. If yes, set `parcel_share: float = 0.7` in `PlotParams`, or pass `PlotParams(parcel_share=0.7)` to `predict`, then re-run predict. The parcels file must exist on the machine that runs predict, at `data/raw/external/cadastre/parcels_32635.geojson`; it is gitignored, so another machine needs `python -m marcaj.cadastre --fetch`. If no, delete `data/raw/external/cadastre/` and nothing else changes.
2. **If on, the credit line.** Say in the README and the pitch where the parcels come from: I.P. Cadastrul Bunurilor Imobile, map.cadastru.md.
3. **Weird shapes.** Parcels do not fix them. #28 and #30 are passage and track cases (best parcel 0.35 and 0.63), and the narrow strips #8, #9 and #26 have no seed. A parcel must never create a plot.
4. **Vine rows inside organizer passages.** On r027_c018, r021_c011 and r020_c010, thin passage polygons cover 1–4 real vine rows. The detector never annotates on passages, and letting it cross them measured worse (§7). If the user wants those rows, they have to be added by hand in Marcaj, or the passage rule needs a narrower exception.

## 7. Blocks that stop short of their outer rows (10:30–10:55, no cadastre)

The canopy agent found 26,301 m² of vineyard outline with no block (`work/canopy_rules/missing.json`). Here is where that area lies:
- **Inside organizer passages or forbidden zones: 6,717 m², or 9,186 m² with a 1 m margin.** On r027_c018 that is 512 of 576 m² missing, on r021_c011 175 of 337 m² (334 within 2 m) and on r020_c010 98 of 275 m² (204 within 2 m). There, thin passage polygons run over 1–4 real vine rows, and the walk stops at them by design. Not fixed; see decision 4 in §6.
- **Outside the imagery:** 4,622 m².
- **Within 6 m of a block, not in a passage:** 9,505 m², and 13,972 m² within 15 m. This is outer rows the walk rejected, headlands, and the half spacing between the outermost row axis and the drawn edge. Blocks are not a Marcaj label, so only the rows count for the import.

Probe: `research/probes/plot_coverage.py` reports plot scores, covered outline area, and row length inside vineyard outlines, on orchards and outside every outline.

| Change | North | South | Outline covered m² | Rows in vineyard / outside outlines, m |
|---|---|---|---|---|
| Current defaults | 0.811 / 0.541 / 0.755 / 0.811 / 5,380 | 0.647 / 0.353 / 0.703 / 0.660 / 2,391 | 115,687 | 42,767 / 3,298 |
| Lower bar for new outer rows only (`walk_side` 0.35 / 0.25 / 0.15 of the plot median) | 0.811 / 0.541 / 0.755–0.758 / 0.811–0.816 / 5,369–5,559 | 0.485–0.647 / 0.229–0.242 / 0.670 / 0.671–0.677 / 2,622–3,581 | 116,445–117,880 | +278–702 / +99–451 |
| Let plots cross passages (usable = everywhere except forbidden zones) | 0.706 / 0.471 / 0.701 / 0.795 / 6,166 | 0.647 / 0.294 / 0.722 / 0.641 / 2,476 | 113,951 | −340 / +863 |
| **Vine green on the lattice.** A new outer row that fails the contrast test is still taken when its ±0.3 m tube is ≥ 30% green (mosaic ExG > 0.08) and its flanks half a spacing to each side are at most 0.7 × as green. The row is placed at the greenest candidate | **0.811 / 0.595 / 0.758 / 0.811 / 5,664** | **0.647 / 0.353 / 0.703 / 0.677 / 2,302** | **116,534** | **43,079 / 3,314** |
| same, green 0.2 / 0.4 (flank 0.7) | 0.811 / 0.595 / 0.758 / 0.811–0.812 / 5,657–5,736 | 0.647 / 0.353 / 0.703 / 0.665–0.678 / 2,304–2,353 | 116,224–116,595 | 42,943–43,107 / 3,333–3,350 |
| same, flank 0.5 / 0.6 / 0.8 / 1.0 (green 0.2–0.3) | 1.0: 0.684 / 0.474 / 0.714 / 0.769 / 13,573; 0.8: 0.814 area, 5,666 | 1.0: 0.438 / 0.250 / 0.470; 0.8: 0.680 area, false 2,701 | 1.0: 119,685 | 1.0: +1,329 / +4,469; 0.8: +550 / +194 |
| Row placed at the best *contrast* candidate instead of the greenest (green 0.2, flank 0.7) | 0.811 / 0.595 / 0.758 / 0.815 / 5,562 | 0.629 / 0.343 / 0.703 / 0.676 / 2,462 | 116,924 | +420 / +117; added rows sit a median 0.24 m off the ExG peak |

**Kept: green 0.3, flank 0.7.**
- The north picks flank 0.7–0.8; 0.8 adds 194 m of rows outside the outlines. It picks green 0.3–0.4 on false area.
- Seven vineyard outlines improve: #33 0.72 → 0.92, #13 0.22 → 0.40, #23 0.55 → 0.67, #6 0.40 → 0.44, #29 0.85 → 0.86, #18 0.15 → 0.17 and #34 0.21 → 0.24. Two get slightly worse: #0 0.944 → 0.932 (166 m² less covered) and #3 0.837 → 0.830.
- **Orchard #38 grows 1,423 → 1,774 m².** That is the open orchard-or-vineyard decision from plots.md, and it accounts for the north's false area rising 5,380 → 5,664 m². South false area falls 2,391 → 2,302 m².
- Added rows: 12 rows, 566 m. Their median offset to the ExG peak is 0.11 m, and 67% are within 0.35 m (base rows 0.06 m, 91%).
- Rows outside every outline: +16 m. The contact sheet shows the new rows on real outer vine rows of #33, #23 and #13.
- Coverage gain is modest: +847 m² of the 26,301 m² missing.

**Downstream.** `plot_pipeline_check.py` gives 44.98 / 50: canopy 0.855, axes 0.970, attributes 0.974, grouping 1.000, counts 0.898, r021 canopy 0.835, r006 0.811. Before, it gave 44.99 with r021 canopy 0.834. Reference-tile row length is identical (902.5 m and 999.6 m), and rows are 50 of 51 and 1,902 of 1,942 m. The 0.01-point difference is noise. `detect_plots` takes 20.5 s and peaks at 2.9 GB RSS; it now finds 36 plots and 603 rows (593 before).

**Not done.**
- Narrow strips #8, #9 and #26 get no seed. The plots agent's top-hat extra seeds were fragile, non-monotonic in the threshold and slightly worse on the north (plots.md §3). There was no time to make them safe.
- Walk look-ahead: dropped (§3).

Files: `backend/src/marcaj/plots.py` gains `PlotParams.side_green` 0.3 and `side_flank` 0.7, and the fallback test in `_walk`; `side_green=0` restores the previous output. New probe: `research/probes/plot_coverage.py`. Outputs: `data/generated/work/fields/green_final.geojson` (new default) and `sg_off.geojson` (previous default). The backup of plots.py before this change is `data/generated/work/fields/plots_current.py`.
