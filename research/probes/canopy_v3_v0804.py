"""V08-04 (the user's "missing a lot of canopy near the prohibited border"): per-row canopy cover of the frozen v4 rows
(share of 0.1 m samples with canopy of the pattern within 0.3 m) against the distance of the row to the forbidden zone,
where the colour pixels (2g - r - b > 25) near the rows go, and crops: the block overview with the forbidden zone (red),
rows (cyan) and canopies of two saved variants (A yellow, B magenta). Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v3_v0804.py A B"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from shapely import STRtree
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "review"))
from build import crop  # noqa: E402
from canopy_v2_lib import frozen_plots  # noqa: E402
from canopy_v3_quick import WORK  # noqa: E402
from canopy_v3_rows import row_cover  # noqa: E402

from marcaj.plots import exclusions  # noqa: E402

BLOCK = "V08-04"
a, b = sys.argv[1:3]
plots = frozen_plots()
rows = [f for f in plots if f["properties"]["label"] == "row" and f["properties"]["block_id"] == BLOCK]
blocks = [shape(f["geometry"]) for f in plots if f["properties"]["label"] == "block" and f["properties"]["block_id"] == BLOCK]
print("patterns", sorted({f["properties"]["pattern_id"] for f in rows}), "rows", len(rows))
forbidden = exclusions()
for k in (a, b):
    C = json.loads((WORK / f"canopies_{k}.json").read_text())
    cover = row_cover(rows, C)
    d = {f["properties"]["row_id"]: shape(f["geometry"]).distance(forbidden) for f in rows}
    near = [cover[r] for r in cover if d[r] < 5]
    print(f"{k}: row cover median {np.median(list(cover.values())):.3f}, rows within 5 m of an exclusion {len(near)} median {np.median(near) if near else 0:.3f}; "
          f"canopy {sum(shape(f['geometry']).area for f in C if f['properties']['vineyard_id'] == BLOCK):.0f} m2")
lo = blocks[0].bounds
for g in blocks[1:]:
    lo = (min(lo[0], g.bounds[0]), min(lo[1], g.bounds[1]), max(lo[2], g.bounds[2]), max(lo[3], g.bounds[3]))
A = [shape(f["geometry"]) for f in json.loads((WORK / f"canopies_{a}.json").read_text())]
B = [shape(f["geometry"]) for f in json.loads((WORK / f"canopies_{b}.json").read_text())]
ta, tb = STRtree(A), STRtree(B)
R = [shape(f["geometry"]) for f in rows]
cells = []
cx, cy = (lo[0] + lo[2]) / 2, (lo[1] + lo[3]) / 2
span = max(lo[2] - lo[0], lo[3] - lo[1]) + 10
w = box(cx - span / 2, cy - span / 2, cx + span / 2, cy + span / 2)
cells.append(np.moveaxis(crop(cx, cy, span, 900, [(forbidden.intersection(w).boundary, (255, 0, 0))] + [(g.boundary, (255, 255, 255)) for g in blocks]), 0, -1))
Image.fromarray(cells[0]).save(WORK / "v0804_overview.jpg", quality=85)
zoom = []
for fx, fy in ((0.5, 0.5), (0.25, 0.75), (0.75, 0.25), (0.1, 0.5), (0.5, 0.1), (0.9, 0.5)):
    x, y = lo[0] + fx * (lo[2] - lo[0]), lo[1] + fy * (lo[3] - lo[1])
    win = box(x - 7, y - 7, x + 7, y + 7)
    ov = [(forbidden.intersection(win).boundary, (255, 0, 0))] + [(r.intersection(win), (0, 255, 255)) for r in R if r.intersects(win)]
    ov += [(A[i], (255, 230, 0)) for i in ta.query(win)] + [(B[i], (255, 0, 255)) for i in tb.query(win)]
    zoom.append(np.moveaxis(crop(x, y, 12.0, 400, ov), 0, -1))
Image.fromarray(np.concatenate([np.concatenate(zoom[:3], 1), np.concatenate(zoom[3:], 1)], 0)).save(WORK / f"v0804_{a}_vs_{b}.jpg", quality=85)
print(WORK / "v0804_overview.jpg", WORK / f"v0804_{a}_vs_{b}.jpg")
