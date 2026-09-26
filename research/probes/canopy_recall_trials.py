"""Canopy-recall trials: each variant over the whole site on the frozen rows, with the judge on the organizer tiles,
POI gap counts, row cover and the length distribution. Appends to data/generated/work/canopy_recall/trials.txt/.jsonl.
Evaluation only. Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_recall_trials.py NAME...
(`list` prints the variants)."""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from canopy_recall_lib import WORK, Variant, cache_probabilities, line, metrics, site  # noqa: E402

from marcaj.canopy import CanopyParams  # noqa: E402

OLD = replace(CanopyParams(), all_parts=False, weak_dn=0, row_young=0)  # the uploaded behaviour (26 Sep 10:54)
A = replace(OLD, all_parts=True)
N = CanopyParams()  # the shipped defaults
VARIANTS = {v.name: v for v in [
    Variant("base", OLD),
    Variant("all_parts", A),
    Variant("defaults", N),
    Variant("rules_only", A, "rules"),
    Variant("or_net05", A, "or", 0.5),
    Variant("or_net07", A, "or", 0.7),
    Variant("and_net01", A, "and", 0.1),
    Variant("green22", replace(A, green_dn=22)),
    Variant("green20", replace(A, green_dn=20)),
    Variant("min015", replace(A, min_area_m2=0.15)),
    Variant("min010", replace(A, min_area_m2=0.10)),
    Variant("row_young6", replace(A, row_young=6)),
    Variant("weak15", replace(A, weak_dn=15, weak_max_m=0)),
    Variant("weak15_s25", replace(A, weak_dn=15, weak_share=0.25, weak_max_m=0)),
    Variant("weak18", replace(A, weak_dn=18, weak_max_m=0)),
    Variant("weak15_ry6", replace(N, weak_max_m=0)),
    Variant("weak15_ry6_m4", replace(N)),
    Variant("weak15_s25_ry6_m4", replace(N, weak_share=0.25)),
    Variant("weak12_ry6_m4", replace(N, weak_dn=12)),
    Variant("defaults_rules", N, "rules"),
    Variant("defaults_and01", N, "and", 0.1),
    Variant("defaults_and005", N, "and", 0.05),
    Variant("defaults_and03", N, "and", 0.3),
]}
# tried and removed from canopy.py (numbers in trials.txt): weak_new (weak pixels only as new pieces), row_young at
# the 0.75 quantile, the network as a verifier of the weak pixels (> 0.02-0.10), relaxed necks on strips over 3-5 m, a lone-piece rule (weak pieces with no other
# piece within 2-3 m along the row dropped: no effect with 0.05 m2 neighbours, and with 0.2 m2 ones it reopens young-block gaps)

if __name__ == "__main__":
    if sys.argv[1:] == ["list"]:
        print("\n".join(VARIANTS))
        sys.exit()
    cache_probabilities()
    for name in sys.argv[1:]:
        started = time.perf_counter()
        found = site(VARIANTS[name])
        (WORK / f"canopies_{name}.json").write_text(json.dumps(found))
        m = metrics(name, found)
        text = line(m) + f" | {time.perf_counter() - started:.0f} s"
        print(text, flush=True)
        with (WORK / "trials.txt").open("a") as handle:
            handle.write(text + "\n")
        with (WORK / "trials.jsonl").open("a") as handle:
            handle.write(json.dumps(m) + "\n")
