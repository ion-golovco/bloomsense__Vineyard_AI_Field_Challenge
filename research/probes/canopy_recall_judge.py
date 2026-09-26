"""marcaj.judge on data/generated/scene.json (read only) with its predicted canopies replaced by a variant's
(canopies_<tag>.json, or `uploaded` to keep them): the same scene the team's 44.88/50 baseline comes from.
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_recall_judge.py TAG..."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from canopy_recall_lib import WORK  # noqa: E402

from marcaj.judge import judge  # noqa: E402
from marcaj.scene import load_projected_scene  # noqa: E402

scene = load_projected_scene()
for tag in sys.argv[1:]:
    features = scene["features"] if tag == "uploaded" else [f for f in scene["features"] if not (f["properties"].get("source") == "prediction" and f["properties"].get("label") == "vineyard")] + json.loads((WORK / f"canopies_{tag}.json").read_text())
    r = judge({**scene, "features": features})
    s = r["scores"]
    tiles = {t["tile"][7:16]: t["counts"]["vineyard"] for t in r["tiles"]}
    print(f"{tag:16s} points {r['points']}/{r['points_available']} | canopy {s['canopy']:.4f} (IoU {r['canopy_iou']:.4f}, F1 {r['canopy_f1']:.4f}) axes {s['axes']:.3f} attributes {s['attributes']:.3f} "
          f"grouping {s['grouping']:.3f} counts {s['counts']:.3f} waste {s['waste']} | canopies {tiles} | measures {r['measures']['scores']}", flush=True)
