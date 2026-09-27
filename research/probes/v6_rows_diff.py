"""Per-field matched-piece differences between two v6_rows_eval reports. Run: python v6_rows_diff.py a_eval.json b_eval.json"""
import json
import sys

a, b = (json.load(open(p))["fields"] for p in sys.argv[1:3])
for f in sorted(set(a) | set(b), key=lambda f: (b.get(f, {}).get("tp", 0) - a.get(f, {}).get("tp", 0))):
    x, y = a.get(f, {}), b.get(f, {})
    if x.get("tp", 0) != y.get("tp", 0) or x.get("pred_pieces") != y.get("pred_pieces"):
        print(f"{f:12s} tp {x.get('tp', 0)} -> {y.get('tp', 0)}  pred {x.get('pred_pieces')} -> {y.get('pred_pieces')}  ref {y.get('ref_pieces', x.get('ref_pieces'))}")
