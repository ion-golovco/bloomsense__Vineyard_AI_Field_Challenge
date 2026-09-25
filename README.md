# GigaHack Marcaj — Vineyard AI Field Challenge

A small monorepo for turning the organizer's Sireț3 GeoTIFF tiles into vineyard annotations, measurements, a legal inspection route, and a map. The [implementation spec](docs/SPEC.md) records the challenge contract and release gates.

## Current state

The app runs end to end on **fictional vector geometry**. The Python service calculates areas and row lengths in EPSG:32635, exports challenge-shaped `route.geojson` and `measurements.csv`, and converts a display copy to longitude/latitude for the Vite map. These demo outputs are **not a Sireț3 submission**. Actual tile ingestion, AI inference, Marcaj CVAT import/export, manual review, and route optimisation are the next work.

## Layout

- `backend/` — Python geospatial scene, measurements, demo export, and thin FastAPI service.
- `web/` — Vite + TypeScript + Tailwind + Leaflet map. No auth or database.
- `docs/SPEC.md` — labels, coordinate rules, scored outputs, and acceptance checks.

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

Open `http://127.0.0.1:5173`. Vite forwards `/api` to the Python service. `http://127.0.0.1:8000/health` returns service health. For a single-process demo, run `npm run build` in `web/` and then start the Python service; it serves `web/dist/` at `/`.

## Generate demo files

```sh
cd backend
uv run --frozen marcaj-export --output-dir ../output/demo
```

The generated files demonstrate the required shapes and CSV columns. They are ignored by Git and must not be placed at the repository root as a final submission.

## Replace the fixture later

The service uses its built-in fictional scene unless `MARCAJ_SCENE_PATH` points to a JSON file. See `.env.example`. The file must be a `FeatureCollection` with top-level `"crs": "EPSG:32635"` and `features` whose `properties.label` values include the challenge labels (`vineyard`, `row`, `interrow_area`, `waste`) plus app-only `block`, `passage`, `forbidden`, `inspection`, and `route` where available. Geometries use projected metre coordinates. The API transforms a separate copy for Leaflet, which expects longitude/latitude GeoJSON. Production processing should emit this scene from corrected Marcaj annotations and organizer route constraints.

## Checks

```sh
cd backend && uv run --frozen python -m compileall src && uv run --frozen marcaj-export --output-dir ../output/demo
cd web && npm run build
```

No real-data quality or route-legality score is claimed yet. The final README must also include full 311-tile processing steps, model weights, runtime and hardware, paid API/LLM disclosure, the working interface link, and real `route.geojson` and `measurements.csv` at the repository root.
