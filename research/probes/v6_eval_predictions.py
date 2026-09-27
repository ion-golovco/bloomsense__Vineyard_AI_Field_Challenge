"""v6: a whole prediction file (predict.py output) scored like the oracle runs: judge canopy on the organizer tiles, row
cover along the reference rows, off-row canopy, the user's canopy labels, and gap POIs from the prediction's own rows +
canopies (as `marcaj.poi` runs on it) against the gap labels and the organizers' example gaps. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/v6_eval_predictions.py PREDICTIONS.geojson [TAG]"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import poi  # noqa: E402

path = Path(sys.argv[1])
tag = sys.argv[2] if len(sys.argv) > 2 else path.stem
features = json.loads(path.read_text())["features"]
canopies = [f for f in features if f["properties"]["label"] == "vineyard"]
pieces = L.ref_pieces()
block = {f["properties"]["row_id"]: f["properties"].get("vineyard_id", "") for f in pieces}
cov = L.cover(L.sampled(pieces, canopies), block)
obstacles = [f for f in features if f["properties"]["label"] == "obstacle"] or L.obstacles()
own = poi.sample_rows([f for f in features if f["properties"]["label"] in ("row", "vineyard")])
pois = poi.gap_pois(own, obstacles=obstacles)
m = {"name": tag, "judge": L.judge_canopy(canopies), "n": len(canopies),
     "cover": {k: v for k, v in cov.items() if k in ("all", "V19-11", "V21-13", "V22-13", "V08-04")},
     "off_row_m2": L.off_row(canopies, pieces), "labels": L.canopy_labels(canopies), "gaps": L.gap_metrics(pois),
     "example_recall": L.example_recall(pois)}
print(json.dumps(m))
with (L.OUT / "predictions_eval.jsonl").open("a") as handle:
    handle.write(json.dumps(m) + "\n")
