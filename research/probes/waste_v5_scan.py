"""Waste v5 probe: one run of marcaj.waste with its defaults (scope "interrow" + margin) and 2 workers against the
current predictions, so every tuning run reads the cache and the verifier can be re-run without a rescan. Writes
data/generated/work/waste_v5/candidates.json (every candidate) and waste_sitewide.geojson (the default boxes). Take
the heavy-run lock first. Run from backend/: uv run --frozen python ../research/probes/waste_v5_scan.py [--boxes-only]"""

import json
import sys
import time
from collections import Counter
from dataclasses import replace

from marcaj import waste
from marcaj.tiles import REPO_ROOT, load_tiles

OUT = REPO_ROOT / "data" / "generated" / "work" / "waste_v5"
PREDICTIONS = REPO_ROOT / "data" / "generated" / "work" / "v45" / "predictions.geojson"  # pinned: other agents may rewrite predictions.geojson


def main() -> None:
    started = time.perf_counter()
    predictions = json.loads(PREDICTIONS.read_text(encoding="utf-8"))["features"]
    params = replace(waste.WasteParams(), workers=2)
    OUT.mkdir(parents=True, exist_ok=True)
    if "--boxes-only" in sys.argv:
        found = json.loads((OUT / "candidates.json").read_text(encoding="utf-8"))
    else:
        found = waste.candidates(load_tiles(), predictions, params=params)
        (OUT / "candidates.json").write_text(json.dumps(found), encoding="utf-8")
    features = waste.boxes(found, predictions, params)
    (OUT / "waste_sitewide.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}), encoding="utf-8")
    print(f"{len(features)} boxes {Counter(f['properties']['location'] for f in features)} from {len(found)} candidates in {time.perf_counter() - started:.0f} s")


if __name__ == "__main__":  # the worker processes spawn and re-import this file
    main()
