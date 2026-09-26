"""Per reference row on the two organizer tiles: signed distance of its two end points to the lattice row, the refit
row (`canopy.refit_rows`, one angle per plot), a free per-row line and a per-tile line through the same canopy slices,
and canopy.py's own per-tile axis (`kept_axes` on the tile's colour mask). Evaluation only; after rows_refit_measure.py."""

import json

import numpy as np
from shapely.geometry import LineString, Point, shape

from marcaj import canopy
from marcaj.canopy import CanopyParams
from marcaj.tiles import load_tiles

from rows_refit_measure import PREDICTIONS, SCENE, WORK, plots_input

NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]


def signed(line, point):
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    n = np.array([-(y1 - y0), x1 - x0]) / line.length
    return float((np.asarray(point) - [x0, y0]) @ n)


def main():
    plots = plots_input()
    features = json.loads(PREDICTIONS.read_text())["features"]
    canopies = [f for f in features if f["properties"]["label"] == "vineyard"]
    refit, _ = canopy.refit_rows(plots, canopies)
    lattice = {f["properties"]["row_id"]: shape(f["geometry"]) for f in plots if f["properties"]["label"] == "row"}
    moved = {f["properties"]["row_id"]: shape(f["geometry"]) for f in refit if f["properties"]["label"] == "row"}
    plot_of = {f["properties"]["row_id"]: f["properties"]["vineyard_id"] for f in plots if f["properties"]["label"] == "row"}
    scene = json.loads(SCENE.read_text())["features"]
    tiles = {t.name: t for t in load_tiles()}
    params = CanopyParams()
    for name in NAMES:
        tile = tiles[name]
        rgb, transform = canopy.read_rgb(tile)
        green = canopy.green_mask(rgb, params)
        ref = [shape(f["geometry"]) for f in scene if f["properties"].get("source") == "reference" and f["properties"].get("label") == "row" and f["properties"].get("tile") == name]
        tile_polys = [shape(f["geometry"]) for f in canopies if shape(f["geometry"]).intersects(tile.bounds)]
        print(name)
        rows = []
        for line in ref:
            ends = [line.coords[0], line.coords[-1]]
            row_id = min(lattice, key=lambda k: sum(lattice[k].distance(Point(e)) for e in ends))
            base = lattice[row_id]
            if base.distance(Point(ends[0])) > 1.2:
                print(f"  ref row with no predicted row within 1.2 m")
                continue
            local = base.intersection(tile.bounds)
            fitted, _ = canopy.fit_axis(LineString(local.coords), green, transform, params)
            # free line through the canopy slices within 0.5 m of the refit row on this tile
            m = moved[row_id]
            (x0, y0), (x1, y1) = m.coords[0], m.coords[-1]
            d = np.array([x1 - x0, y1 - y0]) / m.length
            u, v, w = canopy._canopy_points(tile_polys, np.array([x0, y0]), d, 0.5)
            keep = (np.abs(v) < 0.5)
            a, b = np.polyfit(u[keep], v[keep], 1, w=np.sqrt(w[keep])) if keep.sum() > 3 else (0.0, 0.0)
            n = np.array([-d[1], d[0]])
            pt = lambda t: np.array([x0, y0]) + d * t + n * (a * t + b)
            per_tile = LineString([pt(0), pt(m.length)])
            vals = [[signed(g, e) for e in ends] for g in (base, m, per_tile, fitted)]
            rows.append(vals)
            print(f"  {row_id:9s} lattice {vals[0][0]:+.3f} {vals[0][1]:+.3f} | refit {vals[1][0]:+.3f} {vals[1][1]:+.3f} | "
                  f"tile slices {vals[2][0]:+.3f} {vals[2][1]:+.3f} | canopy.fit_axis {vals[3][0]:+.3f} {vals[3][1]:+.3f}")
        a = np.abs(np.array(rows))
        print("  median |d|: lattice %.3f refit %.3f tile-slices %.3f fit_axis %.3f" % tuple(np.median(a[:, k]) for k in range(4)))


if __name__ == "__main__":
    main()
