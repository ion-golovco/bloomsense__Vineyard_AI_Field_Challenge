"""False rows (the user's "not a row" gap labels) against canopy: each frozen v4 row's canopy cover (share of 0.1 m
samples along it with a canopy of its pattern within 0.3 m), for saved variants. A row a "not a row" label runs along
(within 0.5 m over half the label) is a false row. Prints how many false rows and other rows fall under a cover threshold,
i.e. what a "drop a row with almost no canopy" rule in plots/rows would remove. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/canopy_v3_rows.py VARIANT..."""

import json
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_lib import VERDICTS, frozen_plots  # noqa: E402
from canopy_v3_quick import WORK  # noqa: E402


def false_rows(rows: list[dict]) -> set[str]:
    labels = [shape(v["geometry"]) for v in json.loads(VERDICTS.read_text()) if v.get("properties", {}).get("review_answer") == "not_a_row"]
    lines = [shape(f["geometry"]) for f in rows]
    tree = STRtree(lines)
    out = set()
    for lab in labels:
        for i in tree.query(lab.buffer(0.5)):
            if lab.intersection(lines[i].buffer(0.5)).length >= 0.5 * lab.length:
                out.add(rows[i]["properties"]["row_id"])
    return out, len(labels)


def row_cover(rows: list[dict], canopies: list[dict]) -> dict[str, float]:
    by_pattern: dict[str, list] = {}
    for f in canopies:
        by_pattern.setdefault(f["properties"]["pattern_id"], []).append(shape(f["geometry"]))
    trees = {k: STRtree(v) for k, v in by_pattern.items()}
    out = {}
    for f in rows:
        line = shape(f["geometry"])
        pts = shapely.points(np.array([line.interpolate(d).coords[0] for d in np.arange(0, line.length, 0.1)]))
        tree = trees.get(f["properties"]["pattern_id"])
        hit = np.zeros(len(pts), bool)
        if tree is not None and len(pts):
            hit[np.unique(tree.query(pts, predicate="dwithin", distance=0.3)[0])] = True
        out[f["properties"]["row_id"]] = float(hit.mean()) if len(pts) else 0.0
    return out


if __name__ == "__main__":
    rows = [f for f in frozen_plots() if f["properties"]["label"] == "row"]
    bad, n_labels = false_rows(rows)
    print(f"{n_labels} not-a-row labels on {len(bad)} of {len(rows)} frozen v4 rows")
    for name in sys.argv[1:]:
        cover = row_cover(rows, json.loads((WORK / f"canopies_{name}.json").read_text()))
        parts = []
        for t in (0.02, 0.05, 0.1, 0.2):
            parts.append(f"<{t}: false {sum(cover[r] < t for r in bad)}/{len(bad)}, other {sum(c < t for r, c in cover.items() if r not in bad)}")
        print(f"{name:16s} false-row cover median {np.median([cover[r] for r in bad]):.3f} | " + " | ".join(parts), flush=True)
        (WORK / f"row_cover_{name}.json").write_text(json.dumps({"false_rows": sorted(bad), "cover": cover}))
