"""v6: how far rows may be off before the canopy suffers: judge canopy on the example tiles with the reference rows moved
sideways (all by d, or each row by a random +-d, seed 0) and turned (each row by a random +-a degrees about its middle).
Run from backend/: uv run --frozen --group sam python ../research/probes/v6_canopy_jitter.py"""

import sys
from pathlib import Path

import numpy as np
from shapely import affinity
from shapely.geometry import LineString

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj.canopy import CanopyParams, RowSet  # noqa: E402

plots = L.plot_features()
sets, spacing = L.rowsets(plots), L.spacing_of(plots)
P = CanopyParams()


def moved(fn) -> list[RowSet]:
    return [RowSet(s.vineyard_id, s.angle_deg, [fn(a) for a in s.axes]) for s in sets]


def shift(a: LineString, d: float) -> LineString:
    (x0, y0), (x1, y1) = a.coords[0], a.coords[-1]
    n = np.array([y0 - y1, x1 - x0]) / np.hypot(x1 - x0, y1 - y0)
    return affinity.translate(a, *(n * d))


def score(rs) -> float:
    return L.judge_canopy([f for n in L.NAMES for f in L.tile_canopies(n, P, rs, spacing)])["canopy"]


rng = np.random.default_rng(0)
print("reference rows", score(sets), flush=True)
for d in (0.05, 0.1, 0.2, 0.3, 0.5):
    print(f"all shifted {d} m: {score(moved(lambda a: shift(a, d)))}; each random +-{d} m: "
          f"{score(moved(lambda a: shift(a, rng.uniform(-d, d))))}", flush=True)
for deg in (0.5, 1.0, 2.0):
    print(f"each turned +-{deg} deg: {score(moved(lambda a: affinity.rotate(a, rng.uniform(-deg, deg), origin='centroid')))}", flush=True)
