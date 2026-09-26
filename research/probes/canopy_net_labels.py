"""Pseudo-label snapshot for `marcaj.canopy_net`: the rule-based canopy (`marcaj.canopy`) on every vineyard tile, written
once to data/generated/work/canopy_net/ so later changes to canopy.py or plots.py do not move the training data.
Per tile crossed by a predicted row axis, except the two organizer reference tiles and their 8 neighbours (held out):
`labels_<canopy.py sha256[:8]>/<tile>.npz` with `instance` (uint16, one id per final rule polygon), `mask` (canopy_mask union, before the
minimum area), `band` (+-tube_m of the kept axes) and `near` (+-0.6 m of every axis). Also `plots.json` (the rows
used, made once and reused by the other canopy_net probes), `canopy_snapshot_<sha>.py` (the canopy.py that made the
labels) and `meta_<sha>.json`.
Reads tiles and plots only, never data/review/.
Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_net_labels.py"""

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import replace

import numpy as np
from rasterio.features import rasterize

from marcaj import canopy
from marcaj.canopy import CanopyParams, plot_rows, read_rgb, tube
from marcaj.plots import detect_plots
from marcaj.tiles import REPO_ROOT, load_tiles

WORK = REPO_ROOT / "data" / "generated" / "work" / "canopy_net"
HELD_OUT = ((21, 12), (6, 4))  # organizer reference tiles (row, col); their 8 neighbours are held out too
NEAR_M = 0.6


def held_out(name: str) -> bool:
    r, c = int(name[8:11]), int(name[13:16])
    return any(abs(r - hr) <= 1 and abs(c - hc) <= 1 for hr, hc in HELD_OUT)


def tile_labels(image: np.ndarray, transform, rowsets, params: CanopyParams, green: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """The rule canopy of one tile as rasters: the same calls `canopy.tile_canopies` makes, plus the kept tube.
    `green` replaces the colour threshold, as in `canopy.canopy_mask`."""
    shape_ = image.shape[1:]
    instance = np.zeros(shape_, np.uint16)
    mask, band, near = (np.zeros(shape_, bool) for _ in range(3))
    excess, valid = canopy.excess_green(image, params)
    colour = canopy.green_mask(image, params) if green is None else green & valid
    loose = replace(params, row_gap=0.0, row_contrast=0.0, row_value=0.0)
    next_id = 1
    for plot, axes in rowsets:
        spacing = canopy.row_spacing(plot.axes)
        plot_mask = canopy.canopy_mask(image, transform, axes, params, green, spacing)
        mask |= plot_mask
        band |= tube(canopy.kept_axes(axes, colour, transform, params, spacing, excess), transform, shape_, params.tube_m)
        near |= tube(canopy.kept_axes(axes, colour, transform, loose, spacing), transform, shape_, NEAR_M)
        polygons = canopy.canopy_polygons(plot_mask, transform, plot.angle_deg, params)
        if polygons:
            burnt = rasterize([(p, next_id + i) for i, p in enumerate(polygons)], out_shape=shape_, transform=transform, dtype=np.uint16)
            instance = np.where(burnt > 0, burnt, instance)
            next_id += len(polygons)
    return {"instance": instance, "mask": mask, "band": band, "near": near}


def main() -> None:
    source = REPO_ROOT / "backend" / "src" / "marcaj" / "canopy.py"
    version = hashlib.sha256(source.read_bytes()).hexdigest()[:8]
    out = WORK / f"labels_{version}"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, WORK / f"canopy_snapshot_{version}.py")
    plots_path = WORK / "plots.json"
    started = time.perf_counter()
    if not plots_path.is_file():
        plots_path.write_text(json.dumps(detect_plots()))
    plots = json.loads(plots_path.read_text())
    rows = plot_rows(plots)
    params = CanopyParams()
    tiles = load_tiles()
    written, skipped = [], []
    for tile in tiles:
        crossing = [(p, [a for a in p.axes if a.intersects(tile.bounds)]) for p in rows]
        crossing = [(p, axes) for p, axes in crossing if axes]
        if not crossing:
            continue
        if held_out(tile.name):
            skipped.append(tile.name)
            continue
        image, transform = read_rgb(tile)
        labels = tile_labels(image, transform, crossing, params)
        np.savez_compressed(out / f"{tile.name[:-4]}.npz", **labels)
        written.append({"tile": tile.name, "canopies": int(labels["instance"].max()), "canopy_px": int((labels["instance"] > 0).sum())})
    meta = {
        "canopy_py_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT).stdout.strip(),
        "canopy_py_dirty": bool(subprocess.run(["git", "diff", "--quiet", "--", str(source)], cwd=REPO_ROOT).returncode),
        "params": params.__dict__, "tiles": written, "held_out_vineyard_tiles": skipped,
        "seconds": round(time.perf_counter() - started, 1),
    }
    (WORK / f"meta_{version}.json").write_text(json.dumps(meta, indent=1))
    print(f"labels_{version}: {len(written)} tiles labelled ({sum(t['canopies'] for t in written)} canopies), {len(skipped)} held out, {meta['seconds']} s")


if __name__ == "__main__":
    main()
