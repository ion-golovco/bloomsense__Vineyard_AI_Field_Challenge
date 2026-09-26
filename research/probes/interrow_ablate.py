"""The `marcaj.rows` changes one at a time on the two organizer reference tiles, with the same plots and canopies for
every variant (canopy.py keeps changing under other work), scored with `marcaj.judge`. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/interrow_ablate.py plots.json"""

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, mapping

import marcaj.rows as rows
from marcaj.tiles import REPO_ROOT

sys.path.insert(0, str(Path(__file__).parent))
from interrow_probe import canopies, plots, summary, tiles, NAMES  # noqa: E402
from marcaj.plots import exclusions  # noqa: E402

head_source = subprocess.run(["git", "show", "HEAD:backend/src/marcaj/rows.py"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
    handle.write(head_source)
spec = importlib.util.spec_from_file_location("rows_head", handle.name)
head = importlib.util.module_from_spec(spec)
spec.loader.exec_module(head)
reference_tiles = [tiles[name] for name in NAMES]


def run(module, tag: str, interrows=None) -> None:
    found = plots + (interrows or module.interrow_areas)(plots, exclusions())
    summary(tag, module.per_tile(found + canopies, reference_tiles))


def trapezoid(features, excluded):
    """Ends from one row's end to the other's (the committed shape) instead of square at the shorter row."""
    areas = []
    for plot_id, pairs in rows.kept_rows(features).items():
        along = rows._direction(pairs[0][1])
        across = np.array([-along[1], along[0]])
        frame = lambda u, w: along * u + across * w
        for (_, a), (_, b) in zip(pairs[:-1], pairs[1:]):
            (va, ea), (vb, eb) = sorted((float(np.asarray(line.centroid.coords[0]) @ across), sorted(np.asarray(line.coords) @ along)) for line in (a, b))
            v0, v1 = va + rows.INTERROW_INSET_M, vb - rows.INTERROW_INSET_M
            shape_ = lambda s: Polygon([frame(ea[0] - s[0], v0), frame(ea[1] + s[1], v0), frame(eb[1] + s[1], v1), frame(eb[0] - s[0], v1)])
            reach = [rows.EXTEND_M * shape_((rows.EXTEND_M, 0)).difference(shape_((0, 0))).intersects(excluded),
                     rows.EXTEND_M * shape_((0, rows.EXTEND_M)).difference(shape_((0, 0))).intersects(excluded)]
            polygon = shape_(reach).difference(excluded)
            parts = [p for p in getattr(polygon, "geoms", [polygon]) if p.geom_type == "Polygon"]
            if parts:
                areas.append({"type": "Feature", "geometry": mapping(max(parts, key=lambda p: p.area)),
                              "properties": {"label": "interrow_area", "vineyard_id": plot_id, "interrow_cover": "bare_soil"}})
    return areas


run(head, "A committed rows.py (inset 0.35)")
head.INTERROW_INSET_M = 0.30
run(head, "B A + inset 0.30")
stray, extend = rows.STRAY_SHARE, rows.EXTEND_M
rows.STRAY_SHARE, rows.EXTEND_M = 0.0, 0.0
run(rows, "C B + row_structure, square ends, visible area, 0.1 m2")
rows.STRAY_SHARE = stray
run(rows, "D C + stray rows dropped")
rows.EXTEND_M = extend
run(rows, "E D + ends carried onto exclusions (current)")
run(rows, "F E with trapezoid ends", trapezoid)
