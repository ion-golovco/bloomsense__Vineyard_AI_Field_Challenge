"""Joins the detector output (waste.geojson) with my visual verdicts, checks the control tiles, renders the sheet
data/generated/work/waste/detector_*.jpg (verdict in each caption) and prints precision.
Site-wide v1 detector (before the in-block rule): reads candidates_sitewide_v1.json and waste_sitewide_v1.geojson.
Run from backend/ after `waste_labels.py`."""

import collections
import json
import sys
from pathlib import Path

from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).parent))
from waste_labels import _key  # noqa: E402
from waste_sheet import sheets  # noqa: E402

from marcaj.tiles import REPO_ROOT  # noqa: E402

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
# verdicts for detector boxes that were not on the ranked sheets, from detector_*.jpg
EXTRA = {"siret3_r015_c011.tif": ("likely", "crumpled blue plastic sheet by a shed"),
         "siret3_r028_c029.tif": ("unsure", "white sheet or sack by a yard wall"),
         "siret3_r024_c018.tif": ("not", "concrete base at a pole by the road"),
         "siret3_r036_c021.tif": ("unsure", "small white object in dry grass")}
CONTROL_TILES = {"siret3_r006_c004.tif", "siret3_r021_c012.tif"}

labels = json.loads((W / "labels.json").read_text())
candidates = json.loads((W / "candidates_sitewide_v1.json").read_text())
features = json.loads((W / "waste_sitewide_v1.geojson").read_text())["features"]
kept = []
for feature in features:
    geometry = shape(feature["geometry"])
    member = max((c for c in candidates if c["tile"] in feature["properties"]["tiles"] and geometry.intersects(box(*c["bounds"]))),
                 key=lambda c: c["area_m2"])
    verdict = labels.get(_key(member), {}).get("label") or EXTRA.get(member["tile"], ("unreviewed", ""))[0]
    kept.append({**member, "verdict": verdict, "where": verdict.upper(), "vineyard_id": feature["properties"]["vineyard_id"],
                 "centre": [round(geometry.centroid.x, 1), round(geometry.centroid.y, 1)]})
kept.sort(key=lambda c: -c["area_m2"])
counts = collections.Counter(c["verdict"] for c in kept)
print(f"{len(kept)} boxes: {dict(counts)}; likely share {counts['likely'] / max(len(kept), 1):.2f}")
print("on control tiles:", sum(c["tile"] in CONTROL_TILES for c in kept))
print("likely waste not kept:", [v["sheet"] for k, v in labels.items() if v["label"] == "likely" and k not in {_key(c) for c in kept}])
(W / "detector_review.json").write_text(json.dumps([{k: c[k] for k in ("tile", "px", "centre", "kind", "area_m2", "vineyard_id", "verdict")} for c in kept], indent=1))
for path in sheets(kept, W / "detector"):
    print(path)
