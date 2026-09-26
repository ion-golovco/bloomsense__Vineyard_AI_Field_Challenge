"""Reference canopies on the two organizer tiles that a variant misses: per reference polygon the share covered by
predictions and the best IoU; for the missed ones (covered < 20%) their area, colour share (2g-r-b > 25 and > 15),
network share (> 0.2) and distance to the nearest kept row axis. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_recall_refmiss.py [variant...]"""

import json
import sys
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_recall_lib import NAMES, PROB, WORK  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.canopy import CanopyParams  # noqa: E402
from marcaj.cvat import build_scene  # noqa: E402
from marcaj.tiles import DATA_DIR, load_tiles  # noqa: E402

tiles = {t.name: t for t in load_tiles()}
base = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(tiles.values()))["features"]
P = CanopyParams()
for tag in sys.argv[1:] or ["base"]:
    found = [shape(f["geometry"]) for f in json.loads((WORK / f"canopies_{tag}.json").read_text())]
    tree = STRtree(found)
    rows = []
    for name in NAMES:
        rgb, transform = canopy.read_rgb(tiles[name])
        excess, valid = canopy.excess_green(rgb, P)
        prob = np.load(PROB / f"{name[:-4]}.npy").astype(np.float32) / 255
        ref = [shape(f["geometry"]) for f in base if f["properties"]["source"] == "reference" and f["properties"]["label"] == "vineyard" and f["properties"]["tile"] == name]
        for r in ref:
            near = [found[i] for i in tree.query(r)]
            cover = sum(r.intersection(p).area for p in near) / r.area
            iou = max((r.intersection(p).area / r.union(p).area for p in near), default=0.0)
            if cover < 0.2:
                m = rasterize([r], out_shape=excess.shape, transform=transform).astype(bool)
                rows.append({"tile": name[7:16], "area": round(r.area, 2), "cover": round(cover, 2), "c25": round(float((excess[m] > 25).mean()), 2),
                             "c15": round(float((excess[m] > 15).mean()), 2), "net": round(float((prob[m] > 0.2).mean()), 2),
                             "x": round(r.centroid.x, 1), "y": round(r.centroid.y, 1)})
        n_ref = len(ref)
    print(tag, "missed references (covered < 20%):", len(rows), "area", round(sum(r["area"] for r in rows), 1))
    for key, lo, hi in (("area", 0, 0.3), ("area", 0.3, 0.6), ("area", 0.6, 99)):
        sub = [r for r in rows if lo <= r[key] < hi]
        print(f"  area {lo}-{hi}: {len(sub)}, median c25 {np.median([r['c25'] for r in sub]) if sub else 0:.2f} c15 {np.median([r['c15'] for r in sub]) if sub else 0:.2f} net {np.median([r['net'] for r in sub]) if sub else 0:.2f}")
    (WORK / f"refmiss_{tag}.json").write_text(json.dumps(rows, indent=1))
