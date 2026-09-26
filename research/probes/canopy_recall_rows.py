"""Per-row canopy cover for a variant (share of visible 5 cm samples within 0.3 m of the uploaded row axis that hit a
canopy) against pixel vine green (ExG runs, as marcaj.poi), per row and per plot; lists the rows with the most
uncovered green. Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_recall_rows.py TAG"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
from canopy_recall_lib import WORK, resample_canopy, sampled_rows  # noqa: E402

from marcaj import poi  # noqa: E402

tag = sys.argv[1]
rows = sampled_rows()
resample_canopy(rows, json.loads((WORK / f"canopies_{tag}.json").read_text()))
run = np.ones(round(poi.VINE_RUN_M / poi.SAMPLE_M), bool)
out, plots = [], defaultdict(lambda: np.zeros(3))
for row in rows:
    visible = row.seen & ~row.dark
    green = ndimage.binary_opening(row.green, structure=run) & visible
    n = visible.sum()
    if not n:
        continue
    cover, gshare, uncovered = row.canopy[visible].mean(), green.sum() / n, (green & ~row.canopy).sum() * poi.SAMPLE_M
    out.append({"row_id": row.row_id, "m": round(n * poi.SAMPLE_M, 1), "cover": round(float(cover), 3), "green": round(float(gshare), 3),
                "green_uncovered_m": round(float(uncovered), 1), "tiles": sorted(row.tiles)})
    plots[row.vineyard_id] += [n * poi.SAMPLE_M, row.canopy[visible].sum() * poi.SAMPLE_M, uncovered]
out.sort(key=lambda r: -r["green_uncovered_m"])
(WORK / f"rows_{tag}.json").write_text(json.dumps(out, indent=1))
print("rows", len(out), "green uncovered m", round(sum(r["green_uncovered_m"] for r in out)), "rows with cover < 0.2:", sum(r["cover"] < 0.2 for r in out),
      "of which green > 0.4:", sum(r["cover"] < 0.2 and r["green"] > 0.4 for r in out))
print("worst rows:", [(r["row_id"], r["m"], r["cover"], r["green"], r["green_uncovered_m"]) for r in out[:15]])
print("plots (row m, cover, green uncovered m):", sorted([(k, round(v[0]), round(v[1] / v[0], 2), round(v[2])) for k, v in plots.items()], key=lambda x: -x[3])[:15])
