"""v6 rows: predicted row axes against the team's hand-corrected Marcaj rows (data/generated/work/v6/reference/), site-wide
and per reference field. Evaluation only: it reads the reference, prediction code never does.
(a) the judge's axis F1 (per tile, `judge._match` with `_axis_share` >= 0.8 at 0.4 m) over every tile;
(b) length-weighted recall / precision within 0.3 and 0.5 m; (c) median / p90 lateral offset of reference points within
1 m of a predicted row; (d) row-end errors along the row of matched global rows; (e) rows and spacing per field, missing
and extra fields; (f) vineyard_id grouping (the judge's Rand index on matched pieces, and per-field purity).
A prediction holds either per-tile row pieces (`tile` set, as predict writes) or global rows (detect_plots / refit /
verify output), which are cut here as `rows.per_tile` does: stray rows dropped, clipped to each tile's visible part.
Run from backend/: uv run --frozen python ../research/probes/v6_rows_eval.py [pred=path.geojson] [out=name.json] [fields=1]"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import rasterio
import shapely
from rasterio.features import shapes
from rasterio.transform import Affine, from_origin
from scipy import ndimage
from shapely import STRtree
from shapely.geometry import LineString, box, mapping, shape
from shapely.ops import linemerge, unary_union

from marcaj import judge, rows as rows_mod
from marcaj.layers import exg
from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX

V6 = REPO_ROOT / "data" / "generated" / "work" / "v6"
REF = Path(dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a).get("ref", V6 / "reference"))
WORK = V6 / "rows"
EXTENT = TILE_PX * PIXEL_M


def _tile_box(name: str):
    r, c = int(name[8:11]), int(name[13:16])
    left, top = 628992.0 + EXTENT * c, 5221222.4 - EXTENT * r
    return box(left, top - EXTENT, left + EXTENT, top)


def tile_names() -> list[str]:
    return sorted(p.name for p in (DATA_DIR / "tiles").glob("siret3_r*_c*.tif"))


def visible() -> dict[str, object]:
    """Each tile's visible part, as rows.per_tile computes it (cached: reading 311 tiles takes a minute)."""
    cache = WORK / "visible.geojson"
    if cache.exists():
        return {f["properties"]["tile"]: shape(f["geometry"]) for f in json.loads(cache.read_text())["features"]}
    out = {}
    for name in tile_names():
        bounds = _tile_box(name)
        with rasterio.open(DATA_DIR / "tiles" / name) as source:
            _, valid = exg(source.read())
        nodata = ndimage.binary_opening(~ndimage.binary_fill_holes(valid)[::8, ::8], iterations=3)
        left, _, _, top = bounds.bounds
        out[name] = bounds if not nodata.any() else bounds.intersection(unary_union(
            [shape(g) for g, _ in shapes(np.uint8(~nodata), mask=~nodata, transform=from_origin(left, top, PIXEL_M, PIXEL_M) * Affine.scale(8))]).simplify(0.2))
    WORK.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(g), "properties": {"tile": n}} for n, g in out.items()]}))
    return out


def _vid(p: dict) -> str:
    return p.get("vineyard_id") or p.get("block_id") or ""


def pred_pieces(features: list[dict]) -> list[tuple]:
    """(line, properties) per tile piece of every predicted row."""
    row_feats = [f for f in features if f["properties"]["label"] == "row"]
    if row_feats and all("tile" in f["properties"] for f in row_feats):
        return [(shape(f["geometry"]), f["properties"]) for f in row_feats]
    # global rows: blocks keyed by pattern until assign_blocks; the judge sees the block id
    block_of = {f["properties"].get("pattern_id", f["properties"]["vineyard_id"]): f["properties"].get("block_id", f["properties"]["vineyard_id"])
                for f in features if f["properties"]["label"] == "block"}
    kept = {row_id for rows in rows_mod.kept_rows(features).values() for row_id, _ in rows}
    lines = [(shape(f["geometry"]), f["properties"]) for f in row_feats if f["properties"]["row_id"] in kept]
    tree = STRtree([g for g, _ in lines])
    out = []
    for name, area in visible().items():
        for i in tree.query(area, predicate="intersects"):
            g, p = lines[i]
            piece = g.intersection(area)
            parts = [q for q in getattr(piece, "geoms", [piece]) if q.geom_type == "LineString" and q.length > 0]
            merged = linemerge(parts) if len(parts) > 1 else (parts[0] if parts else LineString())
            for q in getattr(merged, "geoms", [merged]):
                if q.length >= PIXEL_M:
                    pattern = p.get("pattern_id") or p["vineyard_id"]
                    out.append((q, {**p, "tile": name, "vineyard_id": block_of.get(pattern, p["vineyard_id"])}))
    return out


def ref_pieces() -> list[tuple]:
    return [(shape(f["geometry"]), f["properties"]) for f in json.loads((REF / "rows_pieces.geojson").read_text())["features"]
            if f["properties"].get("label", "row") == "row"]


def _global(pieces: list[tuple]) -> dict[str, LineString]:
    """One straight line per row_id: the pieces' extreme points along their direction."""
    by: dict[str, list] = defaultdict(list)
    for g, p in pieces:
        by[p.get("row_id") or f"?{id(g)}"].append(np.asarray(g.coords))
    out = {}
    for row_id, parts in by.items():
        xy = np.vstack(parts)
        d = xy[-1] - xy[0] if len(parts) == 1 else np.linalg.svd(xy - xy.mean(0))[2][0]
        d = d / np.linalg.norm(d)
        t = (xy - xy.mean(0)) @ d
        out[row_id] = LineString([xy[np.argmin(t)], xy[np.argmax(t)]])
    return out


def _samples(line: LineString, step: float = 0.5) -> np.ndarray:
    return np.array([line.interpolate(t).coords[0] for t in np.arange(step / 2, line.length, step)]).reshape(-1, 2)


def evaluate(features: list[dict], field_of_ref: dict | None = None) -> dict:
    ref, pred = ref_pieces(), pred_pieces(features)
    ref_field = lambda p: _vid(p) or "?"
    fields = json.loads((REF / "blocks.geojson").read_text())["features"] if (REF / "blocks.geojson").exists() else []
    field_poly = {f["properties"]["vineyard_id"]: shape(f["geometry"]) for f in fields}
    # (a) judge axis F1 per tile
    by_tile_r, by_tile_p = defaultdict(list), defaultdict(list)
    for k, (g, p) in enumerate(ref):
        by_tile_r[p["tile"]].append(k)
    for k, (g, p) in enumerate(pred):
        by_tile_p[p["tile"]].append(k)
    pairs = []
    for tile in set(by_tile_r) | set(by_tile_p):
        r, q = by_tile_r[tile], by_tile_p[tile]
        pairs += [(q[i], r[j]) for i, j in judge._match([pred[k] for k in q], [ref[k] for k in r], judge._axis_share, judge.AXIS_SHARE, reach=judge.AXIS_TOLERANCE_M)]
    matched_p = {i: j for i, j in pairs}
    matched_r = {j: i for i, j in pairs}
    # a predicted piece belongs to the field of its matched reference piece, else of the reference piece it lies nearest
    ref_tree = STRtree([g for g, _ in ref])
    pred_field = []
    for i, (g, p) in enumerate(pred):
        if i in matched_p:
            pred_field.append(ref_field(ref[matched_p[i]][1]))
            continue
        near = [j for j in ref_tree.query(g.buffer(1.5))]
        best = max(near, key=lambda j: g.intersection(ref[j][0].buffer(1.5)).length, default=None)
        pred_field.append(ref_field(ref[best][1]) if best is not None else f"extra:{_vid(p)}")
    # (b) length recall / precision
    ref_u, pred_u = unary_union([g for g, _ in ref]), unary_union([g for g, _ in pred])
    buf = {t: (pred_u.buffer(t), ref_u.buffer(t)) for t in (0.3, 0.5)}
    rl, pl = ref_u.length, pred_u.length
    # (c) lateral offsets and (d) row ends on global rows
    # the reference's global rows as the integration agent joined them (a row_id splits into parts at a jump or gap)
    ref_rows = [f["properties"] | {"geometry": shape(f["geometry"])} for f in json.loads((REF / "rows.geojson").read_text())["features"]]
    ref_g = {f"{p['row_id']}#{p['part']}": p["geometry"] for p in ref_rows}
    ref_row_field = {f"{p['row_id']}#{p['part']}": ref_field(p) for p in ref_rows}
    pred_g = _global(pred)
    pred_names = list(pred_g)
    pred_tree = STRtree([pred_g[n] for n in pred_names])
    offsets, ends, per_row = defaultdict(list), defaultdict(list), {}
    for row_id, line in ref_g.items():
        pts = _samples(line)
        if not len(pts):
            continue
        geoms = shapely.points(pts)
        idx = pred_tree.query_nearest(geoms, max_distance=1.0, return_distance=True, all_matches=False)
        (pi, ti), dist = idx
        field = ref_row_field.get(row_id, "?")
        offsets[field] += list(dist)
        if not len(ti):
            per_row[row_id] = None
            continue
        near = ti[dist <= 0.5]
        if not len(near):
            per_row[row_id] = None
            continue
        best = pred_names[Counter(near.tolist()).most_common(1)[0][0]]
        a, b = np.asarray(line.coords)[[0, -1]]
        d = (b - a) / np.linalg.norm(b - a)
        t = sorted((np.asarray(pred_g[best].coords) - a) @ d)
        e0, e1 = -t[0], t[1] - (b - a) @ d  # >0: the predicted row runs on past the reference end
        ends[field] += [e0, e1]
        per_row[row_id] = best
    report = {"ref_pieces": len(ref), "pred_pieces": len(pred), "tp": len(pairs)}
    report["axis_f1"] = judge._f1(len(pairs), len(pred), len(ref))
    for t in (0.3, 0.5):
        report[f"recall_{t}"] = ref_u.intersection(buf[t][0]).length / rl if rl else None
        report[f"precision_{t}"] = pred_u.intersection(buf[t][1]).length / pl if pl else None
    allo = np.concatenate([np.asarray(v) for v in offsets.values()]) if offsets else np.zeros(0)
    report["offset_med"], report["offset_p90"] = (float(np.median(allo)), float(np.percentile(allo, 90))) if len(allo) else (None, None)
    alle = np.abs(np.concatenate([np.asarray(v) for v in ends.values()])) if ends else np.zeros(0)
    report["end_med"], report["end_p90"], report["end_over_1m"] = (float(np.median(alle)), float(np.percentile(alle, 90)), float((alle > 1).mean())) if len(alle) else (None, None, None)
    report["grouping_rand"] = judge._rand_index([(_vid(ref[j][1]), _vid(pred[i][1])) for i, j in pairs])
    report["ref_rows"], report["pred_rows"] = len(ref_g), len(pred_g)
    # unmatched pieces: absent (under 20% of it within 0.4 m of the other side) or partial (ends, a lateral shift, a split)
    b04 = (pred_u.buffer(0.4), ref_u.buffer(0.4))
    kinds, field_kinds = Counter(), defaultdict(Counter)
    for side, items, matched, other in (("ref", ref, matched_r, b04[0]), ("pred", pred, matched_p, b04[1])):
        for k, (g, p) in enumerate(items):
            if k not in matched:
                kind = f"{side}_{'absent' if g.intersection(other).length < 0.2 * g.length else 'partial'}"
                kinds[kind] += 1
                field_kinds[ref_field(p) if side == "ref" else pred_field[k]][kind] += 1
    report["unmatched"] = dict(kinds)
    # per field
    per = {}
    ref_by_field, pred_by_field = defaultdict(list), defaultdict(list)
    for j, (g, p) in enumerate(ref):
        ref_by_field[ref_field(p)].append(j)
    for i, f in enumerate(pred_field):
        pred_by_field[f].append(i)
    for field in sorted(set(ref_by_field) | set(pred_by_field)):
        rj, pi = ref_by_field.get(field, []), pred_by_field.get(field, [])
        tp = sum(1 for j in rj if j in matched_r)
        rlen = sum(ref[j][0].length for j in rj)
        plen = sum(pred[i][0].length for i in pi)
        rec = sum(ref[j][0].intersection(buf[0.5][0]).length for j in rj) / rlen if rlen else None
        prec = sum(pred[i][0].intersection(buf[0.5][1]).length for i in pi) / plen if plen else None
        o = np.asarray(offsets.get(field, []))
        e = np.abs(np.asarray(ends.get(field, [])))
        blocks = Counter(_vid(pred[matched_r[j]][1]) for j in rj if j in matched_r)
        ref_ids = {r for r, f in ref_row_field.items() if f == field}
        per_ref_rows = [r for r in ref_ids if r in ref_g]
        pred_ids = {pred[i][1].get("row_id") for i in pi}
        spacing = None
        if field in field_poly or len(ref_ids) > 1:
            rows_here = [ref_g[r] for r in per_ref_rows]
            if len(rows_here) > 1:
                a, b = np.asarray(rows_here[0].coords)[[0, -1]]
                n = np.array([-(b - a)[1], (b - a)[0]]) / np.linalg.norm(b - a)
                v = np.sort([np.asarray(l.centroid.coords[0]) @ n for l in rows_here])
                spacing = float(np.median(np.diff(v)))
        pred_spacing = Counter(round(float(p.get("row_spacing_m", 0) or 0), 2) for i in pi for p in [pred[i][1]]).most_common(1)
        per[field] = {"ref_pieces": len(rj), "pred_pieces": len(pi), "tp": tp, "f1": judge._f1(tp, len(pi), len(rj)),
                      "ref_m": round(rlen), "pred_m": round(plen), "recall_0.5": rec, "precision_0.5": prec,
                      "offset_med": float(np.median(o)) if len(o) else None, "end_med": float(np.median(e)) if len(e) else None,
                      "end_p90": float(np.percentile(e, 90)) if len(e) else None,
                      "ref_rows": len({r.split('#')[0] for r in ref_ids}), "pred_rows": len(pred_ids), "ref_spacing": spacing,
                      "pred_blocks": dict(blocks.most_common(4)), "unmatched": dict(field_kinds.get(field, {})), "purity": blocks.most_common(1)[0][1] / sum(blocks.values()) if blocks else None}
    report["fields"] = per
    report["missing_fields"] = [f for f, v in per.items() if v["ref_pieces"] and (v["recall_0.5"] or 0) < 0.5]
    report["extra_fields"] = [f for f, v in per.items() if not v["ref_pieces"]]
    return report


def fmt(v) -> str:
    return "-" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)


def summary_line(r: dict) -> str:
    keys = ("axis_f1", "recall_0.3", "precision_0.3", "recall_0.5", "precision_0.5", "offset_med", "offset_p90", "end_med", "end_p90", "end_over_1m", "grouping_rand")
    return " | ".join(f"{k} {fmt(r[k])}" for k in keys) + f" | pieces pred/ref {r['pred_pieces']}/{r['ref_pieces']} rows {r['pred_rows']}/{r['ref_rows']}"


def field_table(r: dict) -> str:
    head = "| field | F1 | pieces p/r | rec@.5 | prec@.5 | off med | end med/p90 | rows p/r | spacing | purity | unmatched r-abs/r-part/p-abs/p-part |\n|---|---|---|---|---|---|---|---|---|---|---|"
    lines = [head]
    for f, v in sorted(r["fields"].items(), key=lambda kv: (kv[1]["f1"] if kv[1]["f1"] is not None else 0)):
        lines.append(f"| {f} | {fmt(v['f1'])} | {v['pred_pieces']}/{v['ref_pieces']} | {fmt(v['recall_0.5'])} | {fmt(v['precision_0.5'])} | {fmt(v['offset_med'])} | "
                     f"{fmt(v['end_med'])}/{fmt(v['end_p90'])} | {v['pred_rows']}/{v['ref_rows']} | {fmt(v['ref_spacing'])} | {fmt(v['purity'])} | {'/'.join(str(v['unmatched'].get(k, 0)) for k in ('ref_absent', 'ref_partial', 'pred_absent', 'pred_partial'))} |")
    return "\n".join(lines)


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    src = Path(args.get("pred", str(REPO_ROOT / "data" / "generated" / "work" / "v5" / "predictions.geojson")))
    features = [f for f in json.loads(src.read_text())["features"] if f["properties"]["label"] in ("block", "row")]
    report = evaluate(features)
    print(src.name, summary_line(report))
    print("unmatched:", report["unmatched"])
    print("missing fields:", report["missing_fields"], "extra:", report["extra_fields"])
    if args.get("fields") == "1":
        print(field_table(report))
    if "out" in args:
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / args["out"]).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
