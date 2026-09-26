"""Plots v3 evaluation against the lab's latest outlines (re-read on every run): judge.plot_scores per half, area recall,
false area, on-orchard area, per-outline best IoU, and a per-outline comparison with another run. Evaluation only
(reads data/review/). Run from backend/:
uv run --frozen python ../research/probes/plots_v3_eval.py [src=run.geojson | name=value ...] [out=run.geojson] [vs=before.geojson]
(paths under data/generated/work/plots_v3)"""

import json
import sys
import time
from dataclasses import fields, replace

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from marcaj import plots  # noqa: E402
from marcaj.tiles import REPO_ROOT  # noqa: E402
from plots_recall_eval import line, per_outline, summary  # noqa: E402

WORK = REPO_ROOT / "data" / "generated" / "work" / "plots_v3"


def load(name: str) -> list[dict]:
    return json.loads((WORK / name).read_text())["features"]


def main() -> None:
    args = dict(a.split("=", 1) for a in sys.argv[1:])
    out, src, vs = args.pop("out", ""), args.pop("src", ""), args.pop("vs", "")
    base = plots.PlotParams()
    kinds = {f.name: type(getattr(base, f.name)) for f in fields(base)}
    params = replace(base, **{k: kinds[k](v) for k, v in args.items()})
    started = time.perf_counter()
    features = load(src) if src else plots.assign_blocks(plots.detect_plots(params))
    elapsed = time.perf_counter() - started
    s = summary(features)
    n, so = s["north"], s["south"]
    print(f"{src or args or 'defaults'} ({elapsed:.0f} s): {line(s)}")
    print(f"   on-orchard N {n['on_orchard_m2']:.0f} S {so['on_orchard_m2']:.0f} m2 | false N {n['false_m2']:.0f} S {so['false_m2']:.0f} m2 | "
          f"median IoU N {n['median_best_iou']:.3f} S {so['median_best_iou']:.3f} | predicted N {n['predicted']} S {so['predicted']} of {n['outlines']}/{so['outlines']}")
    table = per_outline(features, params, False)
    old = {r["i"]: r for r in per_outline(load(vs), params, False)} if vs else {}
    for r in sorted(table, key=lambda r: r["iou"]):
        if r["label"] == "vineyard" and (r["iou"] < 0.6 or (old and abs(old[r["i"]]["iou"] - r["iou"]) >= 0.03)):
            was = f" (was {old[r['i']]['iou']:.2f})" if old else ""
            print(f"   #{r['i']} {r['half']} {r['m2']} m2 iou {r['iou']:.2f}{was} cover {r['cover']:.2f}")
    if out:
        (WORK / out).write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}))
        (WORK / out.replace(".geojson", "_outlines.json")).write_text(json.dumps({"summary": s, "outlines": table}, indent=1))


if __name__ == "__main__":
    main()
