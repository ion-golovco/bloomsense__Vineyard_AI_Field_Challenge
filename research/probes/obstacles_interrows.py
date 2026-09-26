"""`rows.cut_obstacles` self-check, then the cut applied after the fact to the v4 per-tile inter-rows (the predict hook
cuts before tiling; the geometry is the same, `interrow_cover` is kept rather than re-read). Writes
work/obstacles/predictions_obstacles.geojson (v4 + cut inter-rows + `obstacle` features) and an inter-row overview crop.
Run from backend/: uv run --frozen python ../research/probes/obstacles_interrows.py"""

import json
from collections import Counter

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds
from shapely.geometry import Point, Polygon, box, mapping, shape
from shapely.ops import unary_union

from marcaj.rows import OBSTACLE_PIECE_M2, cut_obstacles
from marcaj.tiles import REPO_ROOT

# self-check: a hole inside an inter-row splits into two holeless rings of the exact area; a crown across it cuts it in two
strip = box(0, 0, 10, 2)
pieces = cut_obstacles(strip, Point(5, 1).buffer(0.5), np.array([0.0, 1.0]))
assert len(pieces) == 2 and not any(p.interiors for p in pieces), pieces
assert abs(sum(p.area for p in pieces) - strip.difference(Point(5, 1).buffer(0.5)).area) < 1e-9
assert len(cut_obstacles(strip, box(4, -1, 6, 3), np.array([0.0, 1.0]))) == 2
assert cut_obstacles(strip, box(20, 0, 21, 1), np.array([0.0, 1.0]))[0].equals(strip)
assert cut_obstacles(strip, box(0.4, -1, 10, 3), np.array([0.0, 1.0])) == []  # a 0.8 m2 crescent is dropped

WORK = REPO_ROOT / "data" / "generated" / "work" / "obstacles"
features = json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]
obstacles = json.loads((WORK / "obstacles.geojson").read_text())["features"]
blockers = unary_union([shape(f["geometry"]) for f in obstacles])
out, touched, removed, before_m2 = [], Counter(), 0.0, 0.0
holes = 0
for feature in features:
    p = feature["properties"]
    if p["label"] != "interrow_area":
        out.append(feature)
        continue
    polygon = shape(feature["geometry"])
    before_m2 += polygon.area
    if not polygon.intersects(blockers):
        out.append(feature)
        continue
    corners = np.asarray(polygon.minimum_rotated_rectangle.exterior.coords)
    sides = np.diff(corners[:3], axis=0)
    along = max(sides, key=np.linalg.norm)
    along = along / np.linalg.norm(along)
    pieces = cut_obstacles(polygon, blockers, np.array([-along[1], along[0]]))
    holes += sum(len(piece.interiors) for piece in pieces)
    touched[p["vineyard_id"]] += 1
    removed += polygon.area - sum(piece.area for piece in pieces)
    out += [{"type": "Feature", "geometry": mapping(piece), "properties": dict(p)} for piece in pieces]
out += obstacles
print(f"inter-row pieces touched {sum(touched.values())} in {len(touched)} blocks {dict(touched)}")
print(f"inter-row area removed {removed:.0f} m2 of {before_m2:.0f} m2 ({100 * removed / before_m2:.2f}%), holes left {holes}")
print(Counter(f["properties"]["label"] for f in out))
path = WORK / "predictions_obstacles.geojson"
path.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": out}), encoding="utf-8")
print("->", path)

# the V21-13 hut and a V08-04 tree, inter-rows outlined
with rasterio.open(REPO_ROOT / "data" / "generated" / "mosaic_20cm.tif") as source:
    crops = []
    for cx, cy in ((629672, 5220185), (629150, 5220782)):
        L, T, M, C = cx - 20, cy + 20, 40.0, 400
        rgb = source.read(window=from_bounds(L, T - M, L + M, T, source.transform), out_shape=(3, C, C))
        im = Image.fromarray(rgb.transpose(1, 2, 0))
        draw = ImageDraw.Draw(im)
        to = lambda x, y: ((x - L) * C / M, (T - y) * C / M)
        frame = box(L, T - M, L + M, T)
        for f in out:
            if f["properties"]["label"] in ("interrow_area", "obstacle"):
                g = shape(f["geometry"])
                if g.intersects(frame):
                    colour = (255, 255, 0) if f["properties"]["label"] == "interrow_area" else (255, 0, 255)
                    draw.line([to(*c) for c in g.exterior.coords], fill=colour, width=1)
        crops.append(im)
    sheet = Image.new("RGB", (800, 400))
    for k, im in enumerate(crops):
        sheet.paste(im, (400 * k, 0))
    sheet.save(WORK / "interrows_cut.jpg", quality=88)
