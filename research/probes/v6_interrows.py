"""v6: inter-rows and row / inter-row attributes built from the team's reference rows (predict.carry_plots, as the carry
path does), on the two organizer tiles, with the organizer formulas: inter-row F1, attributes (row_structure and
interrow_cover confusion), axes, counts. Canopies from the oracle canopy stage (defaults). Evaluation only.
Run from backend/: uv run --frozen --group sam python ../research/probes/v6_interrows.py"""

import json
import sys
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import rows  # noqa: E402
from marcaj.canopy import CanopyParams  # noqa: E402
from marcaj.cvat import build_scene, document, image_elements, read_cvat  # noqa: E402
from marcaj.judge import AXIS_SHARE, AXIS_TOLERANCE_M, _axis_share, _iou, _match, judge  # noqa: E402
from marcaj.plots import exclusions  # noqa: E402
from marcaj.predict import carry_plots  # noqa: E402
from marcaj.scene import PREDICTION  # noqa: E402
from marcaj.tiles import DATA_DIR  # noqa: E402

TILES = [L.TILES[n] for n in L.NAMES]
BASE = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(L.TILES.values()))["features"]
REFERENCE = [f for f in BASE if f["properties"]["source"] == "reference"]


def merge_lanes(axes: list[dict], part_m: float = 0.6) -> list[dict]:
    """Collinear inter-row axes of one pattern (across positions within `part_m`, any row_id) merged into one."""
    import numpy as np
    from shapely.geometry import LineString, mapping, shape
    by: dict[str, list] = {}
    for a in axes:
        by.setdefault(a["properties"]["vineyard_id"], []).append(shape(a["geometry"]))
    out = []
    for pid, lines in by.items():
        d = rows._direction(max(lines, key=lambda g: g.length))
        n = np.array([-d[1], d[0]])
        lines.sort(key=lambda g: float(np.asarray(g.centroid.coords[0]) @ n))
        lanes: list[list] = []
        for g in lines:
            v = float(np.asarray(g.centroid.coords[0]) @ n)
            if lanes and v - lanes[-1][-1][0] <= part_m:
                lanes[-1].append((v, g))
            else:
                lanes.append([(v, g)])
        for k, lane in enumerate(lanes):
            t = np.concatenate([np.asarray(g.coords) @ d for _, g in lane])
            v = sum(v * g.length for v, g in lane) / sum(g.length for _, g in lane)
            out.append({"type": "Feature", "geometry": mapping(LineString([d * t.min() + n * v, d * t.max() + n * v])),
                        "properties": {"label": "row", "vineyard_id": pid, "row_id": f"{pid}|{k:05d}"}})
    return out


MERGE = False
QUADS = False


def build(canopies: list[dict]) -> list[dict]:
    from v6_interrow_quads import interrow_quads, refit_axes
    given = json.loads((L.REF / "rows.geojson").read_text())["features"]
    found, axes = carry_plots(given)
    axes = merge_lanes(axes) if MERGE else axes
    axes = refit_axes(axes, given) if QUADS else axes
    with mock.patch.object(rows, "STRAY_SHARE", 0.0):
        inter = (interrow_quads if QUADS else rows.interrow_areas)([f for f in found if f["properties"]["label"] != "row"] + axes, exclusions(), [])
        out = rows.per_tile(found + inter + canopies, TILES)
    for f in out:
        if "carry_row_id" in f["properties"]:
            f["properties"]["row_id"] = f["properties"].pop("carry_row_id")
    return out


def report(tag: str, features: list[dict]) -> None:
    marked = [{**f, "properties": {**f["properties"], "source": PREDICTION}} for f in features]
    r = judge({"type": "FeatureCollection", "crs": "EPSG:32635", "features": BASE + marked})
    s, t, m = r["scores"], {x["tile"]: x for x in r["tiles"]}, r["measures"]
    print(f"{tag}: inter-row F1 {t[L.NAMES[0]]['interrow_f1']:.3f} / {t[L.NAMES[1]]['interrow_f1']:.3f} | axes {t[L.NAMES[0]]['axis_f1']:.3f} / "
          f"{t[L.NAMES[1]]['axis_f1']:.3f} | attributes {s['attributes']:.3f} | counts {s['counts']:.3f} | grouping {s['grouping']:.3f} | "
          f"canopy {s['canopy']:.4f} | points {r['points']}")
    print("  measures " + " | ".join(f"{k} {m['predicted'][k]:.1f}/{m['reference'][k]:.1f} -> {m['scores'][k]:.3f}" for k in m["scores"]))
    held = read_cvat(document(list(image_elements(marked, TILES).values())), L.TILES, source=PREDICTION)
    for label, key in (("row", "row_structure"), ("interrow_area", "interrow_cover")):
        table: Counter = Counter()
        ious = []
        for name in L.NAMES:
            ref = [(__import__("shapely").geometry.shape(f["geometry"]), f["properties"]) for f in REFERENCE if f["properties"]["label"] == label and f["properties"]["tile"] == name]
            pred = [(__import__("shapely").geometry.shape(f["geometry"]), f["properties"]) for f in held if f["properties"]["label"] == label and f["properties"]["tile"] == name]
            pairs = _match(pred, ref, _axis_share, AXIS_SHARE, reach=AXIS_TOLERANCE_M) if label == "row" else _match(pred, ref, _iou, 0.5)
            if label == "interrow_area":
                ious += [_iou(pred[i][0], ref[j][0]) for i, j in pairs]
            guess = {j: pred[i][1].get(key) for i, j in pairs}
            table.update((name[7:16], p.get(key), guess.get(j, "missing")) for j, (_, p) in enumerate(ref))
        print(f"  {key}: " + ", ".join(f"{k}: {v}" for k, v in sorted(table.items()) if k[1] != k[2]) + f" | correct {sum(v for k, v in table.items() if k[1] == k[2])}/{sum(table.values())}")
        if ious:
            ious.sort()
            print(f"  matched inter-row IoU min {ious[0]:.3f} median {ious[len(ious) // 2]:.3f}")


def debug_rows(features: list[dict]) -> None:
    from shapely.geometry import shape
    marked = [{**f, "properties": {**f["properties"], "source": PREDICTION}} for f in features]
    held = read_cvat(document(list(image_elements(marked, TILES).values())), L.TILES, source=PREDICTION)
    for name in L.NAMES:
        ref = [(shape(f["geometry"]), f["properties"]) for f in REFERENCE if f["properties"]["label"] == "row" and f["properties"]["tile"] == name]
        pred = [(shape(f["geometry"]), f["properties"]) for f in held if f["properties"]["label"] == "row" and f["properties"]["tile"] == name]
        pairs = dict(_match(pred, ref, _axis_share, AXIS_SHARE, reach=AXIS_TOLERANCE_M))
        for i, (g, p) in enumerate(pred):
            if i not in pairs:
                best = max((_axis_share(g, r) for r, _ in ref), default=0)
                print(f"  {name[7:16]} unmatched pred {p.get('row_id')} len {g.length:.1f} verts {len(g.coords)} best share {best:.2f}")
        ref = [(shape(f["geometry"]), f["properties"]) for f in REFERENCE if f["properties"]["label"] == "interrow_area" and f["properties"]["tile"] == name]
        pred = [(shape(f["geometry"]), f["properties"]) for f in held if f["properties"]["label"] == "interrow_area" and f["properties"]["tile"] == name]
        pairs = {j: i for i, j in _match(pred, ref, _iou, 0.5)}
        for j, (g, p) in enumerate(ref):
            if j not in pairs:
                over = sorted(((_iou(q, g), q.area, q.intersection(g).area) for q, _ in pred if q.intersects(g)), reverse=True)[:3]
                print(f"  {name[7:16]} unmatched ref inter-row area {g.area:.1f}: best (IoU, area, overlap) {[tuple(round(x, 2) for x in o) for o in over]}")
        print(f"  {name[7:16]} inter-rows pred {len(pred)} ref {len(ref)}; unmatched pred areas {sorted(round(pred[i][0].area, 1) for i in range(len(pred)) if i not in pairs.values())}")


if __name__ == "__main__":
    params = CanopyParams()
    plots = L.plot_features()
    canopies = L.run(params, plots, L.NAMES, None, "and", 1)
    report("carry axes as given", build(canopies))
    QUADS = True
    built = build(canopies)
    report("reference rows (carry_plots)", built)
    debug_rows(built)

