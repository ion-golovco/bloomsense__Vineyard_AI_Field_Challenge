"""Inter-rows and row / inter-row attributes (`marcaj.rows`) on the two organizer reference tiles, scored with the
organizer formulas (`marcaj.judge`), with a per-object breakdown: every reference inter-row matched or not, its IoU
and where the difference lies (ends along the rows, sides across them), the five measurements, and the attribute
confusion matrices. Evaluation only; a sanity check, not a holdout.
Run from backend/: uv run --frozen python ../research/probes/interrow_probe.py [plots.json]
(a cached `detect_plots()` output keeps the input fixed while plots.py changes, and skips its ~20 s)."""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from shapely.geometry import shape

from marcaj import rows
from marcaj.canopy import plot_rows, tile_canopies
from marcaj.cvat import build_scene, document, image_elements, read_cvat
from marcaj.judge import AXIS_SHARE, AXIS_TOLERANCE_M, _axis_share, _iou, _match, judge
from marcaj.plots import detect_plots, exclusions
from marcaj.scene import PREDICTION
from marcaj.tiles import DATA_DIR, load_tiles

NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
tiles = {tile.name: tile for tile in load_tiles()}
base = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(tiles.values()))["features"]
reference = [f for f in base if f["properties"]["source"] == "reference"]
cached = Path(sys.argv[1]) if len(sys.argv) > 1 else None
plots = json.loads(cached.read_text()) if cached and cached.is_file() else detect_plots()
if cached and not cached.is_file():
    cached.write_text(json.dumps(plots))
canopies = [f for name in NAMES for f in tile_canopies(tiles[name], plot_rows(plots))]


def predict() -> list[dict]:
    """predict.predict restricted to the reference tiles."""
    found = plots + rows.interrow_areas(plots, exclusions())
    return rows.per_tile(found + canopies, [tiles[name] for name in NAMES])


def uploaded(predicted: list[dict]) -> list[dict]:
    """What Marcaj would hold: the predictions clipped and written per tile, then read back."""
    marked = [{**f, "properties": {**f["properties"], "source": PREDICTION}} for f in predicted]
    return read_cvat(document(list(image_elements(marked, [tiles[n] for n in NAMES]).values())), tiles, source=PREDICTION)


def objects(features: list[dict], label: str, name: str) -> list:
    return [(shape(f["geometry"]), f["properties"]) for f in features if f["properties"]["label"] == label and f["properties"].get("tile") == name]


def frame(name: str) -> tuple[np.ndarray, np.ndarray]:
    """Unit vectors along and across the reference rows of the tile."""
    (x0, y0), (x1, y1) = max((g for g, _ in objects(reference, "row", name)), key=lambda g: g.length).coords
    along = np.array([x1 - x0, y1 - y0]) / np.hypot(x1 - x0, y1 - y0)
    return along, np.array([-along[1], along[0]])


def extent(geometry, axis: np.ndarray) -> tuple[float, float]:
    values = np.asarray(geometry.exterior.coords if geometry.geom_type == "Polygon" else geometry.coords) @ axis
    return float(values.min()), float(values.max())


def interrow_report(features: list[dict]) -> None:
    for name in NAMES:
        ref, pred = objects(reference, "interrow_area", name), objects(features, "interrow_area", name)
        ref_rows = [g for g, _ in objects(reference, "row", name)]
        pairs = dict((j, i) for i, j in _match(pred, ref, _iou, 0.5))
        along, across = frame(name)
        print(f"\n{name}: {len(pred)} predicted, {len(ref)} reference inter-rows, {len(pairs)} matched at IoU >= 0.5")
        ious = []
        for j, (g, props) in sorted(enumerate(ref), key=lambda item: extent(item[1][0], across)[0]):
            best = max(range(len(pred)), key=lambda i: _iou(pred[i][0], g), default=None)
            iou = _iou(pred[best][0], g) if best is not None else 0.0
            a0, a1 = extent(g, along)
            c0, c1 = extent(g, across)
            # the inset: distance from each long side to the nearest reference row axis
            inset = sorted(min(r.distance(g.exterior.interpolate(t, normalized=True)) for r in ref_rows) for t in np.linspace(0, 1, 40))
            line = f"  ref {j:2d} across {c0:9.2f}..{c1:9.2f} width {c1 - c0:4.2f} len {a1 - a0:5.1f} area {g.area:6.1f} inset~{np.median(inset[:20]):.2f}"
            if best is not None:
                p = pred[best][0]
                p0, p1 = extent(p, along)
                q0, q1 = extent(p, across)
                line += (f" | IoU {iou:.3f} {'M' if j in pairs else '-'} ends {p0 - a0:+6.2f} {p1 - a1:+6.2f} sides {q0 - c0:+5.2f} {q1 - c1:+5.2f}"
                         f" pred area {p.area:6.1f}")
            if j in pairs:
                ious.append(iou)
            print(line)
        unmatched = [i for i in range(len(pred)) if i not in pairs.values()]
        for i in unmatched:
            p = pred[i][0]
            print(f"  unmatched pred across {extent(p, across)[0]:9.2f}..{extent(p, across)[1]:9.2f} len {np.subtract(*extent(p, along)[::-1]):5.1f} area {p.area:6.1f}"
                  f" best IoU {max((_iou(p, g) for g, _ in ref), default=0):.3f}")
        if ious:
            print(f"  matched IoU: min {min(ious):.3f} median {np.median(ious):.3f} max {max(ious):.3f}")


def confusion(features: list[dict]) -> None:
    for label, key in (("row", "row_structure"), ("interrow_area", "interrow_cover")):
        table: Counter = Counter()
        for name in NAMES:
            ref, pred = objects(reference, label, name), objects(features, label, name)
            if label == "row":
                pairs = _match(pred, ref, _axis_share, AXIS_SHARE, reach=AXIS_TOLERANCE_M)
            else:
                pairs = _match(pred, ref, _iou, 0.5)
            guess = {j: pred[i][1].get(key) for i, j in pairs}
            table.update((name[7:16], props.get(key), guess.get(j, "missing")) for j, (_, props) in enumerate(ref))
        print(f"\n{key} (tile, reference, predicted): count")
        for k, v in sorted(table.items()):
            print(f"  {k}: {v}")


def summary(tag: str, features: list[dict]) -> dict:
    report = judge({"type": "FeatureCollection", "crs": "EPSG:32635", "features": base + [{**f, "properties": {**f["properties"], "source": PREDICTION}} for f in features]})
    s, t = report["scores"], {x["tile"]: x for x in report["tiles"]}
    m = report["measures"]
    print(f"{tag}: inter-row F1 {t[NAMES[0]]['interrow_f1']:.3f} / {t[NAMES[1]]['interrow_f1']:.3f} | axes {t[NAMES[0]]['axis_f1']:.3f} / {t[NAMES[1]]['axis_f1']:.3f}"
          f" | attributes {s['attributes']:.3f} | counts {s['counts']:.3f} | grouping {s['grouping']:.3f} | canopy {s['canopy']:.3f} | points {report['points']}")
    print("  measures " + " | ".join(f"{k} {m['predicted'][k]:.1f}/{m['reference'][k]:.1f} -> {m['scores'][k]:.3f}" for k in m["scores"]))
    return report


if __name__ == "__main__":
    predicted = predict()
    summary("current rows.py", predicted)
    held = uploaded(predicted)
    interrow_report(held)
    confusion(held)
