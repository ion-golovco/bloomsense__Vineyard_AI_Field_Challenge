"""v6: gap metrics (current poi.py) for saved oracle canopies OUT/canopies_TAG.json on the reference pieces.
Run from backend/: uv run --frozen python ../research/probes/v6_regap.py TAG..."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import poi  # noqa: E402

pieces = L.ref_pieces()
for tag in sys.argv[1:]:
    canopies = json.loads((L.OUT / f"canopies_{tag}.json").read_text())
    rows = L.sampled(pieces, canopies)
    pois = poi.gap_pois(rows, obstacles=L.obstacles())
    g = L.gap_metrics(pois)
    print(f"{tag:12s} targets {g['targets']} prec {g['precision']} flagged {g['labels_flagged']} by {g['by_reason']} | example {L.example_recall(pois)}", flush=True)
