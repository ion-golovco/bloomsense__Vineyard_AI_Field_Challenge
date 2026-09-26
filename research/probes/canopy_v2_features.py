"""Per-piece colour, texture and context features of canopy polygons: the user's labelled long canopies (not a vine /
one plant / several touching plants), the organizer reference canopies on the two tiles, and every current prediction
(the defaults run). Writes data/generated/work/canopy_v2/features.json and prints the class distributions.
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/canopy_v2_features.py"""

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import WORK, labels, prob_path  # noqa: E402
import canopy_recall_lib as crl  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.cvat import read_cvat  # noqa: E402
from marcaj.tiles import DATA_DIR, load_tiles  # noqa: E402

RING = (0.35, 0.9)


def features(geom, rgb, transform, prob) -> dict:
    left, bottom, right, top = geom.buffer(RING[1]).bounds
    (c0, r0), (c1, r1) = ~transform * (left, top), ~transform * (right, bottom)
    r0, c0 = max(int(r0), 0), max(int(c0), 0)
    r1, c1 = min(int(r1) + 1, rgb.shape[1]), min(int(c1) + 1, rgb.shape[2])
    t = transform * transform.translation(c0, r0)
    shape_ = (r1 - r0, c1 - c0)
    inside = rasterize([geom], out_shape=shape_, transform=t).astype(bool)
    ring = rasterize([geom.buffer(RING[1]).difference(geom.buffer(RING[0]))], out_shape=shape_, transform=t).astype(bool)
    r, g, b = rgb[:, r0:r1, c0:c1].astype(np.float32)
    ex = 2 * g - r - b
    bright = (r + g + b) / 3
    if not inside.any():
        return {}
    out = {"area": geom.area, "length": crl.length_m(geom)}
    out["width"] = out["area"] / max(out["length"], 1e-3)
    for name, arr in (("r", r), ("g", g), ("b", b), ("gr", g - r), ("ex", ex), ("bright", bright)):
        out[name] = float(arr[inside].mean())
    out["g_std"] = float(g[inside].std())
    out["dark"] = float((bright[inside] < 60).mean())
    out["ring_green"] = float((ex[ring] > 25).mean()) if ring.any() else 0.0
    out["ring_ex"] = float(np.clip(ex[ring], 0, None).mean() / max(np.clip(ex[inside], 0, None).mean(), 1e-3)) if ring.any() else 0.0
    out["ring_gr"] = float((g - r)[ring & (ex > 25)].mean()) if (ring & (ex > 25)).any() else 0.0
    gx, gy = np.gradient(g)
    out["grad"] = float(np.hypot(gx, gy)[inside].mean())
    if prob is not None:
        out["net"] = float(prob[r0:r1, c0:c1][inside].mean() / 255)
    return out


def main() -> None:
    tiles = {t.name: t for t in load_tiles()}
    items = []  # (group, tile, geometry, extra)
    for lab in labels():
        items.append((lab["properties"]["review_answer"], lab["properties"]["tile"], shape(lab["geometry"]), {"id": lab["id"]}))
    with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
        xml = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
    for f in read_cvat(xml, tiles):
        if f["properties"]["label"] == "vineyard":
            name = next(n for n in crl.NAMES if tiles[n].bounds.contains(shape(f["geometry"]).representative_point()))
            items.append(("reference", name, shape(f["geometry"]), {}))
    defaults = json.loads((WORK / "canopies_defaults.json").read_text())
    for f in defaults:
        items.append(("pred", f["properties"]["tile_run"], shape(f["geometry"]), {"i": len(items)}))
    out = []
    by_tile: dict = {}
    for item in items:
        by_tile.setdefault(item[1], []).append(item)
    for name, group in sorted(by_tile.items()):
        rgb, transform = canopy.read_rgb(tiles[name])
        path = prob_path(name)
        prob = np.load(path) if path.is_file() else None
        for kind, _, geom, extra in group:
            f = features(geom, rgb, transform, prob)
            if f:
                out.append({"group": kind, "tile": name, **extra, **f})
    (WORK / "features.json").write_text(json.dumps(out))
    keys = ["length", "width", "gr", "ex", "bright", "g_std", "dark", "ring_green", "ring_ex", "ring_gr", "grad", "net", "b", "r"]
    for kind in ["not_vine", "several", "one_plant", "reference", "pred"]:
        rows = [o for o in out if o["group"] == kind]
        if kind in ("reference", "pred"):
            rows = [o for o in rows if o["length"] > 2.5]
        print(f"{kind:10s} n={len(rows)}")
        for k in keys:
            v = np.array([o.get(k, np.nan) for o in rows])
            print(f"   {k:10s} " + " ".join(f"{q:7.2f}" for q in np.nanpercentile(v, [5, 25, 50, 75, 95])))


if __name__ == "__main__":
    main()
