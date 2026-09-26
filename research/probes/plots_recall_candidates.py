"""Second-pass plots of a run that the first pass does not have, with acceptance features, and a contact sheet of them.
Evaluation only (reads data/review/). Run from backend/:
uv run --frozen python ../research/probes/plots_recall_candidates.py base.geojson run.geojson sheet.jpg   (under work/plots_recall)"""

import json
import sys

import numpy as np
from rasterio.features import rasterize
from shapely.geometry import shape
from shapely.ops import unary_union

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from plot_sheet import sheet  # noqa: E402
from plots_recall_eval import WORK, outlines  # noqa: E402

from marcaj import plots  # noqa: E402
from marcaj.layers import load_layers  # noqa: E402
from marcaj.mosaic import MOSAIC_PX_M  # noqa: E402

base = unary_union([shape(f["geometry"]) for f in json.loads((WORK / sys.argv[1]).read_text())["features"] if f["properties"]["label"] == "block"])
features = json.loads((WORK / sys.argv[2]).read_text())["features"]
excess, layers, top = plots.load_excess(), load_layers(), load_layers(tophat_m=plots.STRIP_TOPHAT_M)
marked = outlines()
items, table = [], []
for f in features:
    g, p = shape(f["geometry"]), f["properties"]
    if p["label"] != "block" or g.intersection(base).area > 0.5 * g.area:
        continue
    hit = {lab: sum(g.intersection(h).area for _, l, h in marked if l == lab) / g.area for lab in ("vineyard", "orchard", "overgrown")}
    mask = rasterize([g], out_shape=layers.valid.shape, transform=layers.transform).astype(bool)
    angle, spacing = p["row_angle"], p["row_spacing_m"]
    frame = plots._Frame(g, angle, MOSAIC_PX_M)
    profile = (excess.at(frame.x, frame.y) * frame.inside).sum(1) / np.maximum(frame.inside.sum(1), 1)
    profile = profile[frame.inside.sum(1) > 10]
    power = np.abs(np.fft.rfft((profile - profile.mean()) * np.hanning(len(profile)), 4096)) ** 2
    freq = np.fft.rfftfreq(4096, MOSAIC_PX_M)
    band = (freq >= 1 / 3.6) & (freq <= 1 / 2.0)
    low = (freq > 1 / 20) & (freq < 1 / 3.8)
    row = {"id": p["vineyard_id"], "m2": p["area_m2"], "rows": p["rows"], "sp": spacing, "ang": angle,
           "V": round(hit["vineyard"], 2), "O": round(hit["orchard"], 2), "G": round(hit["overgrown"], 2),
           "wave": round(plots._wave(excess, g, angle, spacing), 4),
           "peak_share": round(float(power[band].max() / power[band | low].sum()), 3),
           "ratio": round(float(np.median(layers.vine_over_orchard[mask])), 2), "top": round(float(np.median(top.vine_over_orchard[mask])), 2),
           "green": round(float(np.nanmedian(layers.green_share[mask])), 2), "std": round(float(profile.std()), 4),
           "x": round(g.centroid.x), "y": round(g.centroid.y)}
    table.append(row)
    minx, miny, maxx, maxy = g.bounds
    items.append(((minx + maxx) / 2, (miny + maxy) / 2, max(maxx - minx, maxy - miny) / 2 + 8, f"{row['id']} V{row['V']:.1f} w{row['wave']:.3f} ps{row['peak_share']:.2f}"))
for row in table:
    print(row)
(WORK / sys.argv[3].replace(".jpg", ".json")).write_text(json.dumps(table, indent=1))
sheet(features, items, WORK / sys.argv[3])
