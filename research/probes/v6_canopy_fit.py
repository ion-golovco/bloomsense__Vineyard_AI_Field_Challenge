"""v6: variants of the canopy axis refit (`canopy._line_shift`), monkeypatched: distance of the fitted axes to the
organizers' rows and the judge canopy on the two example tiles, from the team's reference rows. Evaluation only.
Run from backend/: uv run --frozen --group sam python ../research/probes/v6_canopy_fit.py"""

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402
from v6_canopy_axes import distance_to, org_sets  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.canopy import CanopyParams  # noqa: E402

D = CanopyParams()
ORIGINAL = canopy._line_shift


def make(stat: str = "median", weighted: bool = False, robust: float = 0.0, min_px: int = 20, bin_override: float = 0.0, power: float = 1.0):
    def shift(u, v, g, limit, length, bin_m):
        bin_m = bin_override or bin_m
        near = g & (np.abs(v) <= limit)
        bins = np.floor(u[near] / bin_m).astype(int)
        keys, counts = np.unique(bins, return_counts=True)
        ok = counts >= min_px
        keys, counts = keys[ok], counts[ok]
        if not len(keys):
            return np.zeros(2)
        vv = v[near]
        if stat == "median":
            centres = np.array([np.median(vv[bins == k]) for k in keys])
        elif stat == "mean":
            centres = np.array([np.mean(vv[bins == k]) for k in keys])
        else:  # midrange of the 10-90 percentiles
            centres = np.array([np.mean(np.percentile(vv[bins == k], [10, 90])) for k in keys])
        x = (keys + 0.5) * bin_m
        w = counts.astype(float) ** power if weighted else np.ones(len(keys))
        if len(keys) >= 3:
            slope, offset = np.polyfit(x, centres, 1, w=np.sqrt(w))
            if robust:
                for _ in range(2):
                    r = centres - (offset + slope * x)
                    s = 1.4826 * np.median(np.abs(r)) + 1e-3
                    keep = np.abs(r) <= robust * s
                    if keep.sum() >= 3:
                        slope, offset = np.polyfit(x[keep], centres[keep], 1, w=np.sqrt(w[keep]))
        else:
            slope, offset = 0.0, float(np.median(centres))
        return np.clip(offset + slope * np.array([0.0, length]), -limit, limit)
    return shift


VARIANTS = {
    "weighted": make(weighted=True),
    "w_bin0.5": make(weighted=True, bin_override=0.5, min_px=10),
    "w_bin2": make(weighted=True, bin_override=2.0),
    "w_pow2": make(weighted=True, power=2.0),
    "w_mean": make("mean", weighted=True),
    "w_min40": make(weighted=True, min_px=40),
}
_OLD = {
    "current": ORIGINAL,
    "weighted": make(weighted=True),
    "robust2.5": make(robust=2.5),
    "robust2.5w": make(weighted=True, robust=2.5),
    "mean": make("mean"),
    "mid1090": make("mid"),
    "robust2": make(robust=2.0),
}

if __name__ == "__main__":
    plots = L.plot_features()
    team = L.rowsets(plots)
    spacing = L.spacing_of(plots)
    _, refs = org_sets()
    for name, fn in VARIANTS.items():
        canopy._line_shift = fn
        ds = []
        for n in L.NAMES:
            tile = L.TILES[n]
            img, tr = canopy.read_rgb(tile)
            g = canopy.green_mask(img, D)
            tref = [r.intersection(tile.bounds) for r in refs]
            tref = [r for r in tref if r.geom_type == "LineString" and r.length > 1]
            for s in team:
                axes = [a for a in s.axes if a.intersects(tile.bounds)]
                if len(axes) >= 5:
                    ds.append(distance_to(canopy.kept_axes(axes, g, tr, replace(D, row_gap=0, row_value=0, grass_ratio=0), 0, None, None), tref))
        d = np.concatenate(ds)
        found = [f for n in L.NAMES for f in L.tile_canopies(n, D, team, spacing)]
        j = L.judge_canopy(found)
        print(f"{name:12s} axis median {np.median(d) * 100:.2f} cm mean {d.mean() * 100:.2f} p90 {np.percentile(d, 90) * 100:.2f} | "
              f"canopy {j['canopy']} (IoU {j['iou']} F1 {j['f1']})", flush=True)
