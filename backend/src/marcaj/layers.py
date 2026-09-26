"""Preprocessed layers for finding vineyard plots, on a 0.4 m grid over the 0.2 m mosaic. They are the
variables that separated the hand-drawn plots in `research/probes/plot_variables.py`:

- `vine_over_orchard`: row energy at 2.0-3.6 m spacing over energy at 3.8-7 m, both at the strongest of
  12 orientations and smoothed over 2 m. Vineyard vs background AUC 0.96, vs orchard 0.96, vs overgrown 0.95.
- `orchard_energy`: the 3.8-7 m energy. Orchards score higher than vineyards with AUC 0.99.
- `row_angle`: direction of the strongest vine-spacing rows, degrees counter-clockwise from east, in 15 degree
  steps. A seed only: 32 of 35 plots within 8 degrees of the fitted angle, 3 off by about 90.
- `green_share`: share of green pixels (ExG > 0.05) within 4 m. It separates overgrown plots (AUC 0.92) and
  orchards (0.89), but not the background (0.75).

ExG is high-passed by normalised convolution over valid pixels only, and the bank uses smooth log-Gabor
windows, so neither the no-data border nor hard filter edges ring across the map. `tophat_m` swaps the high-pass
for a white top-hat (only structures narrower than that stay): it lifts 10-13 m strips of young vines from ratio 0.7-1.1
to 4-14 and keeps orchards below 1, but as the only seed source it fragments plots (research/notes/plots.md), so it is off."""

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rasterio.transform import Affine
from scipy import ndimage

from marcaj.mosaic import MOSAIC_PX_M, load_mosaic
from marcaj.tiles import DATA_DIR, REPO_ROOT

LAYERS_PATH = REPO_ROOT / "data" / "generated" / "layers_40cm.npz"
LAYER_PX_M = 2 * MOSAIC_PX_M
VINE_SPACING_M = (2.0, 3.6)
ORCHARD_SPACING_M = (3.8, 7.0)
ORIENTATIONS = 12
GREEN_EXG = 0.05


@dataclass(frozen=True)
class Layers:
    vine_over_orchard: np.ndarray
    orchard_energy: np.ndarray
    row_angle: np.ndarray
    green_share: np.ndarray
    valid: np.ndarray
    transform: Affine


def exg(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Excess green (2g - r - b) / (r + g + b) of a (3, H, W) image, and its valid-pixel mask."""
    red, green, blue = rgb.astype(np.float32)
    total = red + green + blue
    valid = total > 30
    return np.where(valid, (2 * green - red - blue) / np.maximum(total, 1), 0).astype(np.float32), valid


def _halve(array: np.ndarray) -> np.ndarray:
    height, width = array.shape[0] // 2 * 2, array.shape[1] // 2 * 2
    return array[:height, :width].reshape(height // 2, 2, width // 2, 2).mean(axis=(1, 3))


def compute_layers(rgb: np.ndarray, transform: Affine, smooth_m: float = 2.0, tophat_m: float | None = None) -> Layers:
    excess, valid_px = exg(rgb)
    green = _halve((excess > GREEN_EXG).astype(np.float32))
    excess, valid = _halve(excess), _halve(valid_px.astype(np.float32)) > 0.99
    px = LAYER_PX_M
    if tophat_m:
        size = round(tophat_m / px)
        background = ndimage.grey_opening(np.where(valid, excess, excess.max()), size=(size, size))
    else:
        weight = ndimage.gaussian_filter(valid.astype(np.float32), 4 / px)
        background = ndimage.gaussian_filter(np.where(valid, excess, 0), 4 / px) / np.maximum(weight, 1e-6)
    spectrum = np.fft.rfft2(np.where(valid, excess - background, 0).astype(np.float32))
    fy = np.fft.fftfreq(excess.shape[0], px)[:, None]
    fx = np.fft.rfftfreq(excess.shape[1], px)[None, :]
    radius, direction = np.hypot(fx, fy), np.arctan2(fy, fx)
    radius[0, 0] = 1e-9

    def band(spacing: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
        """Strongest smoothed energy over orientations in a log-Gabor band, and that orientation's index."""
        spread = np.log(np.sqrt(spacing[1] / spacing[0]))
        radial = np.exp(-np.log(radius * np.sqrt(spacing[0] * spacing[1])) ** 2 / (2 * spread**2)).astype(np.float32)
        best = np.zeros(excess.shape, np.float32)
        index = np.zeros(excess.shape, np.uint8)
        for k in range(ORIENTATIONS):
            offset = np.angle(np.exp(2j * (direction - np.pi * k / ORIENTATIONS))) / 2
            window = radial * np.exp(-offset**2 / (2 * np.radians(10) ** 2)).astype(np.float32)
            energy = ndimage.gaussian_filter(np.fft.irfft2(spectrum * window, excess.shape).astype(np.float32) ** 2, smooth_m / px)
            stronger = energy > best
            best[stronger], index[stronger] = energy[stronger], k
        return best, index

    vine, vine_index = band(VINE_SPACING_M)
    orchard, _ = band(ORCHARD_SPACING_M)
    # the image y axis points south, so a wave at image angle a has rows at world angle 90 - a
    row_angle = (90 - vine_index.astype(np.float32) * 180 / ORIENTATIONS) % 180
    return Layers(
        vine_over_orchard=np.where(valid, vine / np.maximum(orchard, 1e-9), 0).astype(np.float32),
        orchard_energy=np.where(valid, orchard, 0).astype(np.float32),
        row_angle=np.where(valid, row_angle, np.nan).astype(np.float32),
        green_share=np.where(valid, ndimage.gaussian_filter(green, 4 / px), np.nan).astype(np.float32),
        valid=valid,
        transform=transform * Affine.scale(2),
    )


def load_layers(data_dir: Path = DATA_DIR, path: Path = LAYERS_PATH, refresh: bool = False) -> Layers:
    if path.is_file() and not refresh:
        saved = np.load(path)
        return Layers(**{name: saved[name] for name in ("vine_over_orchard", "orchard_energy", "row_angle", "green_share", "valid")},
                      transform=Affine(*saved["transform"]))
    started = time.perf_counter()
    layers = compute_layers(*load_mosaic(data_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, vine_over_orchard=layers.vine_over_orchard, orchard_energy=layers.orchard_energy,
                        row_angle=layers.row_angle, green_share=layers.green_share, valid=layers.valid,
                        transform=np.array(layers.transform[:6]))
    print(f"layers computed in {time.perf_counter() - started:.1f} s, cached at {path}")
    return layers
