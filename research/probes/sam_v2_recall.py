"""SAM recall along uncovered row stretches (marcaj.canopy_sam.recall_tile) on the 18:13 base: judge on the two organizer
tiles, row cover on the central fields (V19-11, V21-13, V22-13 on r019_c011, r021_c013, r022_c013, r020_c012), and the
user's gap labels (vines present / real gap: filled = no canopy-free stretch >= 5 m left along the labelled line).
Evaluation only. Run from backend/ (cached weights only):
HF_HUB_OFFLINE=1 uv run --frozen --group sam python ../research/probes/sam_v2_recall.py NAME... [--gaps]"""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from sam_v2_lib import MID, MID_FIELDS, NAMES, WORK, base_features, by_tile, judge_canopies, labels, row_cover, rows_of  # noqa: E402

from marcaj import canopy, canopy_sam  # noqa: E402
from marcaj.canopy_sam import RecallParams  # noqa: E402
from marcaj.tiles import load_tiles  # noqa: E402

D = RecallParams()
VARIANTS = {
    "base": None,
    "recall": D,
    "recall_dark05": replace(D, max_dark=0.5),
    "recall_ex10": replace(D, min_excess=10),
    "recall_ex15_dark05": replace(D, min_excess=15, max_dark=0.5),
    "recall_min02": replace(D, min_m2=0.2),
    "recall_512": replace(D, crop_px=512),
    "recall_1m": replace(D, plant_m=1.0),
}


def longest_gap(line, geoms, tube_m: float = 0.3, step: float = 0.05) -> tuple[float, float]:
    """Longest stretch of `line` with no canopy within +-`tube_m`, and the covered share."""
    t = np.arange(0, line.length, step)
    pts = np.array([line.interpolate(x).coords[0] for x in t])
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    normal = np.array([-(y1 - y0), x1 - x0]) / line.length
    hit = np.zeros(len(t), bool)
    if geoms:
        union = shapely.union_all(geoms)
        for off in np.linspace(-tube_m, tube_m, 7):
            q = pts + off * normal
            hit |= shapely.contains_xy(union, q[:, 0], q[:, 1])
    edges = np.flatnonzero(np.diff(np.concatenate([[1], hit.astype(int), [1]])))
    return float(max(((b - a) * step for a, b in zip(edges[::2], edges[1::2])), default=0.0)), float(hit.mean())


def gap_metrics(canopies, gap_labels):
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    out: dict[str, list] = {}
    for lab in gap_labels:
        line = shape(lab["geometry"])
        gap, cover = longest_gap(line, [geoms[i] for i in tree.query(line.buffer(0.35))])
        s = out.setdefault(f"{lab['properties']['reason']}/{lab['properties']['review_answer']}", [0, 0, 0.0])
        s[0] += gap < 5.0
        s[1] += 1
        s[2] += cover
    return {k: f"filled {a}/{n}, cover {c / n:.3f}" for k, (a, n, c) in sorted(out.items())}


if __name__ == "__main__":
    with_gaps = "--gaps" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    tiles = {t.name: t for t in load_tiles()}
    features = base_features()
    per_tile = by_tile(features)
    rows = rows_of(features)
    gap_labels = labels("gaps") if with_gaps else []
    todo = list(NAMES + MID)
    if with_gaps:
        lines = [shape(g["geometry"]) for g in gap_labels]
        todo += sorted({n for n, t in tiles.items() for line in lines if line.intersects(t.bounds)} - set(todo))
    mid_rows = [r for r in rows if r["properties"]["tile"] in MID and r["properties"]["vineyard_id"] in MID_FIELDS]
    for name in names:
        params = VARIANTS[name]
        started = time.perf_counter()
        added = []
        if params is not None:
            for n in todo:
                axes = [(shape(r["geometry"]), r["properties"]["vineyard_id"]) for r in rows if r["properties"]["tile"] == n]
                if not axes:
                    continue
                rgb, transform = canopy.read_rgb(tiles[n])
                added += canopy_sam.recall_tile(per_tile.get(n, []), axes, rgb, transform, params)
        seconds = time.perf_counter() - started
        canopies = [f for fs in per_tile.values() for f in fs] + added
        on_refs = [f for f in added if any(shape(f["geometry"]).intersects(tiles[t].bounds) for t in NAMES)]
        result = {"name": name, "judge": judge_canopies(canopies), "added": len(added), "added_m2": round(sum(shape(f["geometry"]).area for f in added), 1),
                  "added_on_refs": len(on_refs), "mid_row_cover": row_cover(canopies, mid_rows),
                  "gap_labels": gap_metrics(canopies, gap_labels) if with_gaps else None, "tiles": len(todo), "seconds": round(seconds, 1)}
        print(json.dumps(result), flush=True)
        if added:
            (WORK / f"recall_{name}.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": added}))
