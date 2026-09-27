"""v6 rows: run plots.detect_plots with PlotParams overrides and score its rows against the team's rows (v6_rows_eval).
Evaluation only. Run from backend/: uv run --frozen python ../research/probes/v6_rows_run.py [name=value ...] [out=name.geojson] [fields=1] [-- name=value ...]"""

import json
import sys
import time
from dataclasses import fields as dc_fields, replace

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import v6_rows_eval as ev  # noqa: E402
from marcaj import plots  # noqa: E402


def run(argv: list[str]) -> None:
    args = dict(a.split("=", 1) for a in argv)
    out, show = args.pop("out", ""), args.pop("fields", "") == "1"
    args.pop("ref", None)
    base = plots.PlotParams()
    kinds = {f.name: type(getattr(base, f.name)) for f in dc_fields(base)}
    params = replace(base, **{k: kinds[k](v) for k, v in args.items()})
    started = time.perf_counter()
    found = plots.detect_plots(params)
    elapsed = time.perf_counter() - started
    report = ev.evaluate(found)
    print(f"{args or 'defaults'} ({elapsed:.0f} s):", ev.summary_line(report), report["unmatched"], flush=True)
    if show:
        print("missing:", report["missing_fields"])
        print(ev.field_table(report))
    if out:
        (ev.WORK / out).write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": found}))
        (ev.WORK / out.replace(".geojson", "_eval.json")).write_text(json.dumps(report, indent=1))


def main() -> None:
    """Several runs in one process (the mosaic stays loaded): separate their arguments with `--`."""
    groups, current = [], []
    for a in sys.argv[1:] + ["--"]:
        if a == "--":
            groups.append(current)
            current = []
        else:
            current.append(a)
    for group in groups:
        run(group)


if __name__ == "__main__":
    main()
