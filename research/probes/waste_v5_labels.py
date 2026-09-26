"""Waste v5 probe (research tooling, reads data/review/ for evaluation only): how many labelled waste objects lie in,
touch, or sit within a margin of the v4.5 predicted inter-rows, and whether they lie inside a predicted block.
Run from backend/: uv run --frozen python ../research/probes/waste_v5_labels.py"""

import json
import sys
from collections import Counter

from shapely.ops import unary_union

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "review"))
import eval_waste as ev  # noqa: E402
from marcaj import waste  # noqa: E402

predictions = json.loads((ev.GEN / "predictions.geojson").read_text(encoding="utf-8"))["features"]
context = waste.load_context(predictions)
union = unary_union([p for p, _ in context.interrows])
blocks = unary_union([p for p, _ in context.blocks])
items = ev.labelled()
print(len(items), Counter((s, p) for _, p, s in items))
for margin in (0.0, 0.3, 0.6, 1.0, 1.5, 2.0, 3.0):
    zone = union.buffer(margin) if margin else union
    counts = Counter()
    for g, pos, source in items:
        if source == "organizer":
            continue
        key = ("waste" if pos else "not") + ("" if source != "user flagged" else " flagged")
        counts[key, zone.intersects(g), blocks.intersects(g)] += 1
    print(f"margin {margin}: " + ", ".join(f"{k[0]} in={k[1]} block={k[2]}: {n}" for k, n in sorted(counts.items())))
