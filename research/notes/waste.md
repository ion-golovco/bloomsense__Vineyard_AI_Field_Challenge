# Waste (10 points): scan, detector, recommendation

26 September 2026. Everything below was measured on the 311 tiles. The verdicts ("likely", "possible", "unsure", "not")
are my own, from contact sheets and 5x zooms. No labelled Sireț3 waste exists, so no number here is an accuracy.

## Verifier retuned on the user's labels (19:55)

**Labels.** 136 user waste answers (50 with rule flags), 563 not waste, plus the eye verdicts. The 4 waste answers on
r006_c004 now count as not waste: the organizers' reference for that tile has no waste, and all 4 are dim inter-row
scraps at luminance 212-215. So the organizers do not box that range, and the user's labels are looser than theirs.
`research/review/eval_waste.py` now also prints:
- F1, with each unlabelled box counted as half a false box;
- one-to-one matches at IoU >= 0.3;
- a north/south split at northing 5220200, the median of the labelled waste (5219600 would leave the south 8 items);
- the example-tile control.

**Changes, outside the inter-rows only.** The inter-row tests are unchanged.
- Row distance: one threshold, `row_m` = 0.55 m, the small tier's. It was 1.2 m near blocks and 1.5 m elsewhere.
- Chroma <= 25 (was 20).
- Clipped share >= 0.05 (was 0.10).

On the 90 waste answers without rule flags:

| Verifier | Boxes | TP / FP / unlab | Prec | Recall | F1 | Interrow | Block | Headland | Outside | North F1 | South F1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 18:50 | 60 | 38 / 13 / 9 | 0.75 | 37/90 | 0.512 | 7/14 | 1/12 | 13/27 | 16/37 | 0.571 | 0.456 |
| now | 74 | 45 / 14 / 15 | 0.76 | 43/90 | 0.557 | 7/14 | 4/12 | 16/27 | 16/37 | 0.564 | 0.550 |

**Honest split.** A coordinate descent over 12 thresholds, fitted on one half:
- Fitted on the north, it picks only clipped >= 0, and the south stays at 0.456.
- Fitted on the south, it picks chroma 25, row 0.5 m, near density 0.20 and outside density 0.05. The north then drops
  to 0.506, from 14 new unlabelled boxes.

The thresholds sit on a plateau and the gains do not transfer. The defaults take both halves' picks except the
density changes, which failed on the other half. The gain is in the south; the north is flat.

**Rejected** (crops checked):
- near density <= 0.20: +3 recalled, +2 false boxes (one a 2.2 × 1.6 m pale soil patch) and 2 unlabelled, one of them a flowering shrub;
- outside density <= 0.05: +16 unlabelled boxes;
- small-tier luminance >= 212: +3 inter-row recalled, +12 false boxes (pale clods).

**Box size: no systematic error, no change.**
- A whitish region (distance >= 90, >= 30 over the background) was grown around each of the 37 confirmed items our
  boxes hit. The median IoU with our box is 0.79, and 1 of 37 is below 0.3.
- The looser grow (any distance >= 60) doubles the box, because it takes in grass and shadow.
- 7 recalled items miss IoU 0.3 against their card. In every one the card is a review cluster 2-8× the object, and our
  box sits on the object (crop sheet checked).

**Remaining misses:**
- dim inter-row scraps (luminance 210-215, the pale-clod range);
- block items clustered on 4 tiles around P02 (r018-r021, c013-c015);
- large rubbish heaps outside, which touch pale structures or dense evidence.

The labelled-not boxes have the same colour statistics as the true ones, which caps precision near 0.75 on these
features. A learned crop verifier (DroneWaste, below) is the next step.

**Output:** `waste_sitewide.geojson`, 74 boxes: interrow 11, block 6, headland 25, and outside 32 with an empty
`vineyard_id`. 0 on the example tiles. By eye, at least 2 of the 15 unlabelled boxes are flowering or silvery shrubs
(r035_c024, r034_c022). `python -m marcaj.waste --workers 2` takes 90 s and 1.2 GB.

## Site-wide detector, rule flags (18:50)

The rules (section 3) box litter "anywhere on the tile" and list the confounders: tubes, stakes, posts, wires,
hoses, stones, bare or pale soil, flowering shrubs, pruning residue, vehicles and machinery. `marcaj.waste` now scans
all 311 tiles: inter-row blobs exactly as before, plus the rest of each tile with the site-wide v1 surroundings tests
(pale structures, ring green, density, 10 m from the organizers' building zones). 228,726 candidates in 40-140 s with
6 processes. `vineyard_id` follows the 10 m rule against the v4 blocks. `WasteParams(scope="interrow")` is the old rule.

**Verifier outside the inter-rows:** white, 0.03-1.5 m2, clipped share >= 0.1, chroma <= 20, textured, ring green
>= 0.5, no pale structure, >= 10 m from buildings; luminance >= 225, density <= 0.03, >= 1.5 m from a row far from the
blocks; luminance >= 220, density <= 0.10, >= 1.2 m from a row in a block or within 10 m of one. Blue as in v1.

**Evaluation** (`research/review/eval_waste.py`, in-sample): 485 labelled objects, the user's 72 waste and 327 not
waste answers plus the earlier eye verdicts (10 likely, 76 not).

| Verifier | Boxes | Labelled waste | Labelled not | Unlabelled | Recall, all | Recall, rule-conform |
|---|---|---|---|---|---|---|
| inter-rows only (before) | 11 | 7 | 2 | 2 | 7/82 | 7/49 |
| site-wide, v1 outside tests | 29 | 13 | 5 | 11 | 13/82 | 13/49 |
| site-wide (now) | 60 | 23 | 5 | 32 | 22/82 | 22/49 |

"Rule-conform" leaves out the 33 of the user's 72 waste answers whose card carries rule flags. Boxes by location:
interrow 11, block 3, headland 17, outside 29 (the last with an empty `vineyard_id`). Control: 0 boxes on the two
example tiles. Output: `data/generated/work/waste/waste_sitewide.geojson`.

**Rule flags** (`research/review/build.py rule_flags`, shown on each card): tiny scrap under 0.02 m2; dark blob; large
dull or tinted patch (>= 0.15 m2, luminance < 215 or chroma > 22, contrast < 170); not bright (luminance < 212,
contrast < 170); low contrast (< 105); within 0.5 m of a row axis; long thin shape. 33 of the 72 waste answers are
flagged (14 tiny, 13 not bright, 6 dull patches, 4 near a row, 3 low contrast, 1 dark). The answers are unchanged;
`confirmed_rule_conform.csv` leaves the flagged ones out.

**New cards:** 215 S-* cards (150 outside, 65 headland): every site-wide box not already a card, plus the best-scoring
new candidates beyond 6 m of the blocks. Existing ids are unchanged.

## Review tool and the small bright tier (13:50)

**Review tool** (`research/review/`, launch `marcaj-review`, http://127.0.0.1:8010). `build.py` runs `waste.tile_candidates`
over every predicted block plus a 6 m ring with no verifier: 108,030 blobs on 147 tiles in 18 s (6 processes), 62,556
items after a 0.25 m dedupe (34,261 interrow, 16,182 block, 12,113 headland), all in
`data/generated/work/waste/candidates_all.geojson`. Every checklist item and both accepted boxes were among them (35/35).
The review set is the top 320 interrow, 100 block and 80 headland items by a ranking score plus every checklist item.
Answers go to `data/review/verdicts.json` (object verdicts with `properties.review_tab`); confirmed waste is exported
to `data/generated/work/waste/confirmed.csv`.

**Small bright tier** in `waste.accept`: white blobs of 0.02-0.15 m2, luminance >= 220, chroma <= 20, distance >= 100,
narrow spread >= 0.03 m, not a line, >= 0.55 m from a row axis. The strict tier missed the crumpled scraps because they
are under 0.03 m2 or 0.55-0.9 m from a row. Measured with `research/review/eval_waste.py` on the user's labels plus the
checklists' "likely" items (in-sample: the tier was set with these labels in view):

| Verifier | Boxes | Labelled waste | Labelled not waste | Unlabelled | Inter-row recall |
|---|---|---|---|---|---|
| strict only (before) | 2 | 2 | 0 | 0 | 2/31 |
| strict + small (now) | 11 | 9 | 1 | 1 | 9/31 |
| small, luminance >= 215 | 19 | 10 | 5 | 4 | 10/31 |

The remaining misses are white scraps at luminance 201-215, where the pale clods are too. Control: still 0 boxes on
the two example tiles.

## Current detector: litter in the inter-rows only (10:20)

The user decided "we only care inter-row". `waste.detect(tiles, predictions, data_dir, params)` keeps the same
signature, but now keeps only boxes whose centre lies in a predicted `interrow_area`. Each box takes that inter-row's
`vineyard_id`. The in-block headland boxes of the earlier version (P03 r018_c013, P07 r022_c013) drop out.

**Candidates.** Blobs inside the inter-row polygons (inset 0.30 m from the row axes) that are:
- white: darkest channel ≥ 170, and ≥ 40 brighter than the 2 m median background;
- coloured: chroma ≥ 60, hue outside the 25–175 band of soil and vegetation;
- black: neutral, luminance ≤ 45, and ≥ 60 darker than the background.

**`accept` keeps:**
- white blobs of 0.03–1.5 m² with luminance ≥ 220, chroma ≤ 15 and distance ≥ 150, that are not a thin line (narrow spread ≥ 0.04 m, length ≤ 3× width) and lie ≥ 0.9 m from a row axis;
- coloured blobs ≥ 0.02 m² with chroma ≥ 80 and distance ≥ 100;
- black blobs never.

**Why these thresholds.**
- A looser first version (luminance 205, chroma 25, no row distance) kept 86 boxes. By eye they were silvery shrubs, pale clods, and white tubes and stakes leaning or lying beside the rows.
- Tubes and stakes lie within about 0.75 m of their row axis.
- Vine shadows in the inter-rows look the same as black plastic.

**Measured.** 97,215 candidates in the 1,727 inter-rows of 131 tiles, in 86 s, 1 GB peak, one core.

**Boxes kept: 2.** The user decided to keep both.

| Tile | EPSG:32635 centre | Block | Box | Verdict |
|---|---|---|---|---|
| r019_c013 | E 629675.3, N 5220198.9 | P02 | 0.45 × 0.41 m | likely: white bag-like blob caught on the anchor wire of an end post, 1.4 m from the row |
| r008_c003 | E 629161.2, N 5220797.2 | P01 | 0.30 × 0.48 m | likely: crumpled white piece in the middle of the inter-row |

**Control.** 0 boxes on the two organizer example tiles (4,580 candidates there).

**In-sample.** The thresholds were set with these crops in view. The sweep below found about 5 more likely
inter-row items that the detector misses: slightly tinted white, pink, and items at the canopy edge. So the
detector's recall is low, and the manual checklist is what should close the gap.

Sheets: `ir_kept2_01.jpg` (the 2 boxes) and `ir_kept_01..04.jpg` (the looser version: 91 blobs, 86 boxes).

## North checklist for manual annotation (tiles with row index ≤ 23)

`data/generated/work/waste/checklist_north.json` and `checklist_north_01.jpg` hold 26 items: 7 likely, 19 unsure.
- **Fields:** tile, x_px, y_px, easting, northing, w_m, h_m, verdict, reason, plus vineyard_id.
- **Order:** the user-hinted P02 north-west item first, then sorted by tile.
- **Each crop:** a 3 m native view and 16 m of context, with the box drawn.

**Likely:**
1. **r020_c011, x 1260, y 1508, P02.** Crumpled white plastic mid inter-row in P02's north-west corner (the user's hint). The detector rejects it, because its chroma of 18 is above 15.
2. **r007_c004, x 1204, y 536, P01.** A pink/salmon L-shaped plastic, 1.4 m long, under a tree edge in the north of P01.
3. **r008_c002, x 1720, y 1192, P01.** A crumpled white piece in the inter-row.
4. **r008_c003, x 625, y 626, P01.** The detector box.
5. **r011_c001, x 1324, y 221, P04.** White paper or packaging in grass.
6. **r019_c013, x 708, y 2029, P02.** The detector box on the anchor wire.
7. **r022_c013, x 888, y 242, P07.** Crumpled clear/white plastic at the canopy edge of a row end, 0.2 m from the axis.

**Unsure** (19): small white scraps and crumpled pieces, a box-like white piece at the P02/P07 row end, and a
yellow box in a P12 shrub (possibly equipment, which is not waste). Also:
- two dark objects: a black bag or an animal in P14, and a round black object by the shed at P02, which could be a tyre or a bucket;
- the P03 headland item, which is outside the inter-rows;
- the P07 bag among lying tubes, 0.59 m from its row, which the inter-row rule excludes.

**How the sweep was done**, all on the northern tiles:
1. **Low-threshold review set.** From the detector's inter-row candidates: white ≥ 0.01 m², luminance ≥ 205, chroma ≤ 30, ≥ 0.45 m from a row; every coloured blob; black ≥ 0.08 m², compact, ≥ 0.7 m from a row. Clustered, that makes 854 spots, all viewed as native 2.4 m crops on 14 sheets (`sweep_north_*.jpg`), with about 60 zoomed at 5× (`zoom_sweep_*`, `zoom_bright_*`).
2. **Yellow and grey pass.** 9 blobs, all leaves (`sweep2_north_01.jpg`).
3. **Canopy-edge pass.** Compact bright blobs within 0.6 m of a row axis, which the inter-row inset hides: 74 blobs (`sweep3_north_*.jpg`), which added the P07 bottle and 4 unsure items.
4. **Tinted-bright pass.** Pink, salmon and bluish white: 105 blobs (`sweep4_north_*.jpg`), which added the pink plastic.
5. **Full panel review.** Every inter-row of P01, P02, P03, P04, P05, P07, P16, P12, P14, P10, P24 and P25 at native resolution, in 12.5 m panels (`panels_*.jpg`, 47 sheets). It found no further items: tubes, stakes, stones and clods only.

**Outside the inter-rows, not in the checklist:** a burnt rubbish and brush heap with white paper, west of P24
(E 629453, N 5220530, `zz_g.jpg`).

**Probes:** `waste_sweep.py`, `waste_yellow.py`, `waste_rowedge.py`, `waste_tinted.py`, `waste_block_panels.py`,
`waste_area.py`, `waste_zoom_sweep.py`, `waste_checklist.py`, `waste_interrow_sheet.py`.

## Earlier in-block version (10:00, superseded)

Only blobs inside predicted blocks and ≥ 0.35 m from the row axes were used. It kept 2 boxes: the P03 headland
item and the P07 row bag. Archived as `waste_inblock_v2.geojson` and `candidates_inblock_v2.json`.

## How waste is scored

- The organizers score F1 = 2·TP / (P + R), matched one-to-one at IoU ≥ 0.3, over a hidden subset of tiles.
  - With any reference waste on those tiles, predicting nothing scores 0.
  - With none, any box on them scores 0, and 0/0 is undefined (scored as 1 or skipped; unknown).
- **Local judge.** `judge._f1` returns `None` for 0/0, so waste drops out of `points_available`.
  - The two example tiles hold no waste, so the local judge can only show "n/a" or 0/10.
  - That is correct for the formula, not a bug, but it means the judge cannot measure waste.
- `cvat.image_elements` clips a box that crosses a tile edge into one box per tile.
- The rules say: "When in doubt whether something is litter, leave it out."
- On r006_c004 the organizers left a pale headland object and the white vine tubes unboxed: their reference is conservative.

## Site-wide scan (v1, superseded by the in-block rule)

**Scan.** White, blue, vivid and dark blobs over all 311 tiles: 25,153 candidates in 84 s.

I viewed 256 crops. They split into:
- **12 likely litter:** white bags and sheets, heaps of white film, printed packaging, a bottle and cup, and blue plastic. They lie in grassland, field edges and yard margins near the village edge.
- **About 120 unsure:** small white objects.
- **About 75 not litter:** shrubs, stones, tubes, walls, roofs, well lids, glints on the black mulch film.

The v1 verifier kept 29 boxes, all outside the blocks except one: 10 likely, 12 unsure, 7 not.

**Superseded.** These are outside vineyards, so by the user's decision they no longer count. They are also not route
targets.

**Files kept for the record:**
- `candidates_sitewide_v1.json`, `waste_sitewide_v1.geojson`
- `r2_*.jpg`, `detector_0*.jpg`, `likely_01.jpg`, `likely_checklist.json`, `labels.json`
- probes `waste_rank.py`, `waste_labels.py`, `waste_review.py`

The cached RT-DETRv2-r18 (COCO, Apache-2.0 per its Hugging Face card) fired "cat" or "donut" on every crop, litter or
not (`waste_rtdetr.py`).

## Recommendation

1. Import the 2 inter-row boxes.
2. This afternoon, annotators go through `checklist_north.json` (and the south agent's list) tile by tile in Marcaj.
   - Draw a tight box for each item they agree is litter.
   - Leave out tubes, stakes and anything doubtful: a false box costs as much as a miss.
3. Every drawn box becomes a route target, so the route must pass within 2 m of it.

## Downloads that would help (not downloaded)

| Download | Enables | Time |
|---|---|---|
| DroneWaste, Zenodo 17045559: 3.88 GB, CC BY 4.0, ~2 cm/px, 4,993 images, 5,135 annotations, 20 materials | A learned verifier on crops around our candidates, plus a held-out precision and recall on DroneWaste | ~0.5–1 h download, 0.5 h conversion, 1–2 h training on MPS, 10 min on our candidates |
| UAVVaste (COCO-like aerial litter, ~770 images) | A second domain for the same verifier | Size and licence need checking first |
