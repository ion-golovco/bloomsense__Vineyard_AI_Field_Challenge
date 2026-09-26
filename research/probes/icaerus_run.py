"""ICAERUS instances per tile, cached: data/generated/work/icaerus/inst_z<zoom>/<tile>.npz (rings in tile px, confidence,
seconds). Usage: icaerus_run.py ZOOM all|tile,tile,... Run with data/generated/work/icaerus/.venv/bin/python."""

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import icaerus_lib as L

zoom = float(sys.argv[1])
names = sorted(p.name for p in L.TILES.glob("*.tif")) if sys.argv[2] == "all" else sys.argv[2].split(",")
out = L.WORK / f"inst_z{zoom:g}"
out.mkdir(exist_ok=True)
model, _ = L.load_model()
total = time.perf_counter()
for k, name in enumerate(names):
    path = out / (name[:-4] + ".npz")
    if path.exists():
        continue
    started = time.perf_counter()
    rgb = L.read_tile(name)
    found = [] if not rgb.any() else L.detect(model, rgb, zoom=zoom)
    L.save_instances(path, found, time.perf_counter() - started)
    print(f"{k + 1}/{len(names)} {name} {len(found)} instances {time.perf_counter() - started:.1f} s", flush=True)
print(f"done {len(names)} tiles in {time.perf_counter() - total:.0f} s on {L.DEVICE}")
