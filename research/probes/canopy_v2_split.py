"""Where the organizer reference splits touching plants and where it draws strips, against the user's labels.

For every predicted piece (defaults run, canopies_defaults.json) longer than 2 m on the two organizer tiles, and every
current piece matching a labelled long canopy: its along-row width profile (the neck the split rule sees), the number
of reference canopies it covers, and its row context (median length of the other pieces on its row). Writes
data/generated/work/canopy_v2/split.json and prints the tables. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v2_split.py"""

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import shapely
from scipy import ndimage
from shapely import STRtree
from shapely.geometry import LineString, shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import NAMES, WORK, frozen_plots, labels, match  # noqa: E402
import canopy_recall_lib as crl  # noqa: E402

from marcaj.cvat import read_cvat  # noqa: E402
from marcaj.tiles import DATA_DIR, load_tiles  # noqa: E402

STEP = 0.05


def axis_frame(geom, angle_deg: float):
    a = np.radians(angle_deg)
    d = np.array([np.cos(a), np.sin(a)])
    c = np.asarray(geom.centroid.coords[0])
    return c, d


def profile(geom, angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Width across the row (m) per STEP along it, and the along positions."""
    c, d = axis_frame(geom, angle_deg)
    n = np.array([-d[1], d[0]])
    pts = np.asarray(geom.exterior.coords) - c
    u = pts @ d
    xs = np.arange(u.min() + STEP / 2, u.max(), STEP)
    lines = [LineString([c + x * d - 2 * n, c + x * d + 2 * n]) for x in xs]
    width = shapely.length(shapely.intersection(np.array(lines, dtype=object), geom))
    return xs - u.min(), width


def necks(width: np.ndarray, least_m: float = 0.3) -> list[tuple[float, int, float]]:
    """Every local minimum with at least `least_m` on each side: (depth = width / smaller side peak, index, width m)."""
    w = ndimage.gaussian_filter1d(width.astype(float), 1.0)
    least = int(least_m / STEP)
    out = []
    for i in range(least, len(w) - least):
        if w[i] <= w[i - 1] and w[i] <= w[i + 1]:
            depth = w[i] / max(min(w[:i].max(), w[i + 1:].max()), 1e-6)
            out.append((float(depth), i, float(w[i])))
    return sorted(out)


def row_key(geom, rows: list[tuple[str, LineString]], tree: STRtree) -> str:
    p = geom.centroid
    hits = tree.query(p.buffer(0.6))
    best = min(hits, key=lambda i: rows[i][1].distance(p), default=None)
    return rows[best][0] if best is not None and rows[best][1].distance(p) < 0.6 else ""


def main() -> None:
    tiles = {t.name: t for t in load_tiles()}
    plots = frozen_plots()
    angle = {f["properties"]["vineyard_id"]: f["properties"]["row_angle"] for f in plots if f["properties"]["label"] == "block"}
    rows = [(f["properties"]["row_id"], shape(f["geometry"])) for f in plots if f["properties"]["label"] == "row"]
    row_tree = STRtree([r for _, r in rows])
    preds = json.loads((WORK / "canopies_defaults.json").read_text())
    geoms = [shape(f["geometry"]) for f in preds]
    lengths = np.array([crl.length_m(g) for g in geoms])
    keys = [row_key(g, rows, row_tree) for g in geoms]
    by_row: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        by_row.setdefault(k, []).append(i)
    with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
        xml = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
    refs = [shape(f["geometry"]) for f in read_cvat(xml, tiles) if f["properties"]["label"] == "vineyard"]
    ref_tree = STRtree(refs)
    lab = {}
    for m in match(labels(), preds):
        for i in m["hits"]:
            lab[i] = m["answer"]
    on_ref = {i for i, f in enumerate(preds) if f["properties"]["tile_run"] in NAMES}
    out = []
    for i in sorted(on_ref | set(lab)):
        g = geoms[i]
        if lengths[i] <= 2.0 or g.geom_type != "Polygon":
            continue
        a = angle.get(preds[i]["properties"]["pattern_id"], 0.0)
        xs, width = profile(g, a)
        ns = necks(width)
        others = [lengths[j] for j in by_row.get(keys[i], []) if j != i and keys[i]]
        n_ref = sum(refs[j].intersection(g).area >= 0.5 * refs[j].area for j in ref_tree.query(g)) if i in on_ref else -1
        out.append({"i": i, "tile": preds[i]["properties"]["tile_run"], "group": lab.get(i, "reference_tile" if i in on_ref else ""),
                    "length": float(lengths[i]), "area": g.area, "n_ref": int(n_ref),
                    "neck1": ns[0][0] if ns else 1.0, "neck1_w": ns[0][2] if ns else 0.0,
                    "neck1_at": float(xs[ns[0][1]] / xs[-1]) if ns else 0.5,
                    "necks05": sum(n[0] < 0.5 for n in ns), "necks06": sum(n[0] < 0.6 for n in ns), "necks07": sum(n[0] < 0.7 for n in ns),
                    "width_med": float(np.median(width)), "row_med": float(np.median(others)) if others else 0.0,
                    "row_n": len(others), "row_long_share": float(np.mean(np.array(others) > 3)) if others else 0.0})
    (WORK / "split.json").write_text(json.dumps(out))

    def table(title, rows_):
        print(f"{title}: n={len(rows_)}")
        for k in ["length", "neck1", "neck1_w", "necks05", "necks06", "width_med", "row_med", "row_long_share", "n_ref"]:
            v = np.array([r[k] for r in rows_], float)
            if len(v):
                print(f"   {k:14s} " + " ".join(f"{q:6.2f}" for q in np.percentile(v, [10, 25, 50, 75, 90])))

    ref = [r for r in out if r["group"] == "reference_tile" or r["n_ref"] >= 0]
    for name in NAMES:
        t = [r for r in ref if r["tile"] == name and r["length"] > 2.5]
        table(f"{name} pieces > 2.5 m covering 0-1 reference (reference keeps whole or none)", [r for r in t if r["n_ref"] <= 1])
        table(f"{name} pieces > 2.5 m covering >= 2 references (reference splits)", [r for r in t if r["n_ref"] >= 2])
    for g in ["several", "one_plant", "not_vine"]:
        table(f"labelled {g} (current pieces)", [r for r in out if r["group"] == g])


if __name__ == "__main__":
    main()
