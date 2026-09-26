"""Re-score saved SAM recall runs (work/sam_v2/recall_<name>.geojson, the canopies SAM added) on the 18:13 base without
running SAM: judge on the organizer tiles, mid-field row cover, canopy and gap labels. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/sam_v2_rescore.py NAME..."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sam_v2_lib import MID, MID_FIELDS, WORK, base_features, judge_canopies, label_metrics, labels, row_cover, rows_of  # noqa: E402
from sam_v2_recall import gap_metrics  # noqa: E402

if __name__ == "__main__":
    features = base_features()
    base = [f for f in features if f["properties"]["label"] == "vineyard"]
    mid_rows = [r for r in rows_of(features) if r["properties"]["tile"] in MID and r["properties"]["vineyard_id"] in MID_FIELDS]
    gap_labels = labels("gaps")
    for name in sys.argv[1:]:
        added = [] if name == "base" else json.loads((WORK / f"recall_{name}.geojson").read_text())["features"]
        canopies = base + added
        print(json.dumps({"name": name, "judge": judge_canopies(canopies), "added": len(added), "mid_row_cover": row_cover(canopies, mid_rows),
                          "canopy_labels": label_metrics(canopies), "gap_labels": gap_metrics(canopies, gap_labels)}), flush=True)
