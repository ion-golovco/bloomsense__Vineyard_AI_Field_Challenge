"""ICAERUS as a "not a vine" check on our canopies: for each of the user's canopy labels (verdicts review_tab "canopies":
several / one_plant / not_vine, polygons around our long pieces), the share of the label covered by ICAERUS instances
(zoom 1) and the instance count inside. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/icaerus_notvine.py"""

import json
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon, shape

sys.path.insert(0, str(Path(__file__).parent))
from icaerus_gaps import auc  # noqa: E402
from sam_v2_lib import labels  # noqa: E402

from marcaj.tiles import PIXEL_M, REPO_ROOT, load_tiles  # noqa: E402

INST = REPO_ROOT / "data" / "generated" / "work" / "icaerus" / "inst_z1"

if __name__ == "__main__":
    tiles = load_tiles()
    rows = []
    for lab in labels("canopies"):
        g = shape(lab["geometry"])
        polys = {0.15: [], 0.25: []}
        for tile in tiles:
            if not tile.bounds.intersects(g):
                continue
            data = np.load(INST / (tile.name[:-4] + ".npz"))
            for ring, conf in zip(np.split(data["xy"], data["ends"][:-1]) if len(data["ends"]) else [], data["conf"]):
                if len(ring) >= 3:
                    p = Polygon(np.column_stack([tile.left + ring[:, 0] * PIXEL_M, tile.top - ring[:, 1] * PIXEL_M])).buffer(0)
                    if p.intersects(g):
                        for c in polys:
                            if conf >= c:
                                polys[c].append(p)
        row = {"answer": lab["properties"]["review_answer"]}
        for c, ps in polys.items():
            row[f"share_c{c}"] = shapely.union_all(ps).intersection(g).area / g.area if ps else 0.0
            row[f"n_c{c}"] = sum(p.intersection(g).area >= 0.5 * p.area for p in ps)
        rows.append(row)
    for key in ("share_c0.15", "share_c0.25", "n_c0.15", "n_c0.25"):
        by = {}
        for r in rows:
            by.setdefault(r["answer"], []).append(r[key])
        vine = by.get("several", []) + by.get("one_plant", [])
        print(key, json.dumps({"mean": {a: round(float(np.mean(v)), 3) for a, v in sorted(by.items())},
                               "auc_vine_vs_not_vine": round(auc(vine, by.get("not_vine", [])), 3),
                               "not_vine_under_0.1": f"{sum(x < 0.1 for x in by.get('not_vine', []))}/{len(by.get('not_vine', []))}",
                               "vine_under_0.1": f"{sum(x < 0.1 for x in vine)}/{len(vine)}"}), flush=True)
