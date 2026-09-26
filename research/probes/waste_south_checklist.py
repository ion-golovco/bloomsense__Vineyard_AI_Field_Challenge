"""Checklist of the southern inter-row waste review: reads the verdicts recorded by eye (south_verdicts.json: tile, x, y,
w_px, h_px, verdict, reason; x/y the item's centre in tile pixels) and writes checklist_south.json (likely and unsure only,
sorted by tile then y, with EPSG:32635 centre and box size) and checklist_south.jpg (their crops).
Run from backend/: uv run --frozen python ../research/probes/waste_south_checklist.py"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from shapely.geometry import Point  # noqa: E402
from waste_south import W, sheet, south_interrows  # noqa: E402

from marcaj.tiles import PIXEL_M  # noqa: E402

LEFT0, TOP0, TILE_M = 628992.0, 5221222.4, 51.2


def main() -> None:
    verdicts = json.loads((W / "south_verdicts.json").read_text())
    interrows = south_interrows()
    items = []
    for v in verdicts:
        if v["verdict"] not in ("likely", "unsure"):
            continue
        r, c = int(v["tile"][1:4]), int(v["tile"][6:9])
        point = Point(LEFT0 + TILE_M * c + v["x"] * PIXEL_M, TOP0 - TILE_M * r - v["y"] * PIXEL_M)
        zones = interrows.get(f"siret3_{v['tile']}.tif", [])
        gap = min((polygon.distance(point) for polygon, _ in zones), default=99.0)
        block = min(zones, key=lambda z: z[0].distance(point))[1] if zones else ""
        items.append({
            "tile": f"siret3_{v['tile']}.tif", "x_px": v["x"], "y_px": v["y"],
            "easting": round(LEFT0 + TILE_M * c + v["x"] * PIXEL_M, 2), "northing": round(TOP0 - TILE_M * r - v["y"] * PIXEL_M, 2),
            "w_m": round(v["w_px"] * PIXEL_M, 2), "h_m": round(v["h_px"] * PIXEL_M, 2),
            "verdict": v["verdict"], "reason": v["reason"],
            "interrow_m": round(gap, 2), "vineyard_id": block,  # 0 = inside a predicted inter-row
        })
    items.sort(key=lambda i: (i["tile"], i["y_px"]))
    (W / "checklist_south.json").write_text(json.dumps(items, indent=1, ensure_ascii=False))
    crops = []
    for k, i in enumerate(items):
        hw, hh = i["w_m"] / PIXEL_M / 2, i["h_m"] / PIXEL_M / 2
        px = [round(i["x_px"] - hw), round(i["y_px"] - hh), round(i["x_px"] + hw), round(i["y_px"] + hh)]
        crops.append((k, {"tile": i["tile"], "px": px, "kind": i["verdict"], "area_m2": i["w_m"] * i["h_m"],
                          "width_m": i["w_m"], "length_m": i["h_m"], "vineyard_id": "", "score": 0.0}))
    if crops:
        sheet(crops, W / "checklist_south.jpg")
    print(f"{len(items)} items ({sum(i['verdict'] == 'likely' for i in items)} likely) -> {W / 'checklist_south.json'}")


if __name__ == "__main__":
    main()
