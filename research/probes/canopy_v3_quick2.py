"""canopy_v3_quick on the organizer tiles, the four central tiles and V08-04's tiles, plus V08-04's row cover and the
user's not-a-row labels on those tiles (canopy area within 0.3 m, labels with cover under 0.2). Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v3_quick2.py NAME..."""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import canopy_v3_quick as q  # noqa: E402
from canopy_v3_trials import not_a_row  # noqa: E402
from canopy_v3_variants import VARIANTS  # noqa: E402

from marcaj.canopy import CanopyParams  # noqa: E402

V0804 = [f"siret3_r{r:03d}_c{c:03d}.tif" for r, c in [(7, 2), (7, 3), (7, 4), (8, 2), (8, 3), (8, 4), (8, 5), (9, 2), (9, 3), (9, 4), (9, 5), (10, 3)]]
q.TILES = q.FIELD_TILES + q.crl.NAMES + V0804
P = CanopyParams()
VARIANTS.update({"now": P, "rv11": replace(P, row_value=1.1), "rv10": replace(P, row_value=1.0), "rv0": replace(P, row_value=0.0)})

for name in (sys.argv[1:] if __name__ == "__main__" else []):
    started = time.perf_counter()
    found = q.run(VARIANTS[name], q.TILES)
    (q.WORK / f"canopies_q2_{name}.json").write_text(json.dumps(found))
    m = q.metrics(name, found)
    v0804 = [m["cover"][n[7:16]] for n in V0804]
    nar = not_a_row(found)
    text = (f"{name:8s} judge canopy {m['canopy']:.4f} (IoU {m['iou']:.4f} F1 {m['f1']:.4f}) | central cover "
            f"{ {k: m['cover'][k] for k in ('r019_c011', 'r021_c013', 'r022_c013', 'r020_c012')} } | V08-04 tiles mean {sum(v0804) / len(v0804):.3f} {v0804} "
            f"area {sum(m['area'][n[7:16]] for n in V0804)} m2 | not-a-row (labels on these tiles) canopy {nar['area_total']} m2 "
            f"cover<0.2 {nar['cover_under_0.2']}/{nar['n']} | {time.perf_counter() - started:.0f} s")
    print(text, flush=True)
    with (q.WORK / "quick2.txt").open("a") as handle:
        handle.write(text + "\n")
