"""The judge on the two organizer tiles for row variants: frozen rows (rows_refit_measure.py) -> variant -> inter-rows
and per-tile attributes (marcaj.rows, as predict.py), with the canopies and waste of the predictions unchanged.
Evaluation only. Run from backend/: PYTHONPATH=../research/probes uv run --frozen python ../research/probes/rows_refit_judge.py"""

import json
import sys
from pathlib import Path

from marcaj import canopy, plots, rows
from marcaj.cvat import build_scene
from marcaj.judge import judge
from marcaj.tiles import DATA_DIR, load_tiles

from rows_refit_measure import PREDICTIONS, WORK, plots_input

NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
EXAMPLES = DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"


def interrow_lines(features, exclusions):
    """rows.interrow_areas with each polygon built from its two actual axes (each offset INTERROW_INSET_M towards the
    other) instead of one plot direction and the axes' centroids: the proposed rows.py change for non-parallel rows."""
    import numpy as np
    from shapely.geometry import Polygon, mapping

    areas = []
    inset = rows.INTERROW_INSET_M
    for plot_id, kept in rows.kept_rows(features).items():
        for (_, a), (_, b) in zip(kept[:-1], kept[1:]):
            da, db = rows._direction(a), rows._direction(b)
            db = db if da @ db > 0 else -db
            along = (da + db) / np.linalg.norm(da + db)
            across = np.array([-along[1], along[0]])
            if (np.asarray(b.centroid.coords[0]) - np.asarray(a.centroid.coords[0])) @ across < 0:
                a, b, da, db = b, a, db, da
            a0, b0 = np.asarray(a.coords[0]), np.asarray(b.coords[0])
            at = lambda p0, d, u, side: p0 + (u - p0 @ along) / (d @ along) * d + side * inset * across
            (ua0, ua1), (ub0, ub1) = (sorted(np.asarray(line.coords) @ along) for line in (a, b))
            u0, u1 = max(ua0, ub0), min(ua1, ub1)
            box = lambda lo, hi: Polygon([at(a0, da, lo, 1), at(a0, da, hi, 1), at(b0, db, hi, -1), at(b0, db, lo, -1)])
            if u1 <= u0 or not box(u0, u1).is_valid or (at(b0, db, u0, -1) - at(a0, da, u0, 1)) @ across <= 0:
                continue
            if rows.EXTEND_M:
                u0 -= rows.EXTEND_M * box(u0 - rows.EXTEND_M, u0).intersects(exclusions)
                u1 += rows.EXTEND_M * box(u1, u1 + rows.EXTEND_M).intersects(exclusions)
            polygon = box(u0, u1).difference(exclusions)
            parts = [part for part in getattr(polygon, "geoms", [polygon]) if part.geom_type == "Polygon"]
            if parts:
                areas.append({"type": "Feature", "geometry": mapping(max(parts, key=lambda part: part.area)),
                              "properties": {"label": "interrow_area", "vineyard_id": plot_id, "interrow_cover": "bare_soil"}})
    return areas


BUILD = {"rows": rows.interrow_areas, "lines": interrow_lines}


def features_for(variant_rows, others, tiles, exclusions, build="rows"):
    found = variant_rows + BUILD[build](variant_rows, exclusions)
    return plots.assign_blocks(rows.per_tile(found + others, tiles))


def attribute_detail(scene_features):
    """row_structure and interrow_cover scores apart, and the median IoU of matched inter-rows, per tile."""
    from shapely.geometry import shape

    from marcaj.judge import _attribute_score, _axis_share, _iou, _match, _objects
    from marcaj.cvat import document, image_elements, read_cvat
    from marcaj.judge import _tiles
    tiles = _tiles({"features": scene_features})
    reference = [f for f in scene_features if f["properties"].get("source") == "reference"]
    predicted = [f for f in scene_features if f["properties"].get("source") == "prediction"]
    uploaded = read_cvat(document(list(image_elements(predicted, [tiles[n] for n in NAMES]).values())), tiles, source="prediction")
    out = {}
    import numpy as np
    for name in NAMES:
        ref = {l: _objects(reference, l, name) for l in ("row", "interrow_area")}
        pred = {l: _objects(uploaded, l, name) for l in ("row", "interrow_area")}
        rp = _match(pred["row"], ref["row"], _axis_share, 0.8, reach=0.4)
        ip = _match(pred["interrow_area"], ref["interrow_area"], _iou, 0.5)
        guess = {j: pred["row"][i][1].get("row_structure") for i, j in rp}
        rs = [(p.get("row_structure"), guess.get(j, "missing")) for j, (_, p) in enumerate(ref["row"])]
        cg = {j: pred["interrow_area"][i][1].get("interrow_cover") for i, j in ip}
        cs = [(p.get("interrow_cover"), cg.get(j, "missing")) for j, (_, p) in enumerate(ref["interrow_area"])]
        ious = [_iou(pred["interrow_area"][i][0], ref["interrow_area"][j][0]) for i, j in ip]
        out[name[7:16]] = {"row_wrong": [x for x in rs if x[0] != x[1]], "cover_wrong": [x for x in cs if x[0] != x[1]],
                           "interrow_iou_median": round(float(np.median(ious)), 3), "interrow_iou_min": round(float(min(ious)), 3)}
    return out


def overlap(variant_rows, canopies, exclusions, build="rows"):
    """Canopy area inside the (global) inter-rows, site-wide."""
    from shapely import STRtree
    from shapely.geometry import shape
    polygons = [shape(f["geometry"]) for f in BUILD[build](variant_rows, exclusions)]
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    total = 0.0
    for p in polygons:
        for j in tree.query(p, predicate="intersects"):
            total += p.intersection(geoms[j]).area
    return total, sum(p.area for p in polygons)


def score(features, all_tiles):
    scene = build_scene([EXAMPLES], features, tiles=all_tiles)
    report = judge(scene)
    print("   ", attribute_detail(scene["features"]))
    s = report["scores"]
    per = {t["tile"][7:16]: (round(t["axis_f1"], 3), round(t["interrow_f1"], 3)) for t in report["tiles"]}
    m = report["measures"]["scores"]
    return (f"points {report['points']:.2f} | canopy {s['canopy']:.3f} axes {s['axes']:.3f} attributes {s['attributes']:.3f} "
            f"counts {s['counts']:.3f} (rows {m['row_count']:.3f} interrow {m['interrow_area_m2']:.3f} length {m['row_length_m']:.3f}) | per tile axes/inter-row {per}")


def main(variants):
    all_tiles = load_tiles()
    tiles = [t for t in all_tiles if t.name in NAMES]
    frozen = plots_input()
    predicted = json.loads(PREDICTIONS.read_text())["features"]
    canopies = [f for f in predicted if f["properties"]["label"] == "vineyard"]
    others = [f for f in predicted if f["properties"]["label"] in ("vineyard", "waste")]
    exclusions = plots.exclusions()
    for name, kwargs in variants:
        kwargs = dict(kwargs or {})
        build = kwargs.pop("build", "rows")
        base = frozen if kwargs.pop("lattice", False) else canopy.refit_rows(frozen, canopies, **kwargs)[0]
        print(f"{name:28s} {score(features_for(base, others, tiles, exclusions, build), all_tiles)}", flush=True)
        area, total = overlap(base, canopies, exclusions, build)
        print(f"    site: canopy inside inter-rows {area:.0f} m2 of {total:.0f} m2 inter-row", flush=True)


VARIANTS = {
    "lattice": dict(lattice=True),
    "shared": dict(),
    "per_row": dict(per_row=True),
    "lattice_lines": dict(lattice=True, build="lines"),
    "shared_lines": dict(build="lines"),
    "per_row_lines": dict(per_row=True, build="lines"),
}

if __name__ == "__main__":
    names = sys.argv[1:] or list(VARIANTS)
    main([(n, VARIANTS[n]) for n in names])
