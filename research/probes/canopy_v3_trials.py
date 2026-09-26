"""Canopy round-three trials over the whole site (frozen v4 rows, cached network probabilities, 2 processes): the
canopy_v2 metrics (judge on the organizer tiles, gaps, the user's canopy and gap labels, row cover of the three central
fields) plus the user's "not a row" gap labels: canopy area within 0.3 m of each labelled line and its canopy cover
(share of the line with canopy within 0.3 m). Appends to data/generated/work/canopy_v3/trials.txt/.jsonl; saves each
variant's canopies (for sheets). Evaluation only; reads data/review/verdicts.json fresh each run.
Run from backend/: uv run --frozen python ../research/probes/canopy_v3_trials.py NAME..."""

import json
import sys
import time
from pathlib import Path

import numpy as np
from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import BASE, RUN, VERDICTS, line, metrics, site  # noqa: E402
from canopy_v3_quick import WORK  # noqa: E402
from canopy_v3_variants import VARIANTS  # noqa: E402


def not_a_row(canopies: list[dict]) -> dict:
    labels = [v for v in json.loads(VERDICTS.read_text()) if v.get("properties", {}).get("review_answer") == "not_a_row"]
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    out = []
    for lab in labels:
        g = shape(lab["geometry"])
        band = g.buffer(0.3, cap_style="flat")
        hits = [geoms[i] for i in tree.query(band, predicate="intersects")]
        area = sum(h.intersection(band).area for h in hits)
        t = np.linspace(0, g.length, max(int(g.length / 0.1), 2))
        near = STRtree(hits) if hits else None
        covered = [bool(near and len(near.query(g.interpolate(d).buffer(0.3), predicate="intersects"))) for d in t]
        out.append({"id": lab["id"], "length": round(g.length, 1), "area": round(area, 2), "cover": round(float(np.mean(covered)), 3)})
    return {"n": len(out), "area_total": round(sum(o["area"] for o in out), 1),
            "cover_under_0.2": sum(o["cover"] < 0.2 for o in out), "rows": out}


if __name__ == "__main__":
    for name in sys.argv[1:]:
        started = time.perf_counter()
        # base: the v4 prediction's own canopies (first pass, on the lattice rows before refit_rows); every other name runs on
        # the frozen v4 rows, which are v4's exported (refit) rows: a second canopy pass on refit rows
        found = [f for f in json.loads(BASE.read_text())["features"] if f["properties"]["label"] == "vineyard"] if name == "base" else site(VARIANTS[name])
        (WORK / f"canopies_{name}.json").write_text(json.dumps(found))
        m = metrics(f"{RUN}:{name}", found)
        m["not_a_row"] = not_a_row(found)
        text = line(m) + f" | not-a-row: n {m['not_a_row']['n']} canopy {m['not_a_row']['area_total']} m2, cover<0.2 {m['not_a_row']['cover_under_0.2']} | {time.perf_counter() - started:.0f} s"
        print(text, flush=True)
        with (WORK / "trials.txt").open("a") as handle:
            handle.write(text + "\n")
        with (WORK / "trials.jsonl").open("a") as handle:
            handle.write(json.dumps(m) + "\n")
