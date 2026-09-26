"""Row axes against the canopy: how far the exported lattice rows sit from a refit to the canopy polygons
(`canopy.refit_rows`), site-wide and on the two organizer tiles against their reference rows. Evaluation only.

Run from backend/: [RR_PREDICTIONS=predictions.geojson] [RR_PLOTS=plots.json] uv run --frozen python ../research/probes/rows_refit_measure.py
Rows are frozen from the predictions: per-tile pieces merged per row_id into one axis (as canopy_recall_lib)."""

import json
import os
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Polygon, mapping, shape
from shapely.ops import unary_union

from marcaj import canopy
from marcaj.tiles import REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "rows_refit"
UPLOADED = REPO_ROOT / "data" / "generated" / "predictions.geojson"  # the rows are always frozen from this one
PREDICTIONS = Path(os.environ.get("RR_PREDICTIONS", UPLOADED))  # canopies from here
PLOTS = os.environ.get("RR_PLOTS")  # a cached detect_plots output to use instead of rows frozen from UPLOADED


def plots_input():
    """The block and row features the probes start from: RR_PLOTS, else the rows frozen by main()."""
    return json.loads(Path(PLOTS).read_text() if PLOTS else (WORK / "plots_frozen.json").read_text())
SCENE = WORK / "scene_before.json"


def frozen(features):
    pieces, meta = {}, {}
    for f in features:
        if f["properties"]["label"] == "row":
            pieces.setdefault(f["properties"]["row_id"], []).append(shape(f["geometry"]))
            meta[f["properties"]["row_id"]] = f["properties"]
    out = [f for f in features if f["properties"]["label"] == "block"]
    for row_id, lines in sorted(pieces.items()):
        longest = max(lines, key=lambda line: line.length)
        (x0, y0), (x1, y1) = longest.coords[0], longest.coords[-1]
        d = np.array([x1 - x0, y1 - y0]) / longest.length
        u = (np.concatenate([np.asarray(line.coords) for line in lines]) - [x0, y0]) @ d
        a, b = np.array([x0, y0]) + u.min() * d, np.array([x0, y0]) + u.max() * d
        out.append({"type": "Feature", "geometry": mapping(LineString([a, b])),
                    "properties": {k: v for k, v in meta[row_id].items() if k not in ("tile", "row_structure")}})
    return out


def q(values):
    values = np.abs(np.asarray(values))
    return " / ".join(f"{np.percentile(values, p):.3f}" for p in (50, 75, 90, 95, 99)) + f" (max {values.max():.3f}, n {len(values)})"


def reference_distances(rows_by_id):
    """Per reference row on the two tiles: mean distance of its two end points to the nearest predicted row."""
    scene = json.loads(SCENE.read_text())["features"]
    ref = [shape(f["geometry"]) for f in scene if f["properties"].get("source") == "reference" and f["properties"].get("label") == "row"]
    out = []
    for line in ref:
        ends = [np.asarray(line.coords[0]), np.asarray(line.coords[-1])]
        best = min(rows_by_id.values(), key=lambda row: sum(row.distance(shape({"type": "Point", "coordinates": e})) for e in ends))
        (x0, y0), (x1, y1) = best.coords[0], best.coords[-1]
        n = np.array([-(y1 - y0), x1 - x0]) / best.length
        out.append([float((e - [x0, y0]) @ n) for e in ends])
    return np.array(out)


def control():
    """Synthetic plot: three rows 2.5 m apart, 1 x 0.5 m canopies every 2 m on the middle one, drawn 0.2 m off it and
    turned 0.2 degrees. The middle row must move onto them, the others only turn; with the canopies 1.5 m off (past
    `reach_m`) nothing moves."""
    turn = np.radians(0.2)
    row = lambda k: {"type": "Feature", "geometry": mapping(LineString([(0, 2.5 * k), (60, 2.5 * k)])), "properties": {"label": "row", "vineyard_id": "T", "row_id": f"T-R{k}"}}
    for off, expect in ((0.2, True), (1.5, False)):
        boxes = [{"type": "Feature", "geometry": mapping(Polygon([(x, 2.5 + off + (x - 30) * turn - 0.25), (x + 1, 2.5 + off + (x - 30) * turn - 0.25),
                                                                   (x + 1, 2.5 + off + (x - 30) * turn + 0.25), (x, 2.5 + off + (x - 30) * turn + 0.25)])),
                  "properties": {"label": "vineyard", "vineyard_id": "T"}} for x in range(0, 60, 2)]
        out, records = canopy.refit_rows([row(0), row(1), row(2)], boxes)
        middle = np.asarray(out[1]["geometry"]["coordinates"])[:, 1] - 2.5
        assert records[1]["moved"] is expect and not records[0]["moved"], records
        if expect:
            assert np.allclose(middle, [off - 30 * turn, off + 30 * turn], atol=0.01), middle
            assert abs(records[0]["turn_deg"] - 0.2) < 0.01, records[0]
        else:
            assert np.allclose(middle, 0, atol=1e-9), middle
    print("control: pass")


def main():
    control()
    plots = json.loads(Path(PLOTS).read_text()) if PLOTS else frozen(json.loads(UPLOADED.read_text())["features"])
    canopies = [f for f in json.loads(PREDICTIONS.read_text())["features"] if f["properties"]["label"] == "vineyard"]
    moved, records = canopy.refit_rows(plots, canopies)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "refit_records.json").write_text(json.dumps(records))
    if not PLOTS:
        (WORK / "plots_frozen.json").write_text(json.dumps(plots))
    r = [x for x in records if x["moved"]]
    print(f"{len(records)} rows, {len(r)} moved, {sum(x['reverted'] for x in records)} reverted by the neighbour check, "
          f"{sum(x['cover_m'] < 3 for x in records)} under 3 m of canopy")
    mid = [(x["shift_start_m"] + x["shift_end_m"]) / 2 for x in r]
    end = [max(abs(x["shift_start_m"]), abs(x["shift_end_m"])) for x in r]
    print("moved rows |shift at centre| p50/p75/p90/p95/p99:", q(mid))
    print("moved rows max |shift| at an end:              ", q(end))
    turns = {x["vineyard_id"]: x["turn_deg"] for x in records}
    print("plot turn (deg):", q(list(turns.values())), {k: round(v, 3) for k, v in sorted(turns.items(), key=lambda kv: -abs(kv[1]))[:6]})
    length = sum(x["length_m"] for x in r)
    print(f"row length moved {length:.0f} m of {sum(x['length_m'] for x in records):.0f} m; "
          f"share of moved length off by > 0.1 / 0.2 / 0.3 m at centre: "
          + " / ".join(f"{sum(x['length_m'] for x, m in zip(r, mid) if abs(m) > t) / length:.3f}" for t in (0.1, 0.2, 0.3)))
    free = {f["properties"]["row_id"]: shape(f["geometry"]) for f in canopy.refit_rows(plots, canopies, per_row=True)[0] if f["properties"]["label"] == "row"}
    variants = {"lattice": plots, "shared (predict.py)": moved, "per_row": canopy.refit_rows(plots, canopies, per_row=True)[0]}
    canopy_union = unary_union([shape(f["geometry"]) for f in canopies])
    for name, features_ in variants.items():
        rows = {f["properties"]["row_id"]: shape(f["geometry"]) for f in features_ if f["properties"]["label"] == "row"}
        off = []
        for row_id, line in rows.items():
            (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
            n = np.array([-(y1 - y0), x1 - x0]) / line.length
            off += [abs(float((np.asarray(p) - [x0, y0]) @ n)) for p in free[row_id].coords]
        tube = unary_union([line.buffer(0.3, cap_style="flat") for line in rows.values()])
        outside = canopy_union.difference(tube).area
        d = reference_distances(rows)
        print(f"{name}: |end offset| from the per-row canopy line p50/p75/p90/p95/p99 {q(off)}")
        print(f"    canopy outside +-0.3 m of the rows {outside:.0f} m2 of {canopy_union.area:.0f} m2; reference rows |end distance| p50/p75/p90/p95 {q(d.ravel())}")

if __name__ == "__main__":
    main()
