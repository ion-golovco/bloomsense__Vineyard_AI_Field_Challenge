"""In-block detector review: joins waste.geojson with my verdicts, checks the control tiles, and renders
detector_inblock_*.jpg (kept boxes) and nearmiss_inblock_*.jpg (candidates at a looser verifier), verdict in captions.
Run from backend/ after `python -m marcaj.waste`."""

import json
import sys
from dataclasses import replace
from pathlib import Path

from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).parent))
from waste_sheet import sheets  # noqa: E402

from marcaj.tiles import REPO_ROOT  # noqa: E402
from marcaj.waste import WasteParams, accept  # noqa: E402

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
CONTROL = {"siret3_r006_c004.tif", "siret3_r021_c012.tif"}
LOOSE = replace(WasteParams(), area_m2=(0.10, 1.5), min_dev=160.0, min_lum=205.0, max_chroma=25.0)
# my verdicts from the crops, keyed by (tile, rounded EPSG:32635 centre)
VERDICTS = {
    ("siret3_r018_c013.tif", 629673, 5220250): ("likely", "white plastic item (bag or basin) with a grey film, P03 headland"),
    ("siret3_r022_c013.tif", 629674, 5220088): ("likely", "white bag at a P07 vine row, blob-shaped among lying white tubes"),
    ("siret3_r021_c015.tif", 629771, 5220128): ("possible", "scattered white paper/plastic pieces in grass by a parked car, P02 corner"),
    ("siret3_r014_c004.tif", 629211, 5220483): ("unsure", "yellow and white object in a shrub at a P12 row: container or equipment"),
    ("siret3_r010_c002.tif", 629099, 5220666): ("not", "white post or tube with a long shadow, P04"),
}


def verdict(c: dict) -> tuple[str, str]:
    b = c["bounds"]
    key = (c["tile"], round((b[0] + b[2]) / 2), round((b[1] + b[3]) / 2))
    # every other near miss in nearmiss_inblock_01.jpg (P13, P15, P20, P21, P31) is a silvery shrub or blossom
    return VERDICTS.get(key, ("not", "silvery shrub or blossom"))


candidates = json.loads((W / "candidates.json").read_text())
features = json.loads((W / "waste.geojson").read_text())["features"]
kept = [c for c in candidates if accept(c)]
loose = [c for c in candidates if accept(c, LOOSE) and not accept(c)]
print(f"{len(features)} boxes; control tiles: {sum(c['tile'] in CONTROL for c in kept)} kept of {sum(c['tile'] in CONTROL for c in candidates)} candidates")
for name, items in (("detector_inblock", kept), ("nearmiss_inblock", loose)):
    for c in items:
        # draw the box the detector writes (its largest bright piece), in tile pixels
        row, col = int(c["tile"][8:11]), int(c["tile"][13:16])
        left, top = 628992.0 + 51.2 * col, 5221222.4 - 51.2 * row
        x0, y0, x1, y1 = c["box"]
        c["px"] = [round((x0 - left) / 0.025), round((top - y1) / 0.025), round((x1 - left) / 0.025), round((top - y0) / 0.025)]
        c["verdict"], c["why"] = verdict(c)
        c["where"] = f"{c['verdict'].upper()} {c['vineyard_id']}"
        b = c["bounds"]
        print(f"  {name}: {c['tile']} E {(b[0] + b[2]) / 2:.1f} N {(b[1] + b[3]) / 2:.1f} {c['vineyard_id']} {c['area_m2']:.2f} m2 {c['verdict']}: {c['why']}")
    if items:
        print(sheets(items, W / name))
for feature in features:
    geometry = shape(feature["geometry"])
    assert any(geometry.intersects(box(*c["bounds"])) for c in kept), feature
