"""Contact sheets of what the young-block minimum area adds and what the grass-strip rule removes site-wide, from
data/generated/work/canopy_rules/site2_changes.json (canopy_rules_site2.py). Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_rules_changes_sheet.py"""

import json
from collections import defaultdict

import numpy as np
from shapely.geometry import shape

from canopy_rules_sheet import sheet
from marcaj.tiles import REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "canopy_rules"
changes = json.loads((WORK / "site2_changes.json").read_text())
rng = np.random.default_rng(0)
for key, tag, count in (("young added", "young_added", 9), ("strips removed", "strips_removed", 15)):
    items = [(t, shape(g), v) for t, g, v in changes[key]]
    if key == "strips removed":
        items.sort(key=lambda x: -x[1].area)
        pick = items[:count]
    else:
        by_tile = defaultdict(list)
        for t, g, v in items:
            by_tile[t].append((g, v))
        pick = [(t, gs[len(gs) // 2][0], gs[0][1]) for t, gs in sorted(by_tile.items(), key=lambda x: -len(x[1]))[:count]]
    cells = []
    for t, g, v in pick:
        near = [shape(h) for tt, h, _ in changes[key] if tt == t] if key == "young added" else [g]
        cells.append((t, g, f"{t[7:16]} {v} {key} {g.area:.2f} m2" + (f" ({len(near)} on tile)" if key == "young added" else ""), near, None, None))
    print(sheet(cells, WORK / f"missing_{tag}.jpg", columns=3 if key == "young added" else 5, size=420 if key == "young added" else 300))
