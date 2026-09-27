"""v6: current gap targets against the organizer-style option (`poi.PLANTED_MIN_M2` / `--min-canopy-m2 0.25`) on the
carry predictions: organizer example gaps found, precision on the user's gap labels, targets per field, and the route
impact (count, spread: Euclidean minimum spanning tree over the targets as a tour-length proxy, new targets far from
every current one). Reads the two POI files `poi.main` wrote. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/v6_poi_orgstyle.py"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.sparse.csgraph import minimum_spanning_tree
from scipy.spatial.distance import cdist
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import poi  # noqa: E402
from marcaj.tiles import Tile  # noqa: E402

# self-check of poi.planted_canopies: a 0.3 m2 plant cut by a tile edge is kept whole, a lone 0.15 m2 piece and two
# touching 0.15 m2 pieces on one tile (two plants) are not, and 0 keeps everything
_t = [Tile("a", "", Path(), 0, 0, 0.0, 51.2), Tile("b", "", Path(), 0, 0, 51.2, 51.2)]
_c = [box(50.9, 10, 51.2, 10.5), box(51.2, 10, 51.5, 10.5), box(20, 20, 20.3, 20.5), box(30, 30, 30.6, 30.5), box(40, 40, 40.3, 40.5), box(40.3, 40, 40.6, 40.5)]
assert [_c.index(g) for g in poi.planted_canopies(_c, _t, 0.25)] == [0, 1, 3]
assert poi.planted_canopies(_c, _t, 0.0) == _c

FILES = {"current": L.OUT / "poi_carry.geojson", "organizer style (0.25 m2)": L.OUT / "poi_carry_orgstyle.geojson"}
START = None


def targets(path: Path) -> list[dict]:
    return [f for f in json.loads(path.read_text())["features"] if f["properties"].get("challenge")]


def mst_m(xy: np.ndarray) -> float:
    return float(minimum_spanning_tree(cdist(xy, xy)).sum()) if len(xy) > 1 else 0.0


sets = {name: targets(path) for name, path in FILES.items()}
xy = {name: np.array([t["geometry"]["coordinates"] for t in ts]) for name, ts in sets.items()}
fields = sorted({t["properties"]["vineyard_id"] for ts in sets.values() for t in ts},
                key=lambda f: -sum(t["properties"]["vineyard_id"] == f for t in sets["organizer style (0.25 m2)"]))
base = xy["current"]
print("| | " + " | ".join(FILES) + " |\n|---|---|---|")
rows = {"challenge targets (gap / planting)": [], "organizer example gaps within 2 m": [], "precision (real + partly) / labelled": [],
        "user labels flagged: real / partly / vines present / not a row": [], "target stretch length": [],
        "fields with targets": [], "spread: MST over targets": [], "targets over 20 m from every current target": [],
        "targets within 2 m of another target": []}
for name, ts in sets.items():
    g = L.gap_metrics(ts)
    f = g["labels_flagged"]
    d = cdist(xy[name], base).min(axis=1) if len(base) else np.full(len(ts), np.inf)
    self_d = cdist(xy[name], xy[name]) + np.eye(len(ts)) * 1e9
    rows["challenge targets (gap / planting)"].append(f"{len(ts)} ({sum(t['properties']['reason'] == 'gap' for t in ts)} / {sum(t['properties']['reason'] == 'planting' for t in ts)})")
    rows["organizer example gaps within 2 m"].append(L.example_recall(ts).split(" ")[0])
    rows["precision (real + partly) / labelled"].append(f"{g['precision']:.2f}")
    rows["user labels flagged: real / partly / vines present / not a row"].append(f"{f['real_gap']}, {f['partly']}, {f['vines_present']}, {f['not_a_row']}")
    rows["target stretch length"].append(f"{g['target_m']} m")
    rows["fields with targets"].append(str(len({t['properties']['vineyard_id'] for t in ts})))
    rows["spread: MST over targets"].append(f"{mst_m(xy[name]) / 1000:.2f} km")
    rows["targets over 20 m from every current target"].append(str(int((d > 20).sum())))
    rows["targets within 2 m of another target"].append(str(int((self_d.min(axis=1) <= 2).sum())))
for key, values in rows.items():
    print(f"| {key} | " + " | ".join(values) + " |")
print("\n| field | " + " | ".join(FILES) + " |\n|---|---|---|")
counts = {name: Counter(t["properties"]["vineyard_id"] for t in ts) for name, ts in sets.items()}
for field in fields:
    print(f"| {field} | " + " | ".join(str(counts[name][field]) for name in FILES) + " |")
