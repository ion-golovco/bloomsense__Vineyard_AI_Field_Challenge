# Canopy rules (`marcaj.canopy`): error analysis and what changed

26 September 2026. All scores are the judge's canopy formula (0.6 × union IoU + 0.4 × one-to-one F1 at IoU 0.5) on the
two organizer tiles, r021_c012 (399 canopies, young vines on pale soil) and r006_c004 (251, dark soil plus a grassed
block). These two tiles are a sanity check, not a holdout. The rows come from one frozen `detect_plots()` output
(`data/generated/work/canopy_rules/plots.json`), so the numbers don't move with the plots agent's edits. The harness
scorer reproduces `judge()` to 4 decimals: 0.6682, 0.8318, 0.8552.

**Result: canopy 0.668 → 0.855.** Union IoU is 0.664 → 0.822 and instance F1 0.674 → 0.905. Counts are 402/399 and
259/251 (they were 374/399 and 260/251). Canopy area is 535.3 m² against 536.2 m², so the judge's area count stays at
0.99. With the reference rows the score is 0.897 (diagnostic; it was 0.727). That is about +4.7 of the 25 canopy points.

## Baseline error analysis (ExG > 0.11, contrast 1.3, neck 0.3)

`research/probes/canopy_rules_analysis.py` [predictions.json]

| | r021_c012 | r006_c004 |
|---|---|---|
| matched / predicted / reference | 258 / 374 / 399 | 175 / 260 / 251 |
| unmatched predicted | 39 merged over 2+ plants (55 m²), 32 fragments of an over-split plant, 16 on-row clumps with no reference, 14 too big, 13 too small | **64 fragments (87 m²) of the grassed block's whole-row reference strips**, 13 too small, 3 merged, 3 no reference |
| unmatched reference | **97 merged into a neighbour**, 15 split, 14 prediction bigger, 8 missed on a row, 3 no row | 23 missed on a row (**4 grassed rows dropped by the contrast test**), 21 prediction smaller, 13 split, 9 no row, 9 merged |
| matched IoU median; area ratio pred/ref | 0.685; 1.13 | 0.730; 0.94 |
| false-positive pixels | 73 m², brightness 48 against 79 on true pixels: **shadow** | 34 m², brightness 59 against 117 |
| false-negative pixels | 35 m², ExG 0.095: sunlit leaves just under the threshold | 73 m², 31% more than 25 cm from any prediction: whole rows missed |

Oracles on the baseline:
- Our mask with a perfect instance split: 0.739.
- The reference mask cut by our split: 0.964. So the split rule is fine and the mask was the problem.
- Dropping predictions that touch no reference: 0.676.
- Adding the missed references: 0.702.

Reference facts that shaped the rules:
- **The reference canopies are cut at exactly 0.30 m from their row line.** In 89.5% and 76.5% of canopies the
  farthest vertex lies within 1 cm of 0.30 m, and 15–19% of all vertices sit on that line. So the organizers' canopy
  is "leaves ∩ ±0.3 m of the drawn row". This is why `tube_m` = 0.3 is optimal (0.25 gives 0.827, 0.35 gives 0.839),
  and why row placement bounds the score.
- Plants seldom touch: only 38/374 and 24/225 neighbouring pairs touch. The gap between neighbours has a median of
  0.35 and 0.47 m, and 17% are under 15 cm. Centroid spacing has a median of 2.06 and 2.88 m.
- **Annotators don't split continuous canopy at planting distance.** Reference along-row length has a p90 of 3.0 and
  5.3 m. 16 reference canopies on r021 are longer than 4 m. On r006 the grassed block is drawn as whole-row strips up
  to 55.8 m long and 31 m². Our length distribution now matches theirs quantile for quantile.
- Style: the references have 14–15 vertices against our 38–49, with the same convexity (0.80) and compactness.
  Changing the vertex count (simplify 0.01–0.06) changes the score by less than 0.001.
- The reference floor is 0.19 m², which matches the rules' "about 0.2 m²". Plants cut by the tile edge are drawn up to
  the edge: 30 per tile.

## Every method tried

Scores are canopy score with (IoU, F1). n is predicted/reference on r021 and r006. s/tile is on the reference tiles
while four other agents were loading the Mac, so it is noisy (0.5–1.6 s).

### Colour index

Pixel IoU is measured inside the ±0.3 m tube of the reference rows, at one threshold shared by both tiles
(`canopy_rules_colour.py`).

| Index | AUC r021 / r006 | best threshold r021 / r006 | shared-threshold IoU r021 / r006 |
|---|---|---|---|
| ExG (2g−r−b)/(r+g+b) (old) | 0.914 / 0.946 | 0.112 / 0.076 | 0.683 / 0.791 |
| **2g−r−b in DN (kept)** | **0.971 / 0.975** | **26.9 / 27.7** | **0.836 / 0.871** |
| −CIVE; −a*+b*/2 (Lab) | 0.971 / 0.975 | coincide too | 0.833–0.836 / 0.869–0.872 |
| −a*; b*; g−b | 0.960 / 0.973; 0.935 / 0.967; 0.945 / 0.969 | | 0.793 / 0.855; 0.724 / 0.837; 0.747 / 0.844 |
| ExG−ExR, GLI, hue, VARI, NGRDI, saturation, g−r | 0.86–0.92 / 0.92–0.95 | | 0.60–0.69 / 0.72–0.80 |
| ExG with a brightness floor (best of a 4 × 6 grid) | | | 0.727 / 0.765 |

Why normalised ExG fails: dividing by brightness lifts dark shadow and noise, which bridges neighbouring plants on
r021, and damps sunlit leaves. The rules say "trace the leaves, not their shadow". DN, −CIVE and Lab are nearly
identical; DN is the simplest.

### Pipeline trials, in the order run

| Trial | Canopy (IoU, F1) | n r021 / r006 | Why |
|---|---|---|---|
| Baseline | 0.668 (0.664, 0.674) | 374 / 260 | |
| DN > 22 / **25** / 27 / 30 / 33 | 0.768 / **0.787** (0.733, 0.868) / 0.773 / 0.717 / 0.696 | 420 / 257 at 25 | Shadow falls out of the mask, so merged plants separate. Above 27 plants fragment (F1 drops) |
| DN 25, smooth 0.025 / 0.075 / 0.1 | 0.773 / 0.791 / 0.773 | | Flat around 0.05, which stays |
| Hysteresis 20/30 … 25/40 | 0.736–0.786 | | High seeds remove nothing new |
| DN 27: no split / neck 0.2 / 0.4 / 0.5 | 0.768 / 0.770 / 0.765 / 0.737 | | Neck 0.3 stays |
| min area 0.15 / 0.25 | 0.770 / 0.780 | | 0.2 is the rule and the best |
| tube 0.25 / 0.35 / 0.4 | 0.752 / 0.768 / 0.749 | | The reference is cut at 0.30 m |
| No contrast test / no refit | 0.789 / 0.731 | | The refit is essential; the contrast test is neutral here |
| Oracle row test (only axes within 0.4 m of a reference row) | 0.832 (r006 0.741 → 0.825) | | Row selection was worth +0.045 |
| **Inter-row drop** (`row_gap` 0.7, weaker axis = less green share) with contrast off | **0.832** | 420 / 267 | Equals the oracle. In the grassed block the detector put axes halfway between rows. The contrast test scored true rows 1.09–1.16 there and the inter-row axes 0.97–1.28 |
| Same, weaker axis by mean DN / by contrast / with contrast 1.3 kept | 0.832 / 0.813 / 0.788 | | Contrast picks the wrong axis of the pair |
| **Closing 0.025 / 0.05 / 0.075 / 0.1 m** (neck 0.3) | 0.840 / **0.845** / 0.840 / 0.823 | 404 / 256 at 0.05 | 23 of 29 over-split plants were separate pieces with a median gap of 7 cm. The rules trace leaves "within about 10 cm" |
| Closing with neck 0.4 | 0.830 / 0.841 / 0.843 / 0.833 | | |
| Neck cut also at depth < 0.5–0.7 when the neck's DN < 30–45 | 0.765–0.840 | | Over-splits |
| Neck 0.45 / 0.6 on pieces longer than 2 / 2.5 / 3 m | 0.794–0.838 | | Annotators keep long canopies whole |
| **Second refit within 0.3 m** / 0.2 m | **0.849** / 0.848 | 400 / 258 | Centres on the canopy away from inter-row weeds; the axes land 1.4–1.9 cm from the reference rows |
| Core tube 0.2–0.3, grown into 0.4–0.5 | 0.767–0.804 | | Grows into weeds and shadow |
| DN 23 / 27 / 29 with closing | 0.827 / 0.839 / 0.819 | | 25 stays |
| **Inset −0.005 / −0.01 / −0.015 / −0.02 m** | 0.852 / **0.854** / 0.854 / 0.850 | | Matched polygons were 10–12% too big (+1.2–1.5 cm of outline). After the inset: 1.02–1.04, and total area −0.2% |
| Simplify 0.01 / 0.04 / 0.06 | 0.848 / 0.848 / 0.847 | | Style doesn't matter |
| **Closing kept at the tile edge** (erosion `border_value=1`) | **0.855** (0.822, 0.905) | 402 / 259 | The closing had eroded 2 px at the tile border, so 0 predictions touched the edge against 30 references per tile. Now 26 / 24 do |
| **Value contrast `row_value` 1.2** (mean clipped DN in the tube over 0.6–1.0 m beside) / 1.3 / 1.5 | **0.855** / 0.855 / 0.807 | | No effect on these tiles up to 1.3. It removes axes over grass verges and weed-covered blocks (see Site) |
| Green-share contrast 1.0 / 1.1 / 1.2 on top | 0.855 / 0.855 / 0.807 | | The margin is thinner than `row_value`: true grassed rows sit at 1.13–1.16 |

Oracles on the final version:
- Reference rows with no refit: 0.897.
- Our axes stretched to the reference row ends: 0.866; also trimmed to them: 0.867.
- Perfect split of our mask: 0.873.
- Dropping predictions that touch no reference: 0.866.
- Adding the missed references: 0.869.

### Final sensitivity, one value at a time around the defaults (`canopy_probe.py`)

| Change | Canopy |
|---|---|
| green_dn 22 / 27 / 30 | 0.829 / 0.838 / 0.788 |
| smooth 0.025 / 0.075 | 0.848 / 0.849 |
| close 0.025 / 0.075 | 0.847 / 0.853 |
| row_gap 0.5 / 0.85 | 0.854 / 0.855 |
| inset 0.005 / 0.02 | 0.853 / 0.850 |
| tube 0.25 / 0.35 | 0.827 / 0.839 |
| min area 0.15 / 0.25 | 0.840 / 0.850 |
| neck 0.2 / 0.4 | 0.850 / 0.851 |
| refine2 0.2 | 0.855 |
| row_value 1.3 / 1.5 | 0.855 / 0.807 |

Each rule off, one at a time:

| Rule off | Canopy |
|---|---|
| ExG instead of DN | 0.669 |
| No refit | 0.772 |
| No closing | 0.839 |
| No neck split | 0.846 |
| No inset | 0.848 |
| No second refit | 0.851 |
| No inter-row drop | 0.854 (`row_value` alone also removes the inter-row axes) |
| Contrast 1.3 back on | 0.807 |
| Otsu | 0.633 |

## What was kept (all in `canopy.py`)

| Rule | Parameter | Tuned constant | Physical reason |
|---|---|---|---|
| 2g−r−b in DN above 25 | `green_dn` 25 | Chosen from 22–33 | Shadow is dark, not green; the best threshold is the same on both soils |
| Two-pass refit | `refine_m` 0.5, `refine2_m` 0.3 | | |
| Inter-row axis drop | `row_gap` 0.7 | | Rows of a plot are evenly spaced |
| Value-contrast row test | `row_value` 1.2 | Picked between the true grassed rows (≥ 1.34) and the verge and weed axes (≤ 1.04) | A vine row is greener than the grass beside it |
| Closing | `close_m` 0.05 | | "Within about 10 cm" |
| Edge-preserving closing | | | Plants are traced up to the tile edge |
| Inset | `inset_m` 0.01 | Corrects a measured +1.2–1.5 cm outline bias | |
| Green-share contrast off | `row_contrast` 0 | | |

The tuned constants are `green_dn`, `close_m`, `inset_m`, `row_gap` and `row_value`, each tried at 2–4 values
against the judge on these tiles. Kept unchanged: tube 0.3 (it matches how the reference is cut), neck 0.3, min area
0.2 (the rule), smoothing 0.05. The old method stays reachable:
`replace(CanopyParams(), green_dn=0, refine2_m=0, row_contrast=1.3, row_gap=0, row_value=0, close_m=0, inset_m=0)`
gives 0.668.

New public helpers: `excess_green`, `green_mask`, `kept_axes` (it takes an optional `spacing_m` and `excess`),
`row_spacing`, `value_contrast` and `close`. `canopy_mask` gained an optional `spacing_m`; `tile_canopies` passes it
the plot's median row spacing. Signatures that were there before are unchanged. `green=` still replaces the colour
threshold.

## Site check (`canopy_rules_site.py`, `canopy_rules_verge.py`)

The run covers 131 of 311 tiles that cross predicted rows, on one core with peak RSS 0.67 GB.

| | New | Old |
|---|---|---|
| Runtime, without the tile read (loaded machine) | 73 s (0.56 s per tile) | 52 s (0.40 s per tile) |
| Canopies | 12,709 | 12,271 |
| Canopy area | 12,751 m² | 12,282 m² |
| On orchard outlines | 180 m² | 186 m² |
| On overgrown outlines | 0 | 0 |
| Outside every vineyard outline | 694 m² | 631 m² |

The review holds no `no_vineyard` tile verdicts, so the judge's `false_canopy_penalty` is empty and these outlines are
the only proxy. "Outside every outline" also counts real vineyards nobody outlined: r029_c016 (P12) is a mature
vineyard with no outline.

Without `row_value`, the outside area was 1,056 m². r031_c018 showed why: the defaults then painted six 0.6 m strips
across a weed-covered block, where the axis value contrast is 0.79–0.92. r035_c021 had a strip along a grass verge,
at 0.99–1.04.

`row_value` against `row_contrast` on the site, as total / outside m²:

| Test | Total | Outside |
|---|---|---|
| Neither | 13,648 | 1,056 |
| `row_contrast` 1.0 / 1.1 / 1.2 | 13,288 / 12,810 / 12,498 | 954 / 703 / 632 |
| `row_value` 1.2 / 1.3 / 1.5 | 12,751 / 12,438 / 11,919 | 694 / 604 / 526 |

## Residual error budget (final: 0.855)

- **Rows, about 0.04** (reference rows give 0.897):
  - About 0.012 are 7 references on r006 drawn as 0.6 m rectangles over the grassy headland, beyond our row ends. No
    plants are visible there, so extending rows into green verges would add false canopy elsewhere. Skipped.
  - About 0.005 is the missed 6 m corner row on r021 (3 plants; plots' domain).
  - About 0.02 is annotator placement noise. A line fitted to the reference canopies themselves lies 0.9–1.2 cm from
    the drawn rows, and the canopies are cut at the drawn line. Irreducible.
- **Split, about 0.018** (perfect-split oracle 0.873):
  - 22 + 7 reference plants merged into a touching neighbour, where the two reference polygons are 1–2 cm apart and
    the neck is too shallow.
  - 16 + 11 fragments.
  - A colour-valley cut and a length-driven cut were both worse.
- **False positives, about 0.011**: 11 + 11 canopies of 0.2–0.3 m² on the rows with no reference. On inspection they
  are mostly small leaf clumps or young plants the annotators left out. Small predictions (0–1 m long) are the least
  precise: 93/110 and 32/47 of them match.
- **Misses, about 0.014**: mostly the headland rectangles above.

## What would help next

1. **plots.py (plots agent).** In grassed plots the row lattice puts axes halfway between rows: in P05 on r006, 2 of
   6 grass axes are inter-rows. Canopy now drops them, but the `row` and `interrow_area` outputs still carry them, and
   so do axes over verges and weed blocks. `canopy.kept_axes` and `canopy.value_contrast` could run in row detection.
   The r021 corner row is also missing.
2. **canopy_net.** Pseudo-labels should be regenerated from the new rule canopy.
   - `canopy_net_labels.py` still builds its own `green` from normalised ExG for `fit_axis`. `canopy.green_mask` and
     `canopy.kept_axes` would give the same axes that `canopy_mask` uses.
   - `canopy_net_train.py` calls `kept_axes` without `excess`, so the `row_value` test is skipped there.
3. **Split.** The only sizeable in-module gain left (≤ 0.018). A 2-D watershed on the distance transform could separate
   touching plants whose neck is shallower than 0.3.
4. **Refresh the docs.** RESEARCH.md says "colour tuning is exhausted" and "canopy is limited by rows, not colour".
   Both are superseded: normalised ExG was the limit.
