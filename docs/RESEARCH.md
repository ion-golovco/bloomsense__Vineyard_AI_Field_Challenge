# Research notes — Vineyard AI Field Challenge

Dated 25 September 2026, evening. Two sources: probes run on the released data, and a web sweep. **Verified** means the fact was read from the source itself: the data, the Zenodo or Hugging Face API, or an opened page.

## 1. What the data shows

| Finding | Evidence |
|---|---|
| The reference tiles encode rows as straight 2-point lines, 2.53–2.78 m apart, with the direction constant per tile (±0.3–1.2°) | the 51 reference rows on `siret3_r021_c012` and `siret3_r006_c004` |
| Canopies are small and separate: median 0.47–0.57 m², 0.24–0.44 plants per metre of row. Canopy is 9–11% of a vineyard tile; inter-row ground is 76–79% | the 650 reference canopies and 49 inter-rows |
| A single 2-D FFT of ExG (`2g−r−b`, normalised RGB) recovers row spacing: 2.75 / 2.60 m, against the measured 2.78 / 2.53 m | whole-tile probe, 311 tiles in 9.5 s at 10 cm/px |
| **Two frequency bands separate vineyard from orchard with no training.** Vineyard windows peak in the 2.0–3.6 m band (318–963× median power) against the 3.8–7 m band (34–53×). Orchard tiles invert: 15–22× against 118–215× | windowed probe: 25.6 m windows, 6.4 m stride, max of ExG and luminance, 17 s for 311 tiles |
| **Vineyard is a mask, not a tile label.** Ranked by tile score, tiles holding only a vineyard corner still appear at rank ~170 of 311; a tile-level classifier dilutes them | contact sheets of all 311 tiles |
| Weak case: vineyards with grassy inter-rows have low ExG contrast and fall into the "loose" tier (vine > 60 and > 2× orchard) | mask render over the mosaic |
| The south is fragmented strip parcels, so there are many small blocks. Block count and grouping (4% combined) depend on getting them right | overview and mask render |
| Passable space (passages minus forbidden) has 2 disconnected components. START is inside passages | `routing.passable_space` |

| **Plot polygons from the mask:** 32 polygons of at least 200 m², 11.2 ha in total. The organizer passages split the START block into its 3 plots. Edges are staircase-shaped at the 6.4 m window cells: median area / minimum-rectangle area is 0.67 | seamless probe on a 0.2 m/px mosaic of all tiles, 15 s |

### Sentinel-2 over Sireț, 2025

Earth Search `sentinel-2-c1-l2a` has 72 scenes under 40% cloud from March to October. They are on MGRS 35TPN in EPSG:32635, the drone CRS. Reflectance = DN × 1e-4 − 0.1; ignoring the offset biases NDVI. The probe read 13 clear dates at 12-day spacing, 49 s over HTTP. "Vineyard" = the drone plot polygons shrunk by 5 m (752 px of 10 m). "Other" = study-area land more than 15 m from any plot (5,917 px).

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
