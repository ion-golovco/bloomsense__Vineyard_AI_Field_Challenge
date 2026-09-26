"""Where the reference cuts touching plants versus the necks it leaves inside its own long canopies, measured on our
pre-split canopy mask (canopy_mask through the network "and", frozen rows) of the two organizer tiles.

- cut: two reference canopies on the same row, under 5 cm apart, lying in one component of our mask. Depth = the mask's
  smallest along-row width within +-0.15 m of the boundary over the smaller of the two canopies' peak widths (as
  canopy._split measures it); also the colour valley (mean 2g-r-b of the tube per 5 cm, same ratio).
- inside: every width minimum inside a reference canopy longer than 2 m (0.3 m clear of its ends), over its peaks on
  either side: the deepest is the neck a rule must not cut.
Also the deepest neck inside each labelled long piece (user: several / one plant / not a vine) on its own tile.
Writes data/generated/work/canopy_v2/refnecks.json. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v2_refnecks.py"""

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import NAMES, WORK, frozen_plots, labels, prob_path  # noqa: E402

from marcaj import canopy, canopy_net  # noqa: E402
from marcaj.canopy import EIGHT, CanopyParams, _along, canopy_mask, excess_green, plot_rows, row_spacing  # noqa: E402
from marcaj.cvat import read_cvat  # noqa: E402
from marcaj.tiles import DATA_DIR, load_tiles  # noqa: E402

STEP = 0.05
P = CanopyParams()


def tile_profiles(tile, rows):
    """Per plot crossing the tile: (component labels, along coordinate, width profile per component, colour profile)."""
    rgb, transform = canopy.read_rgb(tile)
    prob = np.load(prob_path(tile.name)).astype(np.float32)[None] / 255.0
    green = canopy_net.mask(rgb, None, P, 0.2, "and", False, prob)
    excess, _ = excess_green(rgb, P)
    out = []
    for plot in rows:
        axes = [a for a in plot.axes if a.intersects(tile.bounds)]
        if not axes:
            continue
        mask = canopy_mask(rgb, transform, axes, P, green, row_spacing(plot.axes))
        along = _along(transform, mask.shape, plot.angle_deg)
        lab, n = ndimage.label(mask, EIGHT)
        out.append((lab, along, excess, transform))
    return out


def comp_profile(lab, along, excess, k):
    rr, cc = np.nonzero(lab == k)
    u = along[rr, cc]
    u0 = u.min()
    b = ((u - u0) / STEP).astype(int)
    width = np.bincount(b).astype(float) * 0.025**2 / STEP
    colour = np.bincount(b, weights=excess[rr, cc]) / np.maximum(np.bincount(b), 1)
    return u0, ndimage.gaussian_filter1d(width, 1.0), ndimage.gaussian_filter1d(colour, 1.0)


def span(geom, lab, along, transform, k):
    pix = rasterize([geom], out_shape=lab.shape, transform=transform).astype(bool) & (lab == k)
    u = along[pix]
    return (float(u.min()), float(u.max())) if len(u) else None


def inside_necks(u0, w, c, lo, hi, least=0.3):
    i0, i1 = int((lo - u0) / STEP), int((hi - u0) / STEP)
    res = []
    for i in range(max(i0 + int(least / STEP), 1), min(i1 - int(least / STEP), len(w) - 1)):
        if w[i] <= w[i - 1] and w[i] <= w[i + 1]:
            left, right = w[i0:i].max(), w[i + 1:i1 + 1].max()
            cl, cr = c[i0:i].max(), c[i + 1:i1 + 1].max()
            res.append((float(w[i] / max(min(left, right), 1e-6)), float(c[i] / max(min(cl, cr), 1e-6)), float(w[i]),
                        float(min(i - i0, i1 - i) * STEP)))
    return sorted(res)


def main() -> None:
    tiles = {t.name: t for t in load_tiles()}
    rows = plot_rows(frozen_plots())
    with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
        xml = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
    ref_all = [shape(f["geometry"]) for f in read_cvat(xml, tiles) if f["properties"]["label"] == "vineyard"]
    result = {"cut": [], "inside": [], "labels": []}
    for name in NAMES:
        refs = [g for g in ref_all if tiles[name].bounds.contains(g.representative_point())]
        for lab, along, excess, transform in tile_profiles(tiles[name], rows):
            comp_of = {}
            for j, g in enumerate(refs):
                pix = rasterize([g], out_shape=lab.shape, transform=transform).astype(bool)
                vals = lab[pix]
                vals = vals[vals > 0]
                if len(vals) and len(vals) >= 0.3 * pix.sum():
                    comp_of[j] = int(np.bincount(vals).argmax())
            by_comp: dict[int, list[int]] = {}
            for j, k in comp_of.items():
                by_comp.setdefault(k, []).append(j)
            for k, js in by_comp.items():
                u0, w, c = comp_profile(lab, along, excess, k)
                spans = {j: span(refs[j], lab, along, transform, k) for j in js}
                spans = {j: s for j, s in spans.items() if s}
                order = sorted(spans, key=lambda j: spans[j][0])
                for a, b in zip(order[:-1], order[1:]):
                    if refs[a].distance(refs[b]) > 0.05:
                        continue
                    cut = (spans[a][1] + spans[b][0]) / 2
                    i0, i1 = int((cut - 0.15 - u0) / STEP), int((cut + 0.15 - u0) / STEP) + 1
                    i = max(i0, 0) + int(np.argmin(w[max(i0, 0):max(i1, 1)]))
                    pa = w[int((spans[a][0] - u0) / STEP):i + 1].max()
                    pb = w[i:int((spans[b][1] - u0) / STEP) + 1].max()
                    ca = c[int((spans[a][0] - u0) / STEP):i + 1].max()
                    cb = c[i:int((spans[b][1] - u0) / STEP) + 1].max()
                    result["cut"].append({"tile": name, "depth": float(w[i] / max(min(pa, pb), 1e-6)), "colour": float(c[i] / max(min(ca, cb), 1e-6)),
                                          "width": float(w[i]), "len_a": spans[a][1] - spans[a][0], "len_b": spans[b][1] - spans[b][0]})
                for j in js:
                    if j in spans and spans[j][1] - spans[j][0] > 2.0:
                        ns = inside_necks(u0, w, c, *spans[j])
                        result["inside"].append({"tile": name, "length": spans[j][1] - spans[j][0], "deepest": ns[0][0] if ns else 1.0,
                                                 "colour": min((n[1] for n in ns), default=1.0), "width": ns[0][2] if ns else 0.0,
                                                 "n05": sum(n[0] < 0.5 for n in ns), "n04": sum(n[0] < 0.4 for n in ns), "necks": ns})
    labelled = labels()
    for name in sorted({lab["properties"]["tile"] for lab in labelled}):
        items = [lab for lab in labelled if lab["properties"]["tile"] == name]
        for lab, along, excess, transform in tile_profiles(tiles[name], rows):
            for item in items:
                g = shape(item["geometry"])
                pix = rasterize([g], out_shape=lab.shape, transform=transform).astype(bool)
                vals = lab[pix]
                vals = vals[vals > 0]
                if len(vals) < 0.3 * pix.sum():
                    continue
                k = int(np.bincount(vals).argmax())
                s = span(g, lab, along, transform, k)
                if not s:
                    continue
                u0, w, c = comp_profile(lab, along, excess, k)
                ns = inside_necks(u0, w, c, *s)
                result["labels"].append({"tile": name, "answer": item["properties"]["review_answer"], "length": s[1] - s[0],
                                         "deepest": ns[0][0] if ns else 1.0, "colour": min((n[1] for n in ns), default=1.0),
                                         "width": ns[0][2] if ns else 0.0, "n05": sum(n[0] < 0.5 for n in ns), "n04": sum(n[0] < 0.4 for n in ns), "necks": ns})
    (WORK / "refnecks.json").write_text(json.dumps(result))
    q = lambda v: " ".join(f"{x:5.2f}" for x in np.percentile(v, [10, 25, 50, 75, 90])) if len(v) else "-"
    for name in NAMES:
        cuts = [r for r in result["cut"] if r["tile"] == name]
        ins = [r for r in result["inside"] if r["tile"] == name]
        print(f"{name}: {len(cuts)} reference cuts inside one mask component; depth {q([r['depth'] for r in cuts])} | colour {q([r['colour'] for r in cuts])}")
        print(f"   {len(ins)} reference canopies > 2 m; deepest inside neck {q([r['deepest'] for r in ins])} | colour {q([r['colour'] for r in ins])}")
        for t in (0.3, 0.35, 0.4, 0.45, 0.5, 0.6):
            print(f"   split at depth < {t}: cuts reproduced {sum(r['depth'] < t for r in cuts)}/{len(cuts)}, reference canopies > 2 m cut {sum(r['deepest'] < t for r in ins)}/{len(ins)}")
    for answer in ["several", "one_plant", "not_vine"]:
        rs = [r for r in result["labels"] if r["answer"] == answer]
        print(f"labelled {answer}: {len(rs)}; deepest neck {q([r['deepest'] for r in rs])} | colour {q([r['colour'] for r in rs])}")
        for t in (0.3, 0.35, 0.4, 0.45, 0.5, 0.6):
            print(f"   depth < {t}: {sum(r['deepest'] < t for r in rs)}/{len(rs)}")


if __name__ == "__main__":
    main()
