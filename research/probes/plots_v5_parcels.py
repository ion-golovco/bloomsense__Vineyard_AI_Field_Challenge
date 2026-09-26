"""Plots v5: row evidence of every cadastral parcel the current plots leave uncovered, with shrub-robust ExG (clipped),
labelled by the hand-drawn outline it lies on (evaluation only, reads data/review/) to find a rule that takes in the
overgrown vineyards and leaves grass, fields and orchards. Run from backend/:
uv run --frozen python ../research/probes/plots_v5_parcels.py [base=base.geojson]  (under data/generated/work/plots_v5)"""

import json
import sys

import numpy as np
from shapely.geometry import shape
from shapely.ops import unary_union

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from marcaj import plots  # noqa: E402
from marcaj.cadastre import load_parcels  # noqa: E402
from marcaj.review import load_verdicts  # noqa: E402
from plot_experiment import tile_footprint  # noqa: E402
from plots_v5_eval import WORK  # noqa: E402


class Clipped:
    def __init__(self, excess, lo, hi):
        self.values, self.transform = np.clip(excess.values, lo, hi), excess.transform

    at = plots._Excess.at


def spectrum(excess, polygon, angle):
    """Vine-band peak power, its spacing, orchard-band (3.6-8 m) max power and broad-band median, at this angle."""
    frame = plots._Frame(polygon, angle, 0.2)
    counts = frame.inside.sum(1)
    use = counts > 20
    if use.sum() < 16:
        return 0, 0, 0, 0
    profile = ((excess.at(frame.x, frame.y) * frame.inside).sum(1) / np.maximum(counts, 1))[use]
    power = np.abs(np.fft.rfft((profile - profile.mean()) * np.hanning(len(profile)), 4096)) ** 2 / len(profile)
    f = np.fft.rfftfreq(4096, 0.2)
    vine = (f >= 1 / 3.3) & (f <= 1 / 2.0)
    orchard = (f >= 1 / 8) & (f < 1 / 3.6)
    broad = (f >= 1 / 8) & (f <= 1 / 1.0)
    k = np.flatnonzero(vine)[np.argmax(power[vine])]
    return power[k], 1 / f[k], power[orchard].max(), np.median(power[broad])


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    base = json.loads((WORK / args.get("base", "base.geojson")).read_text())["features"]
    blocks = [(shape(f["geometry"]), f["properties"]) for f in base if f["properties"]["label"] == "block"]
    covered = unary_union([g for g, _ in blocks])
    footprint, roads = tile_footprint(), plots.exclusions()
    excess = plots.load_excess()
    clipped = Clipped(excess, float(args.get("lo", -0.05)), float(args.get("hi", 0.12)))
    outlines = [(v["label"], shape(v["geometry"])) for v in load_verdicts() if v["kind"] == "plot"]
    rows = []
    for parcel in load_parcels():
        region = plots._largest(parcel.intersection(footprint).difference(roads.buffer(1.0)))
        if region.area < 300 or region.intersection(covered).area > 0.5 * region.area:
            continue
        near = sorted(((g.distance(region), p["row_angle"], p["row_spacing_m"]) for g, p in blocks))
        if args.get("mode") == "lattice":
            if not near or near[0][0] > float(args.get("reach", 30)):
                continue
            _, a0, s0 = near[0]
            best = None
            for a in a0 + np.arange(-3, 3.1, 1.0):
                pv, sp, po, pb = spectrum(clipped, region, a)
                null = spectrum(clipped, region, a + 90)
                wave = max(plots._wave(clipped, region, a, s) for s in s0 * np.arange(0.94, 1.061, 0.02))
                cand = (wave, a, sp, pv / max(pb, 1e-12), pv / max(po, 1e-12), null[0] / max(null[3], 1e-12))
                best = max(best or cand, cand)
            label = max(((g.intersection(region).area / region.area, l) for l, g in outlines), default=(0, "none"))
            wave, a, sp, snr, vo, nsnr = best
            rows.append({"x": round(region.centroid.x), "y": round(region.centroid.y), "m2": round(region.area),
                         "label": label[1] if label[0] >= 0.5 else "none", "share": round(label[0], 2), "near_m": round(near[0][0], 1),
                         "angle": round(a % 180, 1), "s0": s0, "spacing": round(sp, 2), "snr": round(snr, 1), "null": round(nsnr, 1),
                         "vo": round(vo, 2), "wave": round(wave, 4)})
            continue
        # best angle on the clipped ExG, 3 degree steps, then 0.5
        scan = [(spectrum(clipped, region, a), a) for a in np.arange(0, 180, 3.0)]
        (pv, sp, po, pb), angle = max(scan, key=lambda t: t[0][0] / max(t[0][3], 1e-12))
        for a in angle + np.arange(-2.5, 2.6, 0.5):
            s = spectrum(clipped, region, a)
            if s[0] / max(s[3], 1e-12) > pv / max(pb, 1e-12):
                (pv, sp, po, pb), angle = s, a
        near = min(((g.distance(region), p["row_angle"]) for g, p in blocks), default=(1e9, 0))
        label = max(((g.intersection(region).area / region.area, l) for l, g in outlines), default=(0, "none"))
        wave = plots._wave(clipped, region, angle, sp)
        rows.append({"x": round(region.centroid.x), "y": round(region.centroid.y), "m2": round(region.area),
                     "label": label[1] if label[0] >= 0.5 else "none", "share": round(label[0], 2),
                     "angle": round(angle % 180, 1), "spacing": round(sp, 2), "snr": round(pv / max(pb, 1e-12), 1),
                     "vo": round(pv / max(po, 1e-12), 2), "wave": round(wave, 4),
                     "near_m": round(near[0], 1), "near_dangle": round(abs((near[1] - angle + 90) % 180 - 90), 1)})
    (WORK / f"parcels_{args.get('mode', 'free')}.json").write_text(json.dumps(rows, indent=0))
    for r in sorted(rows, key=lambda r: -r["snr"]):
        print(r)


if __name__ == "__main__":
    main()
