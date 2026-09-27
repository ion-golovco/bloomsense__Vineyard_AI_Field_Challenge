"""v6 quick sweep: judge canopy on the two organizer tiles for single-parameter changes, reference rows (global parts).
Run from backend/: uv run --frozen --group sam python ../research/probes/v6_canopy_sweep.py [v5] field=v1,v2 ...
("v5" runs on the v5 prediction's rows instead)."""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj.canopy import CanopyParams  # noqa: E402

args = sys.argv[1:]
use_v5 = bool(args) and args[0] == "v5"
if use_v5:
    args = args[1:]
    sys.argv = [sys.argv[0]]
    import v6_canopy_v5rows as V  # noqa: E402
    sets, spacing = V.sets, V.spacing
else:
    plots = L.plot_features()
    sets, spacing = L.rowsets(plots), L.spacing_of(plots)
D = CanopyParams()


def score(params: CanopyParams) -> dict:
    return L.judge_canopy([f for n in L.NAMES for f in L.tile_canopies(n, params, sets, spacing)])


base = score(D)
print(f"{'v5 rows' if use_v5 else 'reference rows'} defaults: {base['canopy']} (IoU {base['iou']} F1 {base['f1']}) n {[t['n'][0] for t in base['tiles'].values()]}", flush=True)
for spec in args:
    field, values = spec.split("=")
    kind = type(getattr(D, field))
    for value in values.split(","):
        v = (value.lower() in ("1", "true")) if kind is bool else kind(value)
        s = score(replace(D, **{field: v}))
        print(f"  {field}={v}: {s['canopy']} ({s['canopy'] - base['canopy']:+.4f}; IoU {s['iou']} F1 {s['f1']}) n {[t['n'][0] for t in s['tiles'].values()]}", flush=True)
