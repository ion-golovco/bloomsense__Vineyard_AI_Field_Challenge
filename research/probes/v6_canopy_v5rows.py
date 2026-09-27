"""v6: the canopy stage on the v5 prediction's own rows (per-tile pieces as chords, per pattern) on the two example tiles,
for a variant check that does not lean on the reference rows. Run from backend/: ... v6_canopy_v5rows.py NAME..."""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402
from v6_canopy_oracle import VARIANTS  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.canopy import RowSet  # noqa: E402
from shapely.geometry import LineString, shape  # noqa: E402

features = json.loads((L.GEN / "work" / "v5" / "predictions.geojson").read_text())["features"]
axes, angle, spacing = defaultdict(list), {}, {}
for f in features:
    p = f["properties"]
    if p["label"] == "block":
        for q in p.get("patterns") or [p]:
            pid = q.get("pattern_id", p["vineyard_id"])
            angle[pid], spacing[pid] = q["row_angle"], q["row_spacing_m"]
for f in features:
    p = f["properties"]
    if p["label"] == "row":
        g = shape(f["geometry"])
        axes[p.get("pattern_id") or p["vineyard_id"]].append(LineString([g.coords[0], g.coords[-1]]))
sets = [RowSet(pid, angle.get(pid, 0.0), a) for pid, a in axes.items()]
for name in sys.argv[1:]:
    params = VARIANTS[name][0]
    found = [f for n in L.NAMES for f in L.tile_canopies(n, params, sets, spacing)]
    print(f"v5 rows {name:14s}", L.judge_canopy(found), flush=True)
