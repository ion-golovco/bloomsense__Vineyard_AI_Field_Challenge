"""Precision and recall of marcaj.waste's verifier on every waste label (research tooling; reads data/review/, which
prediction code never does). Labelled set, one entry per object (a user answer wins over an older eye verdict it
overlaps):
- user: review-tool "waste" / "not waste" answers and map "waste here" marks (data/review/verdicts.json);
- checklist: the north and south checklists' "likely" items the user has not answered;
- eye: the earlier eye verdicts, "likely" or "not" (south_verdicts.json, the site-wide v1 labels.json and
  detector_review.json).
Candidates come from data/generated/work/waste/candidates_site.json, written by `python -m marcaj.waste`.
Run from backend/: uv run --frozen python ../research/review/eval_waste.py [--misses]"""

import json
import sys
from dataclasses import replace
from typing import Any

from shapely import STRtree
from shapely.geometry import box, shape
from shapely.ops import unary_union

from marcaj import review, waste
from marcaj.tiles import PIXEL_M, REPO_ROOT, TILE_PX

GEN = REPO_ROOT / "data" / "generated"
WASTE_DIR = GEN / "work" / "waste"
GRID_LEFT, GRID_TOP, TILE_M = 628992.0, 5221222.4, TILE_PX * PIXEL_M
GROUPS = ("interrow", "block", "headland", "outside")


def _px_box(tile: str, x0: float, y0: float, x1: float, y1: float) -> Any:
    left, top = GRID_LEFT + TILE_M * int(tile[13:16]), GRID_TOP - TILE_M * int(tile[8:11])
    return box(left + x0 * PIXEL_M, top - y1 * PIXEL_M, left + x1 * PIXEL_M, top - y0 * PIXEL_M)


def labelled() -> list[tuple[Any, bool, str]]:
    """(EPSG:32635 geometry, is_waste, source) per labelled object."""
    user: list[tuple[Any, bool, str]] = []
    answered: set[str] = set()
    flagged = {it["id"] for it in json.loads((GEN / "work" / "review" / "waste.json").read_text(encoding="utf-8")) if it.get("rule_flags")}
    for v in review.load_verdicts():
        p = v.get("properties") or {}
        if v["kind"] == "object" and p.get("review_tab") == "waste":
            answered |= {p["review_id"], *p.get("checklist_ids", [])}
            if p["review_answer"] in ("waste", "not_waste"):
                positive = p["review_answer"] == "waste"
                user.append((shape(v["geometry"]), positive, "user flagged" if positive and p["review_id"] in flagged else "user"))
        elif v["kind"] == "missed" and v["label"] == "waste":
            user.append((shape(v["geometry"]).buffer(0.3), True, "user"))
    other: list[tuple[Any, bool, str]] = []
    for it in json.loads((GEN / "work" / "review" / "waste.json").read_text(encoding="utf-8")):
        if it.get("prior") == "likely" and it["id"] not in answered and not answered & set(it.get("checklist_ids", [])):
            other.append((box(*it["box"]), True, "checklist"))
    for v in json.loads((WASTE_DIR / "south_verdicts.json").read_text(encoding="utf-8")):
        if v["verdict"] in ("likely", "not"):
            x, y, w, h = v["x"], v["y"], v["w_px"], v["h_px"]
            other.append((_px_box(f"siret3_{v['tile']}.tif", x - w / 2, y - h / 2, x + w / 2, y + h / 2), v["verdict"] == "likely", "eye"))
    for key, v in json.loads((WASTE_DIR / "labels.json").read_text(encoding="utf-8")).items():
        if v["label"] in ("likely", "not"):
            tile, px = key.split("|")
            other.append((_px_box(tile, *map(float, px.split(","))), v["label"] == "likely", "eye"))
    for v in json.loads((WASTE_DIR / "detector_review.json").read_text(encoding="utf-8")):
        if v["verdict"] in ("likely", "not"):
            other.append((_px_box(v["tile"], *v["px"]), v["verdict"] == "likely", "eye"))
    tree = STRtree([g for g, _, _ in user]) if user else None
    kept = list(user)
    for g, positive, source in other:
        if tree is None or not len(tree.query(g.buffer(0.2))):
            kept.append((g, positive, source))
            tree = STRtree([k[0] for k in kept])
    return kept


def locate(geometry: Any, context: waste.Context, interrow_union: Any, reach_m: float = 10.0) -> str:
    centre = geometry.centroid
    if interrow_union.contains(centre):
        return "interrow"
    if any(p.contains(centre) for p, _ in context.blocks):
        return "block"
    return "headland" if waste.vineyard_id(geometry, context.blocks, reach_m) else "outside"


def evaluate(name: str, found: list[dict[str, Any]], items: list[tuple[Any, bool, str, str]]) -> None:
    tree = STRtree([g for g, _, _, _ in items])
    counts = {group: [0, 0, 0] for group in GROUPS}  # TP, FP, unlabelled boxes
    for f in found:
        g = shape(f["geometry"])
        hits = [items[i] for i in tree.query(g.buffer(0.2))]
        counts[f["properties"]["location"]][0 if any(pos for _, pos, _, _ in hits) else 1 if hits else 2] += 1
    found_tree = STRtree([shape(f["geometry"]) for f in found]) if found else None
    hit = lambda g: found_tree is not None and len(found_tree.query(g.buffer(0.2))) > 0
    parts = []
    for group in GROUPS:
        tp, fp, unlabelled = counts[group]
        positives = [g for g, pos, _, where in items if pos and where == group]
        parts.append(f"{group} {tp}/{fp}/{unlabelled} rec {sum(map(hit, positives))}/{len(positives)}")
    tp, fp, unlabelled = (sum(c[i] for c in counts.values()) for i in range(3))
    positives = [g for g, pos, _, _ in items if pos]
    print(f"{name:30s} boxes {len(found):3d} TP {tp:2d} FP {fp:2d} unlab {unlabelled:3d} prec {tp / max(tp + fp, 1):.2f} "
          f"rec {sum(map(hit, positives))}/{len(positives)} | " + " | ".join(parts))


VARIANTS: dict[str, dict[str, Any]] = {
    "before: inter-rows only": {"scope": "interrow"},
    "now": {},
    "outside: v1 verifier": {"rest_area_m2": (0.10, 1.5), "rest_clipped": 0.25, "rest_chroma": 255.0, "rest_row_m": 0.9, "rest_ring_green": 0.4},
    "near: as outside (lum 225, density 0.03, row 1.5)": {"near_lum": 225.0, "near_density": 0.03, "near_row_m": 1.5},
    "near: lum>=218 row>=0.9 chroma<=25": {"near_lum": 218.0, "near_row_m": 0.9, "rest_chroma": 25.0},
    "outside: area>=0.05": {"rest_area_m2": (0.05, 1.5)},
    "outside: density<=0.05": {"rest_density": 0.05},
    "outside: lum>=228": {"rest_lum": 228.0},
}


def main() -> None:
    predictions = json.loads((GEN / "predictions.geojson").read_text(encoding="utf-8"))["features"]
    path = WASTE_DIR / "candidates_site.json"
    if not path.is_file() or path.stat().st_mtime < (GEN / "predictions.geojson").stat().st_mtime:
        raise SystemExit("candidates_site.json is missing or older than predictions.geojson: run uv run --frozen python -m marcaj.waste")
    found = json.loads(path.read_text(encoding="utf-8"))
    context = waste.load_context(predictions)
    union = unary_union([p for p, _ in context.interrows])
    items = [(g, pos, source, locate(g, context, union)) for g, pos, source in labelled()]
    summary = {(source, pos): 0 for _, pos, source, _ in items}
    for _, pos, source, _ in items:
        summary[source, pos] += 1
    print(f"{len(found)} candidates; labelled {len(items)}: " + ", ".join(f"{source} {'waste' if pos else 'not'} {n}" for (source, pos), n in sorted(summary.items())))
    print("per location: TP/FP/unlabelled boxes, recall of labelled waste")
    base = waste.WasteParams()
    conform = [it for it in items if it[2] != "user flagged"]  # the user's waste answers the rule flags question are left out
    for label_set, chosen_items in (("all labels", items), ("rule-conform labels (flagged waste answers left out)", conform)):
        print(f"-- {label_set}: {sum(1 for it in chosen_items if it[1])} waste, {sum(1 for it in chosen_items if not it[1])} not waste")
        for name, change in VARIANTS.items():
            params = replace(base, **change)
            chosen = [c for c in found if params.scope == "site" or c["location"] == "interrow"]
            evaluate(name, waste.boxes(chosen, predictions, params), chosen_items)
    if "--misses" in sys.argv:
        boxes_now = waste.boxes(found, predictions, base)
        tree = STRtree([shape(f["geometry"]) for f in boxes_now])
        ctree = STRtree([box(*c["box"]) for c in found])
        for g, pos, source, where in items:
            if pos and not len(tree.query(g.buffer(0.2))):
                near = [found[i] for i in ctree.query(g.buffer(0.2))]
                c = max(near, key=lambda c: c["area_m2"], default=None)
                stats = "" if c is None else (f"{c['kind']} a{c['area_m2']:.3f} l{c['lum']:.0f} c{c['chroma']:.0f} d{c['dev']:.0f} row{c['row_m']:.1f} "
                                              + (f"fill{c['fill']:.2f} clip{c['clipped']:.2f} std{c['lum_std']:.0f} green{c['ring_green']:.2f} dens{c['density']:.3f} "
                                                 f"struct{c['structure']:.2f} bld{c['forbidden_m']:.0f}" if "fill" in c else ""))
                print("MISS", where, source, c["tile"][7:16] if c else "", c["px"][:2] if c else "", stats)


if __name__ == "__main__":
    main()
