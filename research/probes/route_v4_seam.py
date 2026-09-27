"""Control for route.robust_space's tile-edge fix: a lane cut at a tile edge with a 1e-7 m seam erodes to one polygon
of the uncut lane's exact area; lanes 0.6 m apart across a row stay two; an uncut quad erodes exactly as a plain
0.3 m erosion. Without the fix the cut lane erodes to two pieces with a 0.6 m outside strip between them.
Run from backend/: uv run --frozen python ../research/probes/route_v4_seam.py"""
from shapely.geometry import Polygon, box, mapping

from marcaj import route


def feature(label: str, geometry) -> dict:
    return {"type": "Feature", "geometry": mapping(geometry), "properties": {"label": label, "source": "reference"}}


far = feature("passage", box(100, 0, 110, 5))
cut = route.robust_space([feature("interrow_area", box(40, 0, 51.2 - 1e-7, 2)), feature("interrow_area", box(51.2, 0, 60, 2)), far])
lane = cut.intersection(box(0, -1, 90, 3))
assert lane.geom_type == "Polygon" and abs(lane.area - 19.4 * 1.4) < 1e-6, (lane.geom_type, lane.area)
two = route.robust_space([feature("interrow_area", box(0, 0, 10, 2)), feature("interrow_area", box(0, 2.6, 10, 4.6)), far]).intersection(box(-1, -1, 20, 6))
assert two.geom_type == "MultiPolygon" and len(two.geoms) == 2 and abs(two.area - 2 * 9.4 * 1.4) < 1e-6, two.area
quad = Polygon([(0, 0), (20, 0.5), (20, 3), (0, 2.4)])
assert route.robust_space([feature("interrow_area", quad), far]).intersection(box(-1, -1, 30, 6)).symmetric_difference(quad.buffer(-0.3)).area < 1e-9
unfixed = box(40, 0, 51.2 - 1e-7, 2).union(box(51.2, 0, 60, 2)).buffer(-0.3)
assert unfixed.geom_type == "MultiPolygon"  # the control that must fail without the fix
print("robust_space seam checks: PASS")
