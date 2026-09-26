"""Before/after sheets for canopy round three: pieces of variant A with no overlap in variant B (removed), grouped by
tile and sampled; and fixed tiles side by side. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v3_sheet.py removed A B [N]
                   uv run --frozen python ../research/probes/canopy_v3_sheet.py labels A B   (the user's labels A lost / B lost)"""

import json
import random
import sys
from pathlib import Path

from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import labels, match  # noqa: E402
from canopy_v2_sheet import CYAN, MAGENTA, YELLOW, sheet  # noqa: E402
from canopy_v3_quick import WORK  # noqa: E402


def load(name: str) -> list[dict]:
    return json.loads((WORK / f"canopies_{name}.json").read_text())


def removed(a: str, b: str, n: int = 36) -> None:
    A, B = load(a), load(b)
    ga, gb = [shape(f["geometry"]) for f in A], [shape(f["geometry"]) for f in B]
    tree = STRtree(gb)
    gone = [i for i, g in enumerate(ga) if not len(tree.query(g, predicate="intersects"))]
    area = sum(ga[i].area for i in gone)
    tiles = sorted({A[i]["properties"]["tile_run"] for i in gone})
    print(f"{len(gone)} pieces / {area:.0f} m2 of {a} gone in {b}, on {len(tiles)} tiles: {tiles}")
    random.seed(0)
    pick = random.sample(gone, min(n, len(gone)))
    items = []
    for i in pick:
        near_a = [(g, YELLOW) for g in ga if g.distance(ga[i]) < 6 and g is not ga[i]][:60]
        near_b = [(g, MAGENTA) for g in gb if g.distance(ga[i]) < 6][:60]
        items.append((ga[i], f"{A[i]['properties']['tile_run'][7:16]} {A[i]['properties']['vineyard_id']} {ga[i].area:.2f}m2", near_a + near_b + [(ga[i], CYAN)]))
    print(sheet(items, WORK / f"removed_{a}_vs_{b}.jpg", size_m=10.0))


def lost_labels(a: str, b: str) -> None:
    A, B = load(a), load(b)
    la = {m["id"]: m for m in match(labels(), A)}
    lb = {m["id"]: m for m in match(labels(), B)}
    items = []
    for lab in labels():
        x, y = la[lab["id"]], lb[lab["id"]]
        if x["answer"] != "not_vine" and x["covered"] >= 0.2 and y["covered"] < 0.2 or x["answer"] == "not_vine" and x["covered"] >= 0.2 and y["covered"] < 0.2:
            g = shape(lab["geometry"])
            items.append((g, f"{x['answer']} {x['tile'][7:16]} {x['covered']:.2f}->{y['covered']:.2f}",
                          [(shape(f["geometry"]), YELLOW) for f in A if shape(f["geometry"]).distance(g) < 5] +
                          [(shape(f["geometry"]), MAGENTA) for f in B if shape(f["geometry"]).distance(g) < 5] + [(g, CYAN)]))
    print(sheet(items, WORK / f"labels_lost_{a}_vs_{b}.jpg", size_m=10.0) if items else "none")


if __name__ == "__main__":
    mode, a, b = sys.argv[1:4]
    removed(a, b, int(sys.argv[4]) if len(sys.argv) > 4 else 36) if mode == "removed" else lost_labels(a, b)
