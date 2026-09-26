# Research notes — Vineyard AI Field Challenge

Dated 25 September 2026, evening. Two sources: probes run on the released data, and a web sweep. **Verified** means the fact was read from the source itself: the data, the Zenodo or Hugging Face API, or an opened page.

## 1. What the data shows

| Finding | Evidence |
|---|---|
| The reference tiles encode rows as straight 2-point lines, 2.53–2.78 m apart, with the direction constant per tile (±0.3–1.2°) | the 51 reference rows on `siret3_r021_c012` and `siret3_r006_c004` |
| Canopies are small and separate: median 0.47–0.57 m², 0.24–0.44 plants per metre of row. Canopy is 9–11% of a vineyard tile; inter-row ground is 76–79% | the 650 reference canopies and 49 inter-rows |
| A single 2-D FFT of ExG (`2g−r−b`, normalised RGB) recovers row spacing: 2.75 / 2.60 m, against the measured 2.78 / 2.53 m | `research/probes/tile_ranking.py`, 311 tiles in 9.5 s at 10 cm/px |
| **Two frequency bands separate vineyard from orchard with no training.** Vineyard windows peak in the 2.0–3.6 m band (318–963× median power) against the 3.8–7 m band (34–53×). Orchard tiles invert: 15–22× against 118–215× | 25.6 m windows, 6.4 m stride, max of ExG and luminance (`marcaj.vineyard_mask`) |
| **Vineyard is a mask, not a tile label.** Ranked by tile score, tiles holding only a vineyard corner still appear at rank ~170 of 311; a tile-level classifier dilutes them | `research/probes/contact_sheet.py` (all 311 tiles, sorted by score) |
| Weak case: vineyards with grassy inter-rows have low ExG contrast and fall into the "loose" tier (vine > 60 and > 2× orchard) | mask render over the mosaic |
| The south is fragmented strip parcels, so there are many small blocks. Block count and grouping (4% combined) depend on getting them right | overview and mask render |
| Passable space (passages minus forbidden) has 2 disconnected components. START is inside passages | `routing.passable_space` |

| **Plot polygons from the mask:** 32 polygons of at least 200 m², 11.2 ha in total. The organizer passages split the START block into its 3 plots. Edges are staircase-shaped at the 6.4 m window cells: median area / minimum-rectangle area is 0.67 | `marcaj.vineyard_mask` on a 0.2 m/px mosaic of all tiles: scores 13 s once, then about 0.02 s per re-threshold |

### Plot variables from 49 hand-drawn outlines (26 September)

The lab outlines (35 `vineyard`, 7 `overgrown`, 7 `orchard`) are evaluation evidence, measured by `research/probes/plot_variables.py` (36 s) on `marcaj.layers` (17 s, 4 GB, cached). AUC is "vineyard scores higher"; 0.5 is useless.

| Finding | Evidence |
|---|---|
| **Row periodicity finds vineyards.** Vine-band (2.0–3.6 m) energy over orchard-band (3.8–7 m), at the best of 12 orientations, AUC 0.96 against background, 0.96 against orchards, 0.95 against overgrown | `layers.vine_over_orchard`, 2 m smoothing |
| Orchard-band energy alone separates orchards (AUC 0.99). Green share within 4 m separates overgrown (0.92) and orchards (0.89) but not background (0.75) | `layers.orchard_energy`, `layers.green_share` |
| **Bare soil is not a vineyard test.** 8 of the 35 drawn vineyards have mostly green inter-rows (median cell green share above 0.6), yet clear rows | crops of those plots |
| **Narrow strips need a small scale.** A plain threshold covers 49% of plots under 15 m wide at 2 m smoothing, 36% at 5 m; plots of 15 m or more are covered 71–72% either way. Coverage rises with width (Spearman 0.58) | `vine_over_orchard > 2` |
| Hard-edged FFT band masks ring across the whole map from the no-data border. Smooth log-Gabor windows and a high-pass over valid pixels only remove it | the first filter bank's streak artefacts |
| **Edges are colour steps on open sides.** Across a drawn edge with no road, the green share goes 0.28 (0.8 m inside) → 0.79 (at the line) → 1.00 (0.8 m outside) | 0.4 m profile by signed distance |
| **On road sides colour cannot find the edge**: the dirt track is soil too (0.64–0.79 green 0.8–2.4 m out, 1.00 from 3.2 m). The road polygon must set it | same profile, edges within 6 m of `passages` |
| **Plots are rows bounded by 4 lines.** Drawn edge length: 53% along the rows, 25% square across them, 22% oblique. Most oblique edges run along a road (14% of all edge length). Side edges sit on the outermost row axis (median −0.10 m, IQR −0.8 to +2.0 m) | per-plot row angle from the across-row ExG profile |
| Row spacing per plot is 2.00–3.15 m. Neighbouring plots differ in angle (for example 51° strips beside 127° blocks) | same fit |
| **Canopy colour:** ExG > 0.110 is the best canopy threshold on both reference tiles (AUC 0.966 / 0.942), but pixel IoU is only 0.57 / 0.47, so canopies need shape and row constraints beyond colour. Superseded: plain 2g − r − b does better (below) | organizer canopies at 0.025 m |

### Plots, rows, inter-rows, canopy and waste (26 September)

Details, every variant tried and the reasons are in `research/notes/` (`plots.md`, `interrows.md`, `canopy_rules.md`, `waste.md`). Reference-tile numbers are a sanity check, not a holdout; the constants each note lists were tuned with those tiles in view.

| Finding | Evidence |
|---|---|
| **Plots** (`marcaj.plots`): north F1 0.81 at IoU 0.5, 0.54 at 0.75, median best IoU 0.76, area IoU 0.81, false 5,380 m². South 0.65 / 0.35 / 0.70 / 0.66, false 2,391 m². Before the row walk: 0.59 / 0.44 / 0.65 / 0.79 and 0.47 / 0.12 / 0.52 / 0.55. The south was inspected during development, so it is a validation area, not a clean test | `judge.plot_scores` against the 35 outlines, split at northing 5,219,600 |
| Why plots were short: the fit covered only its seed, and grassy inter-rows drop the seed ratio to 1–2. Kept: a row walk outward one whole row at a time, a wave floor that drops tilled fields and mulch stripes (ExG wave 0.0005–0.0015 against 0.011+ in vineyards), a 5 m merge without a road between, and peak suppression at 0.6 × spacing (was 0.35, which let grass strips 1.1–1.35 m from real rows through as rows). Still missed: 10–13 m strips of young vines, where the bare strip puts energy into the orchard band | `research/notes/plots.md` |
| **Row axes:** axis F1 0.980 and 0.962, 50 rows against 51, 1,902 m against 1,942 m. The r021 corner row is still missed, and r006's rows stop 1–5 m short where the plot edge cuts the tile | `judge` on the two reference tiles |
| **Reference inter-rows are exactly axis ± 0.30 m:** every vertex of the 49 lies 0.298–0.302 m from its row axis. The old 0.35 m inset alone cost 204 m². Inter-row F1 0.979 and 0.941, 3,959 m² against 4,064 m²; ends are carried onto a passage within 1.5 m (the 1 m road setback stopped 547 of 640 ends short of the passage the vines run into) | `research/probes/interrow_probe.py` |
| **`disrupted` counts tile-edge stretches too:** the reference marks a row disrupted for a ≥ 5 m gap between canopies *or* from the tile edge to its first canopy (50 of 51 labels reproduced). Vine green in a ±0.3 m tube, ignoring green runs under 0.55 m, gives 5/5 disrupted and 0/44 false; gaps between our predicted canopies over-call it (missed plants lengthen gaps). Attributes 0.974 | same |
| **Reference canopies are cut at exactly 0.30 m from their row line** (the farthest vertex is within 1 cm of 0.30 m in 76–90% of canopies), so a ±0.3 m tube is right and row placement matters | `research/probes/canopy_rules_analysis.py` |
| **Canopy colour: normalised ExG was the problem.** Dividing by brightness makes dark shadow look green. Plain `2g − r − b` in pixel values has AUC 0.971 / 0.975 within the row tube and the same best threshold on pale and dark soil (26.9 / 27.7), against ExG's 0.112 / 0.076. −CIVE and Lab score the same | `research/probes/canopy_rules_colour.py` |
| **Canopy** (`marcaj.canopy`): judge canopy score 0.855 (union IoU 0.822, instance F1 0.905), 402 against 399 and 259 against 251 canopies, 535 m² against 536 m². Was 0.668 (0.664, 0.674). Kept, each removed on its own: 2g−r−b > 25 (0.669 without), axis refit (0.772), 0.05 m closing to the tile edge (0.839), neck split (0.846), 0.01 m outline shrink (0.848), second refit at 0.3 m (0.851). The old contrast test is off and a mean-green row test (1.2) removes axes over verges and weed blocks. With the reference rows 0.897. Left: rows ~0.04, splitting touching plants ~0.018, small clumps the annotators left out ~0.011 | `research/probes/canopy_probe.py`, `research/notes/canopy_rules.md` |
| **A self-trained U-Net ties the rules but does not beat them.** 2.0 M parameters, plain torch, random init, trained on the rule canopy of 118 tiles in 834 s on MPS. Alone 0.843 (0.807, 0.896); as a filter on the colour mask 0.855; seed spread 0.003. Grass breaks self-training (in grassed plots the teacher's tube sits on inter-rows, so off-tube green must not be a negative), smooth masks bridge plants (a 6× separation weight fixes part), and its probability does not separate the teacher's false polygons (AUC 0.53). A second output for splitting plants over-splits. Diagnostic only: trained on one reference tile's hand canopies it scores 0.65–0.69 on the other, so one tile of labels buys nothing over 118 tiles of rule labels | `research/notes/canopy_net.md`, `research/probes/canopy_net_train.py` |
| **Annotators keep long canopies whole:** 90th percentile length 3.0 and 5.3 m, whole-row strips up to 56 m in r006's grassed block; plants rarely touch (38 of 374 and 24 of 225 neighbouring pairs). Polygon style does not matter (simplify 0.01–0.06 m changes the score under 0.001) | same |
| **Waste exists but is rare:** about 12 likely pieces of litter in 311 tiles (white bags and film, packaging, blue plastic), in grass, field edges and yard margins, one inside a block. The example tiles hold none, and the organizers left a pale headland object unboxed there. `marcaj.waste` keeps 29 boxes: by eye 10 likely, 12 unsure, 7 not litter (in-sample). The cached RT-DETRv2-r18 (COCO) labels litter crops "cat" or "donut" and is useless here | `research/notes/waste.md`, contact sheets in `data/generated/work/waste/` |
| **SAM 2.1 tiny does not beat the rule-based canopy** (measured against the old 0.668 version). Box prompts from the rule-based canopies: 0.496 (0.05 m margin), 0.464 (0.1 m), 0.388 (0.2 m); held to the rule outline +0.15 m 0.545; the closest of its three masks 0.529, held 0.555, against 0.668 without SAM. Masks are aligned but take each plant's cast shadow (median 1.5× the rule area). 14 s per vineyard tile on MPS (36 s with three masks) | `research/probes/sam_probe.py`, `facebook/sam2.1-hiera-tiny` via transformers 5.17 |
| The PyPI package `sam2` is not Meta's: it is uploaded by a third party from a fork (JinsuaFeito-dev/segment-anything-2). Use transformers' `Sam2Model` with the `facebook/` weights, or Meta's GitHub repository | PyPI JSON API |
| Model licences: SegFormer MiT weights are non-commercial (NVIDIA licence); `segmentation_models.pytorch` U-Net (MIT) and DINOv2 (Apache-2.0) are permissive. RoWeeder trained SegFormer-B0 on Hough-row pseudo-labels and beat its own labels (F1 74.3 against 63.0) | web research agent, sources opened |

### Sentinel-2 over Sireț, 2025

Earth Search `sentinel-2-c1-l2a` has 72 scenes under 40% cloud from March to October. They are on MGRS 35TPN in EPSG:32635, the drone CRS. Reflectance = DN × 1e-4 − 0.1; ignoring the offset biases NDVI. The probe read 13 clear dates at 12-day spacing, 49 s over HTTP. "Vineyard" = the detected plots shrunk by 5 m (`research/probes/sentinel_ndvi.py`) (752 px of 10 m). "Other" = study-area land more than 15 m from any plot (5,917 px).

| Date | Vineyard NDVI | Other NDVI | Difference | r(NDVI, drone green share), vineyard / all |
|---|---|---|---|---|
| 2025-04-19 | 0.311 | 0.431 | −0.12 | +0.24 / +0.76 |
| 2025-05-01 | 0.293 | 0.489 | −0.20 | +0.47 / +0.87 |
| 2025-06-02 | 0.383 | 0.632 | −0.25 | +0.62 / +0.83 |
| 2025-07-23 | 0.502 | 0.593 | −0.09 | +0.11 / +0.15 |
| 2025-08-29 | 0.458 | 0.460 | 0.00 | +0.07 / +0.17 |
| 2025-10-16 | 0.381 | 0.331 | +0.05 | — (21% clear) |

- **Phenology fingerprint:** vineyards green up late (−0.25 against other land in early June) and stay green late (+0.05 in October). This is a plot-level second opinion on the drone mask, and a route to mapping vineyards beyond the drone footprint. Not yet tested: tilled crop fields (maize, sunflower) against vineyards.
- **Around the flight date, 10 m NDVI tracks drone green share:** r = 0.83–0.87 over all land and 0.47–0.62 within vineyards. Inside a vineyard the pixel mixes canopy and inter-row grass, so a drone baseline is needed to interpret it.
- **A 5 m row gap is about 2.5% of a 10 m pixel's area**, below what NDVI can resolve. Sentinel cannot find the scored inspection targets.

## 2. External resources

| Resource | What it gives | Licence | Status |
|---|---|---|---|
| [Riseholme UAV vineyard](https://zenodo.org/records/19234907) | 855 images, 40,215 COCO annotations (`pole`, `trunk`, `vine_row`, `vineyard`), 3.34 GB. It is not established that masks are one per plant | CC BY 4.0 | Verified (Zenodo API) |
| [DroneWaste images](https://zenodo.org/records/17045559) | 4,993 images, 5,135 annotations, 20 materials, 3.88 GB. The paper reports ~2 cm/px, close to Sireț's 2.5. Record 17288038 is only the preprint | CC BY 4.0 (Zenodo API); code MIT | Verified |
| [UAVVaste](https://zenodo.org/records/8214061) | 772 images, 3,718 annotations, 3.0 GB. GSD unknown | CC BY 4.0 | Verified (Zenodo API) |
| [GRowSeg](https://huggingface.co/links-ads/gaia-growseg) | SegFormer-B5 vine-row masks, 512 px patches, 0.75–10 cm/px (optimum 1–1.5 cm/px) | MIT; **weights gated, manual approval** | Verified (HF API) |
| [SAM2](https://github.com/facebookresearch/sam2) | Point-prompted masks. Apple MPS support is described as preliminary | Apache-2.0 | Verified (web sweep) |
| [ENI CORINE 2024 Moldova](https://copernicus.discomap.eea.europa.eu/ArcGIS/rest/services/ENI_CLC_Pilot/ENI_CLC_Pilot_2024/MapServer) | Vineyard class 221 at a 25 ha minimum mapping unit: a coarse prior only | EEA terms, not checked | Verified (web sweep) |
| [ONVV vineyard register](https://maia.gov.md/sites/default/files/PressReleas/Documente%20Atasate/3.%20Poster_RO.pdf) | Parcel code, holder, location, area, variety, planting year, **planting scheme**, trellis. No public geometry API found | not stated | Verified (web sweep) |
| Sentinel-2 L2A | Already wired in `bloom-sense-api/src/api/remote-sensing/providers/cdse.ts`: NDVI/NDMI statistics and chips with SCL cloud masking. Anonymous alternative: Earth Search `sentinel-2-c1-l2a` | Copernicus legal notice | Verified (code) |
| OR-Tools / NetworkX | Route solving | Apache-2.0 / BSD-3 | Verified (web sweep) |

**Dead ends:**
- No downloadable, vineyard-trained, per-plant aerial instance model was found.
- AerialWaste classifies whole dump scenes, not litter boxes, and its model is CC BY-NC-ND.
- TACO is ground-level photos.
- LKH-3 is research-use only.
- OSM landuse coverage near Sireț could not be queried: Overpass returned empty bodies.

## 3. Constraint from the brief

The brief asks for "a neural-network model" and its weights, or a reproducible way to obtain them. A purely classical pipeline risks falling short of the brief, so at least one network must do real work and ship weights.
