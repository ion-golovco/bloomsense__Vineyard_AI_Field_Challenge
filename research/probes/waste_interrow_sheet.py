"""Sheets of inter-row candidates: the detector's kept ones (default) or any filter, captioned with the features.
Run from backend/: uv run --frozen python ../research/probes/waste_interrow_sheet.py OUT_PREFIX [kept|all] [N]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from waste_sheet import sheets  # noqa: E402

from marcaj.tiles import REPO_ROOT  # noqa: E402
from marcaj.waste import accept  # noqa: E402

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
prefix, mode = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "kept"
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10 ** 6
items = [c for c in json.loads((W / "candidates.json").read_text()) if mode == "all" or accept(c)]
items.sort(key=lambda c: (c["kind"], -c["area_m2"]))
for c in items:
    c["where"] = f"{c['vineyard_id']} d{c['dev']:.0f} l{c['lum']:.0f} c{c['chroma']:.0f} h{c['hue']:.0f} w{c['width_m']:.2f} L{c['length_m']:.2f}"
(W / f"{prefix}.json").write_text(json.dumps(items[:limit]))
print(len(items), sheets(items[:limit], W / prefix)[-1])
