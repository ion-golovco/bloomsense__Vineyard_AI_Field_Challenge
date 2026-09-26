"""Waste v5 probe (evaluation only; reads data/review/ through eval_waste): held-out check of the hand-set inter-row
tiers. Coordinate descent over the pile-tier thresholds (and an optional dim "mid" tier) fitted on one split half,
scored on the other; halves are north/south of the in-scope median northing, north/south of 5219600, and tile-column
parity. Scores per box set: hits (TP/FP/unlab as eval_waste), recall, F1 (unlabelled = half false) and the strict
F1@IoU 0.3 = 2M / (boxes + positives) with every unmatched box false (card boxes are looser than the objects, so this
understates). Candidates: data/generated/work/waste_v5/candidates.json (scope "interrow", 2 m margin).
Run from backend/: uv run --frozen python ../research/probes/waste_v5_holdout.py"""

import sys
from dataclasses import replace
from pathlib import Path

from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import waste_v5_eval as v5  # noqa: E402
from marcaj import waste  # noqa: E402

LEFT, TILE_M = 628992.0, 51.2
EXAMPLES = {"siret3_r006_c004.tif", "siret3_r021_c012.tif"}


def score(features: list[dict], items: list) -> dict:
    shapes = [shape(f["geometry"]) for f in features]
    tree = STRtree([g for g, *_ in items])
    tp = fp = unlab = 0
    for g in shapes:
        hits = [items[i] for i in tree.query(g.buffer(0.2))]
        if any(it[1] for it in hits):
            tp += 1
        elif hits:
            fp += 1
        else:
            unlab += 1
    positives = [it[0] for it in items if it[1]]
    ftree = STRtree(shapes) if shapes else None
    recalled = sum(1 for g in positives if ftree is not None and len(ftree.query(g.buffer(0.2))))
    pairs = sorted(((shapes[i].intersection(b).area / shapes[i].union(b).area, i, j) for j, b in enumerate(positives)
                    for i in (ftree.query(b) if ftree is not None else [])), reverse=True)
    used_i, used_j = set(), set()
    for iou, i, j in pairs:
        if iou >= 0.3 and i not in used_i and j not in used_j:
            used_i.add(i)
            used_j.add(j)
    m = len(used_i)
    return {"boxes": len(shapes), "tp": tp, "fp": fp, "unlab": unlab, "rec": recalled, "pos": len(positives), "m": m,
            "f1": 2 * recalled / max(2 * recalled + fp + unlab / 2 + len(positives) - recalled, 1),
            "f1iou": 2 * m / max(len(shapes) + len(positives), 1)}


def line(name: str, s: dict) -> str:
    return (f"{name:44s} boxes {s['boxes']:3d} TP {s['tp']:2d} FP {s['fp']:2d} unlab {s['unlab']:2d} prec {s['tp'] / max(s['tp'] + s['fp'], 1):.2f} "
            f"rec {s['rec']:2d}/{s['pos']} F1 {s['f1']:.3f} | IoU0.3 M {s['m']:2d} F1 {s['f1iou']:.3f}")


def halves() -> dict[str, callable]:
    col = lambda g: int((g.centroid.x - LEFT) // TILE_M)
    return {"median N/S": lambda g: g.centroid.y > v5.split_n,
            "column parity": lambda g: col(g) % 2 == 0}  # 5219600 leaves the south 3 in-scope waste labels: dropped


GRID = {"pile_area_m2": [(0.10, 1.5), (0.15, 1.5), (0.20, 1.5), (9.0, 9.0)], "pile_lum": [204, 206, 208, 210, 212, 214, 216, 999], "pile_chroma": [24, 28, 32, 36, 40],
        "pile_dev": [90, 100, 110, 120, 130], "pile_row_m": [0.55, 0.7, 0.9], "pile_hue_max": [40, 45, 50, 55, 60, 999],
        "pile_min_width_m": [0.06, 0.10, 0.14]}


def boxes(params: waste.WasteParams) -> list[dict]:
    return waste.boxes(v5.found_all, v5.predictions, params)


def fit(side, params: waste.WasteParams) -> waste.WasteParams:
    sub = [it for it in v5.items if side(it[0])]
    best = score([f for f in boxes(params) if side(shape(f["geometry"]))], sub)["f1"]
    for _ in range(1):
        for key, values in GRID.items():
            for value in values:
                trial = replace(params, **{key: value})
                f1 = score([f for f in boxes(trial) if side(shape(f["geometry"]))], sub)["f1"]
                if f1 > best + 1e-9:
                    best, params = f1, trial
    return params


if __name__ == "__main__":
    base = waste.WasteParams()
    off = replace(base, pile_area_m2=(9.0, 9.0))
    print(f"labels in scope: {sum(1 for it in v5.items if it[1])} waste, {sum(1 for it in v5.items if not it[1])} not")
    v45 = [f for f in v5.json.loads((v5.ev.GEN / "work" / "v45" / "predictions.geojson").read_text(encoding="utf-8"))["features"]
           if f["properties"].get("label") == "waste"]
    kept = {tuple(c["box"]) for c in v5.in_scope([{"box": list(shape(f["geometry"]).bounds), "location": "rest"} for f in v45], 2.0)}
    v45_in = [f for f in v45 if tuple(shape(f["geometry"]).bounds) in kept]
    sets = {"v4.5 boxes in scope": v45_in, "no pile tier": boxes(off), "default": boxes(base)}
    for name, feats in sets.items():
        print(line(f"{name} [all]", score(feats, v5.items)))
    for split, side in halves().items():
        a = lambda g, s=side: s(g)
        b = lambda g, s=side: not s(g)
        print(f"-- {split}: {sum(1 for it in v5.items if it[1] and a(it[0]))} / {sum(1 for it in v5.items if it[1] and b(it[0]))} waste")
        for name, feats in sets.items():
            for tag, s in (("A", a), ("B", b)):
                print(line(f"{name} [{tag}]", score([f for f in feats if s(shape(f['geometry']))], [it for it in v5.items if s(it[0])])))
        for fit_on, test_on, tag in ((a, b, "A->B"), (b, a, "B->A")):
            fitted = fit(fit_on, base)  # from the hand-set tier; (9, 9) area turns it off
            change = {k: getattr(fitted, k) for k in GRID if getattr(fitted, k) != getattr(base, k)}
            print(line(f"pile fitted {tag} {change}"[:44], score([f for f in boxes(fitted) if test_on(shape(f['geometry']))], [it for it in v5.items if test_on(it[0])])))
            print("   fitted:", change)
    print("control: default boxes on the organizer example tiles (must be 0):", sum(1 for f in boxes(base) if EXAMPLES & set(f["properties"]["tiles"])))
