# GigaHack Marcaj — Vineyard AI Field Challenge

Turns the organizers' Sireț3 GeoTIFF tiles into Marcaj pre-annotations, measurements, a legal inspection route and a client map.

Where to find what:
- [`CLAUDE.md`](CLAUDE.md): rules and architecture.
- [`docs/STATUS.md`](docs/STATUS.md): current state and next steps.
- [`docs/SPEC.md`](docs/SPEC.md): the challenge contract.
- [`docs/ORGANIZER_PLAYBOOK.md`](docs/ORGANIZER_PLAYBOOK.md): the organizers' timeline.
- [`docs/RESEARCH.md`](docs/RESEARCH.md): measured data facts and sources.

## Layout

- `backend/src/marcaj/` — the Python package: tiles, CVAT, upload packing, vineyard detector, judge, review store, routing, measurements, export, imagery and the client API. See `CLAUDE.md` for what each module does.
- `web/` — the client map (Vite, TypeScript, Tailwind, Leaflet), served by the API on :8000. It is for the jury demo and has no review tooling.
- `research/lab.ipynb` — the lab. Review predictions over the imagery, record right, wrong or missed, re-run the detector and re-judge.
- `research/probes/` — the scripts behind the findings in `docs/RESEARCH.md`. The raw Codex research sweep sits next to them.
- `data/review/verdicts.json` — team verdicts from the lab: evaluation evidence only, never uploaded.

## Data

Put the organizer package at `data/raw/marcaj-data.zip` (ignored by Git) and unzip it once:

```sh
unzip data/raw/marcaj-data.zip -d data/raw/marcaj
```

On first use, the tiles are extracted from the five part ZIPs into `data/raw/marcaj/tiles/`. Each is checked against its original CRC, CRS and grid, then made read-only. `MARCAJ_DATA_DIR` moves the whole folder (see `.env.example`).

## Run locally

Requires Python 3.11–3.13, [uv](https://docs.astral.sh/uv/), Node.js 22+ and npm. Dependencies are locked in `backend/uv.lock` and `web/package-lock.json`.

```sh
cd backend && uv sync --frozen
uv run --frozen marcaj-scene --cvat ../data/raw/marcaj/05_examples/siret3_examples_cvat.zip   # writes data/generated/scene.json
cd ../web && npm ci && npm run build
cd ../backend && uv run --frozen uvicorn marcaj.api:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. For frontend work, `npm run dev` in `web/` serves on :5173 and proxies `/api` to :8000. The app needs a scene file. It reads `data/generated/scene.json`, or the path in `MARCAJ_SCENE_PATH`, and there is no mock fallback.

### Lab

```sh
cd backend && uv sync --frozen --group lab
uv run --frozen --group lab jupyter lab ../research/lab.ipynb
```

Open the link Jupyter prints. The notebook explains the controls. `lab.run(...)` re-detects in about 0.02 s because the window scores are cached. `lab.report()` runs the judge, and `lab.save()` updates the client scene.

## Pipeline

Run from `backend/`:

```sh
# 1. Marcaj upload ZIPs: all 311 tiles unchanged plus pre-annotations (omit --scene for tiles only)
uv run --frozen marcaj-pack --scene ../data/generated/predictions.geojson   # packs predictions only -> ../output/upload/siret3_upload_partNofM.zip
#    dry run of our writer first, on the 2 example tiles with the organizers' reference annotations:
uv run --frozen marcaj-pack --scene ../data/generated/scene.json --source reference --only siret3_r021_c012.tif,siret3_r006_c004.tif --output-dir ../output/dryrun

# 2. After correcting in Marcaj: export CVAT XML and build the scene from it
uv run --frozen marcaj-scene --cvat ../data/marcaj-export.zip

# 3. Submission files at the repository root
uv run --frozen marcaj-export --output-dir ..
```

`marcaj-pack` exits non-zero and lists the problems if anything would be rejected or scored as wrong. That covers:
- a changed or missing tile;
- a label, attribute or value spelt wrong;
- an empty ID;
- a shape outside its tile;
- a ZIP over the size budget.

`marcaj-export` refuses a route that would score 0. `marcaj-judge` scores the scene's predictions against the reference tiles using the organizers' formulas.

## Scene format

A scene is a GeoJSON `FeatureCollection` with top-level `"crs": "EPSG:32635"`. Coordinates are projected metres. Each feature has two properties:
- `properties.label`: a challenge label (`vineyard`, `row`, `interrow_area`, `waste`) or an app label (`block`, `start`, `passage`, `forbidden`, `study_area`, `tile`, `inspection`, `route`).
- `properties.source`: `reference` (a Marcaj export or the examples), `organizer`, or `prediction`. Predictions are never measured or routed on.

The API sends Leaflet a separate longitude/latitude copy, and lengths and areas are never computed from it.

## Checks

```sh
cd backend
uv run --frozen python -m compileall -q src
uv run --frozen marcaj-scene --cvat ../data/raw/marcaj/05_examples/siret3_examples_cvat.zip
uv run --frozen marcaj-judge
uv run --frozen marcaj-pack --scene ../data/generated/predictions.geojson
uv run --frozen --group lab jupyter nbconvert --to notebook --execute ../research/lab.ipynb --output /tmp/lab-check.ipynb
cd ../web && npm run build
```

No model quality or route score is claimed yet. Before submission, this README must also give:
- the full 311-tile processing steps;
- the model weights;
- runtime and hardware;
- any paid APIs or LLMs used;
- the working interface link;
- the real `route.geojson` and `measurements.csv` at the repository root.

Imagery: Sireț3, CC BY 4.0, 3DATA COLLECT / OpenAerialMap. Route data © OpenStreetMap contributors, ODbL.
