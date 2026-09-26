"""Contact sheets: crops of the tile around given canopy polygons (red outline) over the image, with a caption strip.
Evaluation only; used by the missing / strip analyses. `sheet(items, path)` with items (tile name, polygon or point, caption, extra outlines)."""

import warnings

import numpy as np
import rasterio
from rasterio.features import rasterize

from marcaj.canopy import read_rgb
from marcaj.tiles import load_tiles

TILES = {t.name: t for t in load_tiles()}
_cache: dict = {}


def crop(name: str, geometry, outlines: list, size: int = 320, extra: list | None = None, overlay=None) -> np.ndarray:
    if name not in _cache:
        _cache.clear()
        _cache[name] = read_rgb(TILES[name])
    image, transform = _cache[name]
    out = image.copy()
    if overlay is not None:
        for band, value in enumerate((255, 0, 255)):
            out[band][overlay] = value
    for group, colour in ((outlines, (255, 40, 40)), (extra or [], (255, 255, 0))):
        if group:
            edge = rasterize([g.boundary if g.geom_type != "LineString" else g for g in group], out_shape=image.shape[1:], transform=transform, all_touched=True).astype(bool)
            edge |= np.roll(edge, 1, 0) | np.roll(edge, 1, 1)
            for band, value in enumerate(colour):
                out[band][edge] = value
    c = geometry.representative_point() if geometry.geom_type != "Point" else geometry
    col, row = ~transform * (c.x, c.y)
    r0, c0 = int(min(max(row - size / 2, 0), 2048 - size)), int(min(max(col - size / 2, 0), 2048 - size))
    return out[:, r0:r0 + size, c0:c0 + size]


def sheet(items: list, path, columns: int = 5, size: int = 320) -> str:
    """items: (tile, geometry, caption, outlines, extra, overlay)."""
    from PIL import Image, ImageDraw
    cells = []
    for name, geometry, caption, outlines, extra, overlay in items:
        img = Image.fromarray(np.moveaxis(crop(name, geometry, outlines, size, extra, overlay), 0, -1))
        canvas = Image.new("RGB", (size, size + 18), "black")
        canvas.paste(img, (0, 18))
        ImageDraw.Draw(canvas).text((3, 3), caption, fill="white")
        cells.append(canvas)
    rows = (len(cells) + columns - 1) // columns
    board = Image.new("RGB", (columns * (size + 4), rows * (size + 22)), "white")
    for k, cell in enumerate(cells):
        board.paste(cell, ((k % columns) * (size + 4), (k // columns) * (size + 22)))
    board.save(path, quality=85)
    return str(path)
