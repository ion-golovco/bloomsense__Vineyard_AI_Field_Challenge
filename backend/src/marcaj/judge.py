"""Local judge: the organizers' formulas, run on the tiles we hold reference annotations for,
plus regression checks of the current predictions against recorded human verdicts, and predicted
plots scored against the hand-drawn plot outlines."""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from shapely import STRtree
from shapely.geometry import shape
from shapely.ops import unary_union

from marcaj.cvat import document, image_elements, read_cvat
from marcaj.review import load_verdicts
from marcaj.scene import PREDICTION, load_projected_scene
from marcaj.tiles import Tile

AXIS_TOLERANCE_M = 0.4
AXIS_SHARE = 0.8
# Tune the plot detector north of this line and report the south as held out
PLOT_SPLIT_NORTHING = 5219600.0
TOLERANCES = {"block_count": 0.15, "row_count": 0.15, "canopy_area_m2": 0.15, "interrow_area_m2": 0.15, "row_length_m": 0.10}
POINTS = {"canopy": 25, "axes": 8, "attributes": 5, "grouping": 2, "counts": 10, "waste": 10}
Obj = tuple[Any, dict[str, Any]]


def _tiles(scene: dict[str, Any]) -> dict[str, Tile]:
    tiles = {}
    for feature in scene["features"]:
        if feature["properties"].get("label") == "tile":
            left, _, _, top = shape(feature["geometry"]).bounds
            name = feature["properties"]["tile"]
            tiles[name] = Tile(name, "", Path(), 0, 0, left, top)
    return tiles


def _objects(features: list[dict[str, Any]], label: str, tile: str) -> list[Obj]:
    return [(shape(f["geometry"]), f["properties"]) for f in features
            if f["properties"].get("label") == label and f["properties"].get("tile") == tile]


def _iou(a, b) -> float:
    union = a.union(b).area
    return a.intersection(b).area / union if union else 0.0


def _axis_share(a, b) -> float:
    if not a.length or not b.length:
        return 0.0
    return min(a.intersection(b.buffer(AXIS_TOLERANCE_M)).length / a.length, b.intersection(a.buffer(AXIS_TOLERANCE_M)).length / b.length)


def _match(predicted: list[Obj], reference: list[Obj], score, threshold: float, reach: float = 0.0) -> list[tuple[int, int]]:
    if not predicted or not reference:
        return []
    index = STRtree([geometry for geometry, _ in reference])
    candidates = []
    for i, (geometry, _) in enumerate(predicted):
        for j in index.query(geometry.buffer(reach) if reach else geometry):
            value = score(geometry, reference[j][0])
            if value >= threshold:
                candidates.append((value, i, int(j)))
    pairs, used_p, used_r = [], set(), set()
    for _, i, j in sorted(candidates, reverse=True):
        if i not in used_p and j not in used_r:
            pairs.append((i, j))
            used_p.add(i)
            used_r.add(j)
    return pairs


def _f1(matched: int, predicted: int, reference: int) -> float | None:
    return 2 * matched / (predicted + reference) if predicted + reference else None


def _attribute_score(truth: list[str], guess: list[str]) -> float | None:
    if not truth:
        return None
    accuracy = sum(t == g for t, g in zip(truth, guess)) / len(truth)
    f1s = []
    for label in set(truth):
        tp = sum(t == g == label for t, g in zip(truth, guess))
        fp = sum(g == label != t for t, g in zip(truth, guess))
        fn = sum(t == label != g for t, g in zip(truth, guess))
        f1s.append(2 * tp / (2 * tp + fp + fn))
    return (accuracy + sum(f1s) / len(f1s)) / 2


def _rand_index(pairs: list[tuple[str, str]]) -> float | None:
    def c2(n: int) -> int:
        return n * (n - 1) // 2
    total = c2(len(pairs))
    if not total:
        return None
    both = sum(c2(n) for n in Counter(pairs).values())
    same_ref = sum(c2(n) for n in Counter(r for r, _ in pairs).values())
    same_pred = sum(c2(n) for n in Counter(p for _, p in pairs).values())
    return (total + 2 * both - same_ref - same_pred) / total


def _measures(features: list[dict[str, Any]]) -> dict[str, float]:
    by_label: dict[str, list] = {}
    for f in features:
        by_label.setdefault(f["properties"].get("label"), []).append(f)
    area = lambda label: unary_union([shape(f["geometry"]) for f in by_label.get(label, [])]).area if by_label.get(label) else 0.0
    return {
        "block_count": len({f["properties"].get("vineyard_id") for label in ("vineyard", "row", "interrow_area") for f in by_label.get(label, []) if f["properties"].get("vineyard_id")}),
        "row_count": len({f["properties"].get("row_id") for f in by_label.get("row", [])}),
        "canopy_area_m2": area("vineyard"),
        "interrow_area_m2": area("interrow_area"),
        "row_length_m": sum(shape(f["geometry"]).length for f in by_label.get("row", [])),
    }


def _regressions(predicted: list[dict[str, Any]], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Checks the current predictions against past verdicts: a wrong object should be gone, a right one
    still there, and a missed marker now covered (half its area for polygons, touched otherwise)."""
    by_label: dict[str, list] = {}
    for f in predicted:
        by_label.setdefault(f["properties"].get("label"), []).append(shape(f["geometry"]))
    trees = {label: (STRtree(geometries), geometries) for label, geometries in by_label.items()}
    outcome: Counter = Counter()
    missed_area = found_area = 0.0
    for verdict in verdicts:
        if verdict["kind"] not in ("object", "missed") or (verdict["kind"] == "object" and verdict.get("source") != PREDICTION):
            continue
        old = shape(verdict["geometry"])
        tree, geometries = trees.get(verdict["label"], (None, []))
        hits = [] if tree is None else [geometries[i] for i in tree.query(old.buffer(AXIS_TOLERANCE_M))]
        if verdict["kind"] == "missed":
            if old.area:
                covered = old.intersection(unary_union(hits)).area if hits else 0.0
                missed_area += old.area
                found_area += covered
                found = covered >= 0.5 * old.area
            else:
                found = any(g.distance(old) <= 0.5 for g in hits)
            outcome[f"missed_{'found' if found else 'still_missed'}"] += 1
            continue
        still = any((_iou(old, g) >= 0.5) if old.area else (_axis_share(old, g) >= AXIS_SHARE) for g in hits)
        outcome[f"{verdict['verdict']}_{'still_there' if still else 'gone'}"] += 1
    report: dict[str, Any] = dict(outcome)
    if missed_area:
        report["missed_area_recall"] = round(found_area / missed_area, 3)
    return report


def plot_scores(predicted: list[dict[str, Any]], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Predicted `block` polygons against the `vineyard` plot outlines, per half of the site. F1 counts one-to-one
    matches at IoU 0.5 and 0.75. `overgrown` outlines are neither right nor wrong, so area on them is not false."""
    blocks = [(shape(f["geometry"]), f["properties"]) for f in predicted if f["properties"].get("label") == "block"]
    outlines = {label: [(shape(v["geometry"]), v) for v in verdicts if v["kind"] == "plot" and v["label"] == label] for label in ("vineyard", "orchard", "overgrown")}
    report: dict[str, Any] = {}
    for half, north in (("north", True), ("south", False)):
        side = lambda objects: [o for o in objects if (o[0].centroid.y >= PLOT_SPLIT_NORTHING) == north]
        pred, ref = side(blocks), side(outlines["vineyard"])
        if not ref:
            continue
        pred_union, ref_union = unary_union([g for g, _ in pred]), unary_union([g for g, _ in ref])
        free = pred_union.difference(ref_union).difference(unary_union([g for g, _ in side(outlines["overgrown"])]))
        best = sorted(max((_iou(r, g) for g, _ in pred), default=0.0) for r, _ in ref)
        report[half] = {
            "outlines": len(ref), "predicted": len(pred),
            "f1_50": _f1(len(_match(pred, ref, _iou, 0.5)), len(pred), len(ref)),
            "f1_75": _f1(len(_match(pred, ref, _iou, 0.75)), len(pred), len(ref)),
            "median_best_iou": best[len(best) // 2],
            "area_iou": _iou(pred_union, ref_union),
            "false_m2": free.area,
            "on_orchard_m2": pred_union.intersection(unary_union([g for g, _ in side(outlines["orchard"])])).area,
        }
    return report


def judge(scene: dict[str, Any]) -> dict[str, Any]:
    tiles = _tiles(scene)
    reference = [f for f in scene["features"] if f["properties"].get("source") == "reference"]
    predicted = [f for f in scene["features"] if f["properties"].get("source") == PREDICTION]
    reference_tiles = sorted({f["properties"]["tile"] for f in reference if f["properties"].get("tile") in tiles})
    verdicts = load_verdicts()
    no_vineyard = sorted({v["tile"] for v in verdicts if v["kind"] == "tile" and v["verdict"] == "no_vineyard"} & set(tiles))
    judged = sorted(set(reference_tiles) | set(no_vineyard))
    uploaded = read_cvat(document(list(image_elements(predicted, [tiles[name] for name in judged]).values())), tiles, source=PREDICTION) if judged else []

    totals: Counter = Counter()
    per_tile, rows_true, rows_guess, cover_true, cover_guess, groups = [], [], [], [], [], []
    for name in reference_tiles:
        ref = {label: _objects(reference, label, name) for label in ("vineyard", "row", "interrow_area", "waste")}
        pred = {label: _objects(uploaded, label, name) for label in ("vineyard", "row", "interrow_area", "waste")}
        canopy_pairs = _match(pred["vineyard"], ref["vineyard"], _iou, 0.5)
        row_pairs = _match(pred["row"], ref["row"], _axis_share, AXIS_SHARE, reach=AXIS_TOLERANCE_M)
        inter_pairs = _match(pred["interrow_area"], ref["interrow_area"], _iou, 0.5)
        waste_pairs = _match(pred["waste"], ref["waste"], _iou, 0.3)
        ref_union = unary_union([g for g, _ in ref["vineyard"]])
        pred_union = unary_union([g for g, _ in pred["vineyard"]])
        totals["canopy_intersection"] += ref_union.intersection(pred_union).area
        totals["canopy_union"] += ref_union.union(pred_union).area
        for key, pairs in (("vineyard", canopy_pairs), ("row", row_pairs), ("interrow_area", inter_pairs), ("waste", waste_pairs)):
            totals[f"{key}_tp"] += len(pairs)
            totals[f"{key}_pred"] += len(pred[key])
            totals[f"{key}_ref"] += len(ref[key])
            groups.extend((ref[key][j][1].get("vineyard_id", ""), pred[key][i][1].get("vineyard_id", "")) for i, j in pairs)
        row_guess = {j: pred["row"][i][1].get("row_structure", "") for i, j in row_pairs}
        rows_true += [p.get("row_structure", "") for _, p in ref["row"]]
        rows_guess += [row_guess.get(j, "missing") for j in range(len(ref["row"]))]
        cover_guess_by_ref = {j: pred["interrow_area"][i][1].get("interrow_cover", "") for i, j in inter_pairs}
        cover_true += [p.get("interrow_cover", "") for _, p in ref["interrow_area"]]
        cover_guess += [cover_guess_by_ref.get(j, "missing") for j in range(len(ref["interrow_area"]))]
        per_tile.append({
            "tile": name,
            "canopy_iou": round(_iou(ref_union, pred_union), 3) if not ref_union.is_empty or not pred_union.is_empty else None,
            "canopy_f1": _f1(len(canopy_pairs), len(pred["vineyard"]), len(ref["vineyard"])),
            "axis_f1": _f1(len(row_pairs), len(pred["row"]), len(ref["row"])),
            "interrow_f1": _f1(len(inter_pairs), len(pred["interrow_area"]), len(ref["interrow_area"])),
            "counts": {label: [len(pred[label]), len(ref[label])] for label in ref},
        })

    canopy_iou = totals["canopy_intersection"] / totals["canopy_union"] if totals["canopy_union"] else None
    canopy_f1 = _f1(totals["vineyard_tp"], totals["vineyard_pred"], totals["vineyard_ref"])
    tile_area = 51.2 * 51.2
    penalties = {name: 0.5 * unary_union([shape(f["geometry"]) for f in uploaded if f["properties"].get("tile") == name and f["properties"].get("label") == "vineyard"]).area / tile_area for name in no_vineyard}
    ref_measures = _measures([f for f in reference if f["properties"].get("tile") in reference_tiles])
    pred_measures = _measures([f for f in uploaded if f["properties"].get("tile") in reference_tiles])
    counts = {key: max(0.0, 1 - abs(pred_measures[key] - ref_measures[key]) / ref_measures[key] / tolerance) if ref_measures[key] else None
              for key, tolerance in TOLERANCES.items()}
    attributes = [value for value in (_attribute_score(rows_true, rows_guess), _attribute_score(cover_true, cover_guess)) if value is not None]
    scores = {
        "canopy": 0.6 * canopy_iou + 0.4 * canopy_f1 if canopy_iou is not None and canopy_f1 is not None else None,
        "axes": _f1(totals["row_tp"], totals["row_pred"], totals["row_ref"]),
        "attributes": sum(attributes) / len(attributes) if attributes else None,
        "grouping": _rand_index(groups),
        "counts": sum(v for v in counts.values() if v is not None) / len([v for v in counts.values() if v is not None]) if any(v is not None for v in counts.values()) else None,
        "waste": _f1(totals["waste_tp"], totals["waste_pred"], totals["waste_ref"]),
    }
    available = {key: POINTS[key] for key, value in scores.items() if value is not None}
    return {
        "reference_tiles": reference_tiles,
        "scores": scores,
        "points": round(sum(POINTS[key] * scores[key] for key in available), 2),
        "points_available": sum(available.values()),
        "canopy_iou": canopy_iou, "canopy_f1": canopy_f1,
        "false_canopy_penalty": penalties,
        "measures": {"predicted": pred_measures, "reference": ref_measures, "scores": counts},
        "tiles": per_tile,
        "verdicts": {"total": len(verdicts), **Counter(f"{v['kind']}:{v['verdict']}" for v in verdicts), "regressions": _regressions(predicted, verdicts)},
        "plots": plot_scores(predicted, verdicts),
        "predictions": len(predicted),
    }


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Score scene predictions against the reference tiles with the organizers' formulas")
    parser.add_argument("--json", type=Path, help="also write the full report here")
    args = parser.parse_args()
    report = judge(load_projected_scene())
    print_report(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")


def print_report(report: dict[str, Any]) -> None:
    print(f"{report['predictions']} predicted objects, reference tiles: {', '.join(report['reference_tiles']) or 'none'}")
    for tile in report["tiles"]:
        print(f"  {tile['tile']}: canopy IoU {_fmt(tile['canopy_iou'])} F1 {_fmt(tile['canopy_f1'])} | axes F1 {_fmt(tile['axis_f1'])} | inter-row F1 {_fmt(tile['interrow_f1'])} | predicted/reference {tile['counts']}")
    for key, value in report["scores"].items():
        print(f"  {key:10s} {_fmt(value)}  x {POINTS[key]} pts")
    print(f"estimate {report['points']} of {report['points_available']} available points (route 25 and engineering 15 are judged elsewhere)")
    if report["false_canopy_penalty"]:
        print(f"false-canopy penalty on tiles reviewed as no-vineyard: {report['false_canopy_penalty']}")
    print(f"verdicts: {report['verdicts']}")
    for half, score in report["plots"].items():
        print(f"plots {half} (split at northing {PLOT_SPLIT_NORTHING:.0f}): {score['predicted']} predicted vs {score['outlines']} outlines | "
              f"F1@0.5 {_fmt(score['f1_50'])} F1@0.75 {_fmt(score['f1_75'])} | median best IoU {_fmt(score['median_best_iou'])} | "
              f"area IoU {_fmt(score['area_iou'])} | false {score['false_m2']:.0f} m², on orchards {score['on_orchard_m2']:.0f} m²")


if __name__ == "__main__":
    main()
