"""Colour indices for canopy pixels inside the +-0.3 m tube of the reference rows (isolates colour from row error):
AUC per tile, best threshold per tile, and pixel IoU on both tiles at one shared threshold. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_rules_colour.py"""

import numpy as np
from scipy import ndimage

from canopy_rules_lib import NAMES, images, raster, ref_objects
from marcaj.canopy import tube
from marcaj.tiles import PIXEL_M


def lab_a(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """CIE a* and b* of sRGB (D65)."""
    def lin(c):
        c = c / 255.0
        return np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    R, G, B = lin(r), lin(g), lin(b)
    x = (0.4124 * R + 0.3576 * G + 0.1805 * B) / 0.95047
    y = 0.2126 * R + 0.7152 * G + 0.0722 * B
    z = (0.0193 * R + 0.1192 * G + 0.9505 * B) / 1.08883
    f = lambda t: np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16 / 116)
    return 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def features(rgb: np.ndarray) -> dict[str, np.ndarray]:
    r, g, b = rgb.astype(np.float32)
    total = np.maximum(r + g + b, 1)
    a, bb = lab_a(r, g, b)
    mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    hue = np.where(mx == mn, 0, np.where(mx == g, 60 * ((b - r) / np.maximum(mx - mn, 1)) + 120, np.where(mx == r, 60 * ((g - b) / np.maximum(mx - mn, 1)) % 360, 60 * ((r - g) / np.maximum(mx - mn, 1)) + 240)))
    exg = (2 * g - r - b) / total
    exr = (1.4 * r - g) / total
    return {
        "ExG": exg, "ExG abs (DN)": 2 * g - r - b, "ExG-ExR": exg - exr, "-CIVE": -(0.441 * r - 0.811 * g + 0.385 * b + 18.787),
        "VARI": (g - r) / np.where(np.abs(g + r - b) < 1, 1, g + r - b), "GLI": (2 * g - r - b) / np.maximum(2 * g + r + b, 1),
        "NGRDI": (g - r) / np.maximum(g + r, 1), "-a* (Lab)": -a, "b* (Lab)": bb, "-a*+b*/2": -a + bb / 2, "hue (HSV)": -np.abs(hue - 90),
        "saturation": (mx - mn) / np.maximum(mx, 1), "g-b": g - b, "g-r": g - r, "brightness": total / 3,
        "ExG x brightness^0.5": exg * np.sqrt(total / 3),
    }


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    values = np.concatenate([pos, neg])
    ranks = np.argsort(np.argsort(values)).astype(np.float64) + 1
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


data = {}
for name in NAMES:
    rgb, transform = images[name]
    band = tube(ref_objects(name, "row"), transform, (2048, 2048), 0.3)
    truth = raster(name, ref_objects(name, "vineyard"))
    feats = {k: ndimage.gaussian_filter(v, 0.05 / PIXEL_M) for k, v in features(rgb).items()}
    data[name] = (band, truth, feats)

print("index | per tile: AUC, best threshold, best pixel IoU in the tube | shared threshold, IoU r021, IoU r006, mean")
for key in data[NAMES[0]][2]:
    line, samples = [f"{key:22s}"], {}
    for name in NAMES:
        band, truth, feats = data[name]
        v, t = feats[key][band], truth[band]
        rng = np.random.default_rng(0)
        pick = rng.choice(len(v), 200000, replace=False)
        a = auc(v[pick][t[pick]], v[pick][~t[pick]])
        grid = np.quantile(v, np.linspace(0.3, 0.99, 140))
        ious = np.array([(t & (v > g)).sum() / (t | (v > g)).sum() for g in grid])
        samples[name] = (v, t)
        line.append(f"{a:.3f} {grid[ious.argmax()]:8.3f} {ious.max():.3f}")
    allv = np.concatenate([samples[n][0] for n in NAMES])
    grid = np.quantile(allv, np.linspace(0.3, 0.99, 140))
    both = np.array([[(t & (v > g)).sum() / (t | (v > g)).sum() for g in grid] for v, t in samples.values()])
    k = both.mean(axis=0).argmax()
    line.append(f"shared {grid[k]:8.3f} {both[0, k]:.3f} {both[1, k]:.3f} {both[:, k].mean():.3f}")
    print(" | ".join(line), flush=True)

print("\nExG with a brightness floor (shadow cut): ExG > t and brightness > b")
for t in (0.08, 0.10, 0.11, 0.12):
    for bmin in (0, 30, 40, 50, 60, 70):
        out = []
        for name in NAMES:
            band, truth, feats = data[name]
            m = (feats["ExG"] > t) & (feats["brightness"] > bmin)
            out.append(((truth & m & band).sum() / ((truth | m) & band).sum()))
        print(f"  t {t:.2f} b {bmin:3d}: IoU {out[0]:.3f} {out[1]:.3f} mean {np.mean(out):.3f}")
