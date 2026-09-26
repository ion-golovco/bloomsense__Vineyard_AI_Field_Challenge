"""The one-pass prediction on the same plots as a two-pass run: its predictions with the canopies swapped for the
first-pass canopies (pass1_canopies.json, as canopy_v3_twopass.py writes them) of the plots verify_plots kept, block ids
as assign_blocks gives them. Evaluation only.
Run: python research/probes/canopy_v3_swap.py TWOPASS.geojson PASS1.json OUT.geojson"""

import json
import sys

predictions, pass1, out = sys.argv[1:4]
data = json.loads(open(predictions).read())
block = {f["properties"]["pattern_id"]: f["properties"]["vineyard_id"] for f in data["features"]
         if f["properties"]["label"] in ("row", "vineyard") and f["properties"].get("pattern_id")}
kept = [f for f in data["features"] if f["properties"]["label"] != "vineyard"]
first = [{**f, "properties": {**f["properties"], "vineyard_id": block[f["properties"]["vineyard_id"]], "pattern_id": f["properties"]["vineyard_id"]}}
         for f in json.loads(open(pass1).read()) if f["properties"]["vineyard_id"] in block]
print(f"{sum(f['properties']['label'] == 'vineyard' for f in data['features'])} two-pass canopies -> {len(first)} first-pass canopies")
data["features"] = kept + first
open(out, "w").write(json.dumps(data))
