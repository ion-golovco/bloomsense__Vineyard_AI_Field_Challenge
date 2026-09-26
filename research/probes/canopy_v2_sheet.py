"""Contact sheets for the canopy round-two probes: crops centred on chosen pieces, the piece in cyan, other canopies in
magenta (optionally a second canopy set in yellow for before/after). Evaluation only.
`sheet(items, path)`: items are (geometry, caption, [(geometry, rgb), ...]) tuples."""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parents[1] / "review"))
from build import crop  # noqa: E402

CYAN, MAGENTA, YELLOW, RED = (0, 255, 255), (255, 0, 255), (255, 230, 0), (255, 40, 40)


def sheet(items, path: Path, size_m: float = 7.0, px: int = 240, cols: int = 6) -> Path:
    cells = []
    for geometry, caption, overlays in items:
        c = geometry.centroid
        span = max(size_m, 1.2 * max(geometry.bounds[2] - geometry.bounds[0], geometry.bounds[3] - geometry.bounds[1]))
        rgb = crop(c.x, c.y, span, px, overlays)
        cells.append((Image.fromarray(np.moveaxis(rgb, 0, -1)).resize((px, px)), caption))
    rows = max(1, (len(cells) + cols - 1) // cols)
    out = Image.new("RGB", (cols * px, rows * (px + 14)), "white")
    draw = ImageDraw.Draw(out)
    for i, (image, caption) in enumerate(cells):
        x, y = (i % cols) * px, (i // cols) * (px + 14)
        out.paste(image, (x, y + 14))
        draw.text((x + 2, y), caption, fill="black")
    out.save(path, quality=85)
    return path
