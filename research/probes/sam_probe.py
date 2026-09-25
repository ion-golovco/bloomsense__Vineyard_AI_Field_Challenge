"""SAM 2.1 tiny refinement (`marcaj.sam`) against the rule-based canopy (`marcaj.canopy`) on the two organizer
reference tiles, scored with the organizer formulas (`marcaj.judge`). Evaluation only; a sanity check, not a holdout.
Run from backend/: uv run --frozen --group sam python ../research/probes/sam_probe.py [plots.json]"""

import json
import sys
import time
from pathlib import Path

from shapely.geometry import mapping, shape

from marcaj.canopy import plot_rows, read_rgb, tile_canopies
from marcaj.cvat import build_scene
from marcaj.judge import judge
from marcaj.plots import detect_plots
from marcaj.sam import refine
from marcaj.tiles import DATA_DIR, load_tiles

NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
tiles = {tile.name: tile for tile in load_tiles()}
base = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(tiles.values()))["features"]
cached = Path(sys.argv[1]) if len(sys.argv) > 1 else None
plots = json.loads(cached.read_text()) if cached and cached.is_file() else detect_plots()
if cached and not cached.is_file():
    cached.write_text(json.dumps(plots))
rows = plot_rows(plots)
candidates = {name: tile_canopies(tiles[name], rows) for name in NAMES}
images = {name: read_rgb(tiles[name]) for name in NAMES}


def score(tag: str, per_tile: dict[str, list[dict]], seconds: float = 0.0) -> None:
    report = judge({"type": "FeatureCollection", "crs": "EPSG:32635", "features": base + [f for found in per_tile.values() for f in found]})
    tiles_ = {t["tile"]: t for t in report["tiles"]}
    detail = " | ".join(f"{name[7:16]} IoU {tiles_[name]['canopy_iou']:.3f} F1 {tiles_[name]['canopy_f1']:.3f} n {tiles_[name]['counts']['vineyard'][0]}/{tiles_[name]['counts']['vineyard'][1]}" for name in NAMES)
    print(f"{tag:38s} canopy {report['scores']['canopy']:.3f} (IoU {report['canopy_iou']:.3f}, F1 {report['canopy_f1']:.3f}) | {detail}" + (f" | {seconds:.1f} s" if seconds else ""))


score("rule-based canopy (marcaj.canopy)", candidates)
for margin, pick in ((0.05, False), (0.05, True), (0.1, True)):
    started = time.perf_counter()
    refined, clipped = {}, {}
    for name in NAMES:
        polygons = [shape(f["geometry"]) for f in candidates[name]]
        outlines = refine(*images[name], polygons, margin_m=margin, pick_closest=pick)
        keep = [(f, o) for f, o in zip(candidates[name], outlines) if o is not None and o.area >= 0.2]
        refined[name] = [{**f, "geometry": mapping(o)} for f, o in keep]
        # the same, held to the rule-based outline grown by 0.15 m so SAM cannot run along the row into a neighbour
        clipped[name] = [{**f, "geometry": mapping(g)} for f, o in keep
                         for g in [o.intersection(shape(f["geometry"]).buffer(0.15))] if g.geom_type == "Polygon" and g.area >= 0.2]
    elapsed = time.perf_counter() - started
    tag = f"SAM box {margin} m{', closest of 3' if pick else ''}"
    score(tag, refined, elapsed)
    score(f"{tag}, held to rule +0.15 m", clipped)
