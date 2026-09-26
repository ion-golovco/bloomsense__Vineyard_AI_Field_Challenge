"""Why canopy is missing on the central fields (V19-11, V21-13, V22-13; tiles r019_c011, r021_c013, r022_c013, r020_c012,
and the organizer tile r021_c012 of V21-13 for comparison): the vine colour within 0.6 m of the frozen v4 axes that the
canopy does not cover, by the step that loses it. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v2_fields.py"""

import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import frozen_plots, prob_path  # noqa: E402

from marcaj import canopy, canopy_net  # noqa: E402
from marcaj.canopy import EIGHT, CanopyParams, excess_green, kept_axes, plot_rows, row_spacing, tube  # noqa: E402
from marcaj.tiles import PIXEL_M, load_tiles  # noqa: E402

TILES = ["siret3_r019_c011.tif", "siret3_r021_c013.tif", "siret3_r022_c013.tif", "siret3_r020_c012.tif", "siret3_r021_c012.tif"]
P = CanopyParams()


def main() -> None:
    tiles = {t.name: t for t in load_tiles()}
    rows = plot_rows(frozen_plots())
    m2 = lambda a: a.sum() * PIXEL_M**2
    for name in TILES:
        tile = tiles[name]
        rgb, transform = canopy.read_rgb(tile)
        prob = np.load(prob_path(name)).astype(np.float32) / 255.0
        excess, valid = excess_green(rgb, P)
        colour = (excess > P.green_dn) & valid
        net = prob > 0.2
        crossing = [(p, [a for a in p.axes if a.intersects(tile.bounds)]) for p in rows]
        crossing = [(p, a) for p, a in crossing if a]
        near_all = tube([a for _, axes in crossing for a in axes], transform, colour.shape, 0.6)
        kept_all, masks, polys = [], np.zeros_like(colour), []
        for plot, axes in crossing:
            green = colour & net
            kept = kept_axes(axes, green, transform, P, row_spacing(plot.axes), excess)
            kept_all += kept
            mask = canopy.canopy_mask(rgb, transform, axes, P, green, row_spacing(plot.axes))
            masks |= mask
            polys += canopy.canopy_polygons(mask, transform, plot.angle_deg, P)
        from rasterio.features import rasterize
        final = rasterize(polys, out_shape=colour.shape, transform=transform).astype(bool) if polys else np.zeros_like(colour)
        band = tube(kept_all, transform, colour.shape, P.tube_m)
        band5 = tube(kept_all, transform, colour.shape, 0.6)
        target = colour & near_all
        lost = target & ~final
        cats = {
            "axis dropped (no kept axis within 0.6 m)": lost & ~band5,
            "beside the kept axis, 0.3-0.6 m": lost & band5 & ~band,
            "network rejected (in tube)": lost & band & ~net,
            "small pieces / strip rules (in tube, net ok)": lost & band & net,
        }
        weak = (excess > 15) & (excess <= P.green_dn) & valid & band & ~final
        print(f"{name}: vine colour within 0.6 m of the axes {m2(target):.0f} m2, covered {m2(target & final) / max(m2(target), 1e-9):.2f}; "
              f"canopy {m2(final):.0f} m2; weak colour (DN 15-25) in the tube not covered {m2(weak):.0f} m2")
        for k, v in cats.items():
            print(f"   {k:46s} {m2(v):6.1f} m2")
        # per-row cover along kept axes (share of 5 cm steps with canopy within 0.3 m)


if __name__ == "__main__":
    main()
