# Status — Friday 25 September 2026, ~23:30

Commit `80b34b9 spec` holds the plumbing and SPEC §10–17. The lab, judge, review store, detector module and client clean-up are uncommitted in the working tree.

## Built and checked

| Piece | Evidence |
|---|---|
| Tile inventory, byte-identical tiles | 311 tiles CRC-matched to the part ZIPs; a tile mirrored in place by an outside process was caught and restored |
| CVAT 1.1 read/write, per-tile clipping, global IDs | The organizer examples round-trip with zero geometry change. A row across a tile edge becomes 2 polylines with 1 `row_id` whose lengths add up |
| Upload ZIPs | 5 ZIPs, 311 tiles, 0 problems, under 1 s. The validators catch bad values, empty IDs, out-of-tile shapes, missing tiles and changed tiles |
| Measurements, `route.geojson`, `measurements.csv` | Measured on the reference tiles: 2 blocks, 51 rows, 1,941.6 m of row, 536.2 m² of canopy, 4,064.4 m² of inter-row. The route checks enforce the zero-score rules |
| Local judge | Passes all controls (see CLAUDE.md). Current predictions score **0 of 48** available points, because only plots are predicted so far |
| Preprocessing (`mosaic`, `layers`) | 0.2 m mosaic in 11 s, plot variables in 17 s; measured against 49 hand-drawn outlines (RESEARCH.md) |
| Plots, rows, inter-rows, canopy (`plots`, `rows`, `canopy`, `predict`) | 40 plots, 1,793 row, 1,772 inter-row and 12,271 canopy pieces in 96 s. Reference tiles: canopy 0.668, axis F1 0.909 / 0.980, inter-row F1 0.800 / 0.958, attributes 0.850, grouping 1.0, counts 0.720; **judge estimate 37.7 of 50 available points**. Packs to 5 ZIPs, 311 tiles, 0 problems (the writer now snaps polygons to its 0.01 px grid; the organizer examples still round-trip unchanged) |
| Lab notebook | `research/lab.ipynb` executes headless. Click, draw, undo and judge regressions were exercised; the map renders with imagery, plots, reference and draw tools |
| Client app | `:8000` serves the real scene (organizer layers, reference tiles, predicted plots) with imagery. No tooling |
| Sentinel-2 probe | 72 scenes. Vineyards show late green-up (−0.25 NDVI in early June) and late senescence. Details in RESEARCH.md |

## Next, in order

1. **Dry-run the Marcaj upload now.** Upload the organizer example ZIP, check it imports, delete it. Then do the same with `marcaj-pack --scene ../data/generated/scene.json --source reference --only siret3_r021_c012.tif,siret3_r006_c004.tif --output-dir ../output/dryrun`, which tests *our* writer on the real platform. Delete both before the real upload.
2. **Plots, rows and inter-rows are in place and frozen** (RESEARCH.md, "Plots, rows and inter-rows"). Plots are only the grouping layer: stop tuning them. Known gaps: plots under 15 m wide, and rows that stop short at a weak stretch.
3. **Plot recall in narrow strips**, only if time allows after canopy: a lower seed threshold held to one row direction, scored on the north half only.
4. **Canopy next steps.** Extend rows to their last green stretch (rows stop 2–5 m short on r006; a corner row is missing on r021), move the row-contrast test into row detection, and derive `row_structure` from canopy gaps. Colour tuning is exhausted (RESEARCH.md).
5. **Waste.** Download DroneWaste (3.9 GB, CC BY 4.0, about 2 cm/px) and fine-tune YOLO on it. Tune for precision, because a false box costs as much as a miss.
6. **Network for the brief.** Self-train a small segmentation net on the pipeline's own canopy/row output over all 311 tiles (not on any hand drawing) and publish its weights. Use permissive weights: `segmentation_models.pytorch` U-Net (MIT) or DINOv2 (Apache-2.0), not SegFormer MiT (non-commercial). Needs torch, which is not yet a dependency.
7. **Route solver.** A graph over inter-row centrelines plus passages; OR-Tools with one choice of lane per target; `check_route` before export.
8. **Freeze and import around Saturday 13:00–14:00**, then publish. Everyone corrects in Marcaj, and every job gets submitted.
9. **Sunday:** Marcaj export → `marcaj-scene --cvat export.zip` → `marcaj-export --output-dir ..`. Then the README (runtime, hardware, weights), a Dockerfile and the pitch.

## Open decisions

- Downloads: DroneWaste 3.9 GB and Riseholme 3.3 GB.
- GRowSeg (pretrained vine-row SegFormer, MIT) needs access requested on Hugging Face; approval is manual.
- Sentinel in the pitch: one chart, or the "scout route" mode as well (about 2–3 h more).
- Slack question for the organizers: whether team verdicts and drawn "missed" hints may be used to *evaluate and tune* models outside Marcaj. Until it is answered, they stay evaluation-only.
