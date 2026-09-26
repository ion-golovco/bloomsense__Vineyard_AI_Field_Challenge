"""Downstream check of a plot detector change: plots -> rows -> inter-rows -> canopies (reference tiles only) -> per-tile
attributes, judged with the organizer formulas on the two reference tiles (a sanity check, not a holdout). Mirrors
`marcaj.predict` and never writes data/generated/predictions.geojson.
Run from backend/: uv run --frozen python ../research/probes/plot_pipeline_check.py [plots.geojson | name=value ...]"""

import json
import sys
import time
from dataclasses import fields, replace
from pathlib import Path

from marcaj import canopy, plots, rows
from marcaj.cvat import build_scene
from marcaj.judge import judge
from marcaj.tiles import DATA_DIR, load_tiles

REFERENCE = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]


def main() -> None:
    started = time.perf_counter()
    cached = next((Path(a) for a in sys.argv[1:] if "=" not in a), None)
    if cached:
        found = [f for f in json.loads(cached.read_text())["features"] if f["properties"]["label"] in ("block", "row")]
    else:
        base = plots.PlotParams()
        kinds = {f.name: type(getattr(base, f.name)) for f in fields(base)}
        found = plots.detect_plots(replace(base, **{k: kinds[k](v) for k, v in (a.split("=") for a in sys.argv[1:])}))
    tiles = load_tiles()
    reference = [tile for tile in tiles if tile.name in REFERENCE]
    plot_rows = canopy.plot_rows(found)
    found = found + rows.interrow_areas(found, plots.exclusions())
    found += [feature for tile in reference for feature in canopy.tile_canopies(tile, plot_rows)]
    predicted = [{**f, "properties": {**f["properties"], "source": "prediction"}} for f in rows.per_tile(found, reference)]
    scene = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], predicted, tiles=tiles)
    report = judge(scene)
    s = report["scores"]
    per_tile = " | ".join(f"{t['tile'][7:16]} axes {t['axis_f1']:.3f} inter-row {t['interrow_f1']:.3f} canopy {t['canopy_iou']:.3f}" for t in report["tiles"])
    print(f"{cached or sys.argv[1:] or 'defaults'}: canopy {s['canopy']:.3f} axes {s['axes']:.3f} attributes {s['attributes']:.3f} "
          f"grouping {s['grouping']:.3f} counts {s['counts']:.3f} -> {report['points']} of {report['points_available']} | {per_tile} | "
          f"rows {report['measures']['predicted']['row_count']}/{report['measures']['reference']['row_count']} "
          f"length {report['measures']['predicted']['row_length_m']:.0f}/{report['measures']['reference']['row_length_m']:.0f} m | {time.perf_counter() - started:.0f} s")


if __name__ == "__main__":
    main()
