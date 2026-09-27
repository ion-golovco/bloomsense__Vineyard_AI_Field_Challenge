"""v6: why the gap targets on the user's labelled stretches are there. For each challenge target a gap label matches
(label_of), the pixels of its stretch on the reference row: tube shares of 2g - r - b over 25 / 15 / 10, network
probability, the canopy area on the stretch, and whether the row's piece on that tile holds any canopy at all (an axis the
canopy's row tests dropped). Evaluation only. Run from backend/: uv run --frozen python ../research/probes/v6_gap_why.py TAG"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy import ndimage
from shapely import STRtree
from shapely.geometry import LineString, shape

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import canopy, poi  # noqa: E402
from marcaj.tiles import PIXEL_M  # noqa: E402


def stretch_pixels(line: LineString, name: str) -> dict:
    tile = L.TILES[name]
    img, tr = canopy.read_rgb(tile)
    r, g, b = img.astype(np.float32)
    ex = ndimage.gaussian_filter(2 * g - r - b, 2)
    band = line.buffer(0.3, cap_style="flat").intersection(tile.bounds)
    if band.is_empty:
        return {}
    rows, cols = canopy._pixels(band, tr, ex.shape)
    if not len(rows):
        return {}
    pp = L.prob_path(name)
    prob = np.load(pp)[rows, cols] / 255 if pp else np.zeros(len(rows))
    v = ex[rows, cols]
    gr = ndimage.gaussian_filter(g - r, 2)[rows, cols]
    return {"t25": float((v > 25).mean()), "t15": float((v > 15).mean()), "t10": float((v > 10).mean()),
            "gr25": float(gr[v > 25].mean()) if (v > 25).any() else -1.0,
            "v25_12": float(((v > 25) & (gr < 12)).mean()), "v25_15": float(((v > 25) & (gr < 15)).mean()),
            "v15_12": float(((v > 15) & (gr < 12)).mean()),
            "p05": float((prob > 0.5).mean()), "bright": float(img[:, rows, cols].mean())}


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "defaults"
    canopies = json.loads((L.OUT / f"canopies_{tag}.json").read_text())
    pieces = L.ref_pieces()
    rows = L.sampled(pieces, canopies)
    pois = poi.gap_pois(rows, obstacles=L.obstacles())
    labels = L.gap_label_lines()
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    piece_geo = [(shape(f["geometry"]), f["properties"]) for f in pieces]
    ptree = STRtree([g for g, _ in piece_geo])
    out = []
    for p in pois:
        q = p["properties"]
        if not q["challenge"]:
            continue
        line = LineString([q["gap_start"], q["gap_end"]])
        answer = L.label_of(line, labels)
        if answer is None:
            continue
        mid = line.interpolate(0.5, normalized=True)
        name = next(n for n, t in L.TILES.items() if t.bounds.contains(mid))
        f = stretch_pixels(line, name)
        # canopy on the row's own piece in that tile, away from the stretch
        own = [g for i in ptree.query(L.TILES[name].bounds) for g, pr in [piece_geo[i]] if pr["row_id"] == q["row_id"] and pr["tile"] == name]
        piece_canopy = sum(geoms[i].intersection(o.buffer(0.3)).area for o in own for i in tree.query(o.buffer(0.3))) if own else -1
        out.append({"answer": answer, "reason": q["reason"], "gap_m": q["gap_m"], "green_share": q["green_share"], "tile": name,
                    "row_id": q["row_id"], "piece_canopy_m2": round(piece_canopy, 2), "piece_m": round(sum(o.length for o in own), 1), **{k: round(v, 3) for k, v in f.items()}})
    (L.OUT / f"gap_why_{tag}.json").write_text(json.dumps(out, indent=0))
    for answer in ("vines_present", "real_gap", "partly", "not_a_row"):
        sel = [o for o in out if o["answer"] == answer]
        if not sel:
            continue
        print(f"{answer}: {len(sel)} targets; piece without canopy {sum(o['piece_canopy_m2'] == 0 for o in sel)}")
        for k in ("gap_m", "green_share", "t25", "t15", "t10", "p05", "bright", "gr25", "v25_12", "v25_15", "v15_12"):
            vals = [o[k] for o in sel if k in o]
            print(f"   {k:12s} " + " ".join(f"{x:6.2f}" for x in np.percentile(vals, [10, 25, 50, 75, 90])))
    print(Counter((o["answer"], o["tile"]) for o in out if o["answer"] == "vines_present").most_common(15))
