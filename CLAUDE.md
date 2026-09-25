# CLAUDE.md — Marcaj Vineyard AI Field Challenge (Deeptech GigaHack 2026)

Turn 311 Sireț3 UAV tiles into Marcaj annotations, measurements, a legal inspection route and a client web map. **Deadline: Sunday 27 September 2026, 15:00 Chișinău**, for the repo and the Marcaj project. Current state and next steps are in [`docs/STATUS.md`](docs/STATUS.md). This file holds rules, architecture and insight only.

| Doc | Holds |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | Challenge contract, output formats, acceptance checks, what the release settled; §10–17 are the canonical architecture, data, routing, satellite and verification contract |
| [`docs/INSPECTION_PRODUCT.md`](docs/INSPECTION_PRODUCT.md) · [`docs/ARCHITECTURE_RESEARCH_2026-09-25.md`](docs/ARCHITECTURE_RESEARCH_2026-09-25.md) | Farmer inspection journey; architecture research |
| [`docs/ORGANIZER_PLAYBOOK.md`](docs/ORGANIZER_PLAYBOOK.md) | The organizers' step-by-step timeline; newer than SPEC where they differ |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | Measured data facts, verified datasets, licences and Sentinel results |
| [`docs/STATUS.md`](docs/STATUS.md) | What is built, what is next, open decisions |
| `data/raw/marcaj/03_docs/*.pdf` | The organizers' brief, annotation rules and Marcaj quick-start (gitignored with the data) |

Scoring: canopies 25 · route 25 · rows + attributes 15 (axes 8, attributes 5, grouping 2) · measurements 10 · waste 10 · engineering 15.

## Rules that can zero the score

1. **Pre-annotations import once, before Publish.** Upload every part, check Marcaj shows 311 files, then publish. The organizers aim for import and publish by **Saturday ~14:00**. First do a dry run: upload the example ZIP, and a 2-tile ZIP from our own writer (`marcaj-pack --only …`), check both import, then delete them.
2. **Manual annotation of Sireț3 happens only in Marcaj.** The lab's verdicts (right, wrong, missed, tile vineyard) are *evaluation evidence*. Prediction code and `marcaj-pack` must never read `data/review/`, and no model is trained on hand-drawn Sireț3 geometry. Fixing a miss means changing the model, not pasting the drawing into the output.
3. **Tiles stay byte-identical.** `load_tiles` CRC-checks every tile against the part ZIPs and makes them read-only. A tile was once found mirrored in place by an outside process, and the check caught it. Never write into `data/raw/marcaj/tiles/`.
4. **Label and attribute names are exact and lower case.** `unassessable` is a class that gets scored, not a skip. An empty `vineyard_id`, `row_id`, `row_structure` or `interrow_cover` is a wrong answer. Only waste more than 10 m from any block may have an empty `vineyard_id`.
5. **Submit every Marcaj job.** After publishing, never add or delete tiles or edit labels. Empty tiles need a human to tick "No objects in this frame".
6. **The route scores 0** if more than 2% of its length is outside inter-rows plus passages, or if it doesn't return within 5 m of START. `marcaj-export` refuses to write such a route.
7. **The brief requires a neural network with published weights.** A purely classical pipeline risks failing that requirement.

## Architecture

Two separate front ends, one Python package.

- **Client** (`web/`, Vite + Leaflet, served by `marcaj.api` on :8000): the pitch demo. It shows map, objects, IDs, measurements, route and rows, and **no tooling**. The API exposes only `/api/scene` and `/api/imagery/{z}/{x}/{y}.png`.
- **Lab** (`research/lab.ipynb` + `marcaj.lab`, Jupyter + ipyleaflet, the `lab` dependency group): review predictions over the imagery, record verdicts, re-run the model, re-judge.

`backend/src/marcaj/`:

| Module | Does |
|---|---|
| `tiles` | Tile inventory from the part ZIPs, CRC and grid checks, exact pixel ↔ EPSG:32635 affine |
| `cvat` | CVAT 1.1 reader/writer, clipping every scene object to every tile it touches, `check_cvat`, `build_scene`, `marcaj-scene` |
| `package` | Upload ZIPs regrouped under 88,000,000 bytes and re-verified. It packs `source=prediction` features only by default; `--source reference --only …` gives the example dry run. `marcaj-pack` |
| `mosaic` | Preprocessing: one seamless 0.2 m/px GeoTIFF of the 311 verified tiles, area-averaged, cached at `data/generated/mosaic_20cm.tif` (11 s) |
| `layers` | Plot variables on a 0.4 m grid: vine-over-orchard row energy, orchard energy, row angle, green share. Cached at `data/generated/layers_40cm.npz` (17 s) |
| `vineyard_mask` | Training-free vineyard plots from row periodicity. Window scores are cached in `data/generated/window_scores.npz`, so a re-threshold takes about 0.02 s |
| `judge` | The organizer formulas on the reference tiles, plus regressions against verdicts; `marcaj-judge` |
| `review` | Verdict store at `data/review/verdicts.json` (tracked) |
| `routing` | Organizer constraints, passable space, `check_route` |
| `scene` | Scene loading (default `data/generated/scene.json`), measurements, the client payload |
| `export` | `route.geojson` (FeatureCollection with the organizers' `crs` member) and `measurements.csv` |
| `imagery` | Map tiles rendered from the source orthomosaic's overviews |
| `api` | The client API |

**Scene contract.** A scene is a GeoJSON FeatureCollection in EPSG:32635 with `"crs": "EPSG:32635"`. Every feature carries `properties.label` and `properties.source`:

| `source` | Meaning | Counted in measurements and route |
|---|---|---|
| `reference` | CVAT input: the Marcaj export, or the organizer examples | yes |
| `organizer` | start, passages, forbidden, study area, the 311 `tile` squares | yes |
| `prediction` | Model output | **no**; `scene.is_scored` excludes it |

**Global first, clip last.** Models produce objects over the whole area with global `vineyard_id` and `row_id`. `cvat.image_elements` cuts them per tile at packing time, so IDs survive tile edges automatically. Never assign IDs per tile.

## Commands

Run from `backend/`:

```sh
uv sync --frozen                                           # add --group lab for Jupyter
uv run --frozen marcaj-scene --cvat ../data/raw/marcaj/05_examples/siret3_examples_cvat.zip [--predictions P.geojson]
uv run --frozen marcaj-judge                               # score predictions in data/generated/scene.json
uv run --frozen marcaj-pack --scene ../data/generated/predictions.geojson    # dry run: --scene ../data/generated/scene.json --source reference --only a.tif,b.tif
uv run --frozen uvicorn marcaj.api:app --reload --port 8000   # client app; build web/ first with npm run build
uv run --group lab jupyter lab ../research/lab.ipynb       # the lab; open the link Jupyter prints
```

The web build (`cd web && npm run build`) also typechecks. There are no test files by choice. Checks are `compileall`, the judge controls described below, the packer's self-verification and the notebook executing headless (`jupyter nbconvert --execute`).

## Data facts and traps

- Tiles are 2048² px at 0.025 m/px, EPSG:32635, JPEG-compressed, no overviews. For `siret3_r<r>_c<c>`: left = 628992.0 + 51.2·c, top = 5221222.4 − 51.2·r.
- CVAT pixel coordinates span 0..2048 with no half-pixel shift. The organizer examples round-trip through world coordinates with zero change.
- The 90 MB upload limit is MiB (the organizers' own 93.8 MB parts are called "under 90 MB"). Adding `annotations.xml` would push part 4 over, so `marcaj-pack` regroups.
- CVAT polygons are a single ring, so inter-row holes are filled with a printed warning.
- The source orthomosaic (EPSG:4326, 659 MB) has overviews; the map imagery comes from it, not from the tiles.
- Passable space has 2 disconnected components, and inter-rows must bridge them. START lies inside a passage. The study area is 81.5 ha, not the brief's 145 ha.
- The reference tiles hold straight 2-point rows 2.5–2.8 m apart, canopies of about 0.5 m², inter-rows about 77% of a vineyard tile and canopy about 10%.
- Sentinel-2 Earth Search `sentinel-2-c1-l2a` uses reflectance = DN × 1e-4 − 0.1; ignore the offset and NDVI is biased. It is on tile 35TPN, the same CRS. It cannot see 5 m gaps.
- Codex CLI: `/opt/homebrew/bin/codex` 0.145 rejects the account's model. Use `/Applications/ChatGPT.app/Contents/Resources/codex exec … < /dev/null`.
- The client server started by a launcher has no `--reload`. After changing Python code, restart it, or it keeps serving the old payload.

## Working rules

- The two reference tiles are a sanity check, not a holdout (SPEC §11). Accuracy claims need the spatial holdout SPEC §11 describes; lab verdicts on other tiles give precision and recall evidence meanwhile.
- Claim a model improvement only with a `marcaj-judge` or `lab.report()` number next to the previous one, and check regressions against the recorded verdicts.
- A new judge or validator check ships with a control that must fail. The judge's controls: an exact copy of the reference scores 50/50; rows shifted 0.35 m sideways score 1.0 and at 0.6 m score 0.0; half the canopies dropped give 0.576; flipped attributes give 0; one block for everything gives grouping 0.52.
- Measure in EPSG:32635 metres only. The display copy in lon/lat is never measured.
- Keep tooling out of `web/`. The client is what the jury sees.
- Don't commit `data/raw`, `data/generated` or `output`. `data/review/verdicts.json` is tracked.
