"""Site-wide contract check of plots v3 without canopies: patterns -> inter-rows -> per-tile attributes -> assign_blocks
-> CVAT elements for every tile -> check_cvat, plus the inter-row count against the pre-v3 rows.py on the same patterns.
Writes nothing. Run from backend/: uv run --frozen python ../research/probes/plots_v3_check.py patterns.geojson"""

import importlib.util
import json
import sys
import time
from collections import Counter

from marcaj import plots, rows
from marcaj.cvat import check_cvat, document, image_elements
from marcaj.tiles import REPO_ROOT, load_tiles

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots_v3"

if __name__ == "__main__":
    started = time.perf_counter()
    found = [f for f in json.loads((WORK / sys.argv[1]).read_text())["features"] if f["properties"]["label"] in ("block", "row")]
    tiles = load_tiles()
    interrows = rows.interrow_areas(found, plots.exclusions())
    spec = importlib.util.spec_from_file_location("rows_before", WORK / "rows_before.py")
    before = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(before)
    assert len(before.interrow_areas(found, plots.exclusions())) == len(interrows), "inter-rows changed on single-pattern ids"
    final = [{**f, "properties": {**f["properties"], "source": "prediction"}} for f in plots.assign_blocks(rows.per_tile(found + interrows, tiles))]
    labels = Counter(f["properties"]["label"] for f in final)
    empty = [f["properties"]["label"] for f in final if f["properties"]["label"] in ("row", "interrow_area") and not (f["properties"].get("vineyard_id") and f["properties"].get("pattern_id"))]
    owners = {}
    for f in final:
        if f["properties"]["label"] == "row":
            owners.setdefault(f["properties"]["row_id"], set()).add(f["properties"]["vineyard_id"])
    blocks = {f["properties"]["vineyard_id"] for f in final if f["properties"]["label"] == "block"}
    used = {f["properties"]["vineyard_id"] for f in final if f["properties"]["label"] in ("row", "interrow_area")}
    problems = check_cvat(document(list(image_elements(final, tiles).values())), {t.name for t in tiles})
    print(f"{dict(labels)} | empty ids {len(empty)} | rows with 2 blocks {sum(len(v) > 1 for v in owners.values())} | "
          f"blocks {len(blocks)}, used {len(used)}, unknown {sorted(used - blocks)} | check_cvat {len(problems)} problems {problems[:5]} | {time.perf_counter() - started:.0f} s")
