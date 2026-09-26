# Waste in the southern inter-rows (tile row r >= 24): review checklist

26 September 2026, 10:15–11:05. The verdicts are my own, from contact sheets, native crops and 3x zooms. No labelled
Sireț3 waste exists, so nothing here is an accuracy figure.

**Checklist:** `data/generated/work/waste/checklist_south.json` and `checklist_south.jpg`. Each entry gives `x_px` and
`y_px`, the item's centre in tile pixels, plus `easting`, `northing`, `w_m`, `h_m`, `verdict`, `reason`, `interrow_m`
(distance to the nearest predicted inter-row; 0 means inside one) and `vineyard_id`.

## Scope

- The tiles with predicted `interrow_area` and r >= 24: 68 tiles, 30,711 m² of inter-row.
- `predictions.geojson` was regenerated at 10:54. After that there are 69 tiles and 31,567 m²; the new tile is
  r028_c016, with 0.4 m² of inter-row. Everything was re-scanned against the 10:54 file.
- The checklist's `vineyard_id` comes from the 10:54 blocks. Block numbering changed between the two runs (P13 became
  P12, P17 became P16), so re-read the ID in Marcaj.

## How the candidates were generated (`research/probes/waste_south.py`)

The scan runs per tile at native 0.025 m, on inter-row pixels only. It compares each pixel with the 2 m median
background and generates seven kinds of evidence:

| Kind | What it catches | Candidates (10:54 inter-rows) |
|---|---|---|
| `white` | Neutral bright pixels | 58 |
| `neutral` | Grey pixels that are bluer than the local soil | 149 |
| `blue` | Blue pixels | 4 |
| `vivid` | Vivid non-soil colour | 0 |
| `glint` | Pixels at luminance 245 or more | 128 |
| `speck` | Small pale items of 0.003–0.03 m² | 1,355 |
| `dark` | Very dark pixels, with a sun-side test for shadows | 1,777 |
| `dev` | Any non-green departure of 55 or more from the background | 18,382 |

- **Totals:** 21,853 candidates against the first predictions and 22,662 against the 10:54 ones. A full scan takes 55 s
  and peaks at about 0.6 GB.
- **Rest of the blocks.** `scan --rest` runs the colour kinds only over block pixels that are not inter-row (canopy
  strips and headlands). It found 163 candidates.
- **Positive control** (`waste_south_control.py`). All 12 likely items from the site-wide scan fire a white or blue cue:
  12/12.

## What I looked at

**Candidate crops: 1,070**, in sheets `south_*.jpg`:

- **All of these kinds:** `white`, `blue`, `neutral`, the 163 rest-of-block candidates, and the 962 new after the
  10:54 re-scan (100 of them, every colour kind plus the top `dev`).
- **Only part of these kinds:**
  - `glint`: top 96 of 128
  - `dark`: all 53 with no green on the sun side
  - `speck`: top 96 plus a random 48 of the 816 isolated ones
  - `dev`: several ranked samples (top, chromatic, compact, bright) and 144 random crops

**Whole-area sweep:**

- All 69 tiles at 0.05 m/px (`south_tile_*.jpg`, `south_strips_*.jpg`).
- All 143 quadrants of 1024 px with at least 3% inter-row, at native resolution (`south_quad_*.jpg`). They hold 99.3% of
  the inter-row area; the other 218 m² is covered by the tile views and the scan.
- 26 native zooms (`south_zoom_q1..q6.jpg`).

**Not waste.** Everything else in the scan is soil, not litter:

- sunlit soil between shadows, pale soil, clods and pebbles
- silver shrubs and white-flowering shrubs
- lying and standing white vine tubes and stakes, A-frame anchors
- shadows of posts and vines
- roofs caught inside the P11 inter-row polygons (r028_c019)
- a burnt patch of pruning residue (r025_c016), a black wire coil, orthomosaic smears

## Verdicts: 1 likely, 8 unsure

| Tile | x, y px | E, N | Box (m) | Off inter-row (m) | Verdict | What |
|---|---|---|---|---|---|---|
| r032_c022 | 303, 705 | 630125.97, 5219566.38 | 0.35 × 0.25 | 0.3 | **likely** | Flat white scrap (paper or plastic) on the NW headland at a row end |
| r028_c019 | 1638, 820 | 630005.75, 5219768.30 | 1.15 × 1.25 | 0.8 | unsure | Grey square slab or sheet on the grass headland at the fence: construction debris, or a well cover |
| r028_c019 | 1686, 872 | 630006.95, 5219767.00 | 0.90 × 0.55 | 1.0 | unsure | White C-shaped rim next to the slab: a plastic basin, or a concrete well ring |
| r028_c023 | 676, 1908 | 630186.50, 5219741.10 | 0.25 × 0.25 | 0.0 | unsure | Lavender-blue piece in a weedy inter-row: a plastic scrap, or a chicory flower |
| r030_c016 | 1900, 1383 | 629858.70, 5219651.83 | 1.00 × 0.60 | 1.7 | unsure | White crumpled object in grass at the block edge: a bag or sack, or a lying animal |
| r031_c019 | 540, 1175 | 629978.30, 5219605.83 | 0.15 × 0.15 | 0.0 | unsure | Tiny white-blue piece at a grassy inter-row edge; low priority |
| r035_c024 | 1786, 1544 | 630265.45, 5219391.80 | 0.15 × 0.10 | 1.1 | unsure | Cream oval on grass: a cup or lid, or a stone |
| r037_c024 | 1814, 1016 | 630266.15, 5219302.60 | 0.25 × 0.30 | 0.0 | unsure | White-violet piece at the foot of a standing tube: a bag scrap, or the tube's own foot |
| r037_c026 | 228, 515 | 630328.90, 5219315.12 | 0.85 × 1.33 | 0.0 | unsure | Grey granular heap in a grassy inter-row with a white bit on its edge; the imagery there is smeared |

Only r028_c023, r031_c019, r037_c024 and r037_c026 lie inside a predicted inter-row (0.0 m). The others are 0.3–1.7 m
outside one, on headlands or block edges.

**Recommendation.** The rules say to leave anything in doubt out.

1. Draw r032_c022.
2. Of the unsure items, the best candidates for a box are the r028_c019 slab and ring, and the r030_c016 sack. Check
   whether the slab and ring are a well before boxing them.
3. Box the small unsure items only if full zoom in Marcaj shows litter clearly.

## Seen outside the scope, not in the checklist

- **r029_c023**, x240 y1272 (E 630175.6, N 5219705.8): a likely flat white sheet, 0.85 × 0.8 m, on grass 21 m from any
  block. It is the site-wide v1 item that was marked "unsure" in `detector_review.json`.
- **r028_c023**, x2020 y944: a small white oval, 30 m from any block.
- **Near a block, not checked closely:**
  - a tyre-like ring by a shed (r027_c018, x1945 y420)
  - two grey heaps in a field (r024_c017)
  - two pale objects on a track (r030_c017, x650 y570)
  - yard boxes and crates (r027_c019, r034_c026)

## What may have been missed

- **Small or low-contrast items.** Items under about 0.1 m, and brown, grey-green or dark plastic, could be missed. Dark
  items in shadow can't be told apart from shadows.
- **The random samples found no waste.** 0 of 144 random `dev` crops and 0 of 48 random specks were waste. That alone
  does not bound the misses tightly; the native sweep is the main recall evidence.
- **Smeared imagery.** Small items would not show in:
  - r029_c015, r029_c016, r030_c016
  - r035_c021, r036_c026
  - r037_c025, r037_c026
  - r038_c024, r038_c025, r039_c023
- **Vine rows with no predicted inter-row.** These were outside the scan and the native sweep, and looked clean at
  0.05 m/px:
  - r033_c019 west, r033_c022 centre and east
  - r037_c025 central block
  - r036_c025 south-east, r036_c026 east
  - r031_c019 south-east
- **Misaligned inter-rows.** The P15 inter-rows in r033_c019 and r033_c020 partly sit on the vine rows.

## Files

- `research/probes/waste_south.py`: `scan [--rest]`, `sheet`, `tiles`, `quads`, `strips`, `zoom`
- `research/probes/waste_south_checklist.py`: builds the checklist from `south_verdicts.json`
- `research/probes/waste_south_control.py`: the positive control
- `data/generated/work/waste/`:
  - `checklist_south.json`, `checklist_south.jpg`
  - `south_verdicts.json`: every verdict, including the "not" and "outside" ones
  - `south_tally.json`: per-sheet counts and sweep notes
  - `south_candidates*.json`, `south_*.jpg`
