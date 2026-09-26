"""Second recall pass on the northern inter-rows for colours the detector's generator leaves to soil and vegetation:
saturated yellow and orange (hue 25-60, chroma >= 100, max channel >= 150) and light grey film or metal (chroma <= 20,
>= 70 brighter than the 2 m background, darkest channel 150-170, i.e. just under the white threshold), blobs >= 0.01 m2.
Writes sweep2_north.json and sweep2_north_*.jpg in the sweep format. Run from backend/."""

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from waste_sheet import sheets  # noqa: E402

from marcaj.tiles import PIXEL_M, REPO_ROOT, TILE_PX, load_tiles  # noqa: E402
from marcaj.waste import BG_FACTOR, EIGHT, _coarse, _fine, _hue  # noqa: E402

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
features = json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]
polygons = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "interrow_area"]
rows = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "row"]
found = []
for tile in load_tiles():
    if int(tile.name[8:11]) > 23:
        continue
    hits = [p for p in polygons if p.intersects(tile.bounds)]
    if not hits:
        continue
    with rasterio.open(tile.path) as source:
        rgb, transform = source.read().astype(np.float32), source.transform
    inside = rasterize(hits, out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool)
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    bg = _fine(ndimage.median_filter(_coarse(lum), size=11))
    mx, mn, hue = rgb.max(0), rgb.min(0), _hue(rgb)
    yellow = inside & (hue >= 25) & (hue <= 60) & (mx - mn >= 100) & (mx >= 150)
    grey = inside & (mx - mn <= 20) & (lum - bg >= 70) & (mn >= 150) & (mn < 170)
    near = [r for r in rows if r.intersects(tile.bounds.buffer(3))]
    for kind, mask in (("yellow", yellow), ("grey", grey)):
        mask = ndimage.binary_opening(mask, np.ones((2, 2), bool))
        labels, _ = ndimage.label(ndimage.binary_dilation(mask, EIGHT, iterations=1), EIGHT)
        labels[~mask] = 0
        for index, window in enumerate(ndimage.find_objects(labels), start=1):
            if window is None:
                continue
            blob = labels[window] == index
            area = blob.sum() * PIXEL_M ** 2
            if not 0.01 <= area <= 2.0:
                continue
            ys, xs = np.nonzero(blob)
            px = [int(window[1].start + xs.min()), int(window[0].start + ys.min()), int(window[1].start + xs.max() + 1), int(window[0].start + ys.max() + 1)]
            centre = tile.to_world(shape({"type": "Point", "coordinates": ((px[0] + px[2]) / 2, (px[1] + px[3]) / 2)}))
            found.append({"tile": tile.name, "px": px, "bounds": list(tile.to_world(shape({"type": "Polygon", "coordinates": [[(px[0], px[1]), (px[2], px[1]), (px[2], px[3]), (px[0], px[3])]]})).bounds),
                          "kind": kind, "area_m2": float(area), "row_m": float(min((r.distance(centre) for r in near), default=99.0)),
                          "where": f"{kind} {area:.2f}m2"})
found.sort(key=lambda c: (c["tile"], c["px"][1]))
(W / "sweep2_north.json").write_text(json.dumps(found))
print(len(found), {k: sum(c["kind"] == k for c in found) for k in ("yellow", "grey")}, sheets(found[:84], W / "sweep2_north")[-1] if found else "")
