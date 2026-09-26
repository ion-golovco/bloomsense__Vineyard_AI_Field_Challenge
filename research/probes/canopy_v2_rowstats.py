"""Per-row canopy statistics on chosen fields (default the three central ones): pieces per 10 m, colour and texture of the
canopy pixels, and the green share of the ground beside the pieces, to find the verge rows whose canopies are grass.
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_v2_rowstats.py [FIELD...]"""

import json
import sys
from pathlib import Path

import numpy as np
from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_features import features  # noqa: E402
from canopy_v2_lib import FIELDS, WORK, frozen_plots, prob_path  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.tiles import load_tiles  # noqa: E402


def row_table(fields, canopies_path=WORK / "canopies_defaults.json"):
    tiles = {t.name: t for t in load_tiles()}
    plots = frozen_plots()
    C = json.loads(canopies_path.read_text())
    G = [shape(f["geometry"]) for f in C]
    tree = STRtree(G)
    cache: dict = {}
    out = []
    for f in plots:
        p = f["properties"]
        if p["label"] != "row" or p["block_id"] not in fields:
            continue
        line = shape(f["geometry"])
        hits = [i for i in tree.query(line.buffer(0.3)) if C[i]["properties"].get("pattern_id") == p["pattern_id"]]
        feats = []
        for i in hits:
            name = C[i]["properties"]["tile_run"]
            if name not in cache:
                cache.clear()
                cache[name] = canopy.read_rgb(tiles[name])
            rgb, transform = cache[name]
            x = features(G[i], rgb, transform, None)
            if x:
                feats.append(x)
        area = sum(x["area"] for x in feats)
        w = lambda k: float(sum(x[k] * x["area"] for x in feats) / area) if area else 0.0
        out.append({"row_id": p["row_id"], "block": p["block_id"], "length": line.length, "pieces_10m": 10 * len(feats) / max(line.length, 1),
                    "area_m": area / max(line.length, 1), "gr": w("gr"), "ex": w("ex"), "bright": w("bright"), "grad": w("grad"),
                    "ring_green": w("ring_green"), "ring_gr": w("ring_gr")})
    return out


if __name__ == "__main__":
    fields = tuple(sys.argv[1:]) or FIELDS
    rows = row_table(fields)
    (WORK / "rowstats.json").write_text(json.dumps(rows))
    for block in fields:
        rs = sorted([r for r in rows if r["block"] == block], key=lambda r: r["row_id"])
        keys = ["pieces_10m", "area_m", "gr", "ex", "bright", "grad", "ring_green", "ring_gr"]
        med = {k: np.median([r[k] for r in rs]) for k in keys}
        print(block, "median", " ".join(f"{k} {med[k]:.2f}" for k in keys))
        for r in rs:
            flag = " <" if r["ring_green"] > 2 * med["ring_green"] + 0.1 or r["grad"] < 0.75 * med["grad"] else ""
            print(f"   {r['row_id']:14s} {r['length']:5.0f} m " + " ".join(f"{r[k]:6.2f}" for k in keys) + flag)
