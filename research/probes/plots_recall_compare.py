"""Per-outline IoU and cover of two plot runs (their *_outlines.json from plots_recall_eval.py), changed outlines only.
Run: python3 research/probes/plots_recall_compare.py a b   (names under data/generated/work/plots_recall, without _outlines.json)"""

import json
import sys
from pathlib import Path

WORK = Path(__file__).resolve().parents[2] / "data" / "generated" / "work" / "plots_recall"
a, b = (json.loads((WORK / f"{n}_outlines.json").read_text())["outlines"] for n in sys.argv[1:3])
for x, y in zip(a, b):
    if abs(x["iou"] - y["iou"]) > 0.01 or abs(x["cover"] - y["cover"]) > 0.01:
        print(f"#{x['i']:2d} {x['label']:9s} {x['half']} {x['m2']:6d} m2  IoU {x['iou']:.2f} -> {y['iou']:.2f}  cover {x['cover']:.2f} -> {y['cover']:.2f}")
