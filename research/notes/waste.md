# Waste (10 points): scan, detector, recommendation

26 September 2026. Everything below was measured on the 311 tiles. The verdicts ("likely", "possible", "unsure", "not")
are my own, from contact sheets and 5x zooms. No labelled Sireț3 waste exists, so no number here is an accuracy.

## Current detector: waste inside vineyard blocks only (10:00)

The user saw 2–3 pieces of garbage in the four big vineyards by START, and decided that only waste inside vineyards
counts. The rules also allow the surrounding zone. The four vineyards are the predicted blocks P03, P02, P16 and P07
(`overview_start.jpg`, 600 m around START).

**Method (`backend/src/marcaj/waste.py`).** Only pixels inside a predicted block and ≥ 0.35 m from every predicted
row axis are used. The row axis is the planting: canopies, white vine tubes, stakes and posts.

1. **Candidates.** Blobs whose RGB distance from the 2 m median background is ≥ 90, that are not green and not shadow.
2. **`accept` keeps a blob when all of these hold:**
   - 0.15–1.5 m²
   - mean distance ≥ 170
   - luminance ≥ 215 and chroma ≤ 20 (white plastic)
   - narrow-axis spread ≥ 0.08 m (a lying stake is a few cm)
   - no pale structure (sheds, trucks, concrete, tracks)
3. **Box.** The box is the largest bright piece of the blob, so a lying tube merged into the blob does not stretch it.
4. **`vineyard_id`** is the block the blob lies in.

**Measured.**
- 20,063 candidates inside the 36 blocks, in 56 s, 0.65 GB peak, one core.
- `detect(tiles, predictions, data_dir)` is unchanged, and `predict.py` calls it before `rows.per_tile`.

**Boxes kept: 2, both likely litter.**

| Tile | EPSG:32635 centre | Block | Box | Verdict |
|---|---|---|---|---|
| r018_c013 | E 629673.4, N 5220250.2 | P03 | 0.98 × 1.05 m | likely: white plastic item (bag or basin) with a grey film, on the headland by the road |
| r022_c013 | E 629673.9, N 5220087.5 | P07 | 0.43 × 0.70 m | likely: white bag at a vine row; blob-shaped, among lying white tubes |

Both boxes are about 1 m from the predicted passable space (inter-rows plus passages), so a route along the
neighbouring inter-row visits them within the 2 m radius.

**Near misses.** A looser setting (0.10 m², distance 160, luminance 205, chroma 25) lets 13 more through. I checked all
13 by eye:

- **Possible third item:** white paper or plastic pieces in grass by a parked car, at the P02 corner (r021_c015, E 629771.4, N 5220128.3, blob 0.24 m²). It has luminance 208 and chroma 21, so it fails the strict thresholds. It is left for a reviewer to draw in Marcaj if they agree.
- **Unsure:** a yellow-and-white object in a shrub at a P12 row (r014_c004, E 629210.8, N 5220483.3): a container, or equipment.
- **Not litter:**
  - a white post with a long shadow, in P04 (r010_c002)
  - 10 silvery shrubs or blossom, in P13, P15, P20, P21 and P31

**Also seen, not boxed.** Several small white pieces of 0.03–0.09 m² on the grassy NE headland of P03/P02 by the road,
near a parked truck (r019_c013, r020_c014). They are paper or plastic bits, or stones: unsure, and below the size gate.

**Control.** The two organizer example tiles have no waste. The detector puts 0 boxes on them, out of 520 candidates there.

**In-sample.** The thresholds were set with these crops in view, so this is in-sample. The two kept items are the ones
found by eye in the central blocks, so the tuning does rest on them.

**Crops** (in `data/generated/work/waste/`):
- `detector_inblock_01.jpg`: the 2 boxes with their verdicts
- `nearmiss_inblock_01.jpg`: the 13 near misses with their verdicts
- `zoom_p03_bag_stitched.jpg`, `zoom_p07_bag.jpg`, `zoom_p02_car.jpg`: native zooms
- `central_*.jpg`, `central_anom_*.jpg`, `central_strong_*.jpg`: the central-block scans
- `inblock_*.jpg`: the ranked in-block candidates

Review with `research/probes/waste_inblock_review.py`.

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

1. Import the 2 boxes. In Marcaj, the reviewer of r018_c013 and r022_c013 confirms each and resizes it to the whole object.
   - The P03 box covers the white part; the grey film around it is part of the same object.
2. Check the P02 car-side pieces (r021_c015). Draw one box, or one box per piece, only if they are clearly litter.
3. Leave everything else empty.
4. Waste boxes are route targets. Both kept boxes are reachable (about 1 m from passable space).

## Downloads that would help (not downloaded)

| Download | Enables | Time |
|---|---|---|
| DroneWaste, Zenodo 17045559: 3.88 GB, CC BY 4.0, ~2 cm/px, 4,993 images, 5,135 annotations, 20 materials | A learned verifier on crops around our candidates, plus a held-out precision and recall on DroneWaste | ~0.5–1 h download, 0.5 h conversion, 1–2 h training on MPS, 10 min on our candidates |
| UAVVaste (COCO-like aerial litter, ~770 images) | A second domain for the same verifier | Size and licence need checking first |
