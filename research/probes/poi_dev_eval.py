"""Fixed 5 m gap rule against the field-deviation rule (`poi.field_gap_m`): challenge gap targets per variant and field,
old targets dropped, and the user's gap-tab labels (data/review/verdicts.json, evaluation only) on the targets.
Needs data/generated/work/poi_dev/rows.pkl (poi_dev_stats.py). Run from backend/:
uv run --frozen python ../research/probes/poi_dev_eval.py"""

import json
import pickle
from collections import Counter

from shapely.geometry import LineString, Point

from marcaj import poi
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

V5 = REPO_ROOT / "data" / "generated" / "work" / "v5"
OUT = REPO_ROOT / "data" / "generated" / "work" / "poi_dev"
rows = pickle.loads((OUT / "rows.pkl").read_bytes())  # our own cache from poi_dev_stats.py
obstacles = [f for f in json.loads((V5 / "predictions.geojson").read_text())["features"] if f["properties"]["label"] == "obstacle"]
labels = {}
for v in load_verdicts():
    if v.get("properties", {}).get("review_tab") == "gaps":
        labels[v["properties"]["review_id"]] = (LineString(v["geometry"]["coordinates"]), v["properties"]["review_answer"])
labels = list(labels.values())


def segment(p: dict) -> LineString:
    return LineString([p["gap_start"], p["gap_end"]])


def label_of(p: dict) -> str | None:
    line = segment(p)
    best = None
    for other, answer in labels:
        d = min(Point(line.interpolate(0.5, normalized=True)).distance(other), Point(other.interpolate(0.5, normalized=True)).distance(line))
        if d <= 1.5 and (best is None or d < best[0]):
            best = (d, answer)
    return best and best[1]


def targets(**kw) -> list[dict]:
    return [f["properties"] for f in poi.gap_pois(rows, obstacles=obstacles, **kw) if f["properties"]["challenge"] and f["properties"]["reason"] == "gap"]


old = targets(rule="fixed")
old_ids = {(p["row_id"], tuple(p["gap_start"])) for p in old}
print(f"fixed: {len(old)} gap targets (v5 poi.geojson has 97)")
variants = {"fixed": old}
for k in (2.0, 3.0, 4.0):
    for floor in (2.5, 3.0, 4.0):
        variants[f"dev k{k:g} f{floor:g}"] = targets(rule="deviation", k=k, floor=floor)
print(f"{'variant':16} {'n':>4} {'old kept':>8} | real partly vines notrow unlab | prec(real+partly / labelled)  m total  m>=5")
for name, pois in variants.items():
    got = Counter(label_of(p) for p in pois)
    kept = sum((p["row_id"], tuple(p["gap_start"])) in old_ids for p in pois)
    good, bad = got["real_gap"] + got["partly"], got["vines_present"] + got["not_a_row"]
    print(f"{name:16} {len(pois):4} {kept:8} | {got['real_gap']:4} {got['partly']:6} {got['vines_present']:5} {got['not_a_row']:6} {got[None]:5} |"
          f" {good / max(good + bad, 1):.2f}  {sum(p['gap_m'] for p in pois):6.0f} {sum(p['gap_m'] >= 5 for p in pois):4}")

thresholds = poi.field_gap_m(rows)
fixed_by, dev_by = Counter(p["vineyard_id"] for p in old), Counter(p["vineyard_id"] for p in variants["dev k3 f3"])
print("\nfield: threshold m (k3 f3) | fixed targets -> deviation targets")
for field in sorted(set(fixed_by) | set(dev_by) | set(thresholds), key=lambda f: -dev_by[f] - fixed_by[f]):
    print(f"  {field:10} {thresholds.get(field, poi.GAP_M):5.2f} | {fixed_by[field]:3} -> {dev_by[field]:3}")
json.dump({name: pois for name, pois in variants.items()}, (OUT / "variants.json").open("w"))
json.dump(sorted({p["id"] for pois in variants.values() for p in pois if label_of(p)}), (OUT / "labelled.json").open("w"))
