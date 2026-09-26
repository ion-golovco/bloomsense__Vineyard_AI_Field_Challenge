"""Planting distance along rows: consecutive centre spacing of canopy pieces in one row, for the organizer reference
canopies and for our baseline pieces (canopy_v2 defaults) on the two organizer tiles and the whole site. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/sam_v2_spacing.py"""

import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
import canopy_recall_lib as crl  # noqa: E402

from marcaj.cvat import read_cvat  # noqa: E402
from marcaj.tiles import DATA_DIR, REPO_ROOT, load_tiles  # noqa: E402

V2 = REPO_ROOT / "data" / "generated" / "work" / "canopy_v2"


def spacings(geoms, angle):
    a = np.radians(angle)
    d, n = np.array([np.cos(a), np.sin(a)]), np.array([-np.sin(a), np.cos(a)])
    pts = np.array([g.centroid.coords[0] for g in geoms])
    lens = np.array([crl.length_m(g) for g in geoms])
    u, v = pts @ d, pts @ n
    order = np.argsort(v)
    rows = np.split(order, np.flatnonzero(np.diff(v[order]) > 0.6) + 1)
    out = []
    for r in rows:
        r = r[np.argsort(u[r])]
        for i, j in zip(r[:-1], r[1:]):
            if lens[i] < 2.5 and lens[j] < 2.5:
                out.append(u[j] - u[i])
    return np.array(out)


def angle_of(geoms):
    long = [g for g in geoms if crl.length_m(g) > 3]
    angs = []
    for g in long:
        c = np.asarray(g.minimum_rotated_rectangle.exterior.coords)
        e = max([c[1] - c[0], c[2] - c[1]], key=lambda x: np.hypot(*x))
        angs.append(np.degrees(np.arctan2(e[1], e[0])) % 180)
    return float(np.median(angs))


tiles = {t.name: t for t in load_tiles()}
with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
    xml = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
ref = [f for f in read_cvat(xml, tiles) if f["properties"]["label"] == "vineyard"]
ours = json.loads((V2 / "canopies_defaults.json").read_text())
q = [10, 25, 50, 75, 90]
for name in crl.NAMES:
    b = tiles[name].bounds
    rg = [shape(f["geometry"]) for f in ref if shape(f["geometry"]).centroid.within(b)]
    ang = angle_of(rg)
    s = spacings(rg, ang)
    print(name, "angle", round(ang, 1), "ref spacing n", len(s), np.round(np.percentile(s, q), 2), "in 0.7-3.5:", np.round(np.median(s[(s > 0.7) & (s < 3.5)]), 2))
    og = [shape(f["geometry"]) for f in ours if f["properties"].get("tile_run") == name]
    s = spacings(og, ang)
    print(name, "ours spacing n", len(s), np.round(np.percentile(s, q), 2), "in 0.7-3.5:", np.round(np.median(s[(s > 0.7) & (s < 3.5)]), 2))
# site: per tile+pattern
by = defaultdict(list)
for f in ours:
    by[(f["properties"]["tile_run"], f["properties"]["pattern_id"])].append(shape(f["geometry"]))
meds = []
for key, gs in by.items():
    if sum(crl.length_m(g) > 3 for g in gs) < 3 or len(gs) < 20:
        continue
    s = spacings(gs, angle_of(gs))
    s = s[(s > 0.7) & (s < 3.5)]
    if len(s) >= 10:
        meds.append(np.median(s))
print("site per tile/plot median spacing:", len(meds), np.round(np.percentile(meds, q), 2))
