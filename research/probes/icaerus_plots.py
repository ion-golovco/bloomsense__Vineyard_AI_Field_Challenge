"""Plot check: ICAERUS vine density (instances per 100 m2 and instance-area share, confidence >= 0.25, zoom 1) inside the
user's hand-drawn plot outlines (verdicts kind "plot": vineyard / overgrown / orchard), split into the part our predicted
blocks cover and the part they miss, against the background (outside every outline and every block). Our own canopy
share is shown beside it. Evaluation only: the outlines are read to score, never to predict.
Run from backend/: uv run --frozen python ../research/probes/icaerus_plots.py"""

import json
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
from sam_v2_lib import base_features  # noqa: E402

from marcaj.review import load_verdicts  # noqa: E402
from marcaj.tiles import PIXEL_M, REPO_ROOT, load_tiles  # noqa: E402

INST = REPO_ROOT / "data" / "generated" / "work" / "icaerus" / "inst_z1"
CONF = 0.25


def instances() -> tuple[np.ndarray, np.ndarray]:
    """World centroids (n, 2) and areas (n,) in m2 of every instance with confidence >= CONF over the 311 tiles."""
    xy, area = [], []
    for tile in load_tiles():
        path = INST / (tile.name[:-4] + ".npz")
        data = np.load(path)
        if not len(data["ends"]):
            continue
        for ring, conf in zip(np.split(data["xy"], data["ends"][:-1]), data["conf"]):
            if conf < CONF or len(ring) < 3:
                continue
            x, y = ring[:, 0].astype(np.float64), ring[:, 1].astype(np.float64)
            area.append(0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) * PIXEL_M**2)
            xy.append((tile.left + x.mean() * PIXEL_M, tile.top - y.mean() * PIXEL_M))
    return np.array(xy), np.array(area)


def density(region, xy, area, canopy_tree, canopies) -> dict:
    if region.is_empty or region.area < 50:
        return {}
    inside = shapely.contains_xy(region, xy[:, 0], xy[:, 1])
    ours = sum(canopies[i].intersection(region).area for i in canopy_tree.query(region))
    return {"m2": round(region.area), "ica_per100": round(100 * inside.sum() / region.area, 2), "ica_share": round(area[inside].sum() / region.area, 3),
            "our_share": round(ours / region.area, 3)}


if __name__ == "__main__":
    features = base_features()
    blocks = unary_union([shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"])
    canopies = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "vineyard"]
    tree = shapely.STRtree(canopies)
    xy, area = instances()
    replaced = {v.get("replaces") for v in load_verdicts() if v.get("replaces")}
    outlines = [v for v in load_verdicts() if v["kind"] == "plot" and v["id"] not in replaced]
    study = unary_union([t.bounds for t in load_tiles()])
    rows = []
    for v in outlines:
        g = shape(v["geometry"])
        covered = g.intersection(blocks).area / g.area
        rows.append({"id": v["id"], "label": v["label"], "block_cover": round(covered, 3),
                     "in_blocks": density(g.intersection(blocks), xy, area, tree, canopies), "missed": density(g.difference(blocks), xy, area, tree, canopies)})
    background = study.difference(unary_union([shape(v["geometry"]) for v in outlines] + [blocks]))
    summary = {"background": density(background, xy, area, tree, canopies)}
    for label in ("vineyard", "overgrown", "orchard"):
        for part in ("in_blocks", "missed"):
            sel = [r[part] for r in rows if r["label"] == label and r[part]]
            m2 = sum(s["m2"] for s in sel)
            if m2:
                summary[f"{label}/{part}"] = {"n": len(sel), "m2": m2, **{k: round(sum(s[k] * s["m2"] for s in sel) / m2, 3) for k in ("ica_per100", "ica_share", "our_share")}}
    print(json.dumps(summary, indent=1))
    for r in sorted(rows, key=lambda r: r["block_cover"]):
        if r["label"] != "orchard" and r["block_cover"] < 0.7 and r["missed"]:
            print(r["id"], r["label"], "block_cover", r["block_cover"], "missed", r["missed"], "in_blocks", r["in_blocks"])
    (REPO_ROOT / "data" / "generated" / "work" / "icaerus" / "plots_density.json").write_text(json.dumps({"summary": summary, "outlines": rows}, indent=1))
