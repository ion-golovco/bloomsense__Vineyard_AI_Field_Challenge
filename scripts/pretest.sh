#!/usr/bin/env bash
# The organizers' pre-test: a closed inspection route from a given start point, asked from the running app
# (scripts/demo.sh), so it takes seconds instead of a fresh plan build.
#
#   scripts/pretest.sh X Y [EPSG] [OUT]
#
# X Y: the start point, by default in Web Mercator (EPSG 3857); pass 32635 for UTM, 4326 for lon lat.
# OUT: default output/route_pretest.geojson, the official format (one LineString in EPSG:32635 with length_m).
# The route file in the repo root is never touched.
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: scripts/pretest.sh X Y [EPSG, default 3857] [OUT, default output/route_pretest.geojson]" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
OUT="${4:-$ROOT/output/route_pretest.geojson}"
mkdir -p "$(dirname "$OUT")"

if ! curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/scene"; then
  echo "the app is not running on port $PORT: start scripts/demo.sh first" >&2
  exit 1
fi

X="$1" Y="$2" EPSG="${3:-3857}" OUT="$OUT" PORT="$PORT" uv run --frozen --project "$ROOT/backend" python - <<'EOF'
import json, os, sys, urllib.error, urllib.request
from pyproj import Transformer

e, n = Transformer.from_crs(int(os.environ["EPSG"]), 32635, always_xy=True).transform(float(os.environ["X"]), float(os.environ["Y"]))
body = json.dumps({"start": {"easting": e, "northing": n}}).encode()


def post(path: str) -> bytes:
    request = urllib.request.Request(f"http://127.0.0.1:{os.environ['PORT']}{path}", body, {"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(request, timeout=600).read()
    except urllib.error.HTTPError as error:
        sys.exit(f"the app refused the start E {e:.2f} N {n:.2f} (EPSG:32635): {error.read().decode()}")


print(f"start: EPSG:{os.environ['EPSG']} {os.environ['X']} {os.environ['Y']} -> EPSG:32635 E {e:.2f} N {n:.2f}")
r = json.loads(post("/api/route"))  # the report; the file request below hits the same cached solve
with open(os.environ["OUT"], "wb") as file:
    file.write(post("/api/route/geojson"))
print(f"route: {r['length_m']:.1f} m, {r['visited']}/{r['targets']} targets, {100 * r['outside_share']:.2f}% outside, "
      f"closed {r['closed']}, legal {r['legal']}, scores {r['scores']}, start moved {r['start']['snapped_m']} m onto walkable ground")
print(f"file: {os.environ['OUT']}")
if r["start"]["snapped_m"] > 5:
    print("WARNING: the start was moved over 5 m; check the point is on a passage and the EPSG is right", file=sys.stderr)
if not (r["legal"] and r["scores"] and r["closed"]):
    sys.exit("WARNING: this route would not score (outside budget or not closed)")
EOF
