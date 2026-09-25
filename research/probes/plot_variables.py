"""Which variables separate the hand-drawn plots, and where their edges sit. Evaluation only: it reads the
lab's plot outlines from data/review/, which prediction code must never do.
Run from backend/: uv run --frozen python ../research/probes/plot_variables.py"""

from collections import Counter

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage, stats
from shapely import contains_xy
from shapely.geometry import LineString, shape
from shapely.ops import unary_union

from marcaj import review
from marcaj.cvat import build_scene
from marcaj.layers import GREEN_EXG, LAYER_PX_M, exg, load_layers
from marcaj.mosaic import MOSAIC_PX_M, load_mosaic
from marcaj.routing import load_constraints
from marcaj.tiles import DATA_DIR, load_tiles

layers = load_layers()
rgb, mosaic_transform = load_mosaic()
excess, _ = exg(rgb)
shape_40 = layers.valid.shape
plots = [v for v in review.load_verdicts() if v["kind"] == "plot"]
outlines = {label: [shape(v["geometry"]) for v in plots if v["label"] == label] for label in sorted({v["label"] for v in plots})}
passages = unary_union([shape(f["geometry"]) for f in load_constraints(DATA_DIR / "02_route") if f["properties"].get("label") == "passage"])
print("outlines:", {label: len(polygons) for label, polygons in outlines.items()})


def mask(polygons):
    return rasterize(polygons, out_shape=shape_40, transform=layers.transform).astype(bool) if polygons else np.zeros(shape_40, bool)


def auc(a, b):
    return stats.mannwhitneyu(a, b).statistic / (len(a) * len(b))


# 1. separability: AUC of "vineyard scores higher"; 0.5 is useless, 0 or 1 is perfect
vineyard, anything = mask(outlines["vineyard"]), mask([p for polygons in outlines.values() for p in polygons])
inside = ndimage.distance_transform_edt(vineyard) * LAYER_PX_M
outside = ndimage.distance_transform_edt(~vineyard) * LAYER_PX_M
far = ndimage.distance_transform_edt(~anything) * LAYER_PX_M
groups = {"background": layers.valid & (far > 5), "orchard": mask(outlines.get("orchard", [])) & layers.valid,
          "overgrown": mask(outlines.get("overgrown", [])) & layers.valid}
core = vineyard & (inside >= 3) & layers.valid
rng = np.random.default_rng(0)
pick = lambda m: rng.choice(np.flatnonzero(m), min(20000, int(m.sum())), replace=False)
picks = {name: pick(m) for name, m in {"vineyard": core, **groups}.items()}
print("\n1. AUC, vineyard interior vs each class")
for name in ("vine_over_orchard", "orchard_energy", "green_share"):
    values = np.nan_to_num(getattr(layers, name)).ravel()
    print(f"  {name:18s} " + "  ".join(f"{group} {auc(values[picks['vineyard']], values[picks[group]]):.2f}" for group in groups))

# 2. edge profiles: green share by signed distance to the drawn edge, road sides apart
green = ndimage.zoom((excess > GREEN_EXG).astype(np.float32), 0.5, order=1)[: shape_40[0], : shape_40[1]]
road = mask([passages])
edge = vineyard ^ ndimage.binary_erosion(vineyard)
_, (rows, cols) = ndimage.distance_transform_edt(~edge, return_indices=True)
road_side = ndimage.binary_dilation(road, iterations=int(6 / LAYER_PX_M))[rows, cols]
signed = np.where(vineyard, -inside, outside)
usable = layers.valid & ~(anything & ~vineyard) & ~(road & ~vineyard)
print("\n2. green share across the drawn edge (m, negative = inside)")
for label, side in (("road side", road_side), ("no road", ~road_side)):
    cells = [(d, np.median(green[usable & side & (signed >= d - 0.4) & (signed < d + 0.4)])) for d in np.arange(-4, 6.1, 0.8)]
    print(f"  {label:9s} " + " ".join(f"{d:+.1f}:{g:.2f}" for d, g in cells))

# 3. outline geometry against the rows and the roads
def row_frame(polygon, angle, pad=0.0):
    c, s = np.cos(angle), np.sin(angle)
    origin = np.asarray(polygon.centroid.coords[0])
    xy = np.asarray(polygon.exterior.coords) - origin
    u, v = xy @ [c, s], xy @ [-s, c]
    uu, vv = np.meshgrid(np.arange(u.min() - pad, u.max() + pad, MOSAIC_PX_M), np.arange(v.min() - pad, v.max() + pad, MOSAIC_PX_M))
    x, y = origin[0] + uu * c - vv * s, origin[1] + uu * s + vv * c
    sampled = ndimage.map_coordinates(excess, [(mosaic_transform.f - y) / MOSAIC_PX_M - 0.5, (x - mosaic_transform.c) / MOSAIC_PX_M - 0.5], order=1)
    return vv[:, 0], sampled, contains_xy(polygon, x, y)


def row_angle(polygon):
    """Angle (degrees from east) whose across-row ExG profile has the strongest 2.0-3.6 m periodicity, and that spacing."""
    best = (0.0, 0.0, 0.0)
    for step, span in ((3, None), (0.5, 4), (0.1, 0.6)):
        for angle in np.arange(0, 180, step) if span is None else best[1] + np.arange(-span, span + 1e-9, step):
            _, sampled, within = row_frame(polygon, np.radians(angle))
            counts = within.sum(1)
            profile = ((sampled * within).sum(1) / np.maximum(counts, 1))[counts > 20]
            if len(profile) < 16:
                continue
            power = np.abs(np.fft.rfft((profile - profile.mean()) * np.hanning(len(profile)), 4096)) ** 2
            frequency = np.fft.rfftfreq(4096, MOSAIC_PX_M)
            power[(frequency < 1 / 3.6) | (frequency > 1 / 2.0)] = 0
            k = int(np.argmax(power))
            if power[k] / len(profile) > best[0]:
                best = (power[k] / len(profile), angle % 180, 1 / frequency[k])
    return best[1], best[2]


lengths, side_offsets, layer_angle_error = Counter(), [], []
for polygon in outlines["vineyard"]:
    angle, spacing = row_angle(polygon)
    for a, b in zip(polygon.exterior.coords[:-1], polygon.exterior.coords[1:]):
        line = LineString([a, b])
        off = abs((np.degrees(np.arctan2(b[1] - a[1], b[0] - a[0])) - angle + 90) % 180 - 90)
        strip = line.buffer(5, cap_style="flat")
        kind = "along" if off < 10 else "across" if off > 80 else "oblique"
        lengths[(kind, "road" if strip.intersection(passages).area > 0.15 * strip.area else "no road")] += line.length
    v, sampled, within = row_frame(polygon, np.radians(angle), pad=8)
    columns = np.flatnonzero(within.any(0))
    middle = slice(columns[0] + len(columns) // 5, columns[-1] - len(columns) // 5)
    profile, rows_in = sampled[:, middle].mean(1), np.flatnonzero(within[:, middle].any(1))
    w = int(0.35 * spacing / MOSAIC_PX_M)
    peaks = v[[i for i in range(w, len(profile) - w) if profile[i] == profile[i - w:i + w + 1].max() and profile[i] > np.percentile(profile[rows_in], 60)]]
    v0, v1 = v[rows_in[0]], v[rows_in[-1]]
    axes = peaks[(peaks >= v0 - spacing / 2) & (peaks <= v1 + spacing / 2)]
    if len(axes) >= 3:
        side_offsets += [axes.min() - v0, v1 - axes.max()]
    layer = layers.row_angle[mask([polygon]) & layers.valid]
    doubled = np.angle(np.exp(2j * np.radians(layer)).mean()) / 2
    layer_angle_error.append(abs((np.degrees(doubled) - angle + 90) % 180 - 90))
total = sum(lengths.values())
print("\n3. drawn edge length by direction to the rows:", {f"{k}/{r}": f"{v / total:.0%}" for (k, r), v in sorted(lengths.items())})
print(f"   side edge minus outermost row axis: median {np.median(side_offsets):+.2f} m, IQR {np.percentile(side_offsets, 25):+.2f}..{np.percentile(side_offsets, 75):+.2f} m")
print(f"   layer row_angle vs fitted row angle: median error {np.median(layer_angle_error):.1f} deg, max {np.max(layer_angle_error):.1f} deg")

# 4. canopy vs inter-row colour on the organizer's reference tiles (native 0.025 m)
tiles = {tile.name: tile for tile in load_tiles()}
reference = [f for f in build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(tiles.values()))["features"]
             if f["properties"].get("source") == "reference"]
print("\n4. canopy vs inter-row ExG on the reference tiles")
for name in ("siret3_r021_c012.tif", "siret3_r006_c004.tif"):
    tile = tiles[name]
    with rasterio.open(tile.path) as source:
        tile_exg, _ = exg(source.read())
        transform = source.transform
    bounds = tile.bounds
    area = lambda label: rasterize([shape(f["geometry"]).intersection(bounds) for f in reference if f["properties"]["label"] == label and shape(f["geometry"]).intersects(bounds)],
                                   out_shape=tile_exg.shape, transform=transform).astype(bool)
    canopy, interrow = area("vineyard"), area("interrow_area")
    interrow &= ~canopy
    domain = canopy | interrow
    iou = lambda t: ((tile_exg > t) & canopy).sum() / (((tile_exg > t) & domain) | canopy).sum()
    threshold = max(np.linspace(0.02, 0.2, 37), key=iou)
    a, b = rng.choice(tile_exg[canopy], 50000), rng.choice(tile_exg[interrow], 50000)
    print(f"  {name}: AUC {auc(a, b):.3f}, best ExG threshold {threshold:.3f}, pixel IoU {iou(threshold):.2f}")
