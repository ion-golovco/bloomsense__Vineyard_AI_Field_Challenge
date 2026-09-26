"""Plots shapes: plots_v5_eval with its outputs under data/generated/work/plots_shapes. Evaluation only (reads data/review/).
Run from backend/: uv run --frozen python ../research/probes/plots_shapes_eval.py [name=value ...] [out=name.geojson]"""

import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import plots_v5_eval  # noqa: E402
from marcaj.tiles import REPO_ROOT  # noqa: E402

plots_v5_eval.WORK = REPO_ROOT / "data" / "generated" / "work" / "plots_shapes"
plots_v5_eval.main()
