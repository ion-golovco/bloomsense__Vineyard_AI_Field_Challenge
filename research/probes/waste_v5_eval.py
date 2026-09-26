"""Waste v5 probe (evaluation only; reads data/review/ through eval_waste, which prediction code never does): the
verifier restricted to the predicted inter-rows plus a margin, scored on the labels in scope. Scope of the labels:
touching an inter-row, or inside a predicted block within LABEL_MARGIN_M of one (piles at row ends and on canopy
strips). Candidates: data/generated/work/waste_v5/candidates.json (waste_v5_scan.py, scope "interrow" with a 2 m margin); a rest-of-tile candidate is
in scope when its box meets the inter-rows, or their margin inside a predicted block, which emulates WasteParams(scope="interrow").
Split: north / south of the median northing of the in-scope waste labels, and leave-tiles-out by tile column parity.
Run from backend/: uv run --frozen python ../research/probes/waste_v5_eval.py [--sheets]"""

import json
import sys
from dataclasses import replace
from pathlib import Path
from statistics import median

from shapely import STRtree
from shapely.geometry import box, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "review"))
import eval_waste as ev  # noqa: E402
from marcaj import waste  # noqa: E402

OUT = ev.GEN / "work" / "waste_v5"
LABEL_MARGIN_M = 2.0

predictions = json.loads((ev.GEN / "predictions.geojson").read_text(encoding="utf-8"))["features"]
context = waste.load_context(predictions)
union = unary_union([p for p, _ in context.interrows])
blocks = unary_union([p for p, _ in context.blocks])
label_zone = union.union(union.buffer(LABEL_MARGIN_M).intersection(blocks))
CANDS = OUT / "candidates.json"
found_all = json.loads(CANDS.read_text(encoding="utf-8"))
items_all = [(g, pos, source, ev.locate(g, context, union)) for g, pos, source in ev.labelled()]
items = [it for it in items_all if label_zone.intersects(it[0])]
conform = [it for it in items if it[2] != "user flagged"]
split_n = median(it[0].centroid.y for it in items if it[1])
_zones: dict[float, object] = {}


def in_scope(found: list[dict], margin_m: float) -> list[dict]:
    if margin_m not in _zones:
        _zones[margin_m] = union.union(union.buffer(margin_m).intersection(blocks)) if margin_m else union
    zone = _zones[margin_m]
    tree = STRtree([box(*c["box"]) for c in found])
    hit = set(tree.query(zone, predicate="intersects"))
    return [c for i, c in enumerate(found) if c["location"] == "interrow" or i in hit]


def run(name: str, found: list[dict], params: waste.WasteParams, verifier=waste.accept, split: bool = True) -> list[dict]:
    boxes = waste.boxes(found, predictions, replace(params, scope="site"), verifier)
    for label_set, chosen in (("all", items), ("conform", conform)):
        ev.evaluate(f"{name} [{label_set}]", boxes, chosen)
    if split:
        for half in ("N", "S"):
            inside = lambda g: (g.centroid.y > split_n) == (half == "N")
            sub = [it for it in items if inside(it[0])]
            ev.evaluate(f"{name} [all {half}]", [b for b in boxes if inside(shape(b["geometry"]))], sub)
    return boxes


if __name__ == "__main__":
    out_waste = sum(1 for it in items_all if it[1] and it[2] != "organizer") - sum(1 for it in items if it[1])
    print(CANDS)
    print(f"labels in scope: {sum(1 for it in items if it[1])} waste ({sum(1 for it in conform if it[1])} rule-conform), "
          f"{sum(1 for it in items if not it[1])} not; out of scope now: {out_waste} waste; split northing {split_n:.0f}")
    v45 = [f for f in json.loads((ev.GEN / "work" / "v45" / "predictions.geojson").read_text(encoding="utf-8"))["features"]
           if f["properties"].get("label") == "waste"]
    in_zone = in_scope([{"box": list(shape(f["geometry"]).bounds), "location": "rest"} for f in v45], 2.0)
    kept = {tuple(c["box"]) for c in in_zone}
    v45_in = [f for f in v45 if tuple(shape(f["geometry"]).bounds) in kept]
    print(f"v4.5 prediction: {len(v45)} boxes, {len(v45_in)} in the 2 m scope")
    for label_set, chosen in (("all", items), ("conform", conform)):
        ev.evaluate(f"v4.5 boxes in scope [{label_set}]", v45_in, chosen)
    for half in ("N", "S"):
        inside = lambda g: (g.centroid.y > split_n) == (half == "N")
        ev.evaluate(f"v4.5 boxes in scope [all {half}]", [b for b in v45_in if inside(shape(b["geometry"]))], [it for it in items if inside(it[0])])
    base = waste.WasteParams()
    off = replace(base, pile_area_m2=(9.0, 9.0))
    for name, params in (("no pile tier", off), ("default (pile tier)", base)):
        for margin in (0.0, 1.0, 2.0):
            run(f"{name}, margin {margin}", in_scope(found_all, margin), params)
