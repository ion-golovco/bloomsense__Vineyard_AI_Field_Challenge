# What to use tomorrow

1. **Gate vineyard tiles first.** Look for a strong, consistent row direction and spacing with local FFT or Gabor features; reject orchard blocks with broad, separated crowns. This is a proposed use of published vine-pattern methods, not a tested Sireț classifier. [VERIFIED source](https://www.isprs.org/proceedings/xxxvi/4-c42/Papers/04_Automated%20classification%20Agriculture/OBIA2006_Delenne_et_al.pdf).
2. **Fit rows in mosaic coordinates, then cut them into tiles.** This is the practical way to keep `row_id` stable across tile edges; Hough clustering and line fitting have published vineyard precedent. [VERIFIED source](https://www.sciencedirect.com/science/article/pii/S0168169915000861).
3. **Try GRowSeg on a small sample.** Its card specifies RGB GeoTIFFs, 512 px patches and approximately 0.75–10 cm/px input, but its weight file is gated. [VERIFIED model card and access page](https://huggingface.co/links-ads/gaia-growseg).
4. **Build plant polygons from row-constrained RGB vegetation and separated canopy components.** Check every split against the reference annotations; a published method locates individual plants from row masks and canopy geometry. [VERIFIED paper search page; method details UNVERIFIED because full text was blocked](https://www.tandfonline.com/doi/full/10.1080/22797254.2017.1308234).
5. **Use SAM2 for prompted correction, then evaluate it.** Its checkpoints are Apache-2.0; Meta describes MPS support as preliminary and warns of different outputs or degraded performance. [VERIFIED licence](https://github.com/facebookresearch/sam2) · [VERIFIED MPS warning](https://github.com/facebookresearch/sam2/blob/main/demo/backend/server/inference/predictor.py).
6. **For waste, trial UAVVaste and DroneWaste on full-resolution crops.** DroneWaste’s images are in record **17045559**, while **17288038** holds the paper. [VERIFIED dataset](https://zenodo.org/records/17045559) · [VERIFIED paper record](https://zenodo.org/records/17288038).
7. **Treat satellite and national maps as block priors.** Sentinel-2’s useful bands are 10–20 m; Moldova’s CORINE vineyard class has a 25 ha minimum mapping unit. Neither resolves plants or walking lanes. [VERIFIED band specification](https://sentinels.copernicus.eu/documents/247904/0/Sentinel-2-product-specifications-document-V14-9.pdf) · [VERIFIED Moldova map metadata](https://copernicus.discomap.eea.europa.eu/ArcGIS/rest/services/ENI_CLC_Pilot/ENI_CLC_Pilot_2024/MapServer).
8. **Ask the grower for the planting scheme and block boundaries.** Moldova’s vineyard-register description includes location, area, variety, planting year, planting scheme and trellis. [VERIFIED ONVV/ACSA poster](https://maia.gov.md/sites/default/files/PressReleas/Documente%20Atasate/3.%20Poster_RO.pdf).
9. **Route on walkable inter-row segments and road passages.** Make each target eligible from any reachable point within 2 m, then solve a closed tour over those access points. Orchard planners already model rows as constrained route choices. [VERIFIED orchard-routing paper](https://www.cs.cmu.edu/~cvalles/papers/automatingOrchards.pdf).
10. **Spend the two labelled tiles on rejection checks:** non-vine false positives, plant splits, row offsets and waste lookalikes. RGB greenness thresholds are useful proposals, but need local checking because vineyard rows can coexist with inter-row vegetation. [VERIFIED RGB vineyard study](https://www.mdpi.com/2072-4292/9/3/268) · [VERIFIED RGB-index formulas](https://pmc.ncbi.nlm.nih.gov/articles/PMC12117064/).

**Status convention:** VERIFIED means I opened the linked page and saw the stated information. UNVERIFIED means a search snippet, inference, or access-limited result. “Licence unclear” is deliberately not a licence grant.

## 1. Vineyard versus non-vineyard — highest priority

| Item | What it gives us | Licence | Link | Status |
|---|---|---|---|---|
| Delenne et al., FFT plus Gabor | Local spectral peaks indicate row direction and period; the paper reports failure modes across vine training patterns. Use peak coherence and known 2.5–2.8 m spacing as a **candidate** block test. | Paper; no reusable code licence identified on the opened PDF. | [ISPRS paper](https://www.isprs.org/proceedings/xxxvi/4-c42/Papers/04_Automated%20classification%20Agriculture/OBIA2006_Delenne_et_al.pdf) | VERIFIED |
| Orchard comparison | Orchards can also be periodic: a published orchard method models repeated tree primitives. Proposed discriminator: small, dense vine crowns along narrow bands versus the team’s wider, isolated orchard crowns; geometry must be checked locally. | Paper; code licence not established. | [Orchard paper](https://ieeexplore.ieee.org/document/6144003/) | UNVERIFIED — abstract/snippet only |
| RGB canopy classifiers | Vineyard RGB study compares spectral indices, clustering, neural networks and random forest; it warns FFT/Gabor performance falls when rows are interrupted. | Paper; no code licence identified. | [Remote Sensing article](https://www.mdpi.com/2072-4292/9/3/268) | VERIFIED |

I did **not** establish an open, pretrained vineyard-versus-orchard block classifier from this sweep.

## 2. Vine row axes

| Item | What it gives us | Licence | Link | Status |
|---|---|---|---|---|
| GRowSeg | SegFormer-B5 binary vine-row mask; 512 px default patch, 256 px stride; card says 0.75–10 cm/px supported and 1–1.5 cm/px optimal. Its 339 MB weight is listed but gated; masks still need axis fitting. | **MIT**, read on model card; access conditions also apply to files. | [Model card](https://huggingface.co/links-ads/gaia-growseg) · [weight listing](https://huggingface.co/links-ads/gaia-growseg/tree/main) | VERIFIED |
| Classical row fitting | Published vineyard pipeline uses dynamic segmentation, Hough-space clustering and total least squares; useful for straight rows and grass between them. No released weights are involved. | Paper; open-code licence not found. | [Comba et al.](https://www.sciencedirect.com/science/article/pii/S0168169915000861) | UNVERIFIED — publisher open failed; snippet inspected |
| RoWeeder | Open row-derived pseudo-label workflow and downloadable **512×512 crop/weed** model; a technique reference, not a vineyard axis checkpoint. | **MIT**, read on repository page. | [Repository](https://github.com/pasqualedem/RoWeeder) | VERIFIED |
| Multispectral vineyard segmentation | Code for SegNet/U-Net-style vine vegetation segmentation; no downloadable trained weights identified on its repository page. | **MIT**, read in repository licence section. | [Repository](https://github.com/Cybonic/DL_vineyard_segmentation_study) | VERIFIED |

For the 0.4 m axis tolerance, fit and number lines over the georeferenced mosaic, then clip each axis to its tile. That workflow is an **inference** from the stated scoring rule and the row-fitting methods above, not a published performance claim.

## 3. Individual vine canopies

| Item | What it gives us | Licence | Link | Status |
|---|---|---|---|---|
| Riseholme, record 19234907 | 855 RGB images and 40,215 COCO annotations across three seasons. Classes are `pole`, `trunk`, `vine_row`, `vineyard`; “vineyard” means canopy, but the page does **not** establish one mask per plant. One 3.3 GB ZIP; no weights listed. | **Unclear:** the opened record’s Rights heading did not display a licence. Do not assume Zenodo’s default applies to this record. | [Dataset record](https://zenodo.org/records/19234907) | VERIFIED |
| SAM2 with samgeo | Prompted masks and GeoTIFF/vector handling; useful for correcting candidate plants, with no vineyard-specific accuracy established here. SAM2 MPS support is preliminary. | **Apache-2.0** for SAM2 checkpoints/code, read in its repository; **MIT** for samgeo, read in its repository. | [SAM2](https://github.com/facebookresearch/sam2) · [samgeo](https://github.com/opengeos/segment-geospatial) · [MPS note](https://github.com/facebookresearch/sam2/blob/main/demo/backend/server/inference/predictor.py) | VERIFIED |
| WGISD / grape Mask R-CNN examples | **Grape-bunch** masks in ground views, not overhead plant-canopy masks; poor direct transfer target. | Licence for these particular images/weights not established in this sweep. | [Example project](https://github.com/LesleyDing/Grape-Segmentation) | VERIFIED |

No **downloadable, vineyard-trained per-plant aerial instance checkpoint** was verified. Riseholme’s mention of YOLOv11 training does not link a released checkpoint on the record.

## 4. Waste boxes

| Item | What it gives us | Licence | Link | Status |
|---|---|---|---|---|
| DroneWaste | 4,993 images, 5,135 polygon/box annotations, 20 materials, 17 sites. **`images.tar.gz` (3.9 GB) and COCO JSON are in record 17045559.** The paper reports mostly ~2 cm/px, some up to 2.8 cm/px, close to Sireț’s scale. Training scripts exist; released trained weights were not identified. | **MIT for code**, read in [code LICENSE](https://github.com/lucamora/dronewaste/blob/main/LICENSE). Dataset **CC BY 4.0 is reported by a secondary catalogue**, but the opened Zenodo Rights field was blank: licence **UNVERIFIED**. | [Dataset](https://zenodo.org/records/17045559) · [paper/GSD excerpt](https://zenodo.org/records/17288038/files/DroneWaste_preprint.pdf?download=1) · [licence report](https://agamiko.github.io/waste-datasets-review/) | VERIFIED for archive/count; UNVERIFIED for GSD/licence |
| UAVVaste | 772 images, 3,718 annotations; 3.0 GB ZIP. GSD was not established. A separate TrUoD repository lists UAVVaste-trained detection weights, but links lead to Baidu and its licence is not stated. | **Apache-2.0** in UAVVaste repository [LICENSE](https://github.com/PUTvision/UAVVaste/blob/main/LICENSE); Zenodo ZIP rights were not independently displayed. TrUoD licence unclear. | [Dataset](https://zenodo.org/records/8214061) · [weights listing](https://github.com/AbitGo/TrUoD) | VERIFIED |
| AerialWaste / TACO | AerialWaste has over 10,000 mixed-source, mostly scene-level waste images; its released model classifies dump scenes at 800×800, not small-object boxes. TACO’s 1,500 images/4,784 annotations are ground-view litter. | AerialWaste dataset says **CC BY**, with Google imagery subject to Google terms, on [record](https://zenodo.org/records/7991872); model repository says **CC BY-NC-ND 4.0** on [README](https://github.com/nahitorres/aerialwaste-model). TACO repository says **MIT** on [GitHub](https://github.com/pedropro/TACO); individual hosted photos need their own rights check. | [AerialWaste](https://zenodo.org/records/7991872) · [TACO](https://github.com/pedropro/TACO) | VERIFIED |

**Practical inference:** crop tiles at native resolution around plausible waste, retain background negatives from roads/soil, and review boxes. A full 2048→640 shrink would reduce already small objects; no Sireț waste recall has been measured.

## 5. Sentinel-2 and Sentinel-1

| Item | What it gives us | Licence | Link | Status |
|---|---|---|---|---|
| Phenology | An orchard/vineyard study fitted vegetation-index seasonal curves and classified phenology metrics; its sites were Mediterranean, **not Eastern Europe**. Transfer to Moldova is unverified. | Paper; Sentinel data governed by the **Copernicus Sentinel Data Legal Notice**, read [here](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice). | [Study](https://www.mdpi.com/2072-4292/15/9/2420/xml) | VERIFIED source; Moldova transfer UNVERIFIED |
| Anonymous catalogues | Earth Search lists `sentinel-2-c1-l2a` and `sentinel-1-grd` at `https://earth-search.aws.element84.com/v1`. CDSE product STAC documents `sentinel-2-l2a` and `sentinel-1-grd` at `https://stac.dataspace.copernicus.eu/v1`. Planetary Computer uses `sentinel-2-l2a` and `sentinel-1-rtc`; its Sentinel-1 RTC asset access has required an account/API key. Catalogue pages were opened; a Sireț asset download was **not** tested. | **Copernicus Sentinel Data Legal Notice** for source imagery; provider service terms should also be checked. | [Earth Search](https://github.com/Element84/earth-search) · [CDSE STAC](https://documentation.v1.dataspace.copernicus.eu/APIs/STAC.html) · [Planetary Computer S1 access](https://github.com/microsoft/PlanetaryComputer/discussions/167) | VERIFIED documentation; live anonymous download UNVERIFIED |
| Existing Moldova map | EEA’s CORINE 2018/2024 Moldova service includes vineyard class 221, but 25 ha mapping units make it only a coarse block prior. | **EEA copyright** appears in [service metadata](https://copernicus.discomap.eea.europa.eu/ArcGIS/rest/services/ENI_CLC_Pilot/ENI_CLC_Pilot_2024/MapServer); exact reuse terms not verified. | [Map service](https://copernicus.discomap.eea.europa.eu/ArcGIS/rest/services/ENI_CLC_Pilot/ENI_CLC_Pilot_2024/MapServer) · [class 221](https://land.copernicus.eu/content/corine-land-cover-nomenclature-guidelines/html/index-clc-221.html) | VERIFIED |

At 10 m, use time series for **block plausibility, broad vigour and multi-year change**; inter-row cover, individual vines and gaps need the UAV image. This resolution-based inference follows the [Sentinel-2 band specification](https://sentinels.copernicus.eu/documents/247904/0/Sentinel-2-product-specifications-document-V14-9.pdf). No validated Sireț vineyard-versus-orchard NDVI signature was found.

## 6. Moldovan and grower data

| Item | What it gives us | Licence | Link | Status |
|---|---|---|---|---|
| ONVV vineyard register | Parcel code, holder/owner, location, area, variety, planting year, planting scheme and trellis are explicitly listed. A government page describes filters by destination, variety, area and locality. No public parcel-geometry API was verified. Missing-vine counts and tractor GNSS tracks are **not established register fields**. | Government pages; reuse licence not stated on the opened material. | [ONVV/ACSA poster](https://maia.gov.md/sites/default/files/PressReleas/Documente%20Atasate/3.%20Poster_RO.pdf) · [MAIA description](https://maia.gov.md/ro/content/4404) | VERIFIED |
| Cadastre and AIPA | Moldova has an INDS map viewer and e-Cadastru public-information functions; AIPA publishes subsidy beneficiaries and vineyard-investment forms. These do not establish accessible vine-row geometry. “ANCRA” appears to be a historical agency name; current portal identifies the Agency of Geodesy, Cartography and Cadastre. | Reuse/API terms not verified. | [INDS viewer](https://geoportalinds.gov.md/viewer/?auto=true) · [e-Cadastru](https://www.ipcbi.gov.md/ro/e-cadastru) · [AIPA](https://aipa.gov.md/informatii-publice-despre-subventii/lista-beneficiarilor/) | VERIFIED |
| OSM near Sireț | `landuse=vineyard` is a defined OSM tag. **Actual coverage at 47.12, 28.71 was not queried**, so do not rely on it for recall. | **ODbL 1.0**, read in [OSM licence text](https://wiki.openstreetmap.org/wiki/Open_Database_License/ODbL-1.0.txt). | [Tag definition](https://wiki.openstreetmap.org/wiki/Tag%3Alanduse%3Dvineyard) | VERIFIED tag/licence; local coverage UNVERIFIED |

## 7. Closed inspection walk

| Item | What it gives us | Licence | Link | Status |
|---|---|---|---|---|
| Formulation | Represent walkable inter-row axes and permitted road crossings as a graph. Give each gap/waste target all graph points within 2 m; choose at least one access point per target and a minimum-length closed walk. This is a **covering-tour/group-TSP formulation inferred for this task**. Orchard literature treats rows and entry ends as constrained route choices. | Paper; no software licence applies. | [Orchard path-planning paper](https://www.cs.cmu.edu/~cvalles/papers/automatingOrchards.pdf) | VERIFIED paper; task formulation is inference |
| Solvers | NetworkX for shortest-path distances and a first approximate tour; OR-Tools for a routed distance matrix. If selecting between multiple access points per target, add a covering choice before or within optimisation. | **BSD-3-Clause**, read in [NetworkX LICENSE](https://github.com/networkx/networkx/blob/main/LICENSE.txt); **Apache-2.0**, read in [OR-Tools repository](https://github.com/google/or-tools). | [NetworkX TSP](https://networkx.org/documentation/stable/reference/algorithms/approximation.html) · [OR-Tools TSP](https://developers.google.com/optimization/routing/tsp) | VERIFIED |
| LKH-3 | Fast tour heuristic, but its README says research use and author-reserved rights; avoid assuming an open-source grant. | **Research-use, rights reserved**, read in README. | [LKH-3 README](https://github.com/c4v4/LKH3/blob/main/README.md) | VERIFIED |

## 8. Two-label-tile regime

| Item | What it gives us | Licence | Link | Status |
|---|---|---|---|---|
| Geometry-led pseudo-labels | Generate high-confidence rows first; train or tune only against reviewed vineyard positives and non-vineyard negatives. RoWeeder demonstrates row-derived pseudo-ground truth, while a vineyard weak-supervision study compares heuristic and transformer pseudo-labels. | RoWeeder **MIT**, read on [repository](https://github.com/pasqualedem/RoWeeder); paper code/licence not identified. | [RoWeeder](https://github.com/pasqualedem/RoWeeder) · [vineyard study](https://www.sciencedirect.com/science/article/pii/S1877050926006526) | VERIFIED repository; vineyard paper UNVERIFIED — publisher blocked |
| RGB indices | Compare ExG = `2g−r−b`, ExGR = `3g−2.4r−b`, VARI = `(g−r)/(g+r−b)` on **normalised** RGB; set thresholds from the reference tiles. Colour/illumination shifts and grass are reasons to review masks. | Paper; formulas read in open article. | [RGB index table](https://pmc.ncbi.nlm.nih.gov/articles/PMC12117064/) · [vineyard grass caveat](https://www.mdpi.com/2072-4292/9/3/268) | VERIFIED |
| GSD/season adaptation | GRowSeg’s stated optimum is finer than Sireț’s 2.5 cm/px. Rescaling and rotated inference are documented options; whether either improves Sireț requires the two-tile check. | **MIT**, read on model card. | [GRowSeg model card](https://huggingface.co/links-ads/gaia-growseg) | VERIFIED options; benefit UNVERIFIED |

## Dead ends checked

- [Riseholme’s record](https://zenodo.org/records/19234907): useful labels, but no displayed licence or downloadable trained checkpoint; canopy-class granularity needs inspection. **VERIFIED.**
- [DroneWaste record 17288038](https://zenodo.org/records/17288038): paper PDF only; [17045559](https://zenodo.org/records/17045559) contains the images. **VERIFIED.**
- [GRowSeg weight listing](https://huggingface.co/links-ads/gaia-growseg/tree/main): file exists, but access is gated. **VERIFIED.**
- [AerialWaste model](https://github.com/nahitorres/aerialwaste-model): released weights classify dump scenes, not litter boxes; model licence is CC BY-NC-ND 4.0. **VERIFIED.**
- [TACO](https://github.com/pedropro/TACO): ground-view litter with hosted-image rights to check individually; weak direct match to overhead 2.5 cm/px waste. **VERIFIED source; transfer judgement is inference.**
- [LKH-3](https://github.com/c4v4/LKH3/blob/main/README.md): research-use, rights-reserved terms. **VERIFIED.**