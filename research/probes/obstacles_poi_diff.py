"""Which gap/planting POIs the obstacles turned into `obstacle` points, per field, checked against the gap verdicts
(data/review/verdicts.json, review_tab "gaps"; old IDs, so matched by geometry: a verdict line within 1.5 m of the POI's
stretch, overlapping it by at least half the shorter one). Also: every "real gap" verdict within `poi.OBSTACLE_M`
of an obstacle (a true gap the obstacle rule would hide).
Run from backend/: uv run --frozen python ../research/probes/obstacles_poi_diff.py [before.geojson] [after.geojson]"""

import json
import sys
from collections import defaultdict
from pathlib import Path

from shapely.geometry import LineString, shape

from marcaj.poi import OBSTACLE_M, _reach
from marcaj.tiles import REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work"
before = json.loads((Path(sys.argv[1]) if len(sys.argv) > 1 else WORK / "poi" / "poi.geojson").read_text())["features"]
after = json.loads((Path(sys.argv[2]) if len(sys.argv) > 2 else WORK / "obstacles" / "poi.geojson").read_text())["features"]
obstacles = [(shape(f["geometry"]), _reach(f["properties"])) for f in json.loads((WORK / "obstacles" / "obstacles.geojson").read_text())["features"]]
verdicts = [v for v in json.loads((REPO_ROOT / "data" / "review" / "verdicts.json").read_text())
            if v.get("properties", {}).get("review_tab") == "gaps"]
lines = [(v["properties"]["review_answer"], LineString(v["geometry"]["coordinates"])) for v in verdicts]


def stretch(p):
    return LineString([p["gap_start"], p["gap_end"]])


def verdict_for(line):
    out = []
    for answer, other in lines:
        if other.distance(line) > 1.5:
            continue
        overlap = other.buffer(1.5, cap_style="flat").intersection(line).length
        if overlap >= 0.5 * min(line.length, other.length):
            out.append(answer)
    return out


key = lambda p: (p["row_id"], tuple(p["gap_start"]), tuple(p["gap_end"]))
old = {key(f["properties"]): f["properties"] for f in before if "gap_start" in f["properties"]}
changed = defaultdict(list)
for f in after:
    p = f["properties"]
    if p["reason"] == "obstacle":
        was = old.get(key(p))
        changed[p["vineyard_id"]].append((was["reason"] if was else "?", bool(was and was["challenge"]), p["gap_m"], p["row_id"], p["obstacle_type"], verdict_for(stretch(p))))
targets = sum(1 for v in changed.values() for c in v if c[1])
print(f"obstacle points {sum(map(len, changed.values()))}, of which challenge targets before: {targets}")
for vid, items in sorted(changed.items()):
    print(vid, f"{len(items)} points, {sum(c[1] for c in items)} were targets")
    for was, target, gap_m, row, kind, answer in items:
        print(f"   {row} was {was}{' TARGET' if target else ''} {gap_m} m next to {kind}; verdicts {answer or '-'}")
hidden = [(answer, line) for answer, line in lines if answer == "real_gap" and any(line.distance(o) <= reach for o, reach in obstacles)]
print(f"'real gap' verdicts within {OBSTACLE_M} m of an obstacle: {len(hidden)} of {sum(a == 'real_gap' for a, _ in lines)}")
for answer, line in hidden:
    print("   ", [round(v, 1) for v in line.coords[0]], round(line.length, 1), "m")
