"""Control for the Sentinel -> drone check: the same zones moved to random places in the same plot (whole 10 m pixels,
still inside the plot shrunk by 5 m). If the drone rule keeps moved zones as often as the real ones, the satellite
adds nothing. Run from backend/ after marcaj.poi and poi_site.py: uv run --frozen python ../research/probes/poi_sentinel_control.py"""

import json
import pickle  # rows.pkl is our own cache from poi_site.py, never outside data
import random

from shapely.affinity import translate
from shapely.geometry import mapping, shape

from marcaj import poi, sentinel

OUT = poi.POI_PATH.parent
rows = pickle.loads((OUT / "rows.pkl").read_bytes())
zones = json.loads((OUT / "sentinel_zones.geojson").read_text())["features"]
features = json.loads(poi.PREDICTIONS_PATH.read_text())["features"]
plots = {k: v.buffer(-sentinel.SHRINK_M) for k, v in sentinel.plot_polygons(features).items()}
_, real = poi.sentinel_pois(rows, zones)
print(f"real zones kept: {sum(r['kept'] for r in real)}/{len(real)}")
random.seed(0)
moved = []
for zone in zones:
    polygon, plot = shape(zone["geometry"]), plots[zone["properties"]["vineyard_id"]]
    for _ in range(400):
        if len(moved) and sum(m["properties"]["of"] == id(zone) for m in moved) >= 20:
            break
        shifted = translate(polygon, 10 * random.randint(-15, 15), 10 * random.randint(-15, 15))
        if plot.buffer(1e-6).contains(shifted) and not shifted.intersects(polygon):
            moved.append({"type": "Feature", "geometry": mapping(shifted), "properties": {**zone["properties"], "of": id(zone)}})
_, control = poi.sentinel_pois(rows, moved)
print(f"moved zones kept: {sum(r['kept'] for r in control)}/{len(control)} = {sum(r['kept'] for r in control) / len(control):.0%}")
for zone in zones:
    mine = [r for m, r in zip(moved, control) if m["properties"]["of"] == id(zone)]
    if mine:
        print(f"  {zone['properties']['index']} {zone['properties']['vineyard_id']} {zone['properties']['pixels']} px: moved kept {sum(r['kept'] for r in mine)}/{len(mine)}")
