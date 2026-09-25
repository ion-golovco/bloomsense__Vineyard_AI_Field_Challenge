# Sireț3 vineyard mapping: research and architecture decision brief

25 September 2026. This is a research and data audit, not a trained model or a claim of score. It uses the released challenge brief, annotation rules, two example tiles, the existing local exploratory notes in `RESEARCH.md`, and the linked primary papers below. Paper findings are evidence from their own sites and sensors; transfer to Sireț3 remains a hypothesis until tested.

## 1. What the task actually scores

The deliverable is not a binary vineyard map. It needs one polygon **per visible plant canopy**, one polyline **per physical vine row**, inter-row polygons excluding canopy and headlands, block/row IDs stable across tile edges, cover and row-structure attributes, waste boxes, and a legal closed route. Canopy is 25% of the score, route 25%, axes/attributes/IDs 15%, engineering 15%, measurements 10%, waste 10%. Row axes must match bidirectionally over at least 80% of their length within 0.4 m. A 5 m visible gap changes `row_structure` but does not create another row. Closely parallel *physical* vine rows must be separate axes even if a coarse image looks like one band. All final spatial outputs use EPSG:32635; Marcaj receives unchanged GeoTIFF tiles and pixel-coordinate CVAT 1.1 shapes. The full organizer annotation rules, not inferred geometry, govern ambiguous objects.

## 2. What the released Sireț3 data can establish now

| Observation | Direct evidence | Consequence |
|---|---|---|
| 311 tiles, 2048 × 2048 RGB pixels, 0.025 m/px, each 51.2 m across, EPSG:32635. The study area is 81.5 ha; the larger ~145 ha figure describes the source mosaic. | Release and raster inventory recorded in `SPEC.md` section 8. | Native-scale detail matters; a full tile resized to 640 px becomes 0.08 m/px. |
| The two organizer examples contain 399 + 251 canopy polygons, 25 + 26 row axes, and 24 + 25 inter-row polygons. Median canopy areas are 0.467 and 0.572 m². | Independently recounted from `05_examples/siret3_examples_cvat.zip` XML with polygon areas converted using 0.025 m/px. | One mask for a whole row fails the scored plant-instance task. Full-tile 640 px inference would reduce a typical ~0.7 m plant to roughly 9 pixels across. |
| The existing local exploratory FFT probe reports ~2.5–2.8 m row spacing on the examples and strong 2–3.6 m periodicity relative to 3.8–7 m on selected orchards. | `RESEARCH.md` section 1. It is a useful lead, but its reported rankings and window scores have no saved independent holdout labels. | Periodicity should be a candidate feature, not a certified vineyard classifier or a hard exclusion rule. |
| Vineyard patches occupy portions of many tiles; the overview includes orchards, dense residential land, narrow parcels, shadows, and planting separated by roads. | Organizer `overview.png`; two annotated previews. | Train/evaluate a **spatial vineyard likelihood map**, not only a whole-tile yes/no label. Include orchard and parallel crop rows as explicit negatives. |
| A quick scan of all 311 tiles by downsampled RGB greenness finds several nearly all-green tiles. A visual check of six such tiles shows irregular trees, scrub, and grass alongside orderly planting. | Read each source GeoTIFF at 256 px width, compute normalized ExG, then inspect a six-tile 512 px contact sheet. This is qualitative; no vineyard labels were assigned. | A greenness threshold alone is unsuitable even for the cheap proposal stage. A model must distinguish spatial organization and plant form. |
| The two examples are mostly visible straight rows and do not label the hard green-interrow cases across the site. | Organizer examples and annotation inventory. | They validate format and scale, but cannot estimate green-interrow recall or full-area quality. |

The released file describes 3.52 cm/px for the original UAV mosaic; the supplied scored GeoTIFF tiles are 2.5 cm/px. Use each actual raster transform in processing and write the distinction clearly in the submission.

## 3. Synthesis from the literature

1. **Geometry is the strongest vineyard cue when grass makes color ambiguous.** The older Fourier, Gabor, Hough, and line-profile studies exploit narrow, repeated, parallel vine lines. Comba et al. explicitly tested dense grassy inter-rows. Yet those cues fail when rows are sparse, disrupted, shaded, or highly irregular. Use them as interpretable evidence alongside a learned model, with a low-confidence review path.
2. **Vine canopy and inter-row cover are different classes even when both are green.** Several UAV studies find RGB spectral thresholds confuse grass and vines. A model should see row-scale context, canopy texture/shadow, and likely axis position; a row-constrained local segmentation may be more reliable than thresholding greenness alone. If height/DSM can be recreated from source photos it could help, but the challenge supplies an RGB orthomosaic, not a height raster; do not design around unavailable bands.
3. **A vineyard gate cannot be allowed to erase the target.** The false-canopy penalty on non-vineyard tiles calls for high precision, but a false-negative gate destroys canopy, row, and route scores. A calibrated, high-recall candidate map with an uncertainty tier and a second rescue pass is safer than a fixed whole-tile classifier threshold.
4. **Use two scales.** A 0.1–0.2 m/px overview gives ~25–50 m context for parcel and row periodicity. Plant instances, waste, and the 0.4 m row-axis tolerance need 2.5 cm/px crops with overlap. The sliced-inference literature supports this for small objects, but its published AP gains are on other aerial datasets, not Sireț3.
5. **Semantic rows are not yet row identities.** A network can propose row pixels/centerlines. A geometric stage must retain multiple nearby peaks, assign each physical line its own identity, bridge real gaps, and reconcile segments across tiles using orientation, offset, endpoints, and canopy support. Do this in projected coordinates over a multi-tile neighborhood. Never join through a road merely because two segments are collinear.
6. **Canopy instance count needs its own representation.** A semantic canopy map can locate plant material; instance segmentation or a center/seed plus boundary head is needed to split touching plants. Fallback splits can use local in-row spacing inferred from visible plants, stakes, or trunks, with confidence recorded. Missing plants cannot be proven from a canopy void alone when neighboring foliage fills it.
7. **Satellite phenology is context, not geometry.** Sentinel-2 time series can help distinguish permanent crop types at parcel scale. A 10 m mixed pixel cannot delineate a 0.5 m² canopy, a 5 m gap, or an individual row, and green interrows complicate its signal. The local exploratory Sentinel results in `RESEARCH.md` are not independently labeled against non-vineyard crops; keep this optional and do not let it override drone evidence.
8. **The route is a separate spatial optimization problem.** Construct legal walking space from reviewed inter-row polygons plus authorized passages minus forbidden areas and canopies. Build a connected graph, snap only reachable target points within the allowed tolerance, solve a shortest closed tour on graph distances, then validate the continuous LineString against the legal space. The organizer route criterion is zero if more than 2% lies outside allowed space or the route fails to close.

## 4. Architecture to test, with choices still open

```text
unchanged GeoTIFFs + reference rules + passage/start/forbidden GeoJSON
  -> grid/CRS inventory, no-data and source-quality map
  -> coarse multi-scale proposal map: P(vineyard), orientation, spacing, uncertainty
       candidates from learned local segmentation/classification + FFT/line texture
       include high-recall rescue over low-confidence and boundary areas
  -> native 2.5 cm/px overlapping crops within candidates + guard band
       (A) canopy pixels + plant-instance seeds/masks
       (B) row-center evidence and local direction; preserve close parallel peaks
       (C) inter-row cover pixels; waste detection on vineyard + surrounding zone
  -> projected-space reconciliation over neighboring tiles
       block boundaries, row graph and global IDs, instance deduplication
       canopy polygon / inter-row polygon exclusion, gap points, attributes
  -> uncertainty-ranked Marcaj pre-annotations -> human correction -> export
  -> measurements + constrained routing + web map + verified submission files
```

Treat this as a **candidate architecture**, not a selected model stack. The gating stage should emit a continuous spatial probability and an uncertainty flag. A tile can be entirely empty, partly planted, or contain more than one block; tile labels are only summaries for scheduling work. The graph stage should operate on global projected coordinates, so cross-tile matching does not depend on image names. For paired lines, use non-maximum suppression at a physical distance below the smallest plausible true pair separation *only after measuring that separation in Sireț3*. A fixed one-line-per-band or one-line-per-2.5 m lattice assumption is not justified.

## 5. Experiments that decide the architecture

Run these in order, before committing to a model. The two organizer examples have already informed exploratory thresholds, so treat them as development examples, **not** an independent test set. Reserve separate geography for honest validation when corrected Marcaj annotations can be exported. Manual correction of Sireț3 belongs only in Marcaj; outside it, use the organizer examples and external open training data.

| Experiment | Comparison | Decision signal |
|---|---|---|
| A. Spatial candidate recall | Local FFT/Gabor and line-density baseline vs small RGB segmentation model vs their union. Sample true vineyard, orchard, green-interrow, fragmented, shadow, tile-edge, and no-vineyard windows. | Window/block recall at a fixed candidate-area budget; false-negative map by subtype. Do not adopt a hard gate until green-interrow and tile-edge misses are acceptably rare. |
| B. Native resolution and context | 512/768/1024 px crops at 2.5 cm/px, each with overlap and a broader low-resolution context input, vs full 2048 resized to 640/1024. | Canopy IoU, 1:1 instance F1 at IoU ≥0.5, row-axis match at 0.4 m, time/GPU memory. All metrics stratified by bare vs grassy interrow and touching vs separated plants. |
| C. Plant separation | Instance model (e.g. light YOLO-seg/Mask R-CNN) vs semantic canopy + center seeds/watershed/row-constrained split. | Instance F1 and canopy IoU jointly, especially older touching vines; compare counts and area bias. Confirm external dataset's `vineyard` annotations actually mean one plant before training. |
| D. Row extraction and pairing | Classical Hough/profile, learned centerline, and fused evidence with global graph linking. | Bidirectional 80%/0.4 m axis F1; separate recall for close parallel pairs; cross-tile identity breaks, accidental merges, and 5 m gap continuity. |
| E. Full pipeline | First run over all 311 tiles, then manual review of uncertainty and geographic seams in Marcaj. | Full-run time/hardware, every tile imported, reviewed corrected objects, final count/area/length error on available examples, legal route checks. No claimed hidden score. |

## 6. Primary paper ledger (31 studies)

The entries summarize what each paper tested, **not** Sireț3 performance. Links lead to the publisher, author repository, or paper preprint. The 2026 review is included as synthesis but does not count toward the 31 empirical/method papers below.

### Vineyard and crop discrimination

1. [Delenne et al., *Vine plot detection in aerial images using Fourier analysis* (2006)](https://www.isprs.org/proceedings/xxxvi/4-c42/Papers/04_Automated%20classification%20Agriculture/OBIA2006_Delenne_et_al.pdf): recursive FFT detects plot periodicity, row orientation, and spacing; directly relevant to a cheap spatial proposal map.
2. [Comba et al., *Vineyard detection from unmanned aerial systems images* (2015)](https://iris.unito.it/handle/2318/1523886): dynamic segmentation, Hough clustering, and total least squares detect vine rows despite dense inter-row grass/shadows in their study; useful classical row baseline.
3. [Weiss & Baret, *Using 3D Point Clouds Derived from UAV RGB Imagery to Describe Vineyard 3D Macro-Structure* (2017)](https://www.mdpi.com/2072-4292/9/2/111): cumulative row profiles and point-cloud height reduce grass interference; height is unavailable in the released tile package.
4. [Poblete-Echeverría et al., *Detection and Segmentation of Vine Canopy in Ultra-High Spatial Resolution RGB Imagery* (2017)](https://www.mdpi.com/2072-4292/9/3/268): compares RGB indices, clustering, ANN and RF; notes grass/vine spectral similarity and disrupted periodic texture.
5. [Primicerio et al., *Individual plant definition and missing plant characterization in vineyards* (2017)](https://www.tandfonline.com/doi/full/10.1080/22797254.2017.1308234): row-first individual plant analysis and missing-plant model; canopy overlap can hide a missing vine in top-down RGB.
6. [Pádua et al., *Multi-Temporal Vineyard Monitoring through UAV-Based RGB Imagery* (2018)](https://www.mdpi.com/2072-4292/10/12/1907): RGB plus derived surface models separate canopy from inter-row; relevant if height data later exists.
7. [Cinat et al., *Comparison of Unsupervised Algorithms for Vineyard Canopy Segmentation* (2019)](https://www.mdpi.com/2072-4292/11/9/1023): RGB HSV, DEM, and clustering have different over/under-segmentation biases across seasons.
8. [Pádua et al., *Individual Grapevine Analysis in a Multi-Temporal Context* (2020)](https://www.mdpi.com/2072-4292/12/1/139): maps individual plants and canopy gaps using RGB across vineyards/seasons; gap inference requires visible structure.
9. [Jones et al., *Impact of Pan-Sharpening and Spectral Resolution on Vineyard Segmentation* (2020)](https://www.mdpi.com/2072-4292/12/6/934): parcel classification degrades with mixed inter-row pixels and low row visibility; supports native RGB detail.
10. [Barros et al., *Multispectral vineyard segmentation: A deep learning comparison study* (2022)](https://www.sciencedirect.com/science/article/abs/pii/S0168169922000990): U-Net/SegNet variants outperform classical methods in their vine segmentation data; high-resolution RGB matched or beat lower-resolution multispectral there.
11. [Abubakar et al., *Delineation of Orchard, Vineyard, and Olive Trees Based on Phenology Metrics* (2023)](https://www.mdpi.com/2072-4292/15/9/2420): Sentinel-2 phenology can separate woody crops at parcel scale, with mixed-pixel limits.
12. [De Petris et al., *Assessing mixed-pixels effects in vineyard mapping from Satellite* (2024)](https://www.sciencedirect.com/science/article/pii/S0168169924004836): UAV-derived vine fraction helps unmix Sentinel-2 NDVI; evidence against using raw satellite NDVI for individual vines.
13. [Leite et al., *Deep-learning Grapevine Segmentation in UAV Imagery Across Different Vineyard Environments* (2025)](https://doi.org/10.1007/s41064-025-00367-6): compares U-Net, FPN, PSPNet on varied vineyard environments; FPN variants perform well with inter-row vegetation in the reported study.
14. [Ghiglieno et al., *Vineyard Groundcover Biodiversity: Using Deep Learning to Differentiate Cover Crop Communities* (2025)](https://www.mdpi.com/2624-7402/7/12/434): multiclass RGB segmentation explicitly separates vine canopy, bare soil, and herbaceous cover.
15. [Comparison of different computer vision methods for vineyard canopy detection using UAV multispectral images (2024)](https://www.sciencedirect.com/science/article/pii/S0168169924006689): Mask R-CNN/U-Net vs OBIA/clustering; learned canopy detection better in the tested conditions, with shadow/background analysis.
16. [Weakly Supervised Semantic Segmentation for UAV-based Vineyard Monitoring (2026)](https://www.sciencedirect.com/science/article/pii/S1877050926006526): CRF-refined spectral pseudo-labels beat DINO token clustering on its vine-row study; a warning against assuming generic foundation features are best without validation.
17. [Classification and Phenological Stage Monitoring of Grape Crop using Sentinel-1 and Sentinel-2 Time Series (2025)](https://isprs-annals.copernicus.org/articles/X-5-W2-2025/397/2025/): satellite crop/non-crop classification, relevant only as optional parcel context.
18. [Turkoglu et al., *Crop mapping from image time series: deep learning with multi-scale label hierarchies* (2021)](https://arxiv.org/abs/2102.08820): hierarchical crop labels improve rare fine-grained classes in Sentinel-2 crop mapping; motivates explicit orchard/vine/other negatives.
19. [Qin et al., *On the Transferability of Learning Models for Semantic Segmentation for Remote Sensing Data* (2023)](https://arxiv.org/abs/2310.10490): source-to-target performance varies across remote-sensing domains; external weights need local validation.
20. [A New Method for Crop Row Detection Using Unmanned Aerial Vehicle Images (2021)](https://www.mdpi.com/2072-4292/13/17/3526): analyzes limitations of Hough peak selection and alternative row fitting; relevant to paired-line preservation.
21. [Silva et al., *Deep learning-based crop row detection for infield navigation* (2024)](https://onlinelibrary.wiley.com/doi/abs/10.1002/rob.22238): U-Net crop-row mask plus center extraction; a method analogue, not vineyard aerial validation.

### Resolution, instance segmentation, and adaptation

22. [Ronneberger et al., *U-Net* (2015)](https://arxiv.org/abs/1505.04597): efficient dense segmentation baseline for limited labels.
23. [Xie et al., *SegFormer* (2021)](https://arxiv.org/abs/2105.15203): multi-scale semantic segmentation architecture; model size must be weighed against available hardware/time.
24. [Cheng et al., *Mask2Former* (2022)](https://arxiv.org/abs/2112.01527): a unified instance/semantic mask architecture, a candidate for individual canopies if compute allows.
25. [Akyon et al., *Slicing Aided Hyper Inference and Fine-tuning* (2022)](https://arxiv.org/abs/2202.06934): overlapping slices improve small-object detection on VisDrone/xView; supports native-resolution crop experiments.
26. [Ravi et al., *SAM 2* (2024)](https://arxiv.org/abs/2408.00714): promptable masks can assist corrections/proposals; no evidence it identifies grapevine semantics zero-shot.
27. [Oquab et al., *DINOv2* (2023)](https://arxiv.org/abs/2304.07193): generic visual representation; test only against simpler baselines, especially given vineyard pseudo-label results above.
28. [Cheng et al., *Boundary IoU* (2021)](https://arxiv.org/abs/2103.16562): boundary-specific evaluation; optional diagnosis beyond the organizer's canopy IoU and instance F1.

### Waste and route

29. [Morandini et al., *DroneWaste* preprint (2025)](https://zenodo.org/records/17288038): aerial waste masks/boxes and detection baselines; check scale, object definitions, and license against Sireț3 before reuse.
30. [UAVWaste: COCO-like dataset and effective waste detection in aerial images (2020)](https://docs.mlinpl.org/virtual-event/2020/posters/34-UAVVaste_COCOlike_dataset_and_effective_waste_detection_in_aerial_images.pdf): additional aerial-waste source; needs scale/domain check.
31. [Global Path Planning for Autonomous Vehicles in Orchards and Vineyards (2024)](https://www.research.ed.ac.uk/en/publications/global-path-planning-for-autonomous-vehicles-in-orchards-and-vine/): graph and hierarchical A* for crop-row navigation; our human route is less kinematically constrained but still needs legal graph connectivity.

Supporting synthesis: [Costa et al., systematic review of vineyard area identification (2026)](https://www.sciencedirect.com/science/article/pii/S2772375526000365) reviewed 108 sources/80 empirical studies and identifies weak validation and model portability as recurring gaps. [Oksanen, agricultural coverage path planning (2009)](https://doi.org/10.1002/rob.20300) and [Höffmann et al., guidance-track review (2024)](https://onlinelibrary.wiley.com/doi/full/10.1002/rob.22286) provide route background; the challenge is target visitation with legal access, not full-area machine coverage.

## 7. Data and model limitations to settle next

- Riseholme is an [open 855-image, 40,215-annotation COCO source](https://zenodo.org/records/19234907) over three UK seasons, with `vineyard` and `vine_row` masks. Inspect actual polygons and GSD: its `vineyard` class is described as canopy but may not be one polygon per plant. Do not map class names to the challenge blindly.
- The [GRowSeg model card](https://huggingface.co/links-ads/gaia-growseg) describes a SegFormer-B5 row mask using 512 px sliding crops and best GSD 1–1.5 cm/px. Sireț3 is 2.5 cm/px; its weights are reported as gated in the existing local research, so availability and transfer need a fresh check before planning around it.
- The two reference tiles cannot certify performance on grassy rows, orchards, and paired lines. Use corrected Marcaj jobs or external labels for these conditions, and reserve some geographic blocks that do not influence model or threshold selection for validation. Do not call the two already-inspected examples held out.
- Final model choice should follow experiments A–D. The initial preference is a small learned spatial proposer plus FFT/row-geometry evidence, then native-resolution canopy instances and global line reconciliation. The empirical result may favor a simpler or larger model.
