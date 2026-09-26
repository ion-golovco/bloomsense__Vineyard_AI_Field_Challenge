#!/usr/bin/env bash
# Supplied tiles -> route.geojson + measurements.csv, by chaining the existing CLIs (see README.md).
#
#   scripts/run_all.sh                     predict, pack the CVAT 1.1 ZIPs Marcaj imports, read them back unchanged,
#                                          then POIs, route and measurements (the uncorrected pre-annotations)
#   scripts/run_all.sh EXPORT.zip [...]    the same from the Marcaj CVAT export(s) after manual correction (the submission)
#
# Env: OUT (default: repo root) receives route.geojson, route_targets.csv, measurements.csv;
#      SCENE (default: data/generated/scene.json, what the app serves); NO_SATELLITE=1 skips the Sentinel-2 layer;
#      MARCAJ_DATA_DIR (default: data/raw/marcaj) is the unzipped organizer package.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GEN="$ROOT/data/generated"
OUT="${OUT:-$ROOT}"
export MARCAJ_SCENE_PATH="${SCENE:-$GEN/scene.json}"
cvat=()
for zip in "$@"; do case "$zip" in /*) ;; *) zip="$PWD/$zip" ;; esac; cvat+=(--cvat "$zip"); done   # before cd
cd "$ROOT/backend"
run() { uv run --frozen --group sam "$@"; }

# plots.detect_plots reads cadastral parcels by default; fetch the public WMS snapshot once if it is missing
[ -f "$ROOT/data/raw/external/cadastre/parcels_32635.geojson" ] || run python -m marcaj.cadastre --fetch

if [ "$#" -eq 0 ]; then
  run python -m marcaj.predict   # -> data/generated/predictions.geojson; a cold run builds the mosaic and layer caches first
  run marcaj-pack --scene "$GEN/predictions.geojson" --output-dir "$ROOT/output/roundtrip"   # not output/upload: keeps the real upload set
  for zip in "$ROOT"/output/roundtrip/siret3_upload_part*.zip; do cvat+=(--cvat "$zip"); done
fi

# obstacles come from the predicted blocks (a Marcaj export has none); poi skips row stretches that end at one
if [ -f "$GEN/predictions.geojson" ]; then run python -m marcaj.obstacles; fi

run marcaj-scene "${cvat[@]}" --output "$MARCAJ_SCENE_PATH"
run python -m marcaj.poi --scene "$MARCAJ_SCENE_PATH" ${NO_SATELLITE:+--no-satellite}
run marcaj-export --output-dir "$OUT" --poi "$GEN/work/poi/poi.geojson"   # refuses a route that would score 0
