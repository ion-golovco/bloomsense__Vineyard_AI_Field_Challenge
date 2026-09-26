# Waste (10 points): scan, detector, recommendation

26 September 2026. Everything below was measured on the 311 tiles. The verdicts ("likely", "unsure", "not") are my own,
from contact sheets and 5x zooms. No labelled Sireț3 waste exists, so no number here is an accuracy.

## How waste is scored

- The organizers score F1 = 2·TP / (P + R), with one-to-one matching at IoU ≥ 0.3, over a hidden subset of tiles.
  - If any reference waste lies on those tiles, predicting nothing scores 0.
  - If there is none, any box on those tiles scores 0, and 0/0 is undefined. The organizers may score it as 1 or skip it; we don't know which.
- `judge._f1` returns `None` for 0/0, so waste drops out of `points_available`. N boxes against 0 reference boxes give 0.0.
  - The two example tiles hold 0 waste. So the local judge can only report "n/a" (no boxes) or 0/10 (any box).
  - That behaviour is correct for the formula, not a bug. The local judge cannot measure waste either way.
- `cvat.image_elements` clips a box that crosses a tile edge into one box per tile. `check_cvat` allows an empty `vineyard_id` on waste.
- Waste is also a route target: it counts when the route passes within 2 m. `routing.route_targets` uses the centroids of scored boxes, which come from the Marcaj export.
- The rules say: "When in doubt whether something is litter, leave it out."
- **Organizer calibration.** Tile r006_c004 has a pale 0.7 m² object at the headland, which was my top raw candidate (score 0.92). The organizers did not box it, and they did not box the white vine tubes either. Their reference is conservative.

## What the scan found

`python -m marcaj.waste` scans every tile at 0.025 m: 25,153 candidates in 84 s, peak RSS 0.59 GB, one core. The candidates are white, blue, vivid or dark blobs.

| Kind | Count | What they are |
|---|---|---|
| white | 23,388 | Mostly flowering shrubs, pale soil, limestone and white stones, vine tubes, walls, kerbs, roofs, concrete well lids, and glints on black mulch film |
| blue | 1,107 | Pools, cars, blue roofs, greenhouses, solar panels, and the black plastic mulch of block P15 (r027–r029 c031–c033). A few pieces of blue plastic |
| vivid | 502 | Red and orange roofs, machinery, flowers. No litter seen |
| dark | 156 | Shadows. No tyres seen |

I viewed 256 crops: the top 160 field candidates (≥ 15 m from buildings), the top 40 near buildings, and 56 in a recall probe. They split into:

- **12 likely litter.** White bags and sheets in grass, two heaps of white film, packaging with print, a bottle and cup, and three pieces of blue plastic.
- **About 120 unsure.** Compact white objects of 0.02–0.15 m². At 2.5 cm/px, and blurred, a bag, paper or a white stone cannot be told apart.
- **About 75 not litter.**

The 12 likely items lie in grassland, field edges and yard margins near the village edge:

- r012_c005 (3 items next to greenhouses)
- r015_c008, r015_c011, r017_c011
- r021_c016 (yard fence), r025_c019, r029_c018, r030_c016
- r032_c027 and r033_c027 (south-east grassland)

Only one lies in a predicted block (P24). **None are inside vine rows.**

Unsure heaps of white rubble or debris, which the verifier deliberately does not accept:

- r020_c010 on a track (E 629506.9, N 5220178.2)
- r023_c017 (plastic sheet beside rubble)
- r024_c018 (debris pile in a yard)
- r028_c028, r034_c025, r007_c001

All files are in `data/generated/work/waste/`:

- `r2_field_01..06.jpg` and `r2_town_01..02.jpg`: the ranked sheets
- `labels.json`: my verdicts
- `detector_01..02.jpg`: the 29 detector boxes, each captioned with its verdict
- `likely_01.jpg` and `likely_checklist.json`: the 12 likely items, with EPSG:32635 centres
- `recall_probe_01..02.jpg`: rejected candidates
- `zoom_*.jpg`
- `waste.geojson` and `candidates.json`

## Detector (`backend/src/marcaj/waste.py`)

The generator described above feeds a hand-set verifier. `accept` keeps:

- **White blobs of 0.1–1.5 m²** that are:
  - bright and partly clipped
  - textured (not a smooth disc)
  - lying in vegetation (ring green share ≥ 0.4)
  - isolated
  - clear of pale structures, ≥ 10 m from buildings and ≥ 0.5 m from row axes
- **Blue blobs of 0.04–1.5 m²** with the same context and a crumpled or printed texture (luminance std ≥ 20).

Touching boxes are merged. `vineyard_id` is the containing block, or the nearest one within 10 m, otherwise empty.

| Measure | Result |
|---|---|
| Boxes kept | 29 |
| Likely | 10 (34%) |
| Unsure | 12 (41%) |
| Not litter | 7 (24%): a well ring, stones ×2, limestone, a wall end, a concrete base, a flowering shrub |
| Likely items left out | 2 of the 12 seen: a 0.04 m² bottle and cup (too small), and a 1.59 m² film heap (too large or dense) |
| Rejected in the recall probe | Flowering shrubs, stones, roofs, mulch; ~6 plausible debris heaps; no other likely item |
| Control | 0 boxes on the two example tiles. The headland object on r006_c004 is rejected (0.5 m from a forbidden zone) |

The thresholds were set with these crops in view, so all numbers are in-sample.

- **Boxes are tight around the white pixels only.** On printed packaging (r025_c019) and the bottle-and-lid item they cover part of the object, so IoU against a hand-drawn box can fall below 0.3. Reviewers should resize them.
- **The cached RT-DETRv2-r18 (COCO) is useless here.** Run on CPU with `research/probes/waste_rtdetr.py`, it returned "cat", "elephant" or "donut" (0.5–0.8) on all 29 crops, litter and non-litter alike. Its licence is Apache-2.0 per the Hugging Face card; the card is not cached, so this was not re-checked offline.
- **SAM 2.1 tiny was not tried.**

**How to wire it in (not done).** In `predict.predict`, after `found` is built and before `rows.per_tile`:

```python
boxes, _ = waste.detect(tiles, found, data_dir)
found += boxes
```

This adds about 85 s. `per_tile` passes other labels through, and `marcaj-pack` writes the boxes as CVAT rectangles.

## Recommendation

1. **Don't import the 29 boxes unreviewed.** About a quarter are clearly not litter, and each false box costs as much as a miss.
2. Pre-annotate the 29 boxes. In Marcaj, delete every box that is not clearly litter, and resize the rest. That takes minutes.
3. The same reviewers check the 2 missed likely items and the 6 heaps from `likely_checklist.json` and this note, and draw them in Marcaj if they agree.
4. Keep the kept boxes as route targets. Most lie outside blocks, so check each is within 2 m of passable space, or list it as unreachable.
5. **Predicting nothing** wins only if the hidden tiles hold no waste. I found about 12 likely items in the tile set, so I would not bet on that. The conservative reference (tubes and the headland object unboxed) argues for boxing only clear items, not for boxing none.

## Downloads that would help (not downloaded)

| Download | Enables | Time |
|---|---|---|
| DroneWaste, Zenodo 17045559: 3.88 GB, CC BY 4.0, ~2 cm/px, 4,993 images, 5,135 annotations, 20 materials | A learned verifier (fine-tune the cached RT-DETRv2-r18, or a small torchvision detector) on crops around our candidates, plus a held-out precision and recall on DroneWaste | ~0.5–1 h download, 0.5 h conversion, 1–2 h training on MPS, 10 min on our candidates. Scale matches; season and scene differ |
| UAVVaste (COCO-like aerial litter, ~770 images) | A second domain for the same verifier | Size and licence need checking before use |
