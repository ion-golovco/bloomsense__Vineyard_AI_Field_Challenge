# EU alignment and business plan evidence

BloomSense, Marcaj Vineyard AI Field Challenge, DeepTech GigaHack 2026. Written 26 September 2026 as the evidence behind the EU Alignment Scorecard notes.

Every line is marked **Built** (exists in this repository today), **Plan** (a decision, not yet done) or **Estimate** (team arithmetic or assumption, not measured). Figures without a source link come from this repository's own runs on the Sireț3 survey; the [README](../README.md) and the [code review](CODE_REVIEW_2026-09-26.md) give their limits.

| Section | Scorecard criteria |
|---|---|
| [1. Green Deal fit](#1-green-deal-fit) | A1, D1 |
| [2. Lifecycle and footprint](#2-lifecycle-and-footprint) | A2, A3 |
| [3. Do no significant harm](#3-do-no-significant-harm) | A4 |
| [4. Digital and AI](#4-digital-and-ai) | B1, B2, B4 |
| [5. Threat model](#5-threat-model) | B3 |
| [6. Data protection](#6-data-protection) | C1 to C4 |
| [7. Pilot protocol](#7-pilot-protocol) | D2, D3, D4 |
| [8. Market](#8-market) | D2, E1 to E4 |
| [9. Funding pathway](#9-funding-pathway) | E5 |

## 1. Green Deal fit

**This helps the EU reach the Farm to Fork goal of a resource-efficient, sustainable food system**, and the CAP wine-sector aim of restructuring and replanting vineyards, by sending people only to the vines that need a look.

| Evidence on Sireț3 | Figure | Status |
|---|---|---|
| Rows found and measured | 603 rows, 46.5 km of rows | Built |
| Targeted inspection route | 9.5 km, visits 127 of 179 targets, 0.62% outside inter-rows | Built, on predicted geometry; to be regenerated from the corrected Marcaj export |
| Walking needed to reach the flagged spots | 9.5 km instead of about 46.5 km, about 80% less | Estimate |
| Replanting input | every candidate gap of 5 m or more, with row ID, endpoints and confidence | Built |
| Soil cover (EU Soil Strategy) | `interrow_cover` per tile: bare soil, mixed or vegetation | Built |

The 80% is a **route-length ratio**: our route length divided by total row length, which stands in for walking every row. It is not a measured labour study. A pilot (section 7) is where the time saving gets measured.

## 2. Lifecycle and footprint

| Stage | What happens | Footprint driver | Design choice | Status |
|---|---|---|---|---|
| Flight | One RGB drone survey, 2.5 cm/px, by a certified operator | Flight energy, noise, travel to site | **Reuse one survey** for canopies, rows, inter-rows, ground cover, waste, gaps and the route, instead of one flight per question. Free Sentinel-2 archive for context instead of extra flights or field sensors | Built |
| Transfer | Tiles in ZIP parts | Data volume | Tiles CRC-checked once and kept read-only, never re-downloaded | Built |
| Processing | One laptop, no cloud GPU | Compute energy | Small model, coarse search first, caches (below) | Built |
| Correction | People correct all annotations in Marcaj | Human time | Pre-annotations cut drawing time; nothing is redrawn from scratch | Built |
| Storage | Local disk: 368 MB tiles, 1.4 GB organizer package, 2.9 GB rebuildable caches, 7.9 MB weights | Disk | Caches are derived and can be deleted and rebuilt; only derived geometry is served to the map | Built |
| Deletion | Delete caches after the season; delete raw imagery at contract end unless the grower asks to keep it | Disk, risk | Written retention rule, manual today | Plan |

Compute footprint:

| Item | Figure | Status |
|---|---|---|
| Full 311-tile prediction | 215 s on one Apple M4 Pro laptop, 3.9 GB peak memory (historical run) | Built, measured |
| Later CPU-only review run | 622 s including scene build and judging | Built, measured |
| Energy per full run | about 2.4 to 4.8 Wh for 215 s at an assumed 40 to 80 W laptop draw | Estimate, power not measured |
| Model | 2.0 M-parameter U-Net, 7.9 MB | Built |
| Inference services | No LLM, no paid API, no cloud GPU | Built |

Mitigations already in the design: SAM 2.1 was tested and left out in favour of the small U-Net; plot search runs on a 0.4 m grid and full 2.5 cm resolution only on tiles crossed by rows; mosaic and layers are cached. Plan: key caches by input hashes so they rebuild only when inputs change.

## 3. Do no significant harm

What breaks or pollutes if this runs on 1,000 farms:

| Risk | Who is harmed | Mitigation | Status |
|---|---|---|---|
| More drone flights (noise, wildlife and neighbour disturbance, flight energy) | Birds and wildlife, neighbours | One survey reused for every output; satellite archive for context; flights only by certified operators within EU drone rules | Built (reuse), Plan (operator rule) |
| False gap flags lead to needless visits or replanting | Grower (cost), soil (extra traffic), vines bought for no reason | Points are inspection candidates, never treatment or replanting advice; each carries a heuristic confidence and the map filters by it; README states the confidence is not a calibrated probability; replant only after a person confirms on site | Built |
| Missed gaps (false negatives) | Grower (lost yield) | Route coverage and skipped targets are reported, not hidden; pilot measures recall | Built (reporting), Plan (pilot) |
| Satellite zones misread as disease or water stress | Grower, input use (needless spraying) | Sentinel-2 zones are a map layer only, never route targets; documented as possibly floor cover, tillage or young vines, not a diagnosis | Built |
| Route cuts through canopy or forbidden areas | Vines, field workers | Export refuses a route over 2% outside inter-rows and passages; forbidden and canopy crossings are reported | Built (2% rule), Plan (reject forbidden crossings) |
| Rebound: cheaper scouting leads to more spraying | Soil, water, biodiversity | We map missing vines, not disease, and give no treatment advice | Built (scope) |
| Data storage growth (years of surveys) | Energy, privacy | Rebuildable caches deleted after the season; retention rule in section 6 | Plan |
| Waste detection misses litter | Environment | Only 2 conservative boxes; scope limited to inter-rows is documented | Built (documented limit) |

## 4. Digital and AI

**Where AI is:** a 2.0 M-parameter U-Net filters canopy pixels. Rules draw rows, inter-rows, gap points and the route. Weights are published in the repository (`models/canopy_net.pt`, SHA-256 `5c32f7d45f6cfe98b0e03bd2baaf7972628c610a24026a222a0663244db797e0`).

**AI Act rough classification: minimal risk** (our reading, to confirm with an adviser). Mapping vine canopies and planning a walk is not a prohibited practice and not an Annex III use (no biometrics, critical infrastructure, education, employment, credit, law enforcement or migration). It is not a safety component of a regulated product and it does not interact with people or generate content. If worker GPS tracking or performance scoring were added, the employment category would have to be checked again.

**Human oversight built in:** people check and correct every annotation in Marcaj before measuring and routing; each gap point shows its confidence and the map filters by it (all, 0.5, 0.7); predictions are tagged `source=prediction` and excluded from measurements; limits are written in the README.

ALTAI-style self-check:

| ALTAI area | What we do | Status |
|---|---|---|
| Human agency and oversight | Human correction in Marcaj; confidence filter; no automatic action | Built |
| Technical robustness and safety | Judge against organizer reference tiles: canopy 0.854, row axes 0.970, attributes 0.974, measurements 0.890; route legality check | Built (2 tiles only, not a holdout) |
| Privacy and data governance | Section 6 | Built / Plan |
| Transparency | Published weights and hash, training recipe, known failure cases (young vines, grassed rows, shadows) in README | Built |
| Fairness | Risk: works less well on young or grassed vineyards, so those growers get worse maps. Pilot on a different vineyard type | Plan |
| Societal and environmental well-being | Sections 1 to 3 | Built / Plan |
| Accountability | Lab verdicts recorded in `data/review/verdicts.json`; every claim tied to a judge number | Built |

**Digital Europe capacity:** AI (computer vision on drone imagery) and data (Copernicus Sentinel-2). Roadmap use of EU resources, all **Plan**, none contacted yet:

| EU resource | What we would use it for | When |
|---|---|---|
| Copernicus Data Space Ecosystem | Move Sentinel-2 access from Earth Search on AWS to the EU's own data space | Before the pilot |
| agrifoodTEF, or an EDIH from the official catalogue | Field test of gap precision and time saved (section 7) with a partner vineyard | Pilot season |
| EuroHPC | Retrain the canopy model on many vineyards and seasons if a laptop is no longer enough | After the pilot, only if needed |

## 5. Threat model

Worst realistic attack: tampered imagery or scene data that gives wrong counts or an unsafe route, or a leak of farm locations and field geometry.

| Threat | Implemented control | Planned control |
|---|---|---|
| Tampered or corrupted input tiles | Every tile CRC-checked against the source ZIPs and made read-only; this caught one tile mirrored in place by an outside process | Signed manifests for customer uploads |
| Wrong or mixed scene data (predictions counted as ground truth) | Every feature carries `source`; measurements and routing exclude `source=prediction`; packer re-opens and re-verifies every ZIP | Version artifacts by input, weights and export hashes and reject mismatches |
| Farm geometry exposed through the API | Only GET endpoints (`/health`, `/api/scene`, `/api/imagery/{z}/{x}/{y}.png`), no write path; tile coordinates typed as integers and range-checked; server run on 127.0.0.1 | Login, TLS and per-customer separation before any hosted version |
| Unsafe or illegal route | `marcaj-export` refuses a route over 2% outside inter-rows and passages or not returning to START | Also reject forbidden and canopy crossings |
| Leaked secrets or poisoned dependencies | No secrets needed at inference (Earth Search is anonymous); `.env` is gitignored; dependencies locked in `uv.lock` and `package-lock.json`; weights hash published above | Dependency audit on each release |

## 6. Data protection

Data inventory:

| Data | Personal? | Source | Purpose | Lawful basis (GDPR Art. 6) | Retention | Minimisation |
|---|---|---|---|---|---|---|
| Demo drone tiles, 2.5 cm/px | Possibly, by chance: people, cars, houses | Public CC BY 4.0 survey (3DATA COLLECT / OpenAerialMap), supplied by organizers | Map vines | Public open licence; legitimate interest (6(1)(f)) for incidental content | Challenge period, then delete local copy (Plan) | Model detects only vines, rows, ground and litter, never people |
| Customer drone surveys | Possibly, by chance | Grower or their drone operator | Map vines, gaps, route | Contract with the grower (6(1)(b)); legitimate interest for incidental people | Until contract end, caches deleted after season (Plan) | Same as above; only derived geometry served |
| Derived geometry: rows, canopies, gaps, route | Only if the grower is a sole trader | Our pipeline | The product | Contract (6(1)(b)) | As customer data | Geometry only, no owner fields |
| Sentinel-2 NDVI/NDMI | No (10 m pixels) | Copernicus open data | Context layer | Not personal | Cached study-area windows only | Clipped to the study area |
| Cadastral parcels | Can link to owners | Public cadastre | Optional context | Not processed by default | Not stored by default | Layer off by default; no owner data fetched |
| Yield notes | Possibly (sole trader) | Typed by the user | User's own records | User's own device, no transfer | Until the user deletes them | Kept in browser localStorage only; never sent to us |
| Grower contact and account data | Yes | Customer | Contract and billing | Contract (6(1)(b)); legal obligation for invoices (6(1)(c)) | Contract term plus accounting period | Not in the prototype; no accounts built |
| Worker GPS tracks while walking the route | Yes (employee monitoring) | Phone | Not built | Would need a balancing test and DPIA | Not collected | Not built, off by default if ever added |

No special-category data (GDPR Art. 9) is processed. The model has no face or person detection, and we will not add biometric processing.

Privacy by design, all **Built**: processing runs on one laptop and no farm imagery is sent to cloud services; no accounts or database; the API serves only derived geometry and map tiles on 127.0.0.1; yield notes stay in the user's browser; the parcel layer is off and no cadastre owner data is fetched. **Plan**: blur people and vehicles in stored imagery, written retention rule, data processing agreement template.

DPIA screening (GDPR Art. 35): **not needed for the demo**. It uses public open imagery, no test users, no monitoring and no special-category data. **Needed** before adding GPS tracking of workers walking the route (systematic monitoring of employees), or before large-scale imaging of inhabited areas. Hand-drawn review marks are evaluation evidence and never train the model.

## 7. Pilot protocol

The next validation step: prove on a working vineyard that the flagged gaps are real and that the route saves time.

| Item | Plan |
|---|---|
| Partner type | One grower plus their agronomy adviser. On 25 Sep 2026 the team spoke with agronomists (orchard context); on 26 Sep they said they would try the map on a field visit they make anyway and check it visually (section 8) |
| Site | One partner vineyard of at least 5 ha with a known history of missing vines |
| Step 1 | Agreement with the grower, DPIA screening, flight booked with a certified operator |
| Step 2 | Fly in full leaf (RGB, about 2.5 cm/px, same spec as Sireț3); if this season is missed, May to June 2027 |
| Step 3 | Run the pipeline on a laptop, correct annotations, generate the route |
| Step 4 | Adviser walks the route; at each point records: vines missing (yes, partly, no), count, obvious cause, clock time at start and end. Paper or a simple form, no GPS tracking of the walker |
| Step 5 | Adviser fully walks a random 10% of rows to find gaps the model missed |
| Metrics | Gap precision (confirmed / visited); recall on the sampled rows; minutes per hectare, route against full walk of the sampled rows scaled up; share of route outside inter-rows |
| Success threshold | Agreed with the partner before the walk (starting proposal: precision of at least 0.7) |
| Duration | About 3 to 4 weeks elapsed, 2 to 3 field days (Estimate) |
| Resources | 2 team members, existing laptop pipeline, one hired operator flight; the adviser's walk rides on a field visit they already make, so no extra adviser days |
| Cost | Team estimate needed |

## 8. Market

**First EU market: Romania** (team decision). Same language as our Romanian interface, borders Moldova, and a large wine sector with CAP support for vineyard restructuring and replanting.

Rough size, Eurostat vineyard survey, reference year 2020 (datasets `vit_t1` and `vit_t2`):

| Figure (Romania, 2020) | Value |
|---|---|
| Total area under vines | 180,683 ha |
| Vineyard holdings | 844,015 (average about 0.2 ha) |
| Holdings of 10 ha or more | 791 holdings, 56,668 ha |
| Holdings of 5 to 9.9 ha | 605 holdings, 4,121 ha |
| **First segment: holdings of 5 ha or more** | **about 1,400 holdings, about 60,800 ha (about a third of the area)** |

Sources: [Eurostat vit_t1](https://ec.europa.eu/eurostat/databrowser/view/vit_t1/default/table) and [vit_t2](https://ec.europa.eu/eurostat/databrowser/view/vit_t2/default/table) (read through the Eurostat dissemination API on 26 Sep 2026), [Vineyards in the EU, Statistics Explained](https://ec.europa.eu/eurostat/statistics-explained/index.php?title=Vineyards_in_the_EU_-_statistics). The next Eurostat vineyard survey may change these numbers.

**Stakeholders and value chain:**

| Who must say yes | Role | Contact so far |
|---|---|---|
| Wine estate or cooperative owner | Buyer | None for this module yet |
| Agronomist or farm manager | User, walks the route | **25 Sep 2026: conversation with agronomists (orchard context)**, details below |
| Drone service provider | Supplies the flight | None yet |
| Marcaj | Annotation and correction platform | Challenge organizers and Marcaj |

What the agronomists told us on 25 Sep 2026 (their statements, not our measurements): maximum canopy about 2.5 m; planting distance about 2 m along the row; the price they pay for field work (noted by the team, used to check our price hypothesis); and **12 to 15 treatments per season in orchards**. These set checks for the orchard version of the model (canopy size limits, expected spacing along the row) and drive the business model below.

Follow-up answers from the agronomists, relayed by the team on 26 Sep 2026 (their statements, not our measurements):

| Question | Their answer | What it changes |
|---|---|---|
| How often do you walk a field? | A few more times than the number of treatments | The walking saved per round repeats more than 12 to 15 times a season |
| Is one missing vine worth flagging, or only 5 m? | Young vines planted this or last year can look empty, but more than 5 m empty is unusual | Supports the 5 m gap rule; young vines are the main false-gap cause, which the canopy model must see |
| Would you trust the map? | "We have to go there anyway": they would try it on a visit and check visually before deciding | The pilot rides on a visit they already make (section 7) |
| Our gap points, looked at by them | Mostly good; some gaps do contain vines | Matches our known canopy misses; the pilot will count them |
| Pilot cost, drone price, replanting subsidies | Not needed or not sure for a small plot | Small plots are not the customer; the first segment stays holdings of 5 ha or more |
| Storing drone photos of the farm | Not a concern: "they are on Google Maps anyway" | Growers do not see aerial imagery as sensitive; we still process it under contract |

**Competitors selling in the EU** (each checked by web search on 26 Sep 2026):

| Competitor | What it does | Our specific difference |
|---|---|---|
| [Chouette](https://www.futurefarming.com/tech-in-focus/autonomous-semi-autosteering-systems/agreenculture-acquires-chouette-to-add-agronomic-intelligence-to-autonomous-machines/) (France, acquired by Agreenculture) | AI vine health and vigour maps, now from cameras on tractors and robots | No tractor sensor needed: one RGB drone flight gives row IDs, gap points and a walkable route |
| [Agremo](https://www.agremo.com/products/plant-counting/) (Serbia) | Cloud drone analytics, plant and stand counts including vineyards | We add global row IDs, inter-rows and a closed inspection walk that stays in the lanes; runs locally; people correct the output |
| [PIX4Dfields](https://www.pix4d.com/product/pix4dfields) | Laptop drone mapping: orthomosaics, index maps, prescription zones | Object-level output (each canopy, row and gap) instead of index maps, plus the route |
| [xFarm](https://www.xfarm.ag/en/the-company) (Italy) | Farm management platform with vineyard records, sensors and disease recognition | Not a drone gap detector; more a possible integration partner than a rival |

**Business model hypothesis** (pricing untested):

| Item | Hypothesis |
|---|---|
| Who pays | Wine estates and orchard growers, per hectare per season; drone service providers and agronomy advisers as resellers |
| Value driver | The agronomists said orchards get 12 to 15 treatments per season and fields are walked a few more times than that. The walking saved per round (section 1) repeats every round. Their statement, not our measurement |
| Who does not pay | Small plots: the agronomists said a pilot, a drone flight or subsidy support is not needed for a small plot, so the first segment is holdings of 5 ha or more |
| Price | Hypothesis to test in the pilot: about €20 per hectare per survey for the analysis (canopies, rows, gaps, route), on top of the operator's flight; a season plan of about €35 per hectare for two surveys (spring gap count, summer canopy). A 10 ha estate would pay about €350 a season. Benchmarks below |
| Scales for free in the single market | The model, the pipeline, the route solver, EU drone rules, GDPR and AI Act compliance work |
| Changes by country | Language, cadastre source and licence, CAP national strategic plan rules, drone operator market, invoicing and VAT |

**Market price benchmarks** (public list prices checked on 26 Sep 2026; 1 EUR is about 5 RON):

| Service | Listed price | About | Source |
|---|---|---|---|
| Drone crop monitoring, Romania, indicative | 100 to 300 RON/ha | €20 to €60/ha | [TerraDron monitoring](https://terradron.ro/servicii/monitoring) |
| Same, operator specialised in vineyards and orchards (Hortidrones) | 120 to 200 RON/ha | €24 to €40/ha | [TerraDron monitoring](https://terradron.ro/servicii/monitoring) |
| Same, operators in Moldova | 160 to 240 MDL/ha | €8 to €12/ha | [TerraDron monitoring](https://terradron.ro/servicii/monitoring) |
| Drone orthophoto mapping, Romania, indicative | 150 to 500 RON/ha | €30 to €100/ha | [TerraDron mapping](https://terradron.ro/servicii/mapping) |
| Agremo drone analytics software | $349/year (10 fields) to $1,950/year (unlimited) | | [Agremo pricing](https://www.agremo.com/agremo-pricing/) |
| Scouting labour (US guide) | 1 to 2 hours per 5 to 10 acres per week in season | | [VitiScribe](https://vitiscribe.com/vineyard-management-cost-per-acre/) |

Our analysis price sits inside what Romanian growers already pay per hectare for a monitoring flight, and it adds object-level counts and a walking route that index maps do not give.

**Entry requirements before the first EU sale** (effort is a team estimate, not a quote):

| Requirement | Why | Effort (Estimate) | Roadmap |
|---|---|---|---|
| CE marking | Not needed: software only, not a machine, medical or safety device | 0.5 day to confirm with an adviser | Before first sale |
| GDPR documents: privacy notice, data processing agreement, records of processing, retention rule | Customer imagery and contacts | 5 to 10 person-days plus optional legal review | Before the pilot |
| AI Act transparency: model card, stated limits, confidence shown, AI literacy for staff | Minimal-risk system, voluntary transparency | 2 to 3 person-days | Before first sale |
| Drone flights by a certified operator under EU drone rules (EASA) | We buy flights, we do not fly | 2 to 3 days to select an operator; flight price to quote. National rules for aerial imaging in Romania to check | Before the pilot |
| Imagery and cadastre reuse licences | CC BY 4.0 attribution for Sireț3; customer imagery under contract; Romanian cadastre terms to check (Moldovan terms unclear, so that layer is off) | 1 to 2 days | Before first sale |

## 9. Funding pathway

**TRL about 4**: the system works end to end on one real survey and is checked on 2 reference tiles; no field pilot yet. Moldova is associated to Horizon Europe and the Digital Europe Programme, so a Moldovan team can access most of these instruments directly.

| Instrument | Fit for our stage | Status |
|---|---|---|
| EDIH services (test before invest) or agrifoodTEF testing | Fits now: in-kind support to run the pilot in section 7 | Plan |
| Horizon Europe, Cluster 6 (food, agriculture, environment) | Fits now as SME partner in a consortium | Plan |
| EIC Transition (roughly TRL 4 to 6) | After a validated pilot; its eligibility conditions are restrictive | Later |
| EIC Accelerator (roughly TRL 6 and above) | After paying customers | Later |

**One concrete 12-month step:** by the end of Q1 2027, pick an agri-focused hub from the official EDIH catalogue, or agrifoodTEF, and request a test service for the section 7 pilot; use the pilot results to join a Horizon Europe Cluster 6 consortium as SME partner in the next suitable call. Eligibility, calls and deadlines: **to check on the official EU Funding and Tenders portal**. We name no call, budget or deadline here.
