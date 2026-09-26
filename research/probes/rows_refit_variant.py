"""A full-site prediction for a row variant, reusing the canopies and waste of a predict.py run (RR_PREDICTIONS):
rows (rows_refit_measure.plots_input: RR_PLOTS, else frozen) -> variant (none = the lattice control, or canopy.refit_rows kwargs as JSON) ->
rows.interrow_areas -> rows.per_tile over all tiles -> plots.assign_blocks, as predict.py does. Evaluation only.
Run from backend/: RR_PREDICTIONS=... PYTHONPATH=../research/probes uv run --frozen python ../research/probes/rows_refit_variant.py OUT.geojson [JSON]"""

import json
import sys
from pathlib import Path

from marcaj import canopy, plots, rows
from marcaj.predict import write
from marcaj.tiles import load_tiles

from rows_refit_measure import PREDICTIONS, plots_input

if __name__ == "__main__":
    out, kwargs = Path(sys.argv[1]), json.loads(sys.argv[2]) if len(sys.argv) > 2 else None
    predicted = json.loads(PREDICTIONS.read_text())["features"]
    canopies = [f for f in predicted if f["properties"]["label"] == "vineyard"]
    frozen = plots_input()
    found = frozen if kwargs is None else canopy.refit_rows(frozen, canopies, **kwargs)[0]
    found = found + rows.interrow_areas(found, plots.exclusions()) + [f for f in predicted if f["properties"]["label"] in ("vineyard", "waste")]
    print(write(plots.assign_blocks(rows.per_tile(found, load_tiles())), out))
