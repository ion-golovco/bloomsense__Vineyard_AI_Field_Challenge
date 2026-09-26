"""Plots v3: detect_plots (per row pattern) and assign_blocks (per block), written under data/generated/work/plots_v3.
Run from backend/: uv run --frozen python ../research/probes/plots_v3_run.py out.geojson [name=value ...]"""

import json
import sys
import time
from collections import Counter
from dataclasses import fields, replace

from marcaj import plots
from marcaj.tiles import REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots_v3"

if __name__ == "__main__":
    out, overrides = sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])
    base = plots.PlotParams()
    kinds = {f.name: type(getattr(base, f.name)) for f in fields(base)}
    started = time.perf_counter()
    patterns = plots.detect_plots(replace(base, **{k: kinds[k](v) for k, v in overrides.items()}))
    blocks = plots.assign_blocks(patterns)
    assert json.dumps(plots.assign_blocks(blocks)) == json.dumps(blocks), "assign_blocks must be idempotent"
    row_ids = [f["properties"]["row_id"] for f in blocks if f["properties"]["label"] == "row"]
    assert len(row_ids) == len(set(row_ids)), "row ids must be unique"
    for name, features in (("patterns_" + out, patterns), (out, blocks)):
        (WORK / name).write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}))
    labels = [f["properties"] for f in blocks if f["properties"]["label"] == "block"]
    multi = [(p["vineyard_id"], [q["pattern_id"] for q in p["patterns"]]) for p in labels if len(p["patterns"]) > 1]
    print(f"{sum(f['properties']['label'] == 'block' for f in patterns)} patterns -> {len(labels)} blocks, {len(row_ids)} rows "
          f"in {time.perf_counter() - started:.0f} s; multi-pattern blocks {multi}; ids {sorted(p['vineyard_id'] for p in labels)}")
