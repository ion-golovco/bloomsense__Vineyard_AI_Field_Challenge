"""SAM 2.1 tiny box refinement (marcaj.sam.refine, the closest of three masks) of the 18:13 base canopies on the two
organizer tiles, held to several limits: none, the +-0.3 m tube of the base rows, the piece grown 0.05 m, the piece itself.
Evaluation only. Run from backend/: HF_HUB_OFFLINE=1 uv run --frozen --group sam python ../research/probes/sam_v2_refine.py"""

import json
import sys
import time
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).parent))
from sam_v2_lib import NAMES, base_features, by_tile, judge_canopies, rows_of  # noqa: E402

from marcaj import canopy  # noqa: E402
from marcaj.sam import refine  # noqa: E402
from marcaj.tiles import load_tiles  # noqa: E402

if __name__ == "__main__":
    tiles = {t.name: t for t in load_tiles()}
    features = base_features()
    per_tile = by_tile(features)
    rest = [f for n, fs in per_tile.items() if n not in NAMES for f in fs]
    outlines, seconds = {}, 0.0
    for n in NAMES:
        rgb, transform = canopy.read_rgb(tiles[n])
        started = time.perf_counter()
        outlines[n] = refine(rgb, transform, [shape(f["geometry"]) for f in per_tile[n]], margin_m=0.05, pick_closest=True)
        seconds += time.perf_counter() - started
    tubes = {n: shapely.union_all([shape(r["geometry"]).buffer(0.3, cap_style="flat") for r in rows_of(features) if r["properties"]["tile"] == n]) for n in NAMES}
    limits = {"none": lambda n, g: None, "tube": lambda n, g: tubes[n], "piece+0.05": lambda n, g: g.buffer(0.05), "piece": lambda n, g: g}
    for tag, limit in limits.items():
        out = []
        for n in NAMES:
            for f, o in zip(per_tile[n], outlines[n]):
                g = shape(f["geometry"])
                if o is None:
                    continue
                held = o if limit(n, g) is None else o.intersection(limit(n, g))
                parts = [p for p in getattr(held, "geoms", [held]) if p.geom_type == "Polygon" and p.area >= 0.2]
                if parts:
                    out.append({**f, "geometry": mapping(max(parts, key=lambda p: p.area))})
        print(json.dumps({"name": f"refine_{tag}", "judge": judge_canopies(rest + out), "n_refs": len(out), "seconds_2_tiles": round(seconds, 1)}), flush=True)
