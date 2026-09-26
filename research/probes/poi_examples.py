"""Gap POIs on the two organizer example tiles: reference rows + canopies (the organizers' own gaps) against predicted
rows + canopies, with and without pixel green. Also the reference's disrupted rows against the gaps found in them.
Run from backend/: uv run --frozen python ../research/probes/poi_examples.py"""

import json
import zipfile

import numpy as np
from shapely.geometry import LineString, Point, shape

from marcaj import poi
from marcaj.cvat import read_cvat
from marcaj.tiles import DATA_DIR, load_tiles

NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
tiles = load_tiles(DATA_DIR)
by_name = {tile.name: tile for tile in tiles}
two = [by_name[name] for name in NAMES]
with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
    xml = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
reference = read_cvat(xml, by_name)
predicted = json.loads(poi.PREDICTIONS_PATH.read_text())["features"]
area = two[0].bounds.union(two[1].bounds)
predicted = [f for f in predicted if f["properties"]["label"] in ("row", "vineyard") and shape(f["geometry"]).intersects(area)]


def show(name, gaps):
    print(f"\n{name}: {len(gaps)} gaps >= 5 m (kept at hidden <= {poi.MAX_HIDDEN}: {sum(g['hidden'] <= poi.MAX_HIDDEN for g in gaps)})")
    for g in sorted(gaps, key=lambda g: g["row_id"]):
        mid = (g["start"] + g["end"]) / 2
        print(f"  {g['row_id']:10s} {g['gap_m']:5.2f} m  hidden {g['hidden']:.2f}  row planted {g['row_planted']:.2f}  mid {mid[0]:.1f} {mid[1]:.1f}")
    return gaps


ref_rows, pred_rows = poi.sample_rows(reference, two), poi.sample_rows(predicted, two)
truth = show("reference rows + reference canopies", poi.row_gaps(ref_rows, green=False, min_gap_m=3.0))
truth_px = show("reference rows + reference canopies + pixels", poi.row_gaps(ref_rows, green=True, min_gap_m=3.0))
runs = {
    "predicted canopies only": poi.row_gaps(pred_rows, green=False, min_gap_m=3.0),
    "predicted canopies + pixel green": poi.row_gaps(pred_rows, green=True, min_gap_m=3.0),
}
for name, gaps in runs.items():
    show(name, gaps)

ref_true = [g for g in truth if g["gap_m"] >= poi.GAP_M]
print(f"\nmatching against the {len(ref_true)} reference gaps >= 5 m (midpoint within 2 m, or the predicted gap covers the reference midpoint)")
for name, gaps in runs.items():
    kept = [g for g in gaps if g["gap_m"] >= poi.GAP_M and g["hidden"] <= poi.MAX_HIDDEN]
    def hit(r, p):
        mid_r, mid_p = (r["start"] + r["end"]) / 2, (p["start"] + p["end"]) / 2
        return np.linalg.norm(mid_r - mid_p) <= 2.0 or LineString([p["start"], p["end"]]).distance(Point(mid_r)) <= 1.0
    found = sum(any(hit(r, p) for p in kept) for r in ref_true)
    true_p = sum(any(hit(r, p) for r in truth) for p in kept)
    print(f"  {name}: recall {found}/{len(ref_true)}, precision {true_p}/{len(kept)} (against reference gaps >= 3 m)")

print("\nreference disrupted rows: longest interior canopy gap and edge stretches from the reference canopies")
for f in reference:
    p = f["properties"]
    if p["label"] == "row" and p["row_structure"] != "regular":
        mine = [g for g in truth if g["row_id"] == p["row_id"]]
        print(f"  {p['row_id']} {p['row_structure']}: interior gaps {[round(g['gap_m'], 1) for g in mine]}")

print("\nfinal gap_pois on the example tiles; reference targets = reference interior gaps >= 5 m + reference row-end stretches >= 5 m")
ref_targets = [r for r in truth if r["gap_m"] >= poi.GAP_M]
print(f"  reference: {sum(r['kind'] == 'gap' for r in ref_targets)} interior gaps, {sum(r['kind'] == 'planting' for r in ref_targets)} row-end stretches")
all_pois = poi.gap_pois(pred_rows)
for name, chosen in (("challenge gaps", [p for p in all_pois if p["properties"]["challenge"]]),
                     ("+ planting with green_share < 0.1", [p for p in all_pois if p["properties"]["challenge"] or p["properties"]["reason"] == "planting" and p["properties"]["green_share"] < 0.1]),
                     ("+ all planting", all_pois)):
    mids = [np.asarray(p["geometry"]["coordinates"]) for p in chosen]
    segments = [LineString([p["properties"]["gap_start"], p["properties"]["gap_end"]]) for p in chosen]
    for kind in ("gap", "planting"):
        ref_mids = [(r["start"] + r["end"]) / 2 for r in ref_targets if r["kind"] == kind]
        near = sum(any(np.linalg.norm(m - q) <= 2.0 for q in mids) for m in ref_mids)
        along = sum(any(seg.distance(Point(m)) <= 2.0 for seg in segments) for m in ref_mids)
        print(f"  {name} ({len(chosen)} POIs): reference {kind} midpoints within 2 m of a POI {near}/{len(ref_mids)}, of a POI's stretch {along}/{len(ref_mids)}")
    on_ref = sum(any(np.linalg.norm(m - (r["start"] + r["end"]) / 2) <= 2.0 or LineString([r["start"], r["end"]]).distance(Point(m)) <= 1.0 for r in truth) for m in mids)
    print(f"    POIs on a reference canopy-free stretch >= 3 m: {on_ref}/{len(chosen)}")
ref_pois = [p for p in poi.gap_pois(ref_rows) if p["properties"]["reason"] == "gap"]
print(f"  the reference scene itself: {len(ref_pois)} gap POIs on rows {sorted({p['properties']['row_id'] for p in ref_pois})}")
