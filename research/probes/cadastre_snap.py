"""Snaps the kept detector's plot edges (data/generated/work/plots/final.geojson) onto parallel cadastral parcel edges
and scores the result with `judge.plot_scores`, as drawn and clipped. Evaluation only (reads data/review/).
Run from backend/: uv run --frozen python ../research/probes/cadastre_snap.py [reach=6] [inward=0] [cover=0.6] [gate=0]"""

import json
import sys

import numpy as np
from shapely.geometry import Polygon, mapping, shape
from shapely.geometry.polygon import orient

sys.path.insert(0, __import__("os").path.dirname(__file__))
from plot_experiment import _fmt, scores, tile_footprint  # noqa: E402

from marcaj import plots  # noqa: E402
from marcaj.cadastre import edge_distance  # noqa: E402
from marcaj.review import load_verdicts  # noqa: E402
from marcaj.tiles import REPO_ROOT  # noqa: E402

args = {k: float(v) for k, v in (a.split("=") for a in sys.argv[1:])}
reach, inward, cover, gate = args.get("reach", 6.0), args.get("inward", 0.0), args.get("cover", 0.6), args.get("gate", 0.0)
distance_at = edge_distance()
excess = plots.load_excess()
roads = plots.exclusions()
features = json.loads((REPO_ROOT / "data/generated/work/plots/final.geojson").read_text())["features"]


def snap(polygon: Polygon, angle: float, spacing: float) -> Polygon:
    ring = np.asarray(orient(polygon.simplify(0.3)).exterior.coords)[:-1]
    lines, moved = [], 0
    for a, b in zip(ring, np.roll(ring, -1, axis=0)):
        length = np.hypot(*(b - a))
        normal = np.array([b[1] - a[1], a[0] - b[0]]) / max(length, 1e-9)
        shift = 0.0
        if length >= 8:
            base = a + np.linspace(0.1, 0.9, max(8, int(length / 0.5)))[:, None] * (b - a)
            steps = np.arange(-inward, reach + 1e-9, 0.25)
            coverage = np.array([(distance_at(base + s * normal) <= 0.6).mean() for s in steps])
            k = int(np.argmax(coverage - 0.01 * np.abs(steps)))
            if coverage[k] >= cover and abs(steps[k]) >= 0.5:
                shift = float(steps[k])
                if shift > 0 and gate:
                    strip = Polygon([a, b, b + normal * shift, a + normal * shift])
                    if plots._wave(excess, strip, angle, spacing) < gate * plots._wave(excess, polygon, angle, spacing):
                        shift = 0.0
        moved += shift != 0
        lines.append((a + normal * shift, b - a))
    corners = []
    for (p1, d1), (p2, d2) in zip(lines[-1:] + lines[:-1], lines):
        det = d1[0] * -d2[1] + d2[0] * d1[1]
        if abs(det) < 1e-9:
            return polygon
        t = ((p2 - p1)[0] * -d2[1] + d2[0] * (p2 - p1)[1]) / det
        corners.append(p1 + t * d1)
    snapped = Polygon(corners)
    if not moved or not snapped.is_valid or abs(snapped.area - polygon.area) > 0.6 * polygon.area:
        return polygon
    return plots._largest(snapped.difference(roads.buffer(1.0)))


out, taken = [], Polygon()
blocks = sorted((f for f in features if f["properties"]["label"] == "block"), key=lambda f: -f["properties"]["area_m2"])
for f in blocks:
    p = f["properties"]
    polygon = plots._largest(snap(shape(f["geometry"]), p["row_angle"], p["row_spacing_m"]).difference(taken))
    taken = taken.union(polygon)
    out.append({**f, "geometry": mapping(polygon)})
print(f"reach {reach} inward {inward} cover {cover} gate {gate}")
for kind, value in scores(out, load_verdicts(), tile_footprint()).items():
    print(f"  {kind:8s} {_fmt(value)}")
