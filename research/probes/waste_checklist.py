"""Checklist of likely and unsure inter-row litter on the northern tiles (row index <= 23) for manual annotation in
Marcaj, from my visual review of the sweep (sweep_north_*.jpg, zooms zoom_sweep_*.jpg, zoom_bright_*.jpg,
area_p02_nw*.jpg). Writes data/generated/work/waste/checklist_north.json (tile, x_px, y_px, easting, northing, w_m,
h_m, verdict, reason, plus vineyard_id) and checklist_north_*.jpg (3 m native crop and 16 m context per item).
The user-hinted P02 north-west item comes first; the rest are sorted by tile. Run from backend/."""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import Point, shape

sys.path.insert(0, str(Path(__file__).parent))
from waste_sheet import _crop  # noqa: E402

from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX  # noqa: E402

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
FIRST = 749
VERDICTS = {  # sweep number: (verdict, reason)
    749: ("likely", "crumpled white plastic (bag or film) mid inter-row on tilled soil, NW corner of P02 (user hint); the detector rejects it (chroma 18 > 15)"),
    737: ("likely", "white bag-like blob on the anchor wire of an end post, 1.4 m from the row (detector box); could be a marker tied to the wire"),
    321: ("likely", "crumpled white piece mid inter-row on dark soil (detector box)"),
    285: ("likely", "crumpled white piece mid inter-row on dark soil, like the detector box in r008_c003"),
    543: ("likely", "white rectangular piece (paper or packaging) in grass, beside a lying white tube"),
    605: ("unsure", "black object with white parts in the inter-row: a black bag with a white item, or an animal"),
    769: ("unsure", "round black object with white and red bits on grass by the shed (the predicted inter-row overlaps the yard): bag, bucket or a person"),
    659: ("unsure", "yellow box with a round lid and a white item in a shrub at a row: container or equipment (machinery is not waste)"),
    706: ("unsure", "small white scraps mid inter-row: plastic, or bits of a broken tube"),
    793: ("unsure", "white piece beside a broken tube at the row"),
    772: ("unsure", "crumpled whitish piece mid inter-row"),
    824: ("unsure", "small crumpled white piece mid inter-row"),
    815: ("unsure", "small white rectangular piece in the inter-row"),
    159: ("unsure", "small crumpled white piece in grass"),
    656: ("unsure", "two small white pieces near the track edge"),
    383: ("unsure", "small white piece (0.02 m2): a cap, paper or a stone"),
    460: ("unsure", "orange piece by the row: plastic or a dead leaf"),
    120: ("unsure", "small white object casting a shadow (standing): a bottle or a tube stub"),
    836: ("unsure", "white bag-like blob among lying white tubes at a P07 row (the tubes are not waste)"),
}
EDGE = {  # sweep3_north.json (canopy-edge pass, waste_rowedge.py) number: (verdict, reason)
    73: ("likely", "crumpled clear/white plastic bottle or bag at the canopy edge of a P07 row end, 0.2 m from the axis"),
    70: ("unsure", "white box-like piece (packaging) at a row end on the P02/P07 headland track; a second piece is 0.5 m away"),
    69: ("unsure", "small white piece next to the box-like piece at the same row end"),
    72: ("unsure", "small white crumpled piece at the canopy edge, P16"),
    64: ("unsure", "round white object beside a vine (cup or cap?), P03 row end"),
}
TINTED = {  # sweep4_north.json (bright tinted pass, waste_tinted.py) number: (verdict, reason)
    25: ("likely", "pink/salmon L-shaped plastic (sheet, tape or hose), 1.4 m, under the tree edge in a P01 inter-row"),
}
# the sweep cluster at 836 includes the tubes; use the bag's own box from the in-block detector run
OVERRIDE = {836: (629673.9, 5220087.5, 0.43, 0.70)}
# not in any sweep: on the P03 headland grass strip, outside the predicted inter-rows (found by the in-block detector)
MANUAL = [("siret3_r018_c013.tif", 629673.4, 5220250.2, 0.98, 1.05, "unsure",
           "white plastic item (bag or basin) with a grey film on the P03 headland grass by the road, outside the inter-rows")]

sweep = {item["n"]: item for item in json.loads((W / "sweep_north.json").read_text())}
edge = {item["n"]: item for item in json.loads((W / "sweep3_north.json").read_text())}
tinted = {item["n"]: item for item in json.loads((W / "sweep4_north.json").read_text())}
features = json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]
AREAS = [(shape(f["geometry"]), f["properties"]["vineyard_id"]) for label in ("interrow_area", "block")
         for f in features if f["properties"]["label"] == label]


def block_of(easting: float, northing: float) -> str:
    """The inter-row, else the block, the centre lies in."""
    return next((name for polygon, name in AREAS if polygon.contains(Point(easting, northing))), "")


def entry(tile: str, easting: float, northing: float, w_m: float, h_m: float, verdict: str, reason: str, key: str) -> dict:
    row, col = int(tile[8:11]), int(tile[13:16])
    left, top = 628992.0 + 51.2 * col, 5221222.4 - 51.2 * row
    x_px, y_px = round((easting - left) / PIXEL_M), round((top - northing) / PIXEL_M)
    assert 0 <= x_px < TILE_PX and 0 <= y_px < TILE_PX, (key, x_px, y_px)
    return {"tile": tile, "x_px": x_px, "y_px": y_px, "easting": round(easting, 2), "northing": round(northing, 2),
            "w_m": round(w_m, 2), "h_m": round(h_m, 2), "verdict": verdict, "reason": reason,
            "vineyard_id": block_of(easting, northing), "key": key}


def from_item(item: dict, verdict: str, reason: str, key: str) -> dict:
    row, col = int(item["tile"][8:11]), int(item["tile"][13:16])
    x0, y0, x1, y1 = item["px"]
    easting, northing = 628992.0 + 51.2 * col + (x0 + x1) / 2 * PIXEL_M, 5221222.4 - 51.2 * row - (y0 + y1) / 2 * PIXEL_M
    return entry(item["tile"], easting, northing, (x1 - x0) * PIXEL_M, (y1 - y0) * PIXEL_M, verdict, reason, key)


rows = []
for n, (verdict, reason) in VERDICTS.items():
    if n in OVERRIDE:
        e, no, w, h = OVERRIDE[n]
        rows.append(entry(sweep[n]["tile"], e, no, w, h, verdict, reason, f"s{n}"))
    else:
        rows.append(from_item(sweep[n], verdict, reason, f"s{n}"))
rows += [from_item(edge[n], verdict, reason, f"e{n}") for n, (verdict, reason) in EDGE.items()]
rows += [from_item(tinted[n], verdict, reason, f"t{n}") for n, (verdict, reason) in TINTED.items()]
rows += [entry(*m, key="manual") for m in MANUAL]
rows.sort(key=lambda r: (r["key"] != f"s{FIRST}", r["tile"], r["y_px"]))
(W / "checklist_north.json").write_text(json.dumps([{k: v for k, v in r.items() if k != "key"} for r in rows], indent=1))

CELL, COLS = 300, 3
sheet = Image.new("RGB", (COLS * 2 * CELL + (COLS - 1) * 8, ((len(rows) - 1) // COLS + 1) * (CELL + 30)), (60, 60, 60))
for i, r in enumerate(rows):
    cell = Image.new("RGB", (2 * CELL, CELL + 30), "white")
    for j, size_m in enumerate((3.0, 16.0)):
        crop = _crop(DATA_DIR / "tiles" / r["tile"], r["x_px"], r["y_px"], size_m, CELL)
        scale = CELL / (size_m / PIXEL_M)
        hw, hh = r["w_m"] / PIXEL_M / 2 * scale + 4, r["h_m"] / PIXEL_M / 2 * scale + 4
        ImageDraw.Draw(crop).rectangle([CELL / 2 - hw, CELL / 2 - hh, CELL / 2 + hw, CELL / 2 + hh], outline=(255, 0, 255), width=2)
        cell.paste(crop, (j * CELL, 0))
    draw = ImageDraw.Draw(cell)
    draw.text((3, CELL + 2), f"{i + 1}. {r['tile'][:-4]} x{r['x_px']} y{r['y_px']} {r['vineyard_id']} {r['verdict'].upper()} {r['w_m']}x{r['h_m']} m", fill="black")
    draw.text((3, CELL + 15), f"E {r['easting']} N {r['northing']}  {r['reason'][:70]}", fill="black")
    sheet.paste(cell, ((i % COLS) * (2 * CELL + 8), (i // COLS) * (CELL + 30)))
sheet.save(W / "checklist_north_01.jpg", quality=88)
print(len(rows), {v: sum(r["verdict"] == v for r in rows) for v in ("likely", "unsure")}, W / "checklist_north_01.jpg")
