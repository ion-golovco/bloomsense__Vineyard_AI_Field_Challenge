"""v6 rows: what trimming each predicted row to its own canopy would score (offline, on a finished prediction's rows
and canopies). Evaluation only. Run from backend/: uv run --frozen python ../research/probes/v6_rows_trim.py [pred=...] [margins=0,0.3,0.6,1.0]"""

import json
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import LineString, mapping, shape

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import v6_rows_eval as ev  # noqa: E402


def trimmed(features: list[dict], margin: float, slack: float, tube: float = 0.35) -> list[dict]:
    """Global rows (one per row_id) trimmed to the along-row extent of their pattern's canopy within `tube` of the row,
    plus `margin`; an end moves only when the canopy stops more than `slack` short of it."""
    pieces = ev.pred_pieces(features)
    rows = ev._global(pieces)
    props = {p["row_id"]: p for _, p in pieces}
    canopy = [f for f in features if f["properties"]["label"] == "vineyard"]
    geoms = [shape(f["geometry"]) for f in canopy]
    pattern = [f["properties"].get("pattern_id") for f in canopy]
    tree = STRtree(geoms)
    out = [f for f in features if f["properties"]["label"] == "block"]
    for row_id, line in rows.items():
        p = props[row_id]
        a, b = np.asarray(line.coords)
        L = np.linalg.norm(b - a)
        d = (b - a) / L
        band = line.buffer(tube, cap_style="flat")
        hits = [geoms[k] for k in tree.query(band, predicate="intersects") if pattern[k] == p.get("pattern_id")]
        u0, u1 = 0.0, L
        if hits:
            xy = np.vstack([np.asarray(part.exterior.coords) for h in hits for part in getattr(shapely.intersection(h, band), "geoms", [shapely.intersection(h, band)]) if part.geom_type == "Polygon"] or [np.zeros((0, 2))])
            if len(xy):
                t = (xy - a) @ d
                lo, hi = t.min() - margin, t.max() + margin
                u0 = lo if lo - u0 > slack else u0
                u1 = hi if u1 - hi > slack else u1
        if u1 - u0 < 2.0:
            continue
        out.append({"type": "Feature", "geometry": mapping(LineString([a + d * u0, a + d * u1])), "properties": {k: v for k, v in p.items() if k != "tile"}})
    return out


def shrunk(features: list[dict], s: float, road_m: float) -> list[dict]:
    """Global rows with each end pulled in by `s` m, except an end within `road_m` of a passage or forbidden zone."""
    from marcaj import plots
    roads = plots.exclusions()
    pieces = ev.pred_pieces(features)
    props = {p["row_id"]: p for _, p in pieces}
    out = [f for f in features if f["properties"]["label"] == "block"]
    for row_id, line in ev._global(pieces).items():
        a, b = np.asarray(line.coords)
        L = np.linalg.norm(b - a)
        d = (b - a) / L
        u0 = 0.0 if roads.distance(shapely.Point(a)) < road_m else s
        u1 = L if roads.distance(shapely.Point(b)) < road_m else L - s
        if u1 - u0 >= 2.0:
            out.append({"type": "Feature", "geometry": mapping(LineString([a + d * u0, a + d * u1])), "properties": {k: v for k, v in props[row_id].items() if k != "tile"}})
    return out


def oracle(features: list[dict], ends: bool, delete: bool, add: bool) -> list[dict]:
    """Upper bounds, not a detector: pred rows given the reference's ends (`ends`), rows with no reference removed
    (`delete`), reference rows nothing predicts added (`add`)."""
    pieces = ev.pred_pieces(features)
    props = {p["row_id"]: p for _, p in pieces}
    pred_g = ev._global(pieces)
    ref = [(shape(f["geometry"]), f["properties"]) for f in json.loads((ev.REF / "rows.geojson").read_text())["features"]]
    names = list(pred_g)
    tree = STRtree([pred_g[n] for n in names])
    best_ref: dict[str, list] = {}
    for g, p in ref:
        pts = ev._samples(g)
        if not len(pts):
            continue
        (_, ti), _ = tree.query_nearest(shapely.points(pts), max_distance=0.5, return_distance=True, all_matches=False)
        if len(ti) >= 0.5 * len(pts):
            best_ref.setdefault(names[np.bincount(ti).argmax()], []).append(g)
        elif add:
            pred_g[f"ADD-{p['row_id']}-{p['part']}"] = g
            props[f"ADD-{p['row_id']}-{p['part']}"] = {"label": "row", "row_id": f"ADD-{p['row_id']}-{p['part']}", "vineyard_id": p["vineyard_id"], "pattern_id": "ADD"}
    out = [f for f in features if f["properties"]["label"] == "block"]
    for n, line in pred_g.items():
        if delete and not n.startswith("ADD") and n not in best_ref:
            continue
        if ends and n in best_ref:
            a, b = np.asarray(line.coords)[[0, -1]]
            d = (b - a) / np.linalg.norm(b - a)
            t = np.concatenate([(np.asarray(g.coords) - a) @ d for g in best_ref[n]])
            line = LineString([a + d * t.min(), a + d * t.max()])
        out.append({"type": "Feature", "geometry": mapping(line), "properties": {k: v for k, v in props[n].items() if k != "tile"}})
    return out


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    src = Path(args.get("pred", str(ev.REPO_ROOT / "data" / "generated" / "work" / "v5" / "predictions.geojson")))
    features = [f for f in json.loads(src.read_text())["features"] if f["properties"]["label"] in ("block", "row", "vineyard")]
    base = [f for f in features if f["properties"]["label"] != "vineyard"]
    if "oracle" in args:
        for combo in args["oracle"].split(","):
            r = ev.evaluate(oracle(base, "e" in combo, "d" in combo, "a" in combo))
            print(f"oracle {combo}", ev.summary_line(r), r["unmatched"])
        return
    if "shrink" in args:
        for s in map(float, args["shrink"].split(",")):
            for road_m in map(float, args.get("road", "2").split(",")):
                r = ev.evaluate(shrunk(base, s, road_m))
                print(f"shrink {s} road {road_m}", ev.summary_line(r), r["unmatched"])
        return
    print("base", ev.summary_line(ev.evaluate(base)))
    for margin in map(float, args.get("margins", "0,0.3,0.6,1.0").split(",")):
        for slack in map(float, args.get("slacks", "0.5").split(",")):
            r = ev.evaluate(trimmed(features, margin, slack))
            print(f"margin {margin} slack {slack}", ev.summary_line(r), r["unmatched"])


if __name__ == "__main__" and not any(a.startswith("outer=") for a in sys.argv[1:]):
    main()


def outer_test() -> None:
    """bare_outer_rows on a finished prediction's rows (joined per row_id) and canopies: v6_rows_trim.py outer=0.2,0.3"""
    from dataclasses import replace
    from marcaj import plots
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    src = Path(args.get("pred", str(ev.REPO_ROOT / "data" / "generated" / "work" / "v5" / "predictions.geojson")))
    features = json.loads(src.read_text())["features"]
    pieces = ev.pred_pieces([f for f in features if f["properties"]["label"] in ("block", "row")])
    props = {p["row_id"]: p for _, p in pieces}
    rows = [{"type": "Feature", "geometry": mapping(g), "properties": {k: v for k, v in props[n].items() if k != "tile"}} for n, g in ev._global(pieces).items()]
    blocks = [f for f in features if f["properties"]["label"] == "block"]
    canopies = [f for f in features if f["properties"]["label"] == "vineyard"]
    for share in map(float, args["outer"].split(",")):
        for low in map(float, args.get("low", "0.2").split(",")):
            kept, _ = plots.bare_outer_rows(blocks + rows, canopies, replace(plots.PlotParams(), verify_outer=share, verify_outer_min=low, verify_outer_n=int(args.get("n", 1))))
            r = ev.evaluate(kept)
            print(f"outer {share} min {low}: dropped {len(rows) + len(blocks) - len(kept)}", ev.summary_line(r), r["unmatched"])


if __name__ == "__main__" and any(a.startswith("outer=") for a in sys.argv[1:]):
    outer_test()
