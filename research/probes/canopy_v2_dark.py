"""Dark-green foliage: does a colour space that separates green hue from darkness add organizer canopy the DN index
misses? Pixels within 0.4 m of the reference rows on the two organizer tiles (r021_c012 is part of field V21-13), split
into reference canopy / not, and our current canopy (canopies_defaults.json) / not. For candidate rules it prints how
many missed reference pixels a rule adds and at what precision, anywhere in the band and only next to our canopy
(within 5 cm, the closing radius). Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v2_dark.py"""

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import NAMES, WORK  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.cvat import read_cvat  # noqa: E402
from marcaj.tiles import DATA_DIR, PIXEL_M, load_tiles  # noqa: E402


def lab(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """CIELAB (D65) of 8-bit sRGB, (3, H, W) -> L, a, b."""
    c = rgb.astype(np.float32) / 255
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
    xyz = np.tensordot(m, c, 1) / np.array([0.9505, 1.0, 1.089], np.float32)[:, None, None]
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def main() -> None:
    tiles = {t.name: t for t in load_tiles()}
    with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
        xml = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
    ref = read_cvat(xml, tiles)
    preds = [shape(f["geometry"]) for f in json.loads((WORK / "canopies_defaults.json").read_text()) if f["properties"]["tile_run"] in NAMES]
    for name in NAMES:
        tile = tiles[name]
        rgb, transform = canopy.read_rgb(tile)
        inside = lambda g: tile.bounds.contains(g.representative_point())
        rows = [shape(f["geometry"]) for f in ref if f["properties"]["label"] == "row" and shape(f["geometry"]).intersects(tile.bounds)]
        cans = [shape(f["geometry"]) for f in ref if f["properties"]["label"] == "vineyard" and inside(shape(f["geometry"]))]
        band = rasterize([r.buffer(0.4) for r in rows], out_shape=rgb.shape[1:], transform=transform).astype(bool)
        truth = rasterize(cans, out_shape=rgb.shape[1:], transform=transform).astype(bool)
        ours = rasterize([p for p in preds if p.intersects(tile.bounds)], out_shape=rgb.shape[1:], transform=transform).astype(bool)
        r, g, b = rgb.astype(np.float32)
        dn = ndimage.gaussian_filter(2 * g - r - b, 2)
        bright = (r + g + b) / 3
        L, A, B = lab(rgb)
        A = ndimage.gaussian_filter(A, 2)
        chroma_g = g / np.maximum(r + g + b, 1)
        near = ndimage.binary_dilation(ours, iterations=2)
        miss = truth & ~ours & band
        extra = ~truth & ~ours & band
        print(f"{name}: band {band.sum() * PIXEL_M**2:.0f} m2, reference {truth[band].sum() * PIXEL_M**2:.1f} m2, ours {ours[band].sum() * PIXEL_M**2:.1f} m2, "
              f"missed reference {miss.sum() * PIXEL_M**2:.1f} m2 ({(miss & near).sum() * PIXEL_M**2:.1f} within 5 cm of ours), "
              f"our pixels outside the reference {(ours & ~truth & band).sum() * PIXEL_M**2:.1f} m2")
        for title, sel in (("missed reference", miss), ("non-canopy band", extra), ("our TP", ours & truth)):
            q = lambda v: " ".join(f"{x:6.1f}" for x in np.percentile(v[sel], [10, 25, 50, 75, 90]))
            print(f"   {title:17s} DN {q(dn)} | bright {q(bright)} | a* {q(A)} | g share {q(chroma_g * 100)}")
        rules = {f"a* < {t}": A < t for t in (-6, -8, -10, -12)}
        rules.update({f"a* < {t} & dn > {d}": (A < t) & (dn > d) for t in (-6, -8) for d in (10, 15, 20)})
        rules.update({f"dn > {d}": dn > d for d in (15, 20, 22)})
        rules.update({f"dark (bright < 70) & g share > {s}": (bright < 70) & (chroma_g > s) for s in (0.36, 0.38, 0.40)})
        for title, rule in rules.items():
            add = rule & ~ours & band
            addn = add & near
            print(f"   {title:32s} adds {add.sum() * PIXEL_M**2:6.1f} m2 at precision {truth[add].mean() if add.any() else 0:.2f} | "
                  f"next to ours {addn.sum() * PIXEL_M**2:6.1f} m2 at {truth[addn].mean() if addn.any() else 0:.2f} "
                  f"(gets {(addn & truth).sum() / max(miss.sum(), 1):.2f} of the missed reference)")


if __name__ == "__main__":
    main()
