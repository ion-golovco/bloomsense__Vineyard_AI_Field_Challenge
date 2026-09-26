"""Vine evidence per predicted block (plots.vine_evidence) on a full prediction, against the lab's latest plot outlines
(re-read on every run) and the review list's block verdicts. Evaluation only (reads data/review/). Run from backend/:
uv run --frozen python ../research/probes/plots_verify_evidence.py [src=predictions.geojson] [out=evidence.json]"""

import json
import sys

from shapely.geometry import shape
from shapely.ops import unary_union

from marcaj import plots
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots_verify"
COLUMNS = ("rows", "row_m", "per_10m", "cover", "rows_any", "canopy_share", "len_med", "len_p90", "long_share",
           "width_med", "gap_cv", "spacing_cv", "on_exg", "off_exg", "contrast")


YOUNG = {"23944114e7": "#8", "66617cf702": "#9", "400efd90f0": "#26"}  # the narrow young-vine strips of research/notes/plots.md


def footprints(features: list[dict], evidence: dict) -> dict[str, object]:
    """Each pattern's area as its rows widened by half a spacing (the finished prediction keeps only block polygons)."""
    rows: dict[str, list] = {}
    for f in features:
        p = f["properties"]
        if p["label"] == "row":
            rows.setdefault(p.get("pattern_id") or p["vineyard_id"], []).append(shape(f["geometry"]))
    return {k: unary_union([r.buffer(e["spacing_m"] / 2, cap_style="flat") for r in rows.get(k, [])]) for k, e in evidence.items()}


def overlaps(features: list[dict], evidence: dict) -> dict[str, dict]:
    """Per pattern, the share of its footprint on vineyard / orchard / overgrown outlines, the outline it overlaps
    most, and the latest review-list verdict on its block."""
    verdicts = load_verdicts()
    plots_ = [v for v in verdicts if v["kind"] == "plot"]
    by = {label: unary_union([shape(v["geometry"]) for v in plots_ if v["label"] == label]) for label in ("vineyard", "orchard", "overgrown")}
    said = {v["properties"].get("vineyard_id", ""): v["verdict"] for v in verdicts
            if v["kind"] == "object" and v.get("label") == "block" and v.get("source") == "prediction"}
    out = {}
    for k, g in footprints(features, evidence).items():
        if g.is_empty:
            out[k] = {"vineyard": 0.0, "orchard": 0.0, "overgrown": 0.0, "outline": "", "said": said.get(evidence[k]["block_id"], "")}
            continue
        best = max(plots_, key=lambda v: shape(v["geometry"]).intersection(g).area)
        hit = shape(best["geometry"]).intersection(g).area > 0
        out[k] = {**{label: round(g.intersection(u).area / g.area, 2) for label, u in by.items()},
                  "outline": (YOUNG.get(best["id"], best["label"][:3] + ":" + best["id"][:4])) if hit else "",
                  "said": said.get(evidence[k]["block_id"], "")}
    return out


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    src = REPO_ROOT / "data" / "generated" / args.get("src", "predictions.geojson")
    features = json.loads(src.read_text())["features"]
    evidence = plots.vine_evidence(features, plots.load_excess())
    over = overlaps(features, evidence)
    blocks = {f["properties"]["vineyard_id"]: f["properties"] for f in features if f["properties"]["label"] == "block"}
    print(f"{'pattern':12s} {'block':9s} {'m2':>6s} {'vin':>4s} {'orc':>4s} {'ovg':>4s} outline    said " + " ".join(f"{c[:8]:>8s}" for c in COLUMNS) + "  verify")
    rows = []
    for pattern, e in sorted(evidence.items(), key=lambda item: over[item[0]].get("vineyard", 0)):
        o = over[pattern]
        ok, why = plots.looks_like_vineyard(e)
        print(f"{pattern:12s} {e['block_id']:9s} {e['area_m2']:6.0f} {o.get('vineyard', 0):4.2f} {o.get('orchard', 0):4.2f} {o.get('overgrown', 0):4.2f} "
              f"{o['outline']:10s} {o.get('said', ''):4s} " + " ".join(f"{e[c]:8.3f}" if isinstance(e[c], float) else f"{e[c]:8d}" for c in COLUMNS) + f"  {'keep' if ok else 'DROP ' + why}")
        rows.append({"pattern": pattern, **e, **o, "keep": ok, "why": why})
    if "out" in args:
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / args["out"]).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
