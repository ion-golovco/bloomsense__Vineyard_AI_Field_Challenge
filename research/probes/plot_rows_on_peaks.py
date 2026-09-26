"""Rows a plot change added (not within 0.2 m of a row of the base run): the across-row offset to the highest ExG within
half a spacing, against a sample of the base run's rows. Run from backend/:
uv run --frozen python ../research/probes/plot_rows_on_peaks.py BASE.geojson NEW.geojson"""

import json
import sys

import numpy as np
from shapely.geometry import shape
from shapely.strtree import STRtree

from marcaj import plots

excess = plots.load_excess()


def load(path: str) -> tuple[list, dict]:
    features = json.loads(open(path).read())["features"]
    return ([(shape(f["geometry"]), f["properties"]) for f in features if f["properties"]["label"] == "row"],
            {f["properties"]["vineyard_id"]: f["properties"] for f in features if f["properties"]["label"] == "block"})


def offset(row, block) -> float:
    spacing, angle = block["row_spacing_m"], np.radians(block["row_angle"])
    normal = np.array([-np.sin(angle), np.cos(angle)])
    points = np.array([row.interpolate(d).coords[0] for d in np.arange(0, row.length, 0.2)])
    offsets = np.arange(-0.5, 0.5 + 1e-9, 0.02) * spacing
    return float(abs(offsets[int(np.argmax([excess.at(*(points + o * normal).T).mean() for o in offsets]))]))


(base, base_blocks), (new, new_blocks) = load(sys.argv[1]), load(sys.argv[2])
tree = STRtree([g for g, _ in base])
added = [(g, p) for g, p in new if not any(base[k][0].distance(g.interpolate(0.5, normalized=True)) < 0.2
                                           for k in tree.query(g.interpolate(0.5, normalized=True).buffer(0.3)))]
for name, rows, blocks in (("added", added, new_blocks), ("base sample", base[::5], base_blocks)):
    d = np.array([offset(g, blocks[p["vineyard_id"]]) for g, p in rows if g.length >= 2])
    print(f"{name}: {len(d)} rows, {sum(g.length for g, _ in rows):.0f} m, offset to ExG peak median {np.median(d):.2f} m, within 0.35 m {(d <= 0.35).mean():.2f}")
