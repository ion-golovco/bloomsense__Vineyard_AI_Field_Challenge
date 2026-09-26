"""`detect_plots` with PlotParams overrides: plot scores as drawn, plus the vineyard outline area its blocks cover and
the row length inside vineyard outlines, on orchards and outside every outline (false rows). Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/plot_coverage.py [out=name.geojson] [name=value ...]"""

import json
import sys
from dataclasses import fields, replace
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
from plot_experiment import _fmt  # noqa: E402

from marcaj.judge import plot_scores  # noqa: E402
from marcaj.plots import PlotParams, detect_plots  # noqa: E402
from marcaj.review import load_verdicts  # noqa: E402
from marcaj.tiles import REPO_ROOT  # noqa: E402

args = dict(a.split("=") for a in sys.argv[1:])
out = args.pop("out", "")
base = PlotParams()
kinds = {f.name: type(getattr(base, f.name)) for f in fields(base)}
features = detect_plots(replace(base, **{k: kinds[k](v) for k, v in args.items()}))
verdicts = load_verdicts()
outlines = {label: unary_union([shape(v["geometry"]) for v in verdicts if v["kind"] == "plot" and v["label"] == label])
            for label in ("vineyard", "orchard", "overgrown")}
blocks = unary_union([shape(f["geometry"]) for f in features if f["properties"]["label"] == "block"])
rows = unary_union([shape(f["geometry"]) for f in features if f["properties"]["label"] == "row"])
anywhere = unary_union(list(outlines.values()))
print(f"{args or 'defaults'}: {_fmt(plot_scores(features, verdicts))}")
print(f"  outline covered {blocks.intersection(outlines['vineyard']).area:,.0f} of {outlines['vineyard'].area:,.0f} m2 | "
      f"rows in vineyard {rows.intersection(outlines['vineyard']).length:,.0f} m, on orchard {rows.intersection(outlines['orchard']).length:,.0f} m, "
      f"outside outlines {rows.difference(anywhere).length:,.0f} m, total {rows.length:,.0f} m")
if out:
    (REPO_ROOT / "data/generated/work/fields" / out).write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}))
