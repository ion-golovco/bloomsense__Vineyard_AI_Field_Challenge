# Status — Saturday 26 September 2026, ~03:30

Commits up to `050fa5d` hold the plumbing, plots, rows, canopy v1 and SAM probe. The plots, rows, canopy v2 and waste work of Saturday night is uncommitted in the working tree; its notes are in `research/notes/`.

## Built and checked

| Piece | Evidence |
|---|---|
| Tile inventory, byte-identical tiles | 311 tiles CRC-matched to the part ZIPs; a tile mirrored in place by an outside process was caught and restored |
| CVAT 1.1 read/write, per-tile clipping, global IDs | The organizer examples round-trip with zero geometry change. A row across a tile edge becomes 2 polylines with 1 `row_id` whose lengths add up |
| Upload ZIPs | 5 ZIPs, 311 tiles, 0 problems, under 1 s. The validators catch bad values, empty IDs, out-of-tile shapes, missing tiles and changed tiles |
| Measurements, `route.geojson`, `measurements.csv` | Measured on the reference tiles: 2 blocks, 51 rows, 1,941.6 m of row, 536.2 m² of canopy, 4,064.4 m² of inter-row. The route checks enforce the zero-score rules |
| Local judge | Passes all controls (see CLAUDE.md). It cannot score waste: both example tiles hold none, so waste is n/a |
| Preprocessing (`mosaic`, `layers`) | 0.2 m mosaic in 11 s, plot variables in 17 s; measured against 49 hand-drawn outlines (RESEARCH.md) |
| Plots, rows, inter-rows, canopy, waste (`plots`, `rows`, `canopy`, `waste`, `predict`) | 36 plots, 1,776 row, 1,727 inter-row, 13,279 canopy pieces and 29 waste boxes in 215 s (3.9 GB peak), with the U-Net filtering the canopy colour mask. Reference tiles: canopy **0.854** (IoU 0.821, F1 0.908; was 0.668; 0.855 with the rules alone), axis F1 0.980 / 0.962, inter-row F1 0.979 / 0.941, attributes 0.974, grouping 1.0, counts 0.890; **judge estimate 44.88 of 50 available points** (was 37.7; 44.99 with the rules alone). Plots against the 35 outlines: north F1@0.5 0.81, south 0.65 (were 0.59 / 0.47). Packs to 5 ZIPs, 311 tiles, 0 problems |
| Lab notebook | `research/lab.ipynb` executes headless. Click, draw, undo and judge regressions were exercised; the map renders with imagery, plots, reference and draw tools |
| Client app | `:8000` serves the real scene (organizer layers, reference tiles, predicted plots) with imagery. No tooling |
| Sentinel-2 probe | 72 scenes. Vineyards show late green-up (−0.25 NDVI in early June) and late senescence. Details in RESEARCH.md |

## Next, in order

1. **Dry-run the Marcaj upload now.** Upload the organizer example ZIP, check it imports, delete it. Then do the same with `marcaj-pack --scene ../data/generated/scene.json --source reference --only siret3_r021_c012.tif,siret3_r006_c004.tif --output-dir ../output/dryrun`, which tests *our* writer on the real platform. Delete both before the real upload.
2. **Review the agent findings that need a human** (details in `research/notes/`):
   - Plots audit (`data/generated/work/plots/audit_sheet.jpg`): A01 (dark stripes 3.55 m apart on r027_c032: vineyard on mulch or a vegetable field?), #38 (drawn orchard, vine-like 3.26 m spacing), #28/#31/#32 take in a track, #30 is two blocks by the rules, #21/#22, #23/#34 and #28/#29 are under 5 m apart.
   - Waste: the 29 boxes (`data/generated/work/waste/detector_01.jpg`) are 34% likely litter by eye. Pre-annotate them, then delete non-litter and resize in Marcaj; 2 missed items and 6 rubble heaps are listed in `likely_checklist.json`.
3. **Plots:** narrow 10–13 m strips of young vines are still missed (a top-hat seed option is in `layers.py`, off), and grassy outer rows stop early. Blocks under 5 m apart share no `vineyard_id` yet: that needs a grouping step at the end of `predict`.
4. **Canopy:** what is left is rows (~0.04: r006's short rows, the r021 corner row), splitting touching plants (~0.018) and small clumps (~0.011). A 2-D watershed is the one untried split.
5. **Waste:** DroneWaste (3.9 GB, CC BY 4.0) would allow a learned verifier and a held-out precision number. Waiting on the download decision.
6. **Network for the brief: in the pipeline, needs publishing.** `marcaj.canopy_net` (U-Net, 2.0 M parameters, self-trained on the rule canopy of 118 tiles) filters the rule's colour mask in `predict`: 0.854 against 0.855 for the rules alone, +35 s. Alone it scores 0.843. It does not beat the rules: grass breaks self-training and it cannot find its teacher's mistakes (`research/notes/canopy_net.md`). The weights `models/canopy_net.pt` (7.9 MB) are untracked: commit them or attach them to a release, and name them in the README. SAM 2.1 tiny lost earlier (0.555 against 0.668).
7. **Route solver.** A graph over inter-row centrelines plus passages; OR-Tools with one choice of lane per target; `check_route` before export. Predicted inter-rows now touch passages at 85% of ends within 4 m, but none bridges the two passage components: the shortest crossing is 3–11 m of bare headland at P17 (`research/notes/interrows.md`), inside the 2% allowance.
8. **Freeze and import around Saturday 13:00–14:00**, then publish. Everyone corrects in Marcaj, and every job gets submitted.
9. **Sunday:** Marcaj export → `marcaj-scene --cvat export.zip` → `marcaj-export --output-dir ..`. Then the README (runtime, hardware, weights), a Dockerfile and the pitch.

## Open decisions

- Downloads: DroneWaste 3.9 GB and Riseholme 3.3 GB.
- The walking route (25 points) has no solver yet; decide who owns it.
- GRowSeg (pretrained vine-row SegFormer, MIT) needs access requested on Hugging Face; approval is manual.
- Sentinel in the pitch: one chart, or the "scout route" mode as well (about 2–3 h more).
- Slack question for the organizers: whether team verdicts and drawn "missed" hints may be used to *evaluate and tune* models outside Marcaj. Until it is answered, they stay evaluation-only.
