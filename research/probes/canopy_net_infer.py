"""Full-run timing of `marcaj.canopy_net` on every vineyard tile (every tile a frozen predicted row crosses), next to the
rule canopy: seconds, canopy count and area per run. Run from backend/:
uv run --frozen --group sam python ../research/probes/canopy_net_infer.py WEIGHTS [--combine and --threshold 0.2]"""

import argparse
import time
from pathlib import Path

import numpy as np
from shapely.geometry import shape

import canopy_net_eval as ev
from marcaj import canopy, canopy_net

parser = argparse.ArgumentParser()
parser.add_argument("weights", type=Path)
parser.add_argument("--combine", default="net")
parser.add_argument("--threshold", type=float, default=0.5)
parser.add_argument("--no-flips", action="store_true")
args = parser.parse_args()

model = canopy_net.load(args.weights)
names = [name for name, tile in ev.tiles.items() if any(a.intersects(tile.bounds) for p in ev.rows for a in p.axes)]
totals = {"net": [0.0, 0, 0.0, 0.0], "rule": [0.0, 0, 0.0, 0.0]}  # seconds, count, area, model seconds
counts = []
for name in names:
    started = time.perf_counter()
    image = canopy.read_rgb(ev.tiles[name])
    read = time.perf_counter() - started
    started = time.perf_counter()
    prob = canopy_net.probabilities(image[0], model, flips=not args.no_flips)
    inference = time.perf_counter() - started
    found = canopy_net.tile_canopies(ev.tiles[name], ev.rows, model=model, threshold=args.threshold, combine=args.combine, rgb=image, prob=prob)
    net_seconds = read + time.perf_counter() - started
    started = time.perf_counter()
    rule = canopy.tile_canopies(ev.tiles[name], ev.rows, rgb=image)
    rule_seconds = read + time.perf_counter() - started
    for key, features, seconds in (("net", found, net_seconds), ("rule", rule, rule_seconds)):
        totals[key][0] += seconds
        totals[key][1] += len(features)
        totals[key][2] += sum(shape(f["geometry"]).area for f in features)
    totals["net"][3] += inference
    counts.append((len(found), len(rule)))
counts_ = np.array(counts)
ratio = counts_[:, 0] / np.maximum(counts_[:, 1], 1)
print(f"{len(names)} vineyard tiles, {args.weights.name} combine={args.combine} t={args.threshold} flips={not args.no_flips}")
for key, (seconds, count, area, model_seconds) in totals.items():
    print(f"  {key:4s} {seconds:6.1f} s ({seconds / len(names):.2f} s/tile{f', network {model_seconds:.1f} s' if key == 'net' else ''}), "
          f"{count} canopies, {area:.0f} m2")
print(f"  per-tile net/rule count ratio: median {np.median(ratio):.3f}, p10 {np.percentile(ratio, 10):.3f}, p90 {np.percentile(ratio, 90):.3f}")
