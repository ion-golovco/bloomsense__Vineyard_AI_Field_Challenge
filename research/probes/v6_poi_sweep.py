"""v6: gap-rule parameters on the reference rows + a canopy run (OUT/canopies_TAG.json): targets, the user's gap labels
(precision, labels flagged) and the organizers' example gaps. Rows are sampled once and cached (OUT/rows_TAG.pkl).
Run from backend/: uv run --frozen python ../research/probes/v6_poi_sweep.py TAG"""

import json
import pickle
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import poi  # noqa: E402

# self-check of poi._bridges: a 6 m hole is bridged, touching pieces and a 20 m hole are not
import numpy as np  # noqa: E402
from shapely.geometry import LineString  # noqa: E402
_lines = [LineString([(16, 0), (30, 0)]), LineString([(0, 0), (10, 0)]), LineString([(30, 0), (40, 0)]), LineString([(60, 0), (70, 0)])]
assert [list(b.coords) for b in poi._bridges(_lines, np.zeros(2), np.array([1.0, 0.0]))] == [[(10.0, 0.0), (16.0, 0.0)]]

tag = sys.argv[1] if len(sys.argv) > 1 else "defaults"
cache = L.OUT / f"rows_{tag}.pkl"
if cache.is_file():
    rows = pickle.loads(cache.read_bytes())
else:
    rows = L.sampled(L.ref_pieces(), json.loads((L.OUT / f"canopies_{tag}.json").read_text()))
    cache.write_bytes(pickle.dumps(rows))
obstacles = L.obstacles()


def show(name: str, **kw) -> None:
    patches = {k: v for k, v in kw.items() if k.isupper()}
    args = {k: v for k, v in kw.items() if not k.isupper()}
    with mock.patch.multiple(poi, **patches) if patches else mock.patch.object(poi, "GAP_M", poi.GAP_M):
        pois = poi.gap_pois(rows, obstacles=obstacles, **args)
    g = L.gap_metrics(pois)
    short = sum(p["properties"]["challenge"] and p["properties"]["gap_m"] < 5 and p["properties"]["reason"] == "gap" for p in pois)
    print(f"{name:28s} targets {g['targets']:3d} (<5 m {short:3d}) prec {g['precision']:.3f} on labels {g['on_label']} flagged {g['labels_flagged']} "
          f"| example {L.example_recall(pois)}", flush=True)


show("fixed 5 m", rule="fixed")
show("deviation k3 f3 (current)")
for k in (2.0, 2.5, 4.0):
    show(f"deviation k{k} f3", k=k)
for floor in (2.5, 3.5, 4.0):
    show(f"deviation k3 f{floor}", floor=floor)
for pitch in (1.8, 2.2, 2.5):
    show(f"pitch cap {pitch}", EXPECTED_PITCH_M=pitch)
for sg in (0.05, 0.2, 0.3):
    show(f"SHORT_GREEN {sg}", SHORT_GREEN=sg)
for mg in (0.5, 0.6):
    show(f"MAX_GREEN {mg}", MAX_GREEN=mg)
for mh in (0.1, 0.3):
    show(f"MAX_HIDDEN {mh}", MAX_HIDDEN=mh)
