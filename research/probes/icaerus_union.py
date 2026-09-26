"""ICAERUS vine masks as extra recall inside our row tubes: our canopy mask (canopy_net "and", as predict.py) OR the
ICAERUS instance pixels, then our whole canopy pipeline (tube, closing, neck splitting, area rules) on the union.
Rows: the 18:13 base predictions (sam_v2 harness). Evaluation only (labels are read to score, never to predict).
Run from backend/: uv run --frozen --group sam python ../research/probes/icaerus_union.py ZOOM
Instances come from icaerus_run.py (data/generated/work/icaerus/inst_z<zoom>/)."""

import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from rasterio.features import rasterize
from shapely.geometry import Polygon, shape

sys.path.insert(0, str(Path(__file__).parent))
from sam_v2_lib import MID, MID_FIELDS, NAMES, base_features, by_tile, judge_canopies, label_metrics, labels, row_cover, rows_of  # noqa: E402
from sam_v2_recall import gap_metrics  # noqa: E402

from marcaj import canopy, canopy_net  # noqa: E402
from marcaj.canopy import CanopyParams  # noqa: E402
from marcaj.tiles import REPO_ROOT, load_tiles  # noqa: E402

torch.set_num_threads(4)
WORK = REPO_ROOT / "data" / "generated" / "work" / "icaerus"
P = CanopyParams()


def icaerus_mask(path: Path, conf: float, size: int = 2048) -> np.ndarray:
    data = np.load(path)
    rings = np.split(data["xy"], data["ends"][:-1]) if len(data["ends"]) else []
    polys = [Polygon(r).buffer(0) for r, c in zip(rings, data["conf"]) if c >= conf and len(r) >= 3]
    polys = [p for p in polys if not p.is_empty]
    return rasterize(polys, out_shape=(size, size)).astype(bool) if polys else np.zeros((size, size), bool)


if __name__ == "__main__":
    zoom = float(sys.argv[1])
    inst = WORK / f"inst_z{zoom:g}"
    tiles = {t.name: t for t in load_tiles()}
    features = base_features()
    per_tile = by_tile(features)
    rows = rows_of(features)
    plot_rows = canopy.plot_rows([f for f in features if f["properties"]["label"] in ("block", "row")])
    gap_labels, can_labels = labels("gaps"), labels("canopies")
    want = set(NAMES + MID) | {lab["properties"]["tile"] for lab in can_labels + gap_labels}
    todo = sorted(n for n in want if (inst / (n[:-4] + ".npz")).exists())
    print(f"{len(todo)} of {len(want)} tiles have zoom {zoom:g} instances", flush=True)
    model = canopy_net.load()
    variants = {"base_rerun": None, "union_c0.25": ("all", 0.25), "union_c0.15": ("all", 0.15), "uniong_c0.25": ("weak", 0.25), "uniong_c0.15": ("weak", 0.15)}
    new = {v: [] for v in variants}
    seconds = {v: 0.0 for v in variants}
    for k, n in enumerate(todo):
        rgb, transform = canopy.read_rgb(tiles[n])
        started = time.perf_counter()
        ours = canopy_net.mask(rgb, model, P, 0.2, "and", False)
        base_s = time.perf_counter() - started
        excess, _ = canopy.excess_green(rgb, P)
        for v, spec in variants.items():
            started = time.perf_counter()
            green = ours
            if spec:
                extra = icaerus_mask(inst / (n[:-4] + ".npz"), spec[1])
                green = ours | (extra & (excess > P.weak_dn) if spec[0] == "weak" else extra)
            new[v] += canopy.tile_canopies(tiles[n], plot_rows, P, (rgb, transform), green)
            seconds[v] += time.perf_counter() - started + base_s
        print(f"{k + 1}/{len(todo)} {n}", flush=True)
    done = set(todo)
    rest = [f for t, fs in per_tile.items() if t not in done for f in fs]
    mid_rows = [r for r in rows if r["properties"]["tile"] in MID and r["properties"]["vineyard_id"] in MID_FIELDS]
    out = []
    for v in ["base_file"] + list(variants):
        canopies = [f for fs in per_tile.values() for f in fs] if v == "base_file" else rest + new[v]
        result = {"name": v, "zoom": zoom, "judge": judge_canopies(canopies), "mid_row_cover": row_cover(canopies, mid_rows),
                  "canopy_labels": label_metrics(canopies), "gap_labels": gap_metrics(canopies, gap_labels),
                  "n_on_tiles": len(new.get(v, [])), "s_per_tile": round(seconds.get(v, 0.0) / max(len(todo), 1), 2), "tiles": len(todo)}
        print(json.dumps(result), flush=True)
        out.append(result)
    (WORK / f"union_z{zoom:g}.jsonl").write_text("\n".join(json.dumps(r) for r in out) + "\n")
