"""The user's gap labels (review_tab "gaps": vines present / real gap / partly) against the pixels and the current POIs.

Per labelled gap line (10:54 rows): the across-row profile of vine colour within +-1 m (where the plants are if the row is
off), the tube's colour shares at several thresholds, blobs of weak colour, the organizers' canopies on the two reference
tiles, and whether a POI of the same reason is still within 2 m in a canopy run (`canopies_<tag>.json`, POIs by
canopy_recall_lib.gap_stats on the 15:43 rows). Evaluation only; labels are never used by prediction code.
Run from backend/: uv run --frozen python ../research/probes/canopy_v2_gaps.py [TAG]"""

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from scipy import ndimage
from shapely import STRtree
from shapely.geometry import LineString, Point, shape

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "review"))
from canopy_v2_lib import VERDICTS, WORK  # noqa: E402
import canopy_recall_lib as crl  # noqa: E402
from build import read_world  # noqa: E402

from marcaj.cvat import read_cvat  # noqa: E402
from marcaj.tiles import DATA_DIR, PIXEL_M, load_tiles  # noqa: E402

GRID_LEFT, GRID_TOP = 628992.0, 5221222.4
REACH = 1.0


def gap_labels() -> list[dict]:
    return [v for v in json.loads(VERDICTS.read_text()) if v.get("properties", {}).get("review_tab") == "gaps"]


def corridor(line: LineString):
    """u (along), v (across) in metres and the RGB of every pixel within REACH of the line."""
    left, bottom, right, top = line.buffer(REACH).bounds
    left = GRID_LEFT + np.floor((left - GRID_LEFT) / PIXEL_M) * PIXEL_M
    top = GRID_TOP - np.floor((GRID_TOP - top) / PIXEL_M) * PIXEL_M
    w, h = int(np.ceil((right - left) / PIXEL_M)), int(np.ceil((top - bottom) / PIXEL_M))
    rgb = read_world(left, top, w, h).astype(np.float32)
    rows, cols = np.indices((h, w))
    x, y = left + (cols + 0.5) * PIXEL_M, top - (rows + 0.5) * PIXEL_M
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    d = np.array([x1 - x0, y1 - y0]) / line.length
    u, v = (x - x0) * d[0] + (y - y0) * d[1], -(x - x0) * d[1] + (y - y0) * d[0]
    return u, v, rgb


def features(line: LineString) -> dict:
    u, v, rgb = corridor(line)
    r, g, b = rgb
    valid = rgb.sum(0) > 0
    ex = ndimage.gaussian_filter(2 * g - r - b, 2)
    inside = (u >= 0) & (u <= line.length) & valid
    out = {}
    tube = inside & (np.abs(v) <= 0.3)
    for t in (25, 15, 10, 5):
        out[f"tube{t}"] = float((ex[tube] > t).mean()) if tube.any() else 0.0
    bins = np.arange(-REACH, REACH + 1e-9, 0.1)
    k = np.digitize(v, bins) - 1
    prof = np.array([(ex[inside & (k == i)] > 15).mean() if (inside & (k == i)).any() else 0 for i in range(len(bins) - 1)])
    centres = (bins[:-1] + bins[1:]) / 2
    out["peak_v"] = float(centres[int(np.argmax(prof))])
    out["peak15"] = float(prof.max())
    out["centre15"] = float(prof[np.abs(centres) <= 0.3].mean())
    # blobs of weak colour (> 10) in the tube and in +-0.8 m, 0.02 m2 or more
    for name, reach in (("blobs_tube", 0.3), ("blobs_08", 0.8)):
        lab, n = ndimage.label((ex > 10) & inside & (np.abs(v) <= reach))
        sizes = np.bincount(lab.ravel())[1:] * PIXEL_M**2
        out[name] = int((sizes >= 0.02).sum())
        out[name + "_m2"] = float(sizes[sizes >= 0.02].sum())
    out["dark_tube"] = float(((rgb.sum(0) / 3) < 50)[tube].mean()) if tube.any() else 0.0
    return out


def still_flagged(labels: list[dict], pois: list[dict], reach_m: float = 2.0) -> list[bool]:
    points = [(Point(p["geometry"]["coordinates"]), p["properties"]["reason"]) for p in pois]
    tree = STRtree([p for p, _ in points])
    out = []
    for lab in labels:
        line = shape(lab["geometry"])
        near = tree.query(line.buffer(reach_m))
        out.append(any(points[i][1] == lab["properties"]["reason"] and points[i][0].distance(line) <= reach_m for i in near))
    return out


def summary(labels: list[dict], flagged: list[bool], title: str) -> str:
    parts = []
    for reason in ("gap", "planting"):
        for answer in ("vines_present", "real_gap", "partly"):
            idx = [i for i, lab in enumerate(labels) if lab["properties"]["reason"] == reason and lab["properties"]["review_answer"] == answer]
            parts.append(f"{reason}/{answer} {sum(flagged[i] for i in idx)}/{len(idx)}")
    return f"{title}: still flagged " + ", ".join(parts)


def main() -> None:
    labels = gap_labels()
    tag = sys.argv[1] if len(sys.argv) > 1 else "defaults"
    canopies = json.loads((WORK / f"canopies_{tag}.json").read_text()) if tag != "base" else \
        [f for f in json.loads(crl.UPLOADED.read_text())["features"] if f["properties"]["label"] == "vineyard"]
    stats = crl.gap_stats(canopies)
    flagged = still_flagged(labels, stats["_pois"])
    print(summary(labels, flagged, tag))
    tiles = {t.name: t for t in load_tiles()}
    with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
        xml = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
    refs = [shape(f["geometry"]) for f in read_cvat(xml, tiles) if f["properties"]["label"] == "vineyard"]
    ref_tree = STRtree(refs)
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    rows = []
    for lab, flag in zip(labels, flagged):
        line = shape(lab["geometry"])
        f = features(line)
        near = [geoms[i] for i in tree.query(line.buffer(0.3))]
        f["canopy_m2_03"] = float(sum(gm.intersection(line.buffer(0.3)).area for gm in near))
        on_ref = lab["properties"]["tile"] in crl.NAMES
        f["ref_m2"] = float(sum(refs[i].intersection(line.buffer(0.5)).area for i in ref_tree.query(line.buffer(0.5)))) if on_ref else -1.0
        rows.append({"id": lab["properties"]["review_id"], "tile": lab["properties"]["tile"], "reason": lab["properties"]["reason"],
                     "answer": lab["properties"]["review_answer"], "gap_m": lab["properties"]["gap_m"], "flagged": flag, **f})
    (WORK / f"gaps_{tag}.json").write_text(json.dumps(rows))
    keys = ["tube25", "tube15", "tube10", "tube5", "peak_v", "peak15", "centre15", "blobs_tube", "blobs_08", "blobs_tube_m2", "dark_tube", "canopy_m2_03"]
    for answer in ("vines_present", "real_gap", "partly"):
        sel = [r for r in rows if r["answer"] == answer and r["reason"] == "gap"]
        print(f"gap/{answer} n={len(sel)}")
        for k in keys:
            print(f"   {k:14s} " + " ".join(f"{q:6.2f}" for q in np.percentile([r[k] for r in sel], [10, 25, 50, 75, 90])))
    print("reference-tile labels:", [(r["tile"][7:16], r["answer"], r["reason"], round(r["gap_m"]), round(r["ref_m2"], 2), r["flagged"]) for r in rows if r["ref_m2"] >= 0])


if __name__ == "__main__":
    main()
