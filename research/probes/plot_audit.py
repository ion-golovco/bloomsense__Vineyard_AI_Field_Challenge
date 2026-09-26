"""Audit of the hand-drawn plot outlines: what the user may have missed or drawn inconsistently. Evaluation only
(reads data/review/). Lists, each with a number, coordinates and tile, and draws them on a contact sheet:
- row-periodic areas (vine-band seeds, and predicted plots) that no outline covers,
- outlines that overlap, cross a passage, run past the imagery, or sit under 5 m from another vineyard
  outline with no passage between (the rules make those one block),
- orchard / overgrown outlines whose rows have vine spacing, and vineyard outlines whose rows do not.
Run from backend/: uv run --frozen python ../research/probes/plot_audit.py [predictions.geojson]"""

import json
import sys
from pathlib import Path

import numpy as np
from rasterio.features import rasterize, shapes
from scipy import ndimage
from shapely.geometry import LineString, shape
from shapely.ops import nearest_points, unary_union

from marcaj import plots
from marcaj.layers import LAYER_PX_M, load_layers
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

sys.path.insert(0, str(Path(__file__).parent))
from plot_experiment import tile_footprint  # noqa: E402
from plot_sheet import sheet  # noqa: E402

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots"


def band_power(excess, polygon, angle: float) -> tuple[float, float, float]:
    """Across-row profile power in the vine band (2.0-3.6 m) and orchard band (3.8-7 m), and the vine-band peak spacing."""
    frame = plots._Frame(polygon, angle, 0.2)
    counts = frame.inside.sum(1)
    use = counts > 20
    profile = ((excess.at(frame.x, frame.y) * frame.inside).sum(1) / np.maximum(counts, 1))[use]
    power = np.abs(np.fft.rfft((profile - profile.mean()) * np.hanning(len(profile)), 4096)) ** 2 / len(profile)
    frequency = np.fft.rfftfreq(4096, 0.2)
    vine = (frequency >= 1 / 3.6) & (frequency <= 1 / 2.0)
    orchard = (frequency >= 1 / 7.0) & (frequency <= 1 / 3.8)
    k = np.flatnonzero(vine)[np.argmax(power[vine])]
    return float(power[vine].max()), float(power[orchard].max()), float(1 / frequency[k])


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else WORK / "baseline.geojson"
    features = json.loads(source.read_text())["features"]
    layers, excess, roads = load_layers(), plots.load_excess(), plots.exclusions()
    passages = unary_union([shape(f["geometry"]) for f in json.loads((plots.DATA_DIR / "02_route" / "passages.geojson").read_text())["features"]])
    footprint = tile_footprint()
    outlines = [(i, v["label"], shape(v["geometry"])) for i, v in enumerate(v for v in load_verdicts() if v["kind"] == "plot")]
    drawn = unary_union([g for _, _, g in outlines])
    findings = []  # (x, y, half-size m, text)

    def add(geometry, text: str, margin: float = 15.0) -> None:
        minx, miny, maxx, maxy = geometry.bounds
        findings.append(((minx + maxx) / 2, (miny + maxy) / 2, max(maxx - minx, maxy - miny) / 2 + margin, text))
        print(f"  A{len(findings):02d} {text} at ({(minx + maxx) / 2:.0f}, {(miny + maxy) / 2:.0f})")

    print("1. row-periodic areas no outline covers")
    blocked = rasterize([roads], out_shape=layers.valid.shape, transform=layers.transform).astype(bool)
    seeds = ndimage.binary_opening((layers.vine_over_orchard > 2.0) & layers.valid & ~blocked, iterations=2)
    seeds &= ~rasterize([drawn.buffer(3)], out_shape=seeds.shape, transform=layers.transform).astype(bool)
    labels, n = ndimage.label(seeds)
    sizes = ndimage.sum(seeds, labels, range(1, n + 1)) * LAYER_PX_M**2
    candidates = [shape(g) for g, value in shapes(labels.astype(np.int32), mask=seeds, transform=layers.transform)
                  if value and sizes[int(value) - 1] >= 300]
    for group in sorted(getattr(unary_union([c.buffer(4) for c in candidates]), "geoms", [unary_union([c.buffer(4) for c in candidates])]), key=lambda g: -g.area):
        if group.is_empty or group.area < 600:
            continue
        seed_area = sum(c.area for c in candidates if c.intersects(group))
        angle, spacing = plots._row_angle(excess, group.buffer(-4) if group.buffer(-4).area > 200 else group)
        vine, orchard, _ = band_power(excess, group, angle)
        add(group, f"uncovered rows: {seed_area:.0f} m2 of vine-band seed, spacing {spacing:.2f} m, angle {angle:.0f}, vine/orchard power {vine / max(orchard, 1e-12):.1f}")
    for f in features:
        if f["properties"]["label"] == "block":
            g = shape(f["geometry"])
            if g.intersection(drawn).area < 0.5 * g.area:
                add(g, f"predicted plot {f['properties']['vineyard_id']} outside the outlines: {g.area:.0f} m2, spacing {f['properties']['row_spacing_m']} m")

    print("2. outline consistency")
    for a in range(len(outlines)):
        i, la, ga = outlines[a]
        if ga.difference(footprint).area > 0.03 * ga.area:
            add(ga, f"#{i} {la} runs past the imagery: {ga.difference(footprint).area:.0f} m2 ({ga.difference(footprint).area / ga.area:.0%}) outside the tiles")
        crossing = ga.intersection(passages)
        if crossing.area > 20:
            add(crossing, f"#{i} {la} overlaps a passage by {crossing.area:.0f} m2")
        for b in range(a + 1, len(outlines)):
            j, lb, gb = outlines[b]
            overlap = ga.intersection(gb).area
            if overlap > 5:
                add(ga.intersection(gb), f"#{i} {la} and #{j} {lb} overlap by {overlap:.0f} m2")
            elif la == lb == "vineyard" and ga.distance(gb) < 5:
                link = LineString(nearest_points(ga, gb))
                if not link.buffer(0.5).intersects(passages):
                    add(link.buffer(10), f"#{i} and #{j} vineyards {ga.distance(gb):.1f} m apart with no passage between: one block by the 5 m rule unless a track separates them")

    print("3. class against row spacing")
    for i, label, g in outlines:
        angle, spacing = plots._row_angle(excess, g)
        vine, orchard, _ = band_power(excess, g, angle)
        ratio = vine / max(orchard, 1e-12)
        print(f"   #{i:2d} {label:9s} spacing {spacing:.2f} m, angle {angle:5.1f}, vine/orchard profile power {ratio:6.1f}")
        if label != "vineyard" and ratio > 3:
            add(g, f"#{i} {label} has vine-spaced rows: {spacing:.2f} m, vine/orchard power {ratio:.1f}")
        if label == "vineyard" and ratio < 1:
            add(g, f"#{i} vineyard with weak vine-band rows: vine/orchard power {ratio:.1f}")
    (WORK / "audit.json").write_text(json.dumps([{"n": k + 1, "x": x, "y": y, "text": t} for k, (x, y, _, t) in enumerate(findings)], indent=1))
    sheet(features, [(x, y, h, f"A{k + 1:02d}") for k, (x, y, h, _) in enumerate(findings)], WORK / "audit_sheet.jpg")


if __name__ == "__main__":
    main()
