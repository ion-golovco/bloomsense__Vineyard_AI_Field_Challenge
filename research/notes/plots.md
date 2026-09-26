# Plot detector: errors, annotation audit, changes (26 September)

Scored by `judge.plot_scores` against the 35 `vineyard` outlines in `data/review/verdicts.json`. The north (northing ≥ 5,219,600, 19 outlines) is for tuning and the south (16) for validation. "Clipped" means the outlines were cut to the tile footprint first, because 7 of them run past the imagery. The 2 reference tiles are a sanity check, not a holdout.

| | North F1@0.5 / F1@0.75 / median best IoU / area IoU / false m² | South, same |
|---|---|---|
| Frozen detector (before) | 0.585 / 0.439 / 0.646 / 0.788 / 6,835 | 0.471 / 0.118 / 0.523 / 0.555 / 1,516 |
| **Kept (now the default)** | **0.811 / 0.541 / 0.755 / 0.811 / 5,380** | **0.647 / 0.353 / 0.703 / 0.660 / 2,391** |
| Before, clipped | 0.585 / 0.439 / 0.641 / 0.816 | 0.529 / 0.118 / 0.543 / 0.562 |
| Kept, clipped | 0.811 / 0.486 / 0.739 / 0.839 | 0.706 / 0.353 / 0.703 / 0.669 |

`detect_plots` takes 23.6 s (19 s before) and peaks at 3.4 GB, with the same signature and feature properties. It finds 36 plots and 593 rows.

## 1. Per-outline errors

Probe: `research/probes/plot_errors.py`. IoU is per outline, clipped (`plot_compare.py`). The class is for the kept detector. For edges, "side" means the outermost rows and "end" means the row ends; "road" means a passage lies within 6 m outside the edge; a negative offset means the prediction stops short of the drawn edge.

| # | half | m² | width m | IoU before | IoU now | class now |
|---|---|---|---|---|---|---|
| 8 | N | 585 | 13 | 0.00 | 0.00 | missed: seed too small |
| 9 | N | 550 | 10 | 0.00 | 0.00 | missed: no seed |
| 26 | N | 783 | 10 | 0.00 | 0.00 | missed: seed too small |
| 18 | S | 2296 | 32 | 0.09 | 0.15 | missed: fit on 2 fragments of a grassy plot |
| 34 | S | 738 | 14 | 0.11 | 0.21 | wrong row direction |
| 13 | S | 674 | 10 | 0.21 | 0.22 | wrong row direction |
| 24 | S | 1017 | 19 | 0.25 | 0.61 | edge off: end (road) −11 m |
| 16 | S | 1041 | 13 | 0.33 | 0.76 | ok (was split) |
| 27 | N | 943 | 10 | 0.33 | 0.62 | edge off: end +3 m (was split at a tree) |
| 19 | S | 1601 | 26 | 0.40 | 0.40 | edge off: side (open) −15 m |
| 23 | S | 2692 | 56 | 0.44 | 0.55 | edge off: side (open) −22 m / +9 m |
| 25 | N | 2290 | 36 | 0.48 | 0.56 | edge off: side (open) −10 m, end (road) −10 m |
| 5 | N | 791 | 10 | 0.49 | 0.76 | ok |
| 6 | N | 2475 | 48 | 0.49 | 0.47 | edge off: side (open) −25 m, end (road) −6 m |
| 14 | S | 734 | 11 | 0.52 | 0.67 | edge off: end (road) −23 m |
| 10 | S | 3169 | 53 | 0.54 | 0.55 | edge off: side (open) −24 m, end (open) −7 m |
| 22 | S | 1390 | 28 | 0.56 | 0.85 | ok |
| 20 | S | 5926 | 75 | 0.60 | 0.75 | ok |
| 30 | N | 7484 | 96 | 0.62 | 0.62 | split: an L-shape across a passage (see A18) |
| 4 | N | 3098 | 42 | 0.64 | 0.74 | ok |
| 28 | N | 4506 | 25 | 0.64 | 0.70 | several small edges; the outline takes in a track (A16) |
| 11 | S | 2494 | 33 | 0.65 | 0.72 | edge off: side (open) −7 m |
| 12 | S | 969 | 13 | 0.70 | 0.70 | edge off: side (open) +4 m |
| 15 | S | 4759 | 58 | 0.72 | 0.83 | ok |
| 7, 17, 21, 3, 29 | | | | 0.75–0.85 | 0.75–0.87 | ok |
| 33 | N | 2004 | 19 | 0.85 | 0.72 | edge off: side −4 m (the stricter peak rule dropped the outer row) |
| 2, 31, 32, 1, 0 | N | 11.5k–18.1k | | 0.89–0.94 | 0.87–0.94 | ok |

**Classes before and now.** Counts; the area at stake is outline m² × (1 − IoU).

| Class | Before: count, m² | Now: count, m² | Why it happens |
|---|---|---|---|
| Edge short | 15, 16,528 | 12, 10,247 | The fit covered only its seed. Seeds need `vine_over_orchard > 2`, and grassy inter-rows drop the ratio to 1–2 (outlines #18, #24: median 1.35–1.51; #20, #25: q25 about 0.9–1.1). The prediction was about 95% inside its outline and too small. Before: 21 open sides and 20 road ends were more than 3 m short, with the open-side median −2.1 m (IQR −8.0 to +0.4). Now: 18 and 10, median −0.9 m (IQR −5.8 to +1.0). The rest are grassy rows whose tube-minus-flank ExG is under half the plot's median |
| Missed | 5, 4,667 | 4, 3,877 | Narrow 10–13 m strips of young vines (#8, #9, #26). The bare strip against grass puts energy into the 3.8–7 m orchard band, so the ratio sits at 0.7–1.1 (median 0.70 for #9, q90 1.06), and no seed survives the opening and the 150 m² floor. #18 is grassy, with profile vine/orchard power 0.5 |
| Split | 3, 4,169 | 1, 2,825 | A tree or a weak stretch cut the seed in two (#27, #16). #30 is an L-shape whose two parts a passage separates |
| Wrong direction | 1, 531 | 2, 1,106 | Strips of 4 rows (#13, #34) beside plots at a different angle. The direction split works on 15° bins of a 2 m-smoothed angle, so the strip takes its neighbour's angle |
| Orchard taken as vineyard | #38, 1,119 m² | #38, 1,563 m² | #38's rows are 3.26 m apart, with profile vine/orchard power 13.8 and a layer ratio median of 3.5; every other orchard is at 0.9 or below. See A26 |
| False plots | 4 (P15 1,940 m², P29 450, P32 376, P40 172) | 1 (P34 319 m²) | P29 and P32 were tractor lines on a tilled field, P15 dark mulch stripes (A01), P40 a road-edge sliver |

## 2. What the annotation may have missed or drawn inconsistently

Probe: `research/probes/plot_audit.py`. The contact sheet is **`data/generated/work/plots/audit_sheet.jpg`**, with crops A01–A27 in order. Outlines are drawn green (vineyard), violet (orchard) and cyan (overgrown), predictions magenta, passages brown. Every vineyard outline, worst first, is in `outlines_final.jpg` (the same sheet for the old detector is `outlines_baseline.jpg`). Numbers `#n` are indices into the plot outlines in `verdicts.json` order.

**Possibly not outlined**
- **A01** (630645, 5219790), r027_c032: 1,972 m² of vine-band seed. Dark stripes 3.55 m apart on a pale field: black mulch film, a young planting, or a vegetable field? The old detector put P15 here. The new one drops it: the ExG wave is 0.0005, which is not green rows. Decide whether it is a vineyard.
- **A02** (630310, 5219471), r034_c026: predicted P34, 319 m², angle 60°, spacing 2.24 m, beside the overgrown #44/#45. Check it.

**Inconsistent outlines**
- **Run past the imagery**, which caps IoU: #1 (10%, A04), #2 (15%, A06), #4 (5%), #6 (14%), #8 (13%), #10 (17%), #17 (5%). Trim them to the tiles, or rely on the clipped score.
- **Overlap a passage.** #28 by 902 m² (A16): the outline takes in the track on its west side, and "the road is not part of the block". #31 by 781 m² (A20) and #32 by 350 m² (A21) along the track between them. #30 by 328 m² (A18): a passage runs between the two legs of its L, so by the rules it is 2 blocks. Smaller overlaps (#0 79 m², #1 166, #2 217, #20 60, orchards #36 39 and #38 28) look like the OSM buffer spilling over the outer row.
- **Under 5 m apart with no passage on the connecting line**, one block by the 5 m rule unless a track separates them: #21/#22, 4.2 m (A14); #23/#34, 0.3 m (A15), where #34 is a strip in #23's notch; #28/#29, 3.9 m (A17), where a track is visible, but it is not in the passages.
- **Overlapping outlines:** #30/#31 by 60 m² (A19), overgrown #45/#46 by 27 m² (A24).

**Class questions**
- **A26, #38 orchard** (629812, 5219710), r029_c016. It has the vine-like numbers given in the class table, and the detector calls it a vineyard. The plants look like young trees, so please confirm.
- **A27, #47 overgrown:** rows 2.03 m apart, power 3.8. Weak; probably right as drawn.
- **A25, #18 vineyard:** power 0.5; the rows are barely visible. Also #6 (lower half), #10 (upper-left), #23 (south-west), #25 (east) and #19 (south-west) are drawn over rows that are much weaker than the rest of their plot. Worth a second look, since those parts decide most of the remaining edge error.

## 3. Changes tried

All scores are as drawn, in the form N F1@0.5 / F1@0.75 / median / area IoU / false m², then the same for S. Tuning decisions used the north only.

| Change | North | South | Verdict |
|---|---|---|---|
| Baseline | 0.585 / 0.439 / 0.646 / 0.788 / 6,835 | 0.471 / 0.118 / 0.523 / 0.555 / 1,516 | |
| Road reach 8 / 12 / 20 m (was 5) | 0.571–0.605 / 0.37–0.38 / 0.64–0.66 / 0.77–0.79 / 7.6k–10.6k | 0.43–0.47 / 0.16–0.23 / 0.50 / 0.54–0.57 | dropped |
| Pixel growth by complex demodulation, in phase with the seed, k 0.3 / 0.5 | 0.619 / 0.143 / 0.595 / 0.610 / 13.6k; 0.600 / 0.150 / 0.607 / 0.604 / 10.5k | 0.667 / 0.278 / 0.588 / 0.580; 0.500 / 0.278 / 0.581 / 0.533 | dropped: #32 has a half-spacing phase jump inside one block, so the phase test cut real plots |
| Same, amplitude only (4 m along), k 0.4 / 0.5 | 0.585 / 0.341 / 0.621 / 0.667 / 28.6k; 0.615 / 0.462 / 0.633 / 0.695 / 23.9k | 0.647 / 0.235 / 0.560 / 0.609; 0.667 / 0.303 / 0.578 / 0.649 | dropped: leaked into tilled fields, parallel orchards (#39) and across tracks (#28/#29) |
| **Wave floor**: drop seeds whose across-row ExG wave is under 0.005 (tilled fields 0.0005–0.0015, vineyards 0.011–0.10) | 0.632 / 0.474 / 0.646 / 0.808 / 4,068 | unchanged | **kept** |
| Wave floor + amplitude growth gated by layer ratio > 1 (orchards < 0.5), k 0.5 | 0.757 / 0.378 / 0.662 / 0.799 / 7,078 | 0.581 / 0.194 / 0.592 / 0.634 | dropped: merged #28 into its neighbour and #16 with the next strip |
| **Row walk** (whole rows outward, then each row's ends), walk 0.3 / 0.4 / 0.5 / 0.7 | 0.737 / 0.526–0.579 / 0.755–0.760 / 0.809–0.815 / 4.9k–5.8k | 0.44–0.61 / 0.22–0.28 / 0.64–0.70 / 0.63–0.69 | **kept at 0.5**; the north is flat over 0.3–0.7 |
| Walk share 0.4 / 0.8, gap 2 / 5 m, smoothing 2 m | north 0.737 / 0.526–0.579 / 0.755–0.757 / 0.812–0.817 | south 0.46–0.63 / 0.22–0.34 | defaults kept (share 0.6, gap 3 m, smoothing 1 m); the north does not choose between them |
| **Merge** fits of one lattice under 5 m apart with no road between, filling ≥ 80% of the joint quad | 0.811 / 0.595 / 0.757 / 0.814 / 5,571 | unchanged | **kept**: #27 0.36 → 0.62 |
| **Peak reach 0.6 spacings** (was 0.35), for the stray rows the inter-rows agent reported | 0.811 / 0.541 / 0.755 / 0.811 / 5,380 | 0.647 / 0.353 / 0.703 / 0.660 / 2,391 | **kept**: rows closer than 0.6 spacing fall from 22 pairs to 0 site-wide. North costs 1 outline at IoU 0.75 (#33); south gains |
| Top-hat layer (`tophat_m=1.6`) as the only seed source, ratio 4 / 5 / 6 / 8 | 0.56–0.63 / 0.24–0.33 / 0.63–0.66 / 0.77–0.80 | 0.59–0.63 / 0.29–0.41 / 0.60–0.69 | dropped: plots fragment (24–31 predicted) |
| Top-hat as *extra* seeds, ratio 6 / 8 / 12 (peak 0.35) | 0.800 / 0.550; **0.842 / 0.579**; 0.757 / 0.486 | 0.611 / 0.278; 0.629 / 0.343; 0.556 / 0.222 | not kept: it finds #8 (0 → 0.59) but is non-monotonic in the threshold (at 12, #28/#29 break) |
| Same at ratio 8 with the kept defaults | 0.789 / 0.526 / 0.758 / 0.813 / 5,518 | 0.647 / 0.412 / 0.723 / 0.664 | not kept: the north is slightly worse |

**Why the top-hat is promising.** On 3 crops of 380 m, with thresholds matched to the background's 98th percentile, a 1.6 m white top-hat instead of the 4 m Gaussian high-pass changes the covered share as follows:

| | Gaussian high-pass | 1.6 m top-hat |
|---|---|---|
| Narrow strips #8 / #9 / #26 | 0.14 / 0.00 / 0.03 | 0.80 / 0.36 / 0.39 |
| Mean vineyard | 0.57 | 0.62 |
| Orchards | 0.16 | 0.05 |
| Pixel AUC, vineyard against background | 0.905 | 0.917 |

## 4. Downstream check on the 2 reference tiles

`research/probes/plot_pipeline_check.py` runs plots → rows → inter-rows → canopy → attributes. I ran the old and new plots side by side, with the current `rows.py` and `canopy.py` from the other agents; those have moved since the brief's 37.7 points.

| Plots | Canopy | Axes | Attributes | Grouping | Counts | Points |
|---|---|---|---|---|---|---|
| Frozen detector | 0.855 | 0.970 | 0.974 | 1.000 | 0.896 | 44.98 / 50 |
| **Kept** | 0.855 | 0.970 | 0.974 | 1.000 | 0.898 | **44.99 / 50** |

The same holds with peak reach 0.35 and 0.6. Both runs give rows 50 of 51 and 1,902 of 1,942 m, axes F1 0.962 / 0.980 and inter-row F1 0.941 / 0.979. There is no regression: both reference plots were already well fitted, so the gains are elsewhere on the site.

On the inter-rows agent's notes:
- **Stray rows:** fixed at the source (peak reach 0.6).
- **Road setback:** kept at 1 m. The reference row ends near passages sit a median 1.73 m *outside* the passage polygon (n = 17 within 5 m, P10 −0.77 m, P90 +4.32 m). Only 15 of 1,942 m of reference row lies inside passages.
- **Missing r021 corner row and short r006 rows:** unchanged by the walk.

## 5. What would help next

1. **Narrow strips** (#8, #9, #26). Add top-hat extra seeds with a strip-specific acceptance test: profile vine/orchard power is 60 / 7.3 / 21.6 on these, and a comb test ("every one of 4–5 rows present"). The threshold must stop being fragile first.
2. **Grassy outer rows** (18 open sides still > 3 m short). Test each new row against its inner neighbour rather than the plot median, or allow a 1-row look-ahead.
3. **Strip direction** (#13, #34). Refit the angle after splitting off a narrow strip; drop the 15° bin vote for strips under 15 m wide.
4. **Blocks for `vineyard_id`.** The rules make touching pieces one block (#30's two legs, #32's two phases). This needs a group id applied at the end of `predict.py`, because `canopy.plot_rows` and `rows.interrow_areas` key rows by `vineyard_id` and would break if two plots shared one now.
5. **User decisions:** A01 (mulch field), #38 (orchard or vineyard), and the passage-crossing #28 and #30.

## Files

- `backend/src/marcaj/plots.py`: `PlotParams` gains `peak_reach` 0.6, `min_wave_exg` 0.005, `walk` 0.5, `walk_reach_m` 40, `gap_m` 3, `new_row_share` 0.6 and `merge_m` 5. New `_wave`, `_walk` and `_merge`. Docstring updated.
- `backend/src/marcaj/layers.py`: optional `tophat_m`, off by default; the cache and default output are unchanged.
- Probes in `research/probes/`: `plot_errors.py` (per-outline table), `plot_audit.py` (audit and sheet), `plot_sheet.py` (contact sheets), `plot_compare.py` (per-outline IoU of two runs), `plot_experiment.py` (growth and extra-seed variants), `plot_score.py` (`detect_plots` with overrides), `plot_pipeline_check.py` (downstream judge), `plot_rows_check.py` (stray rows), `plot_layers_variant.py` (layer variants under `work/plots`).
- Outputs in `data/generated/work/plots/`: `final.geojson`, `baseline.geojson`, `audit_sheet.jpg`, `audit.json`, `outlines_final.jpg`, `outlines_baseline.jpg`, `overview_baseline.jpg`, `errors.json`, `layers_tophat_m1.6.npz`.
