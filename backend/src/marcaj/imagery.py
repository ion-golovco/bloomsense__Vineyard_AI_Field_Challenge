"""Web-map (EPSG:3857 XYZ) imagery tiles rendered from the organizer's source orthomosaic."""

import warnings
from functools import lru_cache

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds

from marcaj.tiles import DATA_DIR

SOURCE = DATA_DIR / "04_source" / "siret3_source_orthomosaic_EPSG4326.tif"
SOURCE_PIXEL_M = 0.024
TILE = 256
_HALF_WORLD = 20037508.342789244


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    size = 2 * _HALF_WORLD / 2**z
    return -_HALF_WORLD + x * size, _HALF_WORLD - (y + 1) * size, -_HALF_WORLD + (x + 1) * size, _HALF_WORLD - y * size


@lru_cache(maxsize=1)
def _source_info() -> tuple[tuple[float, float, float, float], list[int]]:
    with rasterio.open(SOURCE) as source:
        return transform_bounds(source.crs, "EPSG:3857", *source.bounds), source.overviews(1)


def _png(rgba: np.ndarray) -> bytes:
    with warnings.catch_warnings(), MemoryFile() as memory:
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with memory.open(driver="PNG", width=TILE, height=TILE, count=4, dtype="uint8") as png:
            png.write(rgba)
        return memory.read()


@lru_cache(maxsize=4096)
def render_tile(z: int, x: int, y: int) -> bytes:
    left, bottom, right, top = _tile_bounds(z, x, y)
    (source_left, source_bottom, source_right, source_top), factors = _source_info()
    rgb = np.zeros((3, TILE, TILE), dtype=np.uint8)
    if left < source_right and right > source_left and bottom < source_top and top > source_bottom:
        wanted = (right - left) / TILE / SOURCE_PIXEL_M
        level = max((index for index, factor in enumerate(factors) if factor <= wanted), default=None)
        with rasterio.open(SOURCE, overview_level=level) as source:
            reproject(
                rasterio.band(source, (1, 2, 3)), rgb,
                dst_transform=from_bounds(left, bottom, right, top, TILE, TILE),
                dst_crs="EPSG:3857", resampling=Resampling.bilinear,
            )
    alpha = np.where(rgb.any(axis=0), 255, 0).astype(np.uint8)
    return _png(np.concatenate([rgb, alpha[None]]))
