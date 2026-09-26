"""Row cover (poi style, the prediction's own rows) on the four central tiles and V08-04's tiles, and the user's
not-a-row labels, for whole prediction files. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v3_evalpred.py P.geojson..."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import canopy_v3_quick as q  # noqa: E402
from canopy_v3_quick2 import V0804  # noqa: E402  (also sets q.TILES)
from canopy_v3_trials import not_a_row  # noqa: E402

from marcaj import poi  # noqa: E402

for path in sys.argv[1:]:
    features = json.loads(Path(path).read_text())["features"]
    canopies = [f for f in features if f["properties"]["label"] == "vineyard"]
    rows = poi.sample_rows([f for f in features if f["properties"]["label"] == "row"], [q.BY_NAME[n] for n in q.TILES])
    q.resample(rows, canopies, q.TILES)
    c = q.cover(rows, q.TILES)
    v = [c[n[7:16]] for n in V0804]
    nar = not_a_row(canopies)
    print(f"{path}: canopies {len(canopies)} | central {[c[n[7:16]] for n in q.FIELD_TILES]} | V08-04 mean {sum(v) / len(v):.3f} {v} | "
          f"not-a-row canopy {nar['area_total']} m2, labels with cover<0.2 {nar['cover_under_0.2']}/{nar['n']}", flush=True)
