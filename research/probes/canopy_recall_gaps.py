"""Why each gap POI (>= 5 m canopy-free along a predicted row) has no canopy: per gap and tile it crosses, the canopy
pipeline's decision on that row's axis (dropped by the inter-row rule, dropped by the value test, kept) and what the
pixels along the gap hold within +-0.3 m of the re-fitted axis (colour 2g-r-b > 25, > 15, network > 0.2, mean g-r,
brightness). Writes gaps_diag.json and a contact sheet of the gaps. Evaluation only.
Run from backend/: uv run --frozen --group sam python ../research/probes/canopy_recall_gaps.py [pois.geojson] [tag]"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import LineString, Point, box, shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_recall_lib import PROB, WORK, frozen_plots  # noqa: E402

from marcaj import canopy, canopy_net  # noqa: E402
from marcaj.canopy import CanopyParams, fit_axis, row_spacing, value_contrast  # noqa: E402
from marcaj.tiles import PIXEL_M, load_tiles  # noqa: E402

P = CanopyParams()


def diagnose(pois: list[dict], canopies: list[dict]) -> list[dict]:
    plots = frozen_plots()
    rows = {f["properties"]["row_id"]: f for f in plots if f["properties"]["label"] == "row"}
    by_plot = defaultdict(list)
    for f in rows.values():
        by_plot[f["properties"]["vineyard_id"]].append(f)
    tiles = load_tiles()
    gaps = [p for p in pois if p["properties"]["reason"] == "gap"]
    todo = defaultdict(list)
    for g in gaps:
        line = LineString([g["properties"]["gap_start"], g["properties"]["gap_end"]])
        for t in tiles:
            if line.intersects(t.bounds) and line.intersection(t.bounds).length > 0.5:
                todo[t.name].append((g, line.intersection(t.bounds)))
    tile_by = {t.name: t for t in tiles}
    out = []
    for name, items in todo.items():
        tile = tile_by[name]
        rgb, transform = canopy.read_rgb(tile)
        prob = np.load(PROB / f"{name[:-4]}.npy").astype(np.float32) / 255
        excess, valid = canopy.excess_green(rgb, P)
        colour = (excess > P.green_dn) & valid
        green = colour & (prob > 0.2)
        gr = rgb[1].astype(np.float32) - rgb[0]
        bright = rgb.mean(0)
        for g, piece in items:
            pr = g["properties"]
            plot_axes = [shape(f["geometry"]) for f in by_plot[pr["vineyard_id"]]]
            spacing = row_spacing(plot_axes)
            bounds = box(*tile.bounds.bounds)
            clipped = {rid: shape(f["geometry"]).intersection(bounds) for rid, f in rows.items() if f["properties"]["vineyard_id"] == pr["vineyard_id"]}
            clipped = {rid: LineString(c.coords) for rid, c in clipped.items() if c.geom_type == "LineString" and c.length > 0}
            fitted = {rid: fit_axis(c, green, transform, P) for rid, c in clipped.items()}
            kept = canopy.kept_axes(list(clipped.values()), green, transform, P, spacing, excess)
            after_gap = canopy._drop_interrows([a for a, _ in fitted.values()], green, transform, P, spacing)
            axis, _ = fitted.get(pr["row_id"], (None, 0))
            if axis is None:
                continue
            in_gap = lambda a: any(a.equals_exact(k, 1e-6) for k in kept)
            status = "kept" if in_gap(axis) else ("dropped_interrow" if not any(axis.equals_exact(k, 1e-6) for k in after_gap) else "dropped_value")
            vc = value_contrast(axis, excess, transform, P)
            # pixels along the gap piece, within 0.3 m of the fitted axis
            (x0, y0), (x1, y1) = axis.coords[0], axis.coords[-1]
            d = np.array([x1 - x0, y1 - y0]) / axis.length
            rr, cc = canopy._pixels(piece.buffer(0.8, cap_style="flat"), transform, excess.shape)
            xs, ys = transform.c + (cc + 0.5) * transform.a, transform.f + (rr + 0.5) * transform.e
            v = np.abs(-(xs - x0) * d[1] + (ys - y0) * d[0])
            inside = v <= 0.3
            r, c = rr[inside], cc[inside]
            shift = float(np.hypot(*(np.array(piece.interpolate(0.5, normalized=True).coords[0]) - np.array(axis.interpolate(axis.project(piece.interpolate(0.5, normalized=True))).coords[0]))))
            out.append({"id": pr["id"], "tile": name, "row_id": pr["row_id"], "gap_m": pr["gap_m"], "piece_m": round(piece.length, 1),
                        "green_share": pr["green_share"], "status": status, "value_contrast": round(vc, 2), "axis_shift_m": round(shift, 2),
                        "colour25": round(float(colour[r, c].mean()), 3), "dn15": round(float((excess[r, c] > 15).mean()), 3),
                        "net02": round(float((prob[r, c] > 0.2).mean()), 3), "and": round(float(green[r, c].mean()), 3),
                        "gr_on_colour": round(float(gr[r, c][colour[r, c]].mean()), 1) if colour[r, c].any() else None,
                        "bright": round(float(bright[r, c].mean()), 1), "mid": list(piece.interpolate(0.5, normalized=True).coords[0]),
                        "start": pr["gap_start"], "end": pr["gap_end"]})
    return out


def sheet(diag: list[dict], canopies: list[dict], path: Path, n: int = 24, px_m: float = 0.04) -> None:
    from shapely import STRtree
    from poi_render import crop  # noqa: E402

    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    size = 320
    cells = diag[:n]
    img = Image.new("RGB", (4 * size, ((len(cells) - 1) // 4 + 1) * size), "white")
    for k, dg in enumerate(cells):
        mx, my = dg["mid"]
        half = size * px_m / 2
        x0, y0, x1, y1 = mx - half, my - half, mx + half, my + half
        import poi_render
        poi_render.PX_M = px_m
        tile_img = crop(x0, y0, x1, y1).resize((size, size))
        draw = ImageDraw.Draw(tile_img)
        to = lambda x, y: ((x - x0) / px_m, (y1 - y) / px_m)
        for i in tree.query(box(x0, y0, x1, y1)):
            ring = [to(*p) for p in geoms[i].exterior.coords]
            draw.line(ring, fill=(255, 255, 0), width=1)
        draw.line([to(*dg["start"]), to(*dg["end"])], fill=(255, 0, 0), width=2)
        draw.rectangle([0, 0, size, 12], fill="black")
        draw.text((2, 0), f"{dg['row_id']} {dg['gap_m']}m {dg['status'][:9]} vc{dg['value_contrast']} c{dg['colour25']} n{dg['net02']}", fill="white")
        img.paste(tile_img, ((k % 4) * size, (k // 4) * size))
    img.save(path, quality=85)


if __name__ == "__main__":
    pois_path = Path(sys.argv[1]) if len(sys.argv) > 1 else WORK / "pois_uploaded.geojson"
    tag = sys.argv[2] if len(sys.argv) > 2 else "uploaded"
    pois = json.loads(pois_path.read_text())["features"]
    canopies = [f for f in json.loads((Path(__file__).resolve().parents[2] / "data/generated/predictions.geojson").read_text())["features"] if f["properties"]["label"] == "vineyard"] \
        if tag == "uploaded" else json.loads((WORK / f"canopies_{tag}.json").read_text())
    diag = diagnose(pois, canopies)
    (WORK / f"gaps_diag_{tag}.json").write_text(json.dumps(diag, indent=1))
    print(Counter(d["status"] for d in diag))
    for status in ("kept", "dropped_value", "dropped_interrow"):
        sub = [d for d in diag if d["status"] == status]
        if sub:
            keys = ["colour25", "dn15", "net02", "and", "value_contrast", "axis_shift_m", "bright", "piece_m"]
            print(status, len(sub), {k: round(float(np.median([d[k] for d in sub])), 3) for k in keys}, "piece m total", round(sum(d["piece_m"] for d in sub)))
    kept = sorted([d for d in diag if d["status"] == "kept"], key=lambda d: -d["colour25"])
    print("kept with colour25 > 0.2:", sum(d["colour25"] > 0.2 for d in kept), "dn15 > 0.3:", sum(d["dn15"] > 0.3 for d in kept))
    sheet(sorted(diag, key=lambda d: -d["colour25"]), canopies, WORK / f"gaps_{tag}_most_green.jpg")
    sheet(sorted(diag, key=lambda d: d["status"] != "kept" or -d["gap_m"]), canopies, WORK / f"gaps_{tag}_dropped_first.jpg")
