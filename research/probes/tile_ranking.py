import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio

from marcaj.tiles import DATA_DIR, REPO_ROOT

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "data" / "generated"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TILES = DATA_DIR / "tiles"
OUT = OUT_DIR / "tile_features.json"
N = 512
PX_M = 51.2 / N


def band_peak(power, freq_r, lo_m, hi_m):
    mask = (freq_r >= 1 / hi_m) & (freq_r <= 1 / lo_m)
    values = power[mask]
    return float(values.max() / (np.median(values) + 1e-9)), mask


def features(path):
    with rasterio.open(path) as src:
        rgb = src.read(out_shape=(3, N, N)).astype(np.float32)
    total = rgb.sum(axis=0)
    valid = total > 30
    valid_share = float(valid.mean())
    if valid_share < 0.05:
        return {"valid": valid_share}
    r, g, b = (rgb / np.maximum(total, 1))
    exg = 2 * g - r - b
    green = (exg > 0.08) & valid
    signal = np.where(valid, exg - exg[valid].mean(), 0) * np.outer(np.hanning(N), np.hanning(N))
    power = np.abs(np.fft.fftshift(np.fft.fft2(signal))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(N, d=PX_M))
    fx, fy = np.meshgrid(f, f)
    freq_r = np.hypot(fx, fy)
    vine, vine_mask = band_peak(power, freq_r, 2.0, 3.6)
    orchard, _ = band_peak(power, freq_r, 3.8, 7.0)
    masked = np.where(vine_mask, power, 0)
    iy, ix = np.unravel_index(masked.argmax(), masked.shape)
    period_m = 1 / freq_r[iy, ix]
    angle = float(np.degrees(np.arctan2(fy[iy, ix], fx[iy, ix])) % 180)
    brightness = total[valid].mean() / 3
    return {
        "valid": valid_share, "green": float(green.sum() / valid.sum()), "exg_mean": float(exg[valid].mean()),
        "vine_peak": vine, "orchard_peak": orchard, "period_m": float(period_m), "wave_angle": angle,
        "brightness": float(brightness),
    }


started = time.perf_counter()
rows = {}
for path in sorted(TILES.glob("*.tif")):
    rows[path.stem] = features(path)
OUT.write_text(json.dumps(rows, indent=1))
print(f"{len(rows)} tiles in {time.perf_counter() - started:.1f} s")
