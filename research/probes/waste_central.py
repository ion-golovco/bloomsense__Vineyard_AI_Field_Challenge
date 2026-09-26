"""Colour-anomaly scan inside chosen predicted blocks (default the four central ones by START), off the row axes.
Any blob whose colour is far from the 2 m median background, not green and not shadow, 0.01-3 m2, >= 0.35 m from
a row axis. Writes central_anomalies.json and a contact sheet central_anom_*.jpg of the top N.
Run from backend/: uv run --frozen python ../research/probes/waste_central.py [N] [BLOCK ...]"""

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).parent))
from waste_sheet import sheets  # noqa: E402

from marcaj.tiles import PIXEL_M, REPO_ROOT, TILE_PX, load_tiles  # noqa: E402
from marcaj.waste import BG_FACTOR, EIGHT, _coarse, _fine  # noqa: E402

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
top = int(sys.argv[1]) if len(sys.argv) > 1 else 56
names = sys.argv[2:] or ["P03", "P02", "P16", "P07"]
features = json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]
blocks = {f["properties"]["vineyard_id"]: shape(f["geometry"]) for f in features if f["properties"]["label"] == "block" and f["properties"]["vineyard_id"] in names}
rows = [shape(f["geometry"]) for f in features if f["properties"]["label"] == "row" and f["properties"]["vineyard_id"] in names]
found = []
for tile in load_tiles():
    hits = {n: b for n, b in blocks.items() if b.intersects(tile.bounds)}
    if not hits:
        continue
    with rasterio.open(tile.path) as source:
        rgb, transform = source.read().astype(np.float32), source.transform
    inside = rasterize(list(hits.values()), out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool)
    near_rows = [r for r in rows if r.intersects(tile.bounds.buffer(1))]
    if near_rows:
        inside &= ~rasterize([r.buffer(0.35) for r in near_rows], out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool)
    background = np.stack([_fine(ndimage.median_filter(_coarse(band), size=11)) for band in rgb])
    deviation = np.sqrt(((rgb - background) ** 2).sum(0))
    lum = rgb.mean(0)
    exg = (2 * rgb[1] - rgb[0] - rgb[2]) / np.maximum(rgb.sum(0), 1)
    anomaly = inside & (deviation >= 60) & (exg < 0.08) & (lum >= 60)
    anomaly = ndimage.binary_opening(anomaly, EIGHT)
    labels, _ = ndimage.label(ndimage.binary_dilation(anomaly, EIGHT, iterations=2), EIGHT)
    labels[~anomaly] = 0
    for index, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None:
            continue
        mask = labels[window] == index
        area = mask.sum() * PIXEL_M ** 2
        if not 0.01 <= area <= 3.0:
            continue
        ys, xs = np.nonzero(mask)
        r0, c0, r1, c1 = window[0].start + ys.min(), window[1].start + xs.min(), window[0].start + ys.max() + 1, window[1].start + xs.max() + 1
        geometry = tile.to_world(box(c0, r0, c1, r1))
        pix = rgb[:, window[0], window[1]][:, mask]
        dev = float(deviation[window][mask].mean())
        found.append({"tile": tile.name, "px": [int(c0), int(r0), int(c1), int(r1)], "bounds": list(geometry.bounds), "area_m2": float(area),
                      "kind": "anom", "dev": dev, "rgb": [round(float(v)) for v in pix.mean(1)], "score": float(area) ** 0.5 * dev / 100,
                      "where": next(n for n, b in hits.items() if b.intersects(geometry)) if any(b.intersects(geometry) for b in hits.values()) else "",
                      "row_m": min((r.distance(geometry.centroid) for r in near_rows), default=99.0)})
found.sort(key=lambda c: -c["score"])
(W / "central_anomalies.json").write_text(json.dumps(found))
print(len(found), sheets(found[:top], W / "central_anom"))
