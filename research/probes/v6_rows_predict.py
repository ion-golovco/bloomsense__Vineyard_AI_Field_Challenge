"""v6 rows: the whole prediction (marcaj.predict) with PlotParams overrides, written to data/generated/work/v6/rows/<out>.
Run from backend/ under the heavy-run lock: uv run --frozen --group sam python ../research/probes/v6_rows_predict.py out=name.geojson [name=value ...]"""

import json
import sys
from dataclasses import fields, replace

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import v6_rows_eval as ev  # noqa: E402
from marcaj import plots, predict  # noqa: E402


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    out = ev.WORK / args.pop("out")
    base = plots.PlotParams()
    kinds = {f.name: type(getattr(base, f.name)) for f in fields(base)}
    params = replace(base, **{k: kinds[k](v) for k, v in args.items()})
    dropped: list = []
    features = predict.predict(params, dropped_out=dropped)
    predict.write(features, out)
    predict.write(dropped, out.with_name(out.stem + "_dropped.geojson"))
    report = ev.evaluate([f for f in features if f["properties"]["label"] in ("block", "row")])
    print(args, ev.summary_line(report), report["unmatched"])
    (out.with_name(out.stem + "_eval.json")).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":  # waste.detect spawns workers, which re-import this file
    main()
