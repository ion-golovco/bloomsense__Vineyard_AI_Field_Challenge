"""Which necks are plant boundaries? Every local minimum of the along-row width profile of each connected canopy
component (as `canopy._split` sees it), labelled true when the reference plant majority differs on its two sides.
Prints the feature distributions for true and false necks and the cut rules' precision / recall. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_rules_necks.py"""

import numpy as np
from scipy import ndimage

from canopy_rules_lib import NAMES, images, predicted_rows, ref_objects, tiles
from canopy_rules_trials import dn, lattice_mask
from marcaj.canopy import EIGHT, CanopyParams, _along
from marcaj.tiles import PIXEL_M
from rasterio.features import rasterize

P = CanopyParams()
STEP = 2 * PIXEL_M
rows_out = []
for name in NAMES:
    rgb, transform = images[name]
    value = dn(rgb)
    ref = ref_objects(name, "vineyard")
    ref_labels = rasterize([(r, k + 1) for k, r in enumerate(ref)], out_shape=(2048, 2048), transform=transform).astype(np.int32)
    for plot in predicted_rows:
        axes = [a for a in plot.axes if a.intersects(tiles[name].bounds)]
        if not axes:
            continue
        mask = lattice_mask(rgb, transform, axes, P, value > 25, value, contrast_min=0)
        along = _along(transform, mask.shape, plot.angle_deg)
        labels, count = ndimage.label(mask, EIGHT)
        for index, window in enumerate(ndimage.find_objects(labels), start=1):
            m = labels[window] == index
            u = along[window][m]
            bins = ((u - u.min()) / STEP).astype(int)
            raw = np.bincount(bins).astype(float)
            width = ndimage.gaussian_filter1d(raw, 1.0)
            refs = ref_labels[window][m]
            vals = value[window][m]
            n = len(width)
            for i in range(1, n - 1):
                if not (width[i] <= width[i - 1] and width[i] <= width[i + 1]):
                    continue
                left, right = width[:i].max(), width[i + 1:].max()
                depth = width[i] / min(left, right)
                side = int(0.3 / STEP)
                lo = refs[(bins >= i - side) & (bins < i) & (refs > 0)]
                hi = refs[(bins > i) & (bins <= i + side) & (refs > 0)]
                if not len(lo) or not len(hi):
                    kind = "unknown"
                else:
                    kind = "true" if np.bincount(lo).argmax() != np.bincount(hi).argmax() else "false"
                near = vals[(bins >= i - 1) & (bins <= i + 1)]
                rows_out.append((name, kind, depth, width[i] * PIXEL_M**2 / STEP, i * STEP, (n - i) * STEP,
                                 float(np.mean(near)) if len(near) else 0.0, left * PIXEL_M**2 / STEP, right * PIXEL_M**2 / STEP))

kinds = np.array([r[1] for r in rows_out])
depth = np.array([r[2] for r in rows_out])
neck_w = np.array([r[3] for r in rows_out])  # across-row width at the neck, m
piece = np.minimum(np.array([r[4] for r in rows_out]), np.array([r[5] for r in rows_out]))
val = np.array([r[6] for r in rows_out])
peak = np.minimum(np.array([r[7] for r in rows_out]), np.array([r[8] for r in rows_out]))
tile = np.array([r[0][7:16] for r in rows_out])
for t in ("r021_c012", "r006_c004"):
    sel = tile == t
    print(f"{t}: necks {sel.sum()}, true {(sel & (kinds == 'true')).sum()}, false {(sel & (kinds == 'false')).sum()}, unknown {(sel & (kinds == 'unknown')).sum()}")
for label, x in (("depth", depth), ("neck width m", neck_w), ("shorter side m", piece), ("mean DN at neck", val), ("thinner peak width m", peak)):
    q = lambda k: np.percentile(x[kinds == k], [10, 25, 50, 75, 90]).round(3)
    print(f"{label:22s} true {q('true')}  false {q('false')}")
print("\nrule: cut where depth < d and shorter side >= 0.3 m (current rule is d = 0.3)")
for d in (0.2, 0.3, 0.4, 0.5, 0.6):
    for w in (None, 0.10, 0.15, 0.2):
        cut = (depth < d) & (piece >= 0.3)
        if w is not None:
            cut |= (neck_w < w) & (piece >= 0.3)
        tp, fp = (cut & (kinds == "true")).sum(), (cut & (kinds == "false")).sum()
        fn = (~cut & (kinds == "true")).sum()
        print(f"  depth<{d} {'or neck<' + str(w) + ' m' if w else '':14s}: cuts true {tp}, false {fp}, missed true {fn}")
