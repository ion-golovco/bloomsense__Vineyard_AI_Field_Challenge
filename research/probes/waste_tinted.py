"""Recall pass for bright tinted objects (pink, salmon, orange-white, bluish-white) on the northern inter-rows, which
fall between the sweep's white (chroma <= 30) and colour (chroma >= 60, darkest channel < 170) classes: detector
candidates of kind white with chroma > 30 and hue < 25 or > 175, >= 0.01 m2, clustered as in waste_sweep.py.
Writes sweep4_north.json and sweep4_north_*.jpg. Run from backend/ after `python -m marcaj.waste`."""

import json
import sys
from pathlib import Path

from shapely.geometry import box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
from waste_sweep import W, render  # noqa: E402

candidates = [c for c in json.loads((W / "candidates.json").read_text()) if int(c["tile"][8:11]) <= 23 and c["kind"] == "white"
              and c["chroma"] > 30 and (c["hue"] < 25 or c["hue"] > 175) and c["area_m2"] >= 0.01]
items = []
for tile in sorted({c["tile"] for c in candidates}):
    mine = [c for c in candidates if c["tile"] == tile]
    merged = unary_union([box(*c["px"]).buffer(10) for c in mine])
    for part in getattr(merged, "geoms", [merged]):
        members = [c for c in mine if part.intersects(box(*c["px"]))]
        items.append({"tile": tile, "px": [min(c["px"][0] for c in members), min(c["px"][1] for c in members),
                                           max(c["px"][2] for c in members), max(c["px"][3] for c in members)],
                      "kinds": ["tinted"], "area_m2": round(sum(c["area_m2"] for c in members), 3), "vineyard_id": members[0]["vineyard_id"],
                      "lum": round(max(c["lum"] for c in members)), "row_m": round(min(c["row_m"] for c in members), 2)})
items.sort(key=lambda i: (i["tile"], i["px"][1], i["px"][0]))
for n, item in enumerate(items, start=1):
    item["n"] = n
(W / "sweep4_north.json").write_text(json.dumps(items, indent=0))
render(items, "sweep4_north")
print(len(items), "items")
