"""v6 rows: what the team changed against a prediction (default v5), per field: rows deleted (outer or inner), rows added
(outer, inner, or in a new field), row ends moved (which way, how far, and whether the reference end is at a passage).
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/v6_rows_edits.py [pred=path.geojson] [out=edits.json]"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import v6_rows_eval as ev  # noqa: E402
from marcaj import plots  # noqa: E402


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    src = Path(args.get("pred", str(ev.REPO_ROOT / "data" / "generated" / "work" / "v5" / "predictions.geojson")))
    features = [f for f in json.loads(src.read_text())["features"] if f["properties"]["label"] in ("block", "row")]
    pred = ev.pred_pieces(features)
    pred_g = ev._global(pred)
    pred_vid = {p["row_id"]: ev._vid(p) for _, p in pred}
    ref_rows = [f["properties"] | {"geometry": shape(f["geometry"])} for f in json.loads((ev.REF / "rows.geojson").read_text())["features"]]
    roads = plots.exclusions()
    names = list(pred_g)
    tree = STRtree([pred_g[n] for n in names])
    ref_tree = STRtree([r["geometry"] for r in ref_rows])
    out = defaultdict(lambda: Counter())
    moves = defaultdict(list)
    used = set()
    for r in ref_rows:
        line = r["geometry"]
        pts = ev._samples(line)
        if not len(pts):
            continue
        (_, ti), dist = tree.query_nearest(shapely.points(pts), max_distance=0.5, return_distance=True, all_matches=False)
        field = r["vineyard_id"]
        if len(ti) < 0.5 * len(pts):
            # added: outer if the nearest predicted row of the field is about one spacing away, else new field / inner
            near = [names[k] for k in tree.query(line.buffer(3.5))]
            kind = "added_new_field" if not near else "added_near"
            out[field][kind] += 1
            continue
        best = names[Counter(ti.tolist()).most_common(1)[0][0]]
        used.add(best)
        a, b = np.asarray(line.coords)[[0, -1]]
        d = (b - a) / np.linalg.norm(b - a)
        t = sorted((np.asarray(pred_g[best].coords) - a) @ d)
        for end, delta, point in (("start", -t[0], a), ("end", t[1] - (b - a) @ d, b)):
            if abs(delta) > 1:
                at_road = roads.distance(shapely.Point(point)) < 2.0
                out[field][f"{'longer' if delta > 0 else 'shorter'}{'_road' if at_road else ''}"] += 1
                moves[field].append(round(float(delta), 1))
        out[field]["kept"] += 1
    for n, g in pred_g.items():
        if n in used:
            continue
        pts = ev._samples(g)
        (_, ti), _ = ref_tree.query_nearest(shapely.points(pts), max_distance=0.5, return_distance=True, all_matches=False) if len(pts) else ((None, []), None)
        if len(ti) < 0.5 * max(len(pts), 1):
            out[pred_vid[n]]["deleted"] += 1
    total = Counter()
    for field, c in sorted(out.items()):
        total.update(c)
        print(f"{field:10s} {dict(c)} moves {sorted(moves[field])[:12]}")
    print("total", dict(total))
    if "out" in args:
        (ev.WORK / args["out"]).write_text(json.dumps({"fields": {k: dict(v) for k, v in out.items()}, "moves": moves}, indent=1))


if __name__ == "__main__":
    main()
