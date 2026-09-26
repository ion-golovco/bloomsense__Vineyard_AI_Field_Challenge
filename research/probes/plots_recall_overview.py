"""Site overview at 1 m/px: the lab outlines (vineyard green, orchard violet, overgrown cyan) and one or two plot runs
(first magenta, second yellow), passages brown. Evaluation only (reads data/review/). Run from backend/:
uv run --frozen python ../research/probes/plots_recall_overview.py out.jpg a.geojson [b.geojson]   (paths under work/plots_recall)"""

import json
import sys

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import shape

from marcaj.mosaic import MOSAIC_PX_M, load_mosaic
from marcaj.plots import exclusions
from marcaj.review import load_verdicts

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from plot_sheet import COLOURS, _rings  # noqa: E402
from plots_recall_eval import WORK  # noqa: E402

STEP = 5  # mosaic pixels per overview pixel: 1 m


def main() -> None:
    out, runs = sys.argv[1], sys.argv[2:]
    rgb, t = load_mosaic()
    small = rgb[:, ::STEP, ::STEP]
    image = Image.fromarray(np.moveaxis(small, 0, -1).copy())
    draw = ImageDraw.Draw(image)
    px = lambda coords: [((x - t.c) / (MOSAIC_PX_M * STEP), (t.f - y) / (MOSAIC_PX_M * STEP)) for x, y in coords]
    for ring in _rings(exclusions()):
        draw.line(px(ring), fill=(150, 100, 40), width=1)
    for v in load_verdicts():
        if v["kind"] == "plot":
            draw.line(px(shape(v["geometry"]).exterior.coords), fill=COLOURS[v["label"]], width=3)
    for run, colour, width in zip(runs, ((255, 0, 255), (255, 230, 0)), (3, 2)):
        for f in json.loads((WORK / run).read_text())["features"]:
            if f["properties"]["label"] == "block":
                for ring in _rings(shape(f["geometry"])):
                    draw.line(px(ring), fill=colour, width=width)
    for i, v in enumerate(v for v in load_verdicts() if v["kind"] == "plot"):
        c = shape(v["geometry"]).centroid
        draw.text(px([(c.x, c.y)])[0], f"#{i}", fill=(255, 255, 255))
    image.save(WORK / out, quality=85)
    print(f"-> {WORK / out} {image.size}")


if __name__ == "__main__":
    main()
