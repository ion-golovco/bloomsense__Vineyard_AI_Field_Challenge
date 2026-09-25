"""Preprocessing: one seamless, georeferenced RGB mosaic of the 311 verified tiles at 0.2 m/px, cached as a
GeoTIFF with overviews (it opens in QGIS). Each tile is area-averaged 8:1, not decimated, so vine-row
frequencies (2-3.6 m) do not alias."""

import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.windows import Window

from marcaj.tiles import CRS, DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, load_tiles

MOSAIC_PATH = REPO_ROOT / "data" / "generated" / "mosaic_20cm.tif"
FACTOR = 8
MOSAIC_PX_M = PIXEL_M * FACTOR


def build_mosaic(data_dir: Path = DATA_DIR, path: Path = MOSAIC_PATH) -> Path:
    started = time.perf_counter()
    tiles = load_tiles(data_dir)
    size = TILE_PX // FACTOR
    left, top = min(tile.left for tile in tiles), max(tile.top for tile in tiles)
    step = TILE_PX * PIXEL_M
    width = round((max(tile.left for tile in tiles) - left) / step + 1) * size
    height = round((top - min(tile.top for tile in tiles)) / step + 1) * size
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {"driver": "GTiff", "width": width, "height": height, "count": 3, "dtype": "uint8", "crs": CRS,
               "transform": Affine(MOSAIC_PX_M, 0, left, 0, -MOSAIC_PX_M, top), "tiled": True, "compress": "deflate"}
    with rasterio.open(path, "w", **profile) as out:
        for tile in tiles:
            with rasterio.open(tile.path) as source:
                rgb = source.read(out_shape=(3, size, size), resampling=Resampling.average)
            out.write(rgb, window=Window(round((tile.left - left) / step) * size, round((top - tile.top) / step) * size, size, size))
        out.build_overviews([2, 4, 8, 16], Resampling.average)
    print(f"mosaic {width}x{height} px at {MOSAIC_PX_M} m/px built in {time.perf_counter() - started:.1f} s: {path}")
    return path


def load_mosaic(data_dir: Path = DATA_DIR, path: Path = MOSAIC_PATH, refresh: bool = False) -> tuple[np.ndarray, Affine]:
    """(3, H, W) uint8 RGB and its EPSG:32635 transform. Pixels outside the tiles are 0."""
    if refresh or not path.is_file():
        build_mosaic(data_dir, path)
    with rasterio.open(path) as source:
        return source.read(), source.transform


if __name__ == "__main__":
    build_mosaic()
