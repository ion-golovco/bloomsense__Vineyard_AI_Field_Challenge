"""Plots v5: would verify_plots keep the third pass's plots? Runs the pipeline's canopy (canopy_net, combine="and") on the
tiles the new plots touch only, then vine_evidence / looks_like_vineyard per new pattern. No review data. Run from backend/:
OMP_NUM_THREADS=4 uv run --frozen --group sam python ../research/probes/plots_v5_verify.py"""

from dataclasses import replace

from shapely.geometry import shape
from shapely.ops import unary_union

from marcaj import canopy, canopy_net, plots
from marcaj.tiles import load_tiles

found = plots.detect_plots()
old = unary_union([shape(f["geometry"]) for f in plots.detect_plots(replace(plots.PlotParams(), overgrown_snr=0)) if f["properties"]["label"] == "block"])
new = {f["properties"]["pattern_id"]: shape(f["geometry"]) for f in found
       if f["properties"]["label"] == "block" and shape(f["geometry"]).intersection(old).area < 0.2 * shape(f["geometry"]).area}
print("new patterns:", {k: round(g.area) for k, g in new.items()})
area = unary_union(list(new.values()))
tiles = [t for t in load_tiles() if t.bounds.intersects(area)]
print(len(tiles), "tiles")
canopies = [f for t in tiles for f in canopy_net.tile_canopies(t, canopy.plot_rows(found), combine="and", threshold=0.2, flips=False)]
found = canopy.refit_rows(found, canopies)[0]
evidence = plots.vine_evidence(found + canopies, plots.load_excess())
for k in new:
    e = evidence[k]
    weedy = any(f["properties"].get("overgrown") for f in found if f["properties"]["label"] == "block" and f["properties"]["pattern_id"] == k)
    print(k, e["block_id"], "overgrown" if weedy else "", plots.looks_like_vineyard(e, replace(plots.PlotParams(), verify_cover=0, verify_contrast=plots.PlotParams().overgrown_contrast) if weedy else plots.PlotParams()), {n: round(e[n], 3) for n in ("cover", "contrast", "len_med", "rows", "canopies")})
# verify_plots itself: which new patterns it keeps (plots off these tiles have no canopy here, so they drop; ignore them)
kept, _, dropped = plots.verify_plots(found, canopies)
kept_area = unary_union([shape(f["geometry"]) for f in kept if f["properties"]["label"] == "block"])
for k, g in new.items():
    print("verify_plots:", k, "kept" if g.intersection(kept_area).area > 0.5 * g.area else "dropped")
