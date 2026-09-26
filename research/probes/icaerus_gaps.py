"""ICAERUS as a "vines present" check on gap candidates: for each of the user's gap labels (verdicts review_tab "gaps",
a line along a row stretch our canopy leaves open), the share of the line with an ICAERUS instance (zoom 1) within
+-0.3 m, against the share our base canopy covers. Reports, per answer, the mean share and how many labels a share
threshold would call "vines present", and the AUC of vines_present against real_gap. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/icaerus_gaps.py"""

import json
import sys
from pathlib import Path

import numpy as np
import shapely
import shapely.affinity
from shapely.geometry import Polygon, shape

sys.path.insert(0, str(Path(__file__).parent))
from sam_v2_lib import base_features, labels  # noqa: E402

from marcaj.tiles import PIXEL_M, REPO_ROOT, load_tiles  # noqa: E402

INST = REPO_ROOT / "data" / "generated" / "work" / "icaerus" / "inst_z1"


def cover(line, union) -> float:
    t = np.arange(0, line.length, 0.05)
    pts = np.array([line.interpolate(x).coords[0] for x in t])
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    normal = np.array([-(y1 - y0), x1 - x0]) / line.length
    hit = np.zeros(len(t), bool)
    for off in np.linspace(-0.3, 0.3, 7):
        q = pts + off * normal
        hit |= shapely.contains_xy(union, q[:, 0], q[:, 1])
    return float(hit.mean())


def auc(pos: list[float], neg: list[float]) -> float:
    return float(np.mean([(p > n) + 0.5 * (p == n) for p in pos for n in neg])) if pos and neg else float("nan")


if __name__ == "__main__":
    tiles = load_tiles()
    gaps = labels("gaps")
    ours = [shape(f["geometry"]) for f in base_features() if f["properties"]["label"] == "vineyard"]
    ours_tree = shapely.STRtree(ours)
    result = {}
    rows = []
    for lab in gaps:
        line = shape(lab["geometry"])
        area = line.buffer(0.35)
        # control: the same line moved 1.25 m sideways (mid inter-row) must lose the signal
        shifted = shapely.affinity.translate(line, *(1.25 * np.array([-(line.coords[-1][1] - line.coords[0][1]), line.coords[-1][0] - line.coords[0][0]]) / line.length))
        area = area.union(shifted.buffer(0.35))
        inst = {c: [] for c in (0.15, 0.25, 0.35)}
        for tile in tiles:
            if not tile.bounds.intersects(area):
                continue
            data = np.load(INST / (tile.name[:-4] + ".npz"))
            if not len(data["ends"]):
                continue
            for ring, conf in zip(np.split(data["xy"], data["ends"][:-1]), data["conf"]):
                world = np.column_stack([tile.left + ring[:, 0] * PIXEL_M, tile.top - ring[:, 1] * PIXEL_M])
                if len(world) < 3:
                    continue
                poly = Polygon(world).buffer(0)
                if poly.intersects(area):
                    for c in inst:
                        if conf >= c:
                            inst[c].append(poly)
        near = [ours[i] for i in ours_tree.query(area)]
        row = {"answer": f"{lab['properties']['reason']}/{lab['properties']['review_answer']}",
               "ours": cover(line, shapely.union_all(near)) if near else 0.0}
        for c, polys in inst.items():
            row[f"ica_c{c}"] = cover(line, shapely.union_all(polys)) if polys else 0.0
            row[f"shift_c{c}"] = cover(shifted, shapely.union_all(polys)) if polys else 0.0
        rows.append(row)
    for key in ("ours", "ica_c0.15", "ica_c0.25", "ica_c0.35", "shift_c0.15", "shift_c0.25"):
        by = {}
        for r in rows:
            by.setdefault(r["answer"].split("/")[1], []).append(r[key])
        result[key] = {"mean": {a: round(float(np.mean(v)), 3) for a, v in sorted(by.items())},
                       "auc_vines_present_vs_real_gap": round(auc(by.get("vines_present", []), by.get("real_gap", [])), 3),
                       "called_present_at_0.3": {a: f"{sum(x >= 0.3 for x in v)}/{len(v)}" for a, v in sorted(by.items())},
                       "called_present_at_0.5": {a: f"{sum(x >= 0.5 for x in v)}/{len(v)}" for a, v in sorted(by.items())}}
        print(key, json.dumps(result[key]), flush=True)
    (REPO_ROOT / "data" / "generated" / "work" / "icaerus" / "gaps_check.json").write_text(json.dumps({"summary": result, "labels": rows}, indent=1))
