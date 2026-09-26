"""Positive control for waste_south cue masks: the 12 likely items of the site-wide scan (likely_checklist.json, all
outside inter-rows) must fire a cue. Per item, a 4 m window around its centre is treated as 'inside'.
Run from backend/: uv run --frozen python ../research/probes/waste_south_control.py"""

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
from waste_south import CUE_MIN_M2, W, cue_masks  # noqa: E402

from marcaj.tiles import DATA_DIR, PIXEL_M  # noqa: E402
from marcaj.waste import EIGHT, _coarse, _fine  # noqa: E402

HALF = 256  # 6.4 m window, divisible by the 8 px background grid
items = json.loads((W / "likely_checklist.json").read_text())
missed = 0
for item in items:
    c0, r0, c1, r1 = item["px"]
    cx, cy = (c0 + c1) // 2 // 8 * 8, (r0 + r1) // 2 // 8 * 8
    with rasterio.open(DATA_DIR / "tiles" / item["tile"]) as source:
        rgb = source.read(window=Window(cx - HALF, cy - HALF, 2 * HALF, 2 * HALF), boundless=True, fill_value=0).astype(np.float32)
    coarse = rgb.reshape(3, 2 * HALF // 8, 8, 2 * HALF // 8, 8).mean((2, 4))
    background = np.stack([np.repeat(np.repeat(ndimage.median_filter(b, size=11), 8, 0), 8, 1) for b in coarse])
    deviation = np.sqrt(((rgb - background) ** 2).sum(0))
    inside = np.zeros((2 * HALF, 2 * HALF), bool)
    inside[HALF - 80:HALF + 80, HALF - 80:HALF + 80] = True
    fired = {}
    for kind, mask in cue_masks(rgb, deviation, inside).items():
        labels, _ = ndimage.label(mask, EIGHT)
        sizes = np.bincount(labels.ravel())[1:] * PIXEL_M ** 2 if labels.max() else np.zeros(1)
        if sizes.max() >= CUE_MIN_M2[kind]:
            fired[kind] = round(float(sizes.max()), 3)
    hit = any(k in fired for k in ("white", "blue", "vivid"))
    missed += not hit
    print(item["tile"], item["kind"], round(item["area_m2"], 2), "HIT" if hit else "MISS", fired)
print(f"{len(items) - missed}/{len(items)} fire a white, blue or vivid cue")
