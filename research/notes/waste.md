# Waste (10 points): scan, detector, recommendation

26 September 2026. Everything below was measured on the 311 tiles. The verdicts ("likely", "possible", "unsure", "not")
are my own, from contact sheets and 5x zooms. No labelled Sireț3 waste exists, so no number here is an accuracy.

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
