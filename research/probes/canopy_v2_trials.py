"""Canopy round-two trials: each variant over the whole site on the frozen 15:43 rows, with the judge on the organizer
tiles, gap counts, lengths and the user's long-canopy labels. Appends to data/generated/work/canopy_v2/trials.txt/.jsonl.
`base` scores the 15:43 predictions' own canopies. Evaluation only.
Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_v2_trials.py NAME... (`list` prints them)."""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import BASE, RUN, WORK, cache_probabilities, line, metrics, site  # noqa: E402

from marcaj.canopy import CanopyParams  # noqa: E402

D = CanopyParams()
SAVE = {"defaults", "veg_off"}
OFF = replace(D, veg_gr=0)  # the 15:43 behaviour
VARIANTS: dict[str, CanopyParams] = {
    "defaults": D,
    "veg_off": OFF,
    "veg_gr9": replace(D, veg_gr=9),
    "veg_ring03": replace(D, veg_ring=0.3),
    "veg_rgr7": replace(D, veg_ring_gr=7, veg_gr=9),
    "veg_L25": replace(D, veg_m=2.5),
    "refine065": replace(D, refine_m=0.65),
    "refine08": replace(D, refine_m=0.8),
    "weak12": replace(D, weak_dn=12),
    "tuft05": replace(D, tuft_m2=0.05),
    "tuft03": replace(D, tuft_m2=0.03),
    "tuft03_w12": replace(D, tuft_m2=0.03, weak_dn=12),
    "tuft02_w10": replace(D, tuft_m2=0.02, weak_dn=10),
    "weak10": replace(D, weak_dn=10),
    "lab8": replace(D, lab_a=-8),
    "lab6": replace(D, lab_a=-6),
    "lab10": replace(D, lab_a=-10),
    "tube035": replace(D, tube_m=0.35),
    "tube040": replace(D, tube_m=0.40),
    "min010": replace(D, min_area_m2=0.10),
    "green20": replace(D, green_dn=20),
    "neck035": replace(D, split_neck=0.35),
    "neck040": replace(D, split_neck=0.4),
    "neck050": replace(D, split_neck=0.5),
    "L3_n04": replace(D, long_m=3, long_neck=0.4),
    "L3_n05": replace(D, long_m=3, long_neck=0.5),
    "L25_n05": replace(D, long_m=2.5, long_neck=0.5),
    "L3_n07": replace(D, long_m=3, long_neck=0.7),
    "L25_any": replace(D, long_m=2.5, long_neck=1.01),
    "L4_n05": replace(D, long_m=4, long_neck=0.5),
    "L5_n05": replace(D, long_m=5, long_neck=0.5),
}

if __name__ == "__main__":
    if sys.argv[1:] == ["list"]:
        print("\n".join(VARIANTS))
        sys.exit()
    cache_probabilities()
    for name in sys.argv[1:]:
        started = time.perf_counter()
        if name == "base":
            found = [f for f in json.loads(BASE.read_text())["features"] if f["properties"]["label"] == "vineyard"]
        else:
            found = site(VARIANTS[name])
            if name in SAVE:  # 36 MB each; the disk is nearly full
                (WORK / f"canopies_{name}.json").write_text(json.dumps(found))
        m = metrics(f"{RUN}:{name}", found)
        text = line(m) + f" | {time.perf_counter() - started:.0f} s"
        print(text, flush=True)
        with (WORK / "trials.txt").open("a") as handle:
            handle.write(text + "\n")
        with (WORK / "trials.jsonl").open("a") as handle:
            handle.write(json.dumps(m) + "\n")
