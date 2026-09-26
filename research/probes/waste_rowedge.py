"""Recall pass for litter at the canopy edge, which the inter-row generator misses (inter-rows start 0.30 m from the
row axis): compact bright blobs inside the predicted blocks within 0.6 m of a row axis, on the northern tiles.
Bright = darkest channel >= 185, luminance >= 205 and >= 45 over the 2 m median, chroma <= 40; blobs 0.015-0.6 m2, length <= 2.5x width, narrow
spread >= 0.045 m (standing tube tops are smaller, lying tubes are lines). Writes sweep3_north.json and
sweep3_north_*.jpg (sweep format, numbered). Run from backend/."""

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import Point, box, shape

sys.path.insert(0, str(Path(__file__).parent))
from waste_sweep import render  # noqa: E402

from marcaj.tiles import PIXEL_M, REPO_ROOT, TILE_PX, load_tiles  # noqa: E402
from marcaj.waste import EIGHT, _coarse, _fine  # noqa: E402

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
features = json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]
blocks = [(shape(f["geometry"]), f["properties"]["vineyard_id"]) for f in features if f["properties"]["label"] == "block"]
rows = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "row"]
items = []
for tile in load_tiles():
    if int(tile.name[8:11]) > 23:
        continue
    hits = [(p, name) for p, name in blocks if p.intersects(tile.bounds)]
    near = [r for r in rows if r.intersects(tile.bounds.buffer(1))]
    if not hits or not near:
        continue
    with rasterio.open(tile.path) as source:
        rgb, transform = source.read().astype(np.float32), source.transform
    zone = rasterize([p for p, _ in hits], out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool)
    zone &= rasterize([r.buffer(0.6) for r in near], out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool)
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    mx, mn = rgb.max(0), rgb.min(0)
    contrast = lum - _fine(ndimage.median_filter(_coarse(lum), size=11))
    mask = ndimage.binary_opening(zone & (mn >= 185) & (lum >= 205) & (mx - mn <= 40) & (contrast >= 45), np.ones((2, 2), bool))
    labels, _ = ndimage.label(ndimage.binary_dilation(mask, EIGHT), EIGHT)
    labels[~mask] = 0
    for index, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None:
            continue
        blob = labels[window] == index
        area = blob.sum() * PIXEL_M ** 2
        if not 0.015 <= area <= 0.6:
            continue
        ys, xs = np.nonzero(blob)
        spread = np.sqrt(np.maximum(np.linalg.eigvalsh(np.cov(np.vstack([xs, ys]).astype(float))), 0)) * PIXEL_M
        if spread[0] < 0.045 or spread[1] > 2.5 * spread[0]:
            continue
        px = [int(window[1].start + xs.min()), int(window[0].start + ys.min()), int(window[1].start + xs.max() + 1), int(window[0].start + ys.max() + 1)]
        centre = tile.to_world(Point((px[0] + px[2]) / 2, (px[1] + px[3]) / 2))
        items.append({"tile": tile.name, "px": px, "kinds": ["edge"], "area_m2": round(float(area), 3),
                      "vineyard_id": next((name for p, name in hits if p.contains(centre)), ""),
                      "lum": round(float(lum[window][blob].mean())), "row_m": round(min(r.distance(centre) for r in near), 2)})
items.sort(key=lambda i: (i["tile"], i["px"][1], i["px"][0]))
for n, item in enumerate(items, start=1):
    item["n"] = n
(W / "sweep3_north.json").write_text(json.dumps(items, indent=0))
render(items, "sweep3_north")
print(len(items), "items")
