"""v6 oracle canopy: the canopy stage on the team's reference rows, per variant.
  quick: judge canopy on the two organizer tiles only (seconds).
  site:  every tile a reference row crosses (heavy lock, 2 workers): judge, row cover (all, central fields, V08-04),
         off-row canopy, the user's canopy labels, gap POIs on the reference rows against the gap labels and the
         organizers' own gaps. Appends to OUT/oracle.jsonl, saves canopies per variant.
Run from backend/: uv run --frozen --group sam python ../research/probes/v6_canopy_oracle.py quick|site VARIANT..."""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import v6_canopy_lib as L  # noqa: E402

from marcaj import poi  # noqa: E402
from marcaj.canopy import CanopyParams  # noqa: E402

D = CanopyParams()
P = lambda **kw: replace(D, **kw)  # noqa: E731
# name -> (params, combine, axes): axes "global" = one straight line per row_id (what predict.py feeds), "pieces" = the
# reference per-tile segments
VARIANTS = {
    "defaults": (D, "and", "global"),
    "unweighted": (P(refine_weighted=False), "and", "global"),
    "w_nodrop": (P(row_gap=0, row_value=0, grass_ratio=0), "and", "global"),
    "w_rv10": (P(row_value=1.0), "and", "global"),
    "w_rv11": (P(row_value=1.1), "and", "global"),
    "pieces": (D, "and", "pieces"),
    "rules": (D, "rules", "global"),
    "norefit": (P(refine_m=0, refine2_m=0), "and", "global"),
    "norefit_pieces": (P(refine_m=0, refine2_m=0), "and", "pieces"),
    "refine1only": (P(refine2_m=0), "and", "global"),
    "refine03": (P(refine_m=0.3, refine2_m=0.2), "and", "global"),
    "refine02": (P(refine_m=0.2, refine2_m=0.1), "and", "global"),
    "nodrop": (P(row_gap=0, row_value=0, grass_ratio=0), "and", "global"),
    "norowvalue": (P(row_value=0), "and", "global"),
    "norowgap": (P(row_gap=0), "and", "global"),
    "nograss": (P(grass_ratio=0), "and", "global"),
    "nostrip": (P(strip_gr=0), "and", "global"),
    "noveg": (P(veg_gr=0), "and", "global"),
    "noweak": (P(weak_dn=0), "and", "global"),
    "noyoung": (P(row_young=0, young_pieces=0), "and", "global"),
}


def eval_site(name: str, canopies: list, pieces: list, plots: list) -> dict:
    block = {f["properties"]["row_id"]: f["properties"].get("vineyard_id", "") for f in pieces}
    rows = L.sampled(pieces, canopies)
    cov = L.cover(rows, block)
    pois = poi.gap_pois(rows, obstacles=L.obstacles())
    fixed = poi.gap_pois(rows, obstacles=L.obstacles(), rule="fixed")
    m = {"name": name, "judge": L.judge_canopy(canopies), "n": len(canopies), "area": round(sum(__import__("shapely").geometry.shape(f["geometry"]).area for f in canopies)),
         "cover": {k: v for k, v in cov.items() if k in ("all", "V19-11", "V21-13", "V22-13", "V08-04")},
         "off_row_m2": L.off_row(canopies, pieces), "labels": L.canopy_labels(canopies),
         "gaps": L.gap_metrics(pois), "gaps_fixed": L.gap_metrics(fixed), "example_recall": L.example_recall(pois)}
    return m


def main() -> None:
    mode, names = sys.argv[1], sys.argv[2:]
    pieces = L.ref_pieces()
    plots = L.plot_features()
    print(f"reference: {len(pieces)} pieces, {sum(f['properties']['label'] == 'row' for f in plots)} rows, "
          f"{sum(f['properties']['label'] == 'block' for f in plots)} patterns ({'READY' if (L.REF / 'READY').is_file() else 'export fallback'})", flush=True)
    tiles = L.NAMES if mode == "quick" else L.vine_tiles(plots)
    L.cache_probabilities(tiles)
    lock = L.heavy_lock() if mode == "site" else None
    try:
        for name in names:
            params, combine, axes = VARIANTS[name]
            started = time.perf_counter()
            canopies = L.run(params, plots, tiles, pieces if axes == "pieces" else None, combine)
            if mode == "quick":
                j = L.judge_canopy(canopies)
                text = f"{name:18s} {json.dumps(j)}"
            else:
                (L.OUT / f"canopies_{name}.json").write_text(json.dumps(canopies))
                m = eval_site(name, canopies, pieces, plots)
                m["seconds"] = round(time.perf_counter() - started)
                with (L.OUT / "oracle.jsonl").open("a") as handle:
                    handle.write(json.dumps(m) + "\n")
                text = json.dumps(m)
            print(text, flush=True)
            with (L.OUT / f"{mode}.txt").open("a") as handle:
                handle.write(time.strftime("%H:%M ") + text + "\n")
    finally:
        if lock:
            L.release(lock)


if __name__ == "__main__":
    main()
