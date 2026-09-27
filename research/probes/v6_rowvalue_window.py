"""v6: `row_value` per stretch instead of per axis (prototype, monkeypatched into canopy.canopy_mask): an axis is kept
whole, and each `WINDOW_M` stretch of its tube whose mean clipped 2g - r - b is under `row_value` times that 0.6-1.0 m
beside it (in the same stretch) is taken out of the band. Vines standing in grass stay where they stand out; tubes
full of weeds go. Run from backend/: uv run --frozen --group sam python ../research/probes/v6_rowvalue_window.py quick|site W"""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.canopy import FLANK_M, CanopyParams  # noqa: E402

WINDOW_M = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
ORIGINAL_KEPT = canopy.kept_axes
ORIGINAL_TUBE = canopy.tube
_last: dict = {}


def kept(axes, green, transform, params, spacing_m=0.0, excess=None, rgb=None):
    out = ORIGINAL_KEPT(axes, green, transform, replace(params, row_value=0), spacing_m, excess, rgb)
    _last["excess"], _last["params"] = excess, params
    return out


def windowed_tube(axes, transform, shape_, half_width_m):
    band = ORIGINAL_TUBE(axes, transform, shape_, half_width_m)
    excess, params = _last.get("excess"), _last.get("params")
    if excess is None or not params.row_value or half_width_m != params.tube_m:
        return band
    for axis in axes:
        rows, cols = canopy._pixels(axis.buffer(FLANK_M[1], cap_style="flat"), transform, shape_)
        if not len(rows) or not axis.length:
            continue
        (x0, y0), (x1, y1) = axis.coords[0], axis.coords[-1]
        d = np.array([x1 - x0, y1 - y0]) / axis.length
        dx, dy = transform.c + (cols + 0.5) * transform.a - x0, transform.f + (rows + 0.5) * transform.e - y0
        u, v = dx * d[0] + dy * d[1], np.abs(-dx * d[1] + dy * d[0])
        value = np.clip(excess[rows, cols], 0, None)
        w = np.floor(u / WINDOW_M).astype(int)
        w -= w.min()
        inside, flank = v <= params.tube_m, (v >= FLANK_M[0]) & (v <= FLANK_M[1])
        t = np.bincount(w, weights=value * inside) / np.maximum(np.bincount(w, weights=inside), 1)
        f = np.bincount(w, weights=value * flank) / np.maximum(np.bincount(w, weights=flank), 1)
        low = t < params.row_value * np.maximum(f, 1e-3)
        drop = low[w] & inside
        band[rows[drop], cols[drop]] = False
    return band


if __name__ == "__main__":
    mode = sys.argv[1]
    canopy.kept_axes, canopy.tube = kept, windowed_tube
    plots = L.plot_features()
    params = CanopyParams()
    if mode == "quick":
        found = [f for n in L.NAMES for f in L.tile_canopies(n, params, L.rowsets(plots), L.spacing_of(plots))]
        print(f"window {WINDOW_M}", L.judge_canopy(found))
    else:
        import v6_canopy_oracle as O  # noqa: E402
        lock = L.heavy_lock()
        try:
            started = time.perf_counter()
            L._init(plots, None)
            found = [f for n in L.vine_tiles(plots) for f in L._run((n, params, "and"))]
            name = f"rvwin{WINDOW_M:g}"
            (L.OUT / f"canopies_{name}.json").write_text(json.dumps(found))
            m = O.eval_site(name, found, L.ref_pieces(), plots)
            m["seconds"] = round(time.perf_counter() - started)
            print(json.dumps(m))
            with (L.OUT / "oracle.jsonl").open("a") as handle:
                handle.write(json.dumps(m) + "\n")
        finally:
            L.release(lock)
