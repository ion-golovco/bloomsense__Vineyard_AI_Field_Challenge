"""Baseline for the canopy-recall work: the uploaded predictions' canopies, and the current code on the frozen rows
(uploaded predictions' blocks and rows). Writes metrics and the baseline POIs to data/generated/work/canopy_recall/.
Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_recall_base.py"""

import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from canopy_recall_lib import WORK, Variant, cache_probabilities, gap_stats, line, metrics, site, uploaded_features  # noqa: E402

if __name__ == "__main__":
    started = time.perf_counter()
    uploaded = [f for f in uploaded_features() if f["properties"]["label"] == "vineyard"]
    m = metrics("uploaded", uploaded)
    print(line(m), f"{time.perf_counter() - started:.0f} s", flush=True)
    stats = gap_stats(uploaded)
    (WORK / "pois_uploaded.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": stats["_pois"]}))
    cache_probabilities()
    started = time.perf_counter()
    found = site(Variant("current"))
    print(f"site {time.perf_counter() - started:.0f} s", flush=True)
    (WORK / "canopies_current.json").write_text(json.dumps(found))
    m2 = metrics("current code, frozen rows", found)
    print(line(m2), flush=True)
    (WORK / "base_metrics.json").write_text(json.dumps([m, m2], indent=1))
