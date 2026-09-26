"""Plots v5: recall on the overgrown / weedy vineyards. Runs detect_plots (or reads a run), scores it as plots_v3_eval
does (judge.plot_scores per half, area recall) and lists every vineyard and overgrown outline with its cover and best
IoU, plus diagnostics (plots_recall_eval diag) for the ones under `max_cover`. Evaluation only (reads data/review/).
Run from backend/: uv run --frozen python ../research/probes/plots_v5_eval.py [src=path.geojson | name=value ...] [out=name.geojson] [diag=1]
(out under data/generated/work/plots_v5)"""

import json
import sys
import time
from dataclasses import fields, replace
from pathlib import Path

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from marcaj import plots  # noqa: E402
from marcaj.tiles import REPO_ROOT  # noqa: E402
from plots_recall_eval import line, per_outline, summary  # noqa: E402

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots_v5"


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    out, src, diag = args.pop("out", ""), args.pop("src", ""), args.pop("diag", "") == "1"
    base = plots.PlotParams()
    kinds = {f.name: type(getattr(base, f.name)) for f in fields(base)}
    params = replace(base, **{k: kinds[k](v) for k, v in args.items()})
    started = time.perf_counter()
    features = json.loads(Path(src).read_text())["features"] if src else plots.assign_blocks(plots.detect_plots(params))
    features = [f for f in features if f["properties"]["label"] in ("block", "row")]
    elapsed = time.perf_counter() - started
    s = summary(features)
    print(f"{src or args or 'defaults'} ({elapsed:.0f} s): {line(s)}")
    table = per_outline(features, params, diag)
    for r in sorted(table, key=lambda r: (r["label"], r["cover"])):
        if r["label"] in ("vineyard", "overgrown") and r["cover"] < 0.8:
            print("  ", {k: v for k, v in r.items()})
    if out:
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / out).write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}))
        (WORK / out.replace(".geojson", "_outlines.json")).write_text(json.dumps({"summary": s, "outlines": table}, indent=1))


if __name__ == "__main__":
    main()
