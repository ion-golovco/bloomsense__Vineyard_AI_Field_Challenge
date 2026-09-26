"""Sheet and table of the user's "not a row" gap labels with the canopies of two saved variants (A yellow, B magenta,
label cyan): per label its length, the v4 row it runs along and the canopy area within 0.3 m of the label line in A and
B. Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_v3_notrow.py A B"""

import json
import sys
from pathlib import Path

from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import VERDICTS  # noqa: E402
from canopy_v2_sheet import CYAN, MAGENTA, YELLOW, sheet  # noqa: E402
from canopy_v3_quick import WORK  # noqa: E402

a, b = sys.argv[1:3]
labels = [v for v in json.loads(VERDICTS.read_text()) if v.get("properties", {}).get("review_answer") == "not_a_row"]
A = [shape(f["geometry"]) for f in json.loads((WORK / f"canopies_{a}.json").read_text())]
B = [shape(f["geometry"]) for f in json.loads((WORK / f"canopies_{b}.json").read_text())]
ta, tb = STRtree(A), STRtree(B)
items, total = [], [0.0, 0.0]
for lab in labels:
    g = shape(lab["geometry"])
    band = g.buffer(0.3, cap_style="flat")
    area = [sum(G[i].intersection(band).area for i in t.query(band, predicate="intersects")) for G, t in ((A, ta), (B, tb))]
    total = [total[0] + area[0], total[1] + area[1]]
    p = lab["properties"]
    print(f"{lab['id']} {p['reason']:8s} {p['tile'][7:16]} {g.length:6.1f} m green {p['green_share']:.2f} canopy on line {area[0]:5.2f} -> {area[1]:5.2f} m2")
    mid = g.interpolate(0.5, normalized=True).buffer(12)
    items.append((mid, f"{p['tile'][7:16]} {p['reason']} {g.length:.0f}m {area[0]:.1f}->{area[1]:.1f}",
                  [(A[i], YELLOW) for i in ta.query(mid.buffer(3))] + [(B[i], MAGENTA) for i in tb.query(mid.buffer(3))] + [(g, CYAN)]))
print(f"total canopy on the {len(labels)} labelled lines: {total[0]:.1f} -> {total[1]:.1f} m2")
print(sheet(items, WORK / f"not_a_row_{a}_vs_{b}.jpg", size_m=24.0, px=260, cols=6))
