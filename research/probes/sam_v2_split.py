"""SAM per-plant splitting of long canopy pieces (marcaj.canopy_sam.split_tile) on the 18:13 base canopies: judge on the
two organizer tiles and the user's long-canopy labels (tiles holding a label). Evaluation only.
Run from backend/: HF_HUB_OFFLINE=1 uv run --frozen --group sam python ../research/probes/sam_v2_split.py NAME... [--refs-only]"""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sam_v2_lib import NAMES, WORK, base_features, by_tile, judge_canopies, label_metrics, label_tiles  # noqa: E402

from marcaj import canopy, canopy_sam  # noqa: E402
from marcaj.canopy_sam import SamParams  # noqa: E402
from marcaj.tiles import load_tiles  # noqa: E402

D = SamParams()
VARIANTS = {
    "base": None,
    "equal": replace(D, mode="equal"),
    "equal_2m": replace(D, mode="equal", plant_m=2.0),
    "assign": D,
    "assign_noneg": replace(D, negatives=False),
    "assign_512": replace(D, crop_px=512),
    "assign_2m": replace(D, plant_m=2.0),
    "assign_L4": replace(D, long_m=4.0),
    "assign_L6": replace(D, long_m=6.0),
    "separate": replace(D, mode="separate"),
    "separate_512": replace(D, mode="separate", crop_px=512),
    "separate_2m": replace(D, mode="separate", plant_m=2.0),
    "separate_large": replace(D, mode="separate", model_id="facebook/sam2.1-hiera-large"),
    "assign_large": replace(D, model_id="facebook/sam2.1-hiera-large"),
}

if __name__ == "__main__":
    refs_only = "--refs-only" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    tiles = {t.name: t for t in load_tiles()}
    features = base_features()
    per_tile = by_tile(features)
    todo = NAMES + ([] if refs_only else [n for n in label_tiles() if n not in NAMES])
    for name in names:
        params = VARIANTS[name]
        started = time.perf_counter()
        out = dict(per_tile)
        if params is not None:
            for n in todo:
                rgb, transform = canopy.read_rgb(tiles[n])
                out[n] = canopy_sam.split_tile(per_tile.get(n, []), rgb, transform, params)
        seconds = time.perf_counter() - started
        canopies = [f for fs in out.values() for f in fs]
        result = {"name": name, "judge": judge_canopies(canopies), "labels": None if refs_only else label_metrics(canopies),
                  "tiles": len(todo), "seconds": round(seconds, 1), "n": len(canopies)}
        print(json.dumps(result), flush=True)
        if params is not None:
            (WORK / f"split_{name}.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635",
                                                                   "features": [f for n in todo for f in out[n] if f["properties"].get("sam")]}))
