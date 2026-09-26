"""Re-scores saved variant canopies (canopies_<tag>.json) with canopy_recall_lib.metrics. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_recall_rescore.py TAG..."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from canopy_recall_lib import WORK, line, metrics  # noqa: E402

for tag in sys.argv[1:]:
    print(line(metrics(tag, json.loads((WORK / f"canopies_{tag}.json").read_text()))), flush=True)
