"""Plot scores (judge.plot_scores, per half) and the blocks without an outline, before and after verify_plots, against
the lab's latest outlines (re-read on every run). Evaluation only (reads data/review/). Run from backend/:
uv run --frozen python ../research/probes/plots_verify_eval.py [before=predictions.geojson] [after=work/plots_verify/predictions.geojson]"""

import json
import sys

from shapely.geometry import shape
from shapely.ops import unary_union

from marcaj.judge import plot_scores
from marcaj.review import load_verdicts
from marcaj.tiles import REPO_ROOT

GEN = REPO_ROOT / "data" / "generated"


def report(name: str, features: list[dict]) -> None:
    verdicts = load_verdicts()
    vineyards = unary_union([shape(v["geometry"]) for v in verdicts if v["kind"] == "plot" and v["label"] == "vineyard"])
    blocks = [f for f in features if f["properties"]["label"] == "block"]
    for half, s in plot_scores(features, verdicts).items():
        print(f"{name:7s} {half}: {s['predicted']} blocks / {s['outlines']} outlines | F1@0.5 {s['f1_50']:.3f} F1@0.75 {s['f1_75']:.3f} | "
              f"median best IoU {s['median_best_iou']:.3f} | area IoU {s['area_iou']:.3f} | false {s['false_m2']:,.0f} m2 | on orchards {s['on_orchard_m2']:,.0f} m2")
    lonely = [(f["properties"]["vineyard_id"], round(shape(f["geometry"]).area)) for f in blocks
              if shape(f["geometry"]).intersection(vineyards).area < 0.5 * shape(f["geometry"]).area]
    print(f"{name:7s} blocks under 50% on vineyard outlines: {lonely}")


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    for name in ("before", "after"):
        path = GEN / args.get(name, "predictions.geojson" if name == "before" else "work/plots_verify/predictions.geojson")
        report(name, json.loads(path.read_text())["features"])


if __name__ == "__main__":
    main()
