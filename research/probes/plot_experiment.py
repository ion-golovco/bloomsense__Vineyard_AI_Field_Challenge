"""Plot-detector experiments against the hand-drawn outlines, without touching `marcaj.plots` until a change wins.
Evaluation only: scores read data/review/. Run from backend/:
uv run --frozen python ../research/probes/plot_experiment.py [name=value ...] [layers=|extra=file.npz extra_ratio=8] [out=f.geojson]
(PlotParams or GrowParams fields; a layer file is under data/generated/work/plots/, see plot_layers_variant.py)"""

import json
import sys
import time
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

import numpy as np
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import unary_union

from marcaj import plots
from marcaj.judge import plot_scores
from marcaj.layers import LAYER_PX_M, load_layers
from marcaj.mosaic import MOSAIC_PX_M
from marcaj.review import load_verdicts
from marcaj.tiles import DATA_DIR, REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots"


@dataclass(frozen=True)
class GrowParams:
    k: float = 0.0          # grow where the in-phase lattice amplitude exceeds k x the seed's median; 0 = off
    along_m: float = 1.5    # demodulation smoothing along the rows
    across_m: float = 1.5   # ...and across them
    pad_m: float = 40.0     # how far beyond the seed the plot may grow
    clean_m: float = 1.0    # opening radius that cuts thin bridges out of the grown mask
    phase: bool = False     # require the lattice in phase with the seed (else amplitude only)
    ratio: float = 0.0      # ...and where the layer's vine-over-orchard ratio exceeds this (orchards sit below 0.5)


def tile_footprint():
    names = [p.stem for p in (DATA_DIR / "tiles").glob("siret3_r*_c*.tif")]
    return unary_union([box(628992.0 + 51.2 * c, 5221222.4 - 51.2 * (r + 1), 628992.0 + 51.2 * (c + 1), 5221222.4 - 51.2 * r)
                        for r, c in ((int(n[8:11]), int(n[13:16])) for n in names)])


def scores(features: list[dict[str, Any]], verdicts: list[dict[str, Any]], footprint) -> dict[str, Any]:
    """judge.plot_scores as is, and with every outline clipped to the imagery (several run past the tiles)."""
    clipped = [{**v, "geometry": mapping(plots._largest(shape(v["geometry"]).intersection(footprint)))} if v["kind"] == "plot" else v for v in verdicts]
    return {"as_drawn": plot_scores(features, verdicts), "clipped": plot_scores(features, clipped)}


def _valid_sampler(excess: plots._Excess, layers) -> Any:
    def at(x: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
        t = layers.transform
        return ndimage.map_coordinates(grid.astype(np.uint8), [(t.f - y) / LAYER_PX_M - 0.5, (x - t.c) / LAYER_PX_M - 0.5], order=0, cval=0).astype(bool)
    return at


def grow_region(excess, region: Polygon, angle: float, spacing: float, usable_at, ratio_at, g: GrowParams) -> Polygon:
    """The seed's planting followed outward by complex demodulation at the seed's own row spacing and angle:
    kept where the local lattice is in phase with the seed and at least `k` times its median amplitude."""
    frame = plots._Frame(region, angle, MOSAIC_PX_M, pad=g.pad_m)
    values = excess.at(frame.x, frame.y)
    usable = usable_at(frame.x, frame.y)
    carrier = np.exp(-2j * np.pi * frame.v / spacing)[:, None]
    sigma = (g.across_m / MOSAIC_PX_M, g.along_m / MOSAIC_PX_M)
    weight = ndimage.gaussian_filter(usable.astype(np.float32), sigma)
    signal = np.where(usable, values, 0) * carrier
    z = (ndimage.gaussian_filter(signal.real, sigma) + 1j * ndimage.gaussian_filter(signal.imag, sigma)) / np.maximum(weight, 1e-3)
    seed = frame.inside & usable
    if seed.sum() < 50:
        return region
    # a linear phase over the seed absorbs small errors in the fitted angle and spacing
    dv = np.angle((z[1:] * np.conj(z[:-1]))[seed[1:] & seed[:-1]].sum())
    du = np.angle((z[:, 1:] * np.conj(z[:, :-1]))[seed[:, 1:] & seed[:, :-1]].sum())
    iv, iu = np.indices(z.shape)
    ramp = np.exp(-1j * (dv * iv + du * iu))
    phase0 = np.angle((z * ramp)[seed].sum())
    inphase = (z * ramp * np.exp(-1j * phase0)).real if g.phase else np.abs(z)
    level = np.median(inphase[seed])
    if level <= 0:
        return region
    coherent = (inphase > g.k * level) & usable & (weight > 0.5)
    if g.ratio:
        coherent &= ratio_at(frame.x, frame.y) > g.ratio
    radius = max(1, round(g.clean_m / MOSAIC_PX_M))
    coherent = ndimage.binary_opening(coherent, iterations=radius)
    labels, _ = ndimage.label(coherent)
    keep = np.unique(labels[seed & coherent])
    grown = ndimage.binary_fill_holes(np.isin(labels, keep[keep > 0]) | seed)
    if not grown.any():
        return region
    c, s = np.cos(frame.angle), np.sin(frame.angle)
    u0, v0 = frame.u[0] - MOSAIC_PX_M / 2, frame.v[0] - MOSAIC_PX_M / 2
    ox, oy = frame.origin
    transform = Affine(MOSAIC_PX_M * c, -MOSAIC_PX_M * s, ox + u0 * c - v0 * s, MOSAIC_PX_M * s, MOSAIC_PX_M * c, oy + u0 * s + v0 * c)
    polygons = [shape(geometry) for geometry, value in shapes(grown.astype(np.uint8), mask=grown, transform=transform) if value]
    return plots._largest(unary_union(polygons).buffer(0))


def detect(params: plots.PlotParams, grow: GrowParams, layers, excess, roads, blocked, extra=None) -> list[dict[str, Any]]:
    """`marcaj.plots.detect_plots` with two options that were tried and not kept: pixel growth before the fit, and
    extra seeds from a second layer (e.g. the top-hat variant) at their own ratio threshold."""
    usable_grid = layers.valid & ~blocked
    sample = _valid_sampler(excess, layers)
    usable_at = lambda x, y: sample(x, y, usable_grid)
    t = layers.transform
    ratio_at = lambda x, y: ndimage.map_coordinates(layers.vine_over_orchard, [(t.f - y) / LAYER_PX_M - 0.5, (x - t.c) / LAYER_PX_M - 0.5], order=1, cval=0)
    fitted = []
    regions = plots._regions(layers, blocked, params)
    if extra is not None:
        extra_layers, extra_ratio = extra
        regions += plots._regions(extra_layers, blocked, replace(params, ratio_min=extra_ratio))
    for region in regions:
        angle, spacing = plots._row_angle(excess, region)
        if not spacing or plots._wave(excess, region, angle, spacing) < params.min_wave_exg:
            continue
        if grow.k:
            region = grow_region(excess, region, angle, spacing, usable_at, ratio_at, grow)
            angle, spacing = plots._row_angle(excess, region)
            if not spacing:
                continue
        result = plots._walk(excess, region, angle, spacing, usable_at, params)
        if result is None:
            continue
        quad, axes = result
        fitted.append((plots._largest(plots._onto_roads(quad, roads, params)), angle, spacing, axes))
    if params.merge_m:
        fitted = plots._merge(fitted, excess, roads, params)
    chosen, taken = [], Polygon()
    for polygon, angle, spacing, axes in sorted(fitted, key=lambda item: -item[0].area):
        if polygon.intersection(taken).area > 0.5 * polygon.area:
            continue
        polygon = plots._largest(polygon.difference(taken))
        if polygon.area < params.min_area_m2:
            continue
        taken = taken.union(polygon)
        rows = [plots._longest(axis.intersection(polygon.buffer(0.05, join_style="mitre"))) for axis in axes]
        chosen.append((polygon, angle, spacing, [row for row in rows if row.length >= params.min_row_m]))
    features = []
    for number, (polygon, angle, spacing, rows) in enumerate(chosen, start=1):
        plot_id = f"P{number:02d}"
        features.append({"type": "Feature", "geometry": mapping(polygon),
                         "properties": {"label": "block", "vineyard_id": plot_id, "area_m2": round(polygon.area), "row_angle": round(angle, 1),
                                        "row_spacing_m": round(spacing, 2), "rows": len(rows)}})
        features += [{"type": "Feature", "geometry": mapping(row),
                      "properties": {"label": "row", "vineyard_id": plot_id, "row_id": f"{plot_id}-R{k:03d}", "row_structure": "regular", "length_m": round(row.length, 2)}}
                     for k, row in enumerate(rows, start=1)]
    return features


def _fmt(report: dict[str, Any]) -> str:
    return " || ".join(f"{half[0].upper()} n{s['predicted']:2d} F1 {s['f1_50']:.3f}/{s['f1_75']:.3f} med {s['median_best_iou']:.3f} area {s['area_iou']:.3f} false {s['false_m2']:5.0f} orch {s['on_orchard_m2']:4.0f}"
                       for half, s in report.items())


def main() -> None:
    changes = dict(a.split("=") for a in sys.argv[1:] if "=" in a and not a.startswith(("out=", "layers=")))
    layers_path = next((WORK / a[7:] for a in sys.argv[1:] if a.startswith("layers=")), None)
    extra_path = next((WORK / a[6:] for a in sys.argv[1:] if a.startswith("extra=")), None)
    changes = {k: v for k, v in changes.items() if k not in ("extra", "extra_ratio")}
    extra_ratio = float(next((a[12:] for a in sys.argv[1:] if a.startswith("extra_ratio=")), "8"))
    out = next((a[4:] for a in sys.argv[1:] if a.startswith("out=")), "")
    names = {f.name for f in fields(plots.PlotParams)}
    cast = lambda obj, k, v: type(getattr(obj, k))(v)
    params = replace(plots.PlotParams(), **{k: cast(plots.PlotParams(), k, v) for k, v in changes.items() if k in names})
    grow = replace(GrowParams(), **{k: cast(GrowParams(), k, v) for k, v in changes.items() if k not in names})
    layers = load_layers(path=layers_path) if layers_path else load_layers()
    excess, roads = plots.load_excess(), plots.exclusions()
    blocked = rasterize([roads], out_shape=layers.valid.shape, transform=layers.transform).astype(bool)
    started = time.perf_counter()
    features = detect(params, grow, layers, excess, roads, blocked, (load_layers(path=extra_path), extra_ratio) if extra_path else None)
    elapsed = time.perf_counter() - started
    report = scores(features, load_verdicts(), tile_footprint())
    print(f"{changes or 'baseline'} {elapsed:.1f} s")
    for kind, value in report.items():
        print(f"  {kind:8s} {_fmt(value)}")
    if out:
        (WORK / out).write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features,
                                            "params": {**asdict(params), **asdict(grow)}}))


if __name__ == "__main__":
    main()
