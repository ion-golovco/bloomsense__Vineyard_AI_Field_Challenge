# GigaHack Marcaj — Vineyard AI Field Challenge

A small monorepo for turning the organizer's Sireț3 GeoTIFF tiles into vineyard annotations, measurements, a legal inspection route, and a map. The [implementation spec](docs/SPEC.md) records the challenge contract and release gates.

## Current state

The plumbing runs on the organizer data. The 311 tiles are inventoried and byte-verified, CVAT for images 1.1 is read and written, objects are clipped per tile with global IDs, and the Marcaj upload ZIPs are packed and self-verified. Measurements come from projected annotations, and export refuses a route that would score 0. The map shows the source orthomosaic as imagery. The organizer examples round-trip with zero geometry change. Not built yet: the models that produce pre-annotations, and the route solver.

## Layout

- `backend/src/marcaj/` holds the Python backend:
  - `tiles` — tile inventory and pixel/world transforms
  - `cvat` — CVAT 1.1 reader and writer, and the `marcaj-scene` command
  - `package` — upload ZIPs, via `marcaj-pack`
  - `routing` — organizer constraints and route checks
  - `scene` — measurements
  - `export` — `route.geojson` and `measurements.csv`
  - `imagery` — map tiles
  - `api` — FastAPI
- `web/` — Vite + TypeScript + Tailwind + Leaflet map. No auth or database.
- `docs/SPEC.md` — labels, coordinate rules, scored outputs, acceptance checks, and what the release settled.

## Data

Put the organizer package at `data/raw/marcaj-data.zip` (ignored by Git) and unzip it once:

```sh
unzip data/raw/marcaj-data.zip -d data/raw/marcaj
```

On first use, the tiles are extracted from the five part ZIPs into `data/raw/marcaj/tiles/`. Every tile is checked against its original CRC, CRS and grid. `MARCAJ_DATA_DIR` moves the whole folder (see `.env.example`).

## Pipeline

Run from `backend/`:

```sh
# 1. Upload ZIPs for Marcaj: all 311 tiles unchanged, plus pre-annotations from an EPSG:32635 scene (omit --scene for tiles only)
uv run --frozen marcaj-pack --scene ../data/generated/predictions.json    # -> ../output/upload/siret3_upload_partNofM.zip

# 2. After correcting in Marcaj: export CVAT XML and turn it (plus the organizer route constraints) into a scene
uv run --frozen marcaj-scene --cvat ../data/marcaj-export.zip --output ../data/generated/scene.json

# 3. Submission files at the repository root
MARCAJ_SCENE_PATH=../data/generated/scene.json uv run --frozen marcaj-export --output-dir ..
```

The organizer examples work as a stand-in for a Marcaj export: `--cvat ../data/raw/marcaj/05_examples/siret3_examples_cvat.zip`. `marcaj-pack` exits non-zero and lists problems if anything would be rejected or scored as wrong. That includes a changed or missing tile, a label, attribute or value spelt wrong, an empty ID, a shape outside the tile, or a ZIP over the size budget.

## Run locally

Requires Python 3.11–3.13, [uv](https://docs.astral.sh/uv/), Node.js 22+, and npm. Dependencies are locked in `backend/uv.lock` and `web/package-lock.json`.

Terminal 1:

```sh
cd backend
uv sync --frozen
uv run --frozen uvicorn marcaj.api:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2:

```sh
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Vite forwards `/api` to the Python service. Set `MARCAJ_SCENE_PATH` to a generated scene to see real annotations. Imagery tiles are served from the source orthomosaic at `/api/imagery/{z}/{x}/{y}.png`. `http://127.0.0.1:8000/health` returns service health. For a single-process demo, run `npm run build` in `web/` and then start the Python service; it serves `web/dist/` at `/`.

## Generate demo files

```sh
cd backend
uv run --frozen marcaj-export --output-dir ../output/demo
```

The generated files demonstrate the required shapes and CSV columns. They are ignored by Git and must not be placed at the repository root as a final submission.

## Scene format

Without `MARCAJ_SCENE_PATH`, the service uses a built-in fictional scene. A scene is a `FeatureCollection` with top-level `"crs": "EPSG:32635"`. Each feature's `properties.label` is one of:

- the challenge labels: `vineyard`, `row`, `interrow_area`, `waste`
- app-only labels: `start`, `passage`, `forbidden`, `study_area`, `inspection`, `route`, `block`

Coordinates are projected metres. The API sends Leaflet a separate longitude/latitude copy. Lengths and areas are never computed from that copy.

## Checks

```sh
cd backend
uv run --frozen python -m compileall -q src
uv run --frozen marcaj-export --output-dir ../output/demo
uv run --frozen marcaj-scene --cvat ../data/raw/marcaj/05_examples/siret3_examples_cvat.zip --output ../data/generated/examples_scene.json
uv run --frozen marcaj-pack --scene ../data/generated/examples_scene.json
cd ../web && npm run build
```

No model quality or route score is claimed yet. The final README must also include full 311-tile processing steps, model weights, runtime and hardware, paid API/LLM disclosure, the working interface link, and real `route.geojson` and `measurements.csv` at the repository root.
