"""Explore obstacle candidate masks on the 0.2 m mosaic inside predicted blocks: per-block overlays.
Run from backend/: uv run --frozen python ../research/probes/obstacles_explore.py"""

import json
import sys

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from scipy import ndimage
from shapely.geometry import shape

from marcaj.tiles import REPO_ROOT

OUT = REPO_ROOT / "data" / "generated" / "work" / "obstacles"
OUT.mkdir(parents=True, exist_ok=True)
features = json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]
blocks = [f for f in features if f["properties"]["label"] == "block"]
only = sys.argv[1:]
with rasterio.open(REPO_ROOT / "data" / "generated" / "mosaic_20cm.tif") as source:
    for block in blocks:
        vid = block["properties"]["vineyard_id"]
        if only and vid not in only:
            continue
        polygon = shape(block["geometry"])
        L, B, R, T = polygon.buffer(8).bounds
        window = from_bounds(L, B, R, T, source.transform).round_offsets().round_lengths()
        rgb = source.read(window=window).astype(np.float32)
        transform = source.window_transform(window)
        r, g, b = rgb
        exg = 2 * g - r - b
        bright = rgb.mean(0)
        chroma = rgb.max(0) - rgb.min(0)
        inside = rasterize([polygon.buffer(4)], out_shape=bright.shape, transform=transform).astype(bool)
        tree = (exg > 30) & (bright < 90)
        roof = ((b - r > 8) & (bright > 60)) | ((r - (g + b) / 2 > 35) & (r > 110)) | (bright > 200)
        disk = lambda rad: (np.hypot(*np.mgrid[-rad:rad + 1, -rad:rad + 1]) <= rad)
        tree_o = ndimage.binary_opening(tree, disk(5))
        roof_o = ndimage.binary_opening(roof, disk(4))
        view = rgb.transpose(1, 2, 0).astype(np.uint8).copy()
        over = view.copy()
        over[tree_o & inside] = [255, 0, 255]
        over[roof_o & inside] = [0, 255, 255]
        im = Image.fromarray(np.concatenate([view, over], 1))
        im.save(OUT / f"explore_{vid}.jpg", quality=85)
        print(vid, "tree px", int((tree_o & inside).sum()), "roof px", int((roof_o & inside).sum()))
