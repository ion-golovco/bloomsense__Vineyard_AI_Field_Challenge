"""Long canopy strips: vine hedge or grass? Per-polygon colour and shape features for the reference canopies (by length),
our predictions on the reference tiles, and the longest predicted canopies site-wide (data/generated/predictions.geojson,
read only). Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_rules_strips.py"""

import json
from collections import defaultdict

import numpy as np
from rasterio.features import rasterize
from shapely.geometry import shape

from marcaj.canopy import CanopyParams, excess_green, read_rgb
from marcaj.cvat import build_scene
from marcaj.tiles import DATA_DIR, REPO_ROOT, load_tiles

P = CanopyParams()
WORK = REPO_ROOT / "data" / "generated" / "work" / "canopy_rules"
tiles = {t.name: t for t in load_tiles()}


def length(g) -> float:
    c = list(g.minimum_rotated_rectangle.exterior.coords)
    return max(np.hypot(c[0][0] - c[1][0], c[0][1] - c[1][1]), np.hypot(c[1][0] - c[2][0], c[1][1] - c[2][1]))


def features(name: str, polygons: list) -> list[dict]:
    image, transform = read_rgb(tiles[name])
    excess, valid = excess_green(image, P)
    r, g, b = image.astype(np.float32)
    out = []
    for p in polygons:
        inside = rasterize([p], out_shape=excess.shape, transform=transform).astype(bool)
        ring = rasterize([p.buffer(0.3).difference(p.buffer(0.05))], out_shape=excess.shape, transform=transform).astype(bool)
        if not inside.any():
            continue
        e = excess[inside]
        L = length(p)
        out.append({"length": L, "area": p.area, "fill": p.area / max(L * 2 * P.tube_m, 1e-6), "dn_median": float(np.median(e)),
                    "dn_p75": float(np.percentile(e, 75)), "dn_std": float(e.std()), "ring_green": float((excess[ring] > P.green_dn).mean()),
                    "bright": float(((r + g + b) / 3)[inside].mean()), "g_minus_r": float((g - r)[inside].mean())})
    return out


def summary(tag: str, rows: list[dict]) -> None:
    if not rows:
        return
    keys = ("length", "fill", "dn_median", "dn_p75", "dn_std", "ring_green", "bright", "g_minus_r")
    q = {k: np.percentile([r[k] for r in rows], [10, 50, 90]) for k in keys}
    print(f"{tag:44s} n {len(rows):4d} | " + " | ".join(f"{k} {q[k][0]:.2f}/{q[k][1]:.2f}/{q[k][2]:.2f}" for k in keys))


reference = [f for f in build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(tiles.values()))["features"]
             if f["properties"]["source"] == "reference" and f["properties"]["label"] == "vineyard"]
by_tile = defaultdict(list)
for f in reference:
    by_tile[f["properties"]["tile"]].append(shape(f["geometry"]))
ref_rows = [dict(r, tile=name) for name, polys in by_tile.items() for r in features(name, polys)]
print("percentiles 10/50/90")
for lo, hi in ((0, 2), (2, 5), (5, 99)):
    for name in by_tile:
        summary(f"reference {name[7:16]} length {lo}-{hi} m", [r for r in ref_rows if r["tile"] == name and lo <= r["length"] < hi])

predicted = [f for f in json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"] if f["properties"]["label"] == "vineyard"]
flags = {}
for f in predicted:
    g = shape(f["geometry"])
    L = length(g)
    if L > 10:
        c = g.representative_point()
        r_, c_ = int((5221222.4 - c.y) // 51.2), int((c.x - 628992.0) // 51.2)
        flags.setdefault(f"siret3_r{r_:03d}_c{c_:03d}.tif", []).append(g)
site = [dict(r, tile=name) for name, polys in flags.items() for r in features(name, polys)]
summary("site predictions longer than 10 m", site)
(WORK / "strips_site.json").write_text(json.dumps(site))
(WORK / "strips_ref.json").write_text(json.dumps(ref_rows))
