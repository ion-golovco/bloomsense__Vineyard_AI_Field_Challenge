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
| Vineyard plot detector (`vineyard_mask`) | 32 plots, 11.2 ha. Orchards are excluded, and passages split blocks. **Recall is incomplete:** loosening the thresholds adds only about 2 ha, so the misses have weak row signal |
| Lab notebook | `research/lab.ipynb` executes headless. Click, draw, undo and judge regressions were exercised; the map renders with imagery, plots, reference and draw tools |
| Client app | `:8000` serves the real scene (organizer layers, reference tiles, predicted plots) with imagery. No tooling |
| Sentinel-2 probe | 72 scenes. Vineyards show late green-up (−0.25 NDVI in early June) and late senescence. Details in RESEARCH.md |

## Next, in order

1. **Dry-run the Marcaj upload now.** Upload the organizer example ZIP, check it imports, delete it. Then do the same with `marcaj-pack --scene ../data/generated/scene.json --source reference --only siret3_r021_c012.tif,siret3_r006_c004.tif --output-dir ../output/dryrun`, which tests *our* writer on the real platform. Delete both before the real upload.
2. **Vineyard plots as row-bounded quadrilaterals.** 49 plots are outlined in the lab and the variables are measured (RESEARCH.md, "Plot variables"). Next: seed plots from `layers.vine_over_orchard` at 2 m, split them by `row_angle`, fit the exact angle per plot, and put the sides on the outermost row axes. Row ends are square to the rows, or follow the passage edge on road sides. Score against the outlines with IoU, holding out one area.
3. **Rows and inter-rows from the lattice.** Per plot, fit row angle and spacing with an FFT, project ExG to find each row's offset, and fit straight lines in world coordinates. Number rows per plot. Inter-rows are the bands between neighbouring axes minus canopy. Score them with the judge's axis F1.
4. **Canopies.** ExG within ±0.4 m of each axis, split into components and cut at the vine spacing. Then `row_structure` and inspection points from gaps of 5 m or more, and `interrow_cover` from the ExG share.
5. **Waste.** Download DroneWaste (3.9 GB, CC BY 4.0, about 2 cm/px) and fine-tune YOLO on it. Tune for precision, because a false box costs as much as a miss.
6. **Network for the brief.** A segmentation net self-trained on the lattice's confident output (not on any hand drawing); publish its weights.
7. **Route solver.** A graph over inter-row centrelines plus passages; OR-Tools with one choice of lane per target; `check_route` before export.
8. **Freeze and import around Saturday 13:00–14:00**, then publish. Everyone corrects in Marcaj, and every job gets submitted.
9. **Sunday:** Marcaj export → `marcaj-scene --cvat export.zip` → `marcaj-export --output-dir ..`. Then the README (runtime, hardware, weights), a Dockerfile and the pitch.

## Open decisions

- Downloads: DroneWaste 3.9 GB and Riseholme 3.3 GB.
- GRowSeg (pretrained vine-row SegFormer, MIT) needs access requested on Hugging Face; approval is manual.
- Sentinel in the pitch: one chart, or the "scout route" mode as well (about 2–3 h more).
- Slack question for the organizers: whether team verdicts and drawn "missed" hints may be used to *evaluate and tune* models outside Marcaj. Until it is answered, they stay evaluation-only.
