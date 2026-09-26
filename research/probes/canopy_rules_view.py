"""Side-by-side instance views: reference canopies (left) and predicted (right), each instance a random colour over
the image, rows cyan. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_rules_view.py NAME PRED.json [x0 y0 size step]"""

import json
import sys
import warnings

import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely.geometry import shape

from canopy_rules_lib import WORK, images, ref_objects


def paint(name: str, polygons: list, rng: np.random.Generator, lines: list | None = None) -> np.ndarray:
    image, transform = images[name]
    out = image.astype(np.float32)
    if polygons:
        labels = rasterize([(p, k + 1) for k, p in enumerate(polygons)], out_shape=(2048, 2048), transform=transform).astype(np.int32)
        colours = rng.integers(60, 256, (len(polygons) + 1, 3)).astype(np.float32)
        edge = rasterize([p.boundary for p in polygons], out_shape=(2048, 2048), transform=transform).astype(bool)
        inside = labels > 0
        for band in range(3):
            out[band][inside] = 0.45 * out[band][inside] + 0.55 * colours[labels[inside], band]
            out[band][edge] = 255
    rows = rasterize([r for r in ref_objects(name, "row")], out_shape=(2048, 2048), transform=transform).astype(bool)
    out[0][rows], out[1][rows], out[2][rows] = 0, 255, 255
    for line, colour in lines or []:
        mask = rasterize([line], out_shape=(2048, 2048), transform=transform, all_touched=True).astype(bool)
        mask |= np.roll(mask, 1, 0) | np.roll(mask, 1, 1)
        for band in range(3):
            out[band][mask] = colour[band]
    return out.astype(np.uint8)


def view(name: str, predicted: list, x0: int = 0, y0: int = 0, size: int = 2048, step: int = 4, tag: str = "", lines: list | None = None) -> str:
    """`lines`: (geometry, rgb) drawn on the right panel, e.g. predicted axes."""
    left = paint(name, ref_objects(name, "vineyard"), np.random.default_rng(1))[:, y0:y0 + size:step, x0:x0 + size:step]
    right = paint(name, predicted, np.random.default_rng(2), lines)[:, y0:y0 + size:step, x0:x0 + size:step]
    both = np.concatenate([left, np.full((3, left.shape[1], 6), 255, np.uint8), right], axis=2)
    path = WORK / f"view_{name[7:16]}{tag}_{x0}_{y0}_{size}.jpg"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with rasterio.open(path, "w", driver="JPEG", width=both.shape[2], height=both.shape[1], count=3, dtype="uint8", quality=88) as sink:
            sink.write(both)
    return str(path)


if __name__ == "__main__":
    name = sys.argv[1]
    found = json.loads(open(sys.argv[2]).read())[name] if len(sys.argv) > 2 and sys.argv[2] != "-" else []
    args = [int(a) for a in sys.argv[3:]]
    print(view(name, [shape(g) for g in found], *args))
