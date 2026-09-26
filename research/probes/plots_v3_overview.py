"""Site overview of plots v3 (plots_recall_overview.py pointed at data/generated/work/plots_v3): the lab outlines, the
first run magenta, the second yellow, and the second run's block ids. Evaluation only (reads data/review/). Run from
backend/: uv run --frozen python ../research/probes/plots_v3_overview.py out.jpg before.geojson after.geojson"""

import json
import sys

from PIL import Image, ImageDraw
from shapely.geometry import shape

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import plots_recall_overview as overview  # noqa: E402
from marcaj.mosaic import MOSAIC_PX_M, load_mosaic  # noqa: E402
from plots_v3_eval import WORK  # noqa: E402

if __name__ == "__main__":
    overview.WORK = WORK
    overview.main()
    out, after = WORK / sys.argv[1], WORK / sys.argv[-1]
    image = Image.open(out)
    draw, (_, t) = ImageDraw.Draw(image), load_mosaic()
    for f in json.loads(after.read_text())["features"]:
        if f["properties"]["label"] == "block":
            p = shape(f["geometry"]).representative_point()
            draw.text(((p.x - t.c) / (MOSAIC_PX_M * overview.STEP) - 18, (t.f - p.y) / (MOSAIC_PX_M * overview.STEP)), f["properties"]["vineyard_id"], fill=(255, 255, 0))
    image.save(out, quality=85)
