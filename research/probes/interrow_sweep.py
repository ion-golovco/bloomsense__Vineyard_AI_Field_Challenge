"""Whole-site sanity sweep of predicted inter-rows and row / inter-row attributes: overlaps with canopies, rows,
each other and the organizer exclusions, slivers and giants, corner pieces beyond a tile's outermost row, pieces
outside their block, CVAT hole filling, attribute mixes per plot, and what they mean for the walking route.
Read-only; renders examples to data/generated/work/interrows/.
Run from backend/: uv run --frozen python ../research/probes/interrow_sweep.py [predictions.geojson | --rebuild plots.json]
--rebuild re-runs `marcaj.rows` on cached plots with the canopies of data/generated/predictions.geojson and sweeps that
(written to data/generated/work/interrows/predictions_rows.geojson); predictions.geojson itself is never written."""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

from marcaj.rows import INTERROW_INSET_M as INSET
from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, load_tiles

PREDICTIONS = REPO_ROOT / "data" / "generated" / "predictions.geojson"
OUT = REPO_ROOT / "data" / "generated" / "work" / "interrows"
OUT.mkdir(parents=True, exist_ok=True)
tiles = {tile.name: tile for tile in load_tiles()}
if len(sys.argv) > 2 and sys.argv[1] == "--rebuild":
    from marcaj import rows as rows_module
    from marcaj.plots import exclusions
    cached = json.loads(Path(sys.argv[2]).read_text())
    old = [f for f in json.loads(PREDICTIONS.read_text())["features"] if f["properties"]["label"] == "vineyard"]
    import time
    started = time.perf_counter()
    features = rows_module.per_tile(cached + rows_module.interrow_areas(cached, exclusions()) + old, list(tiles.values()))
    print(f"interrow_areas + per_tile on all tiles: {time.perf_counter() - started:.1f} s")
    PREDICTIONS = OUT / "predictions_rows.geojson"
    PREDICTIONS.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}))
else:
    features = json.loads((Path(sys.argv[1]) if len(sys.argv) > 1 else PREDICTIONS).read_text())["features"]
route = DATA_DIR / "02_route"
passages = unary_union([shape(f["geometry"]) for f in json.loads((route / "passages.geojson").read_text())["features"]])
forbidden = unary_union([shape(f["geometry"]) for f in json.loads((route / "forbidden.geojson").read_text())["features"]])

blocks = {f["properties"]["vineyard_id"]: f for f in features if f["properties"]["label"] == "block"}
by_label: dict[str, list] = defaultdict(list)
for f in features:
    by_label[f["properties"]["label"]].append((shape(f["geometry"]), f["properties"]))
inter, rows, canopies = by_label["interrow_area"], by_label["row"], by_label["vineyard"]
tile_rows: dict[tuple[str, str], list] = defaultdict(list)
for g, p in rows:
    tile_rows[(p["tile"], p["vineyard_id"])].append(g)
canopy_tree = STRtree([g for g, _ in canopies])
inter_tree = STRtree([g for g, _ in inter])
row_tree = STRtree([g for g, _ in rows])

anomalies: dict[str, list] = defaultdict(list)
widths = []
for i, (g, p) in enumerate(inter):
    block = blocks[p["vineyard_id"]]["properties"]
    angle = np.radians(block["row_angle"])
    across = np.array([-np.sin(angle), np.cos(angle)])
    width = np.ptp(np.asarray(g.exterior.coords) @ across)
    widths.append(width)
    expected = block["row_spacing_m"] - 2 * INSET
    tag = (p["tile"], p["vineyard_id"], round(g.area, 1))
    if not g.is_valid:
        anomalies["invalid polygon"].append(tag)
    if g.interiors:
        anomalies["holes (CVAT fills them)"].append((*tag, round(sum(Polygon(h).area for h in g.interiors), 1)))
    if g.area < 1.0:
        anomalies["sliver < 1 m2"].append(tag)
    if width > 1.5 * expected:
        anomalies["giant: wider than 1.5x (spacing - 2 inset)"].append((*tag, round(width, 2), block["row_spacing_m"]))
    if width < 0.5 * expected and g.area >= 1.0:
        anomalies["narrow: under half the expected width"].append((*tag, round(width, 2), block["row_spacing_m"]))
    over = sum(g.intersection(canopies[j][0]).area for j in canopy_tree.query(g))
    if over > 0.01:
        anomalies["overlaps canopy > 0.01 m2"].append((*tag, round(over, 3)))
    crossing = sum(g.buffer(-0.01).intersection(rows[j][0]).length for j in row_tree.query(g))
    if crossing > 0.05:
        anomalies["a row axis runs inside it"].append((*tag, round(crossing, 1)))
    shared = sum(g.intersection(inter[j][0]).area for j in inter_tree.query(g) if j != i)
    if shared > 0.01:
        anomalies["overlaps another inter-row"].append((*tag, round(shared, 2)))
    if g.intersection(passages).area > 0.01:
        anomalies["on a passage"].append((*tag, round(g.intersection(passages).area, 2)))
    if g.intersection(forbidden).area > 0.01:
        anomalies["on a forbidden zone"].append((*tag, round(g.intersection(forbidden).area, 2)))
    outside = g.difference(shape(blocks[p["vineyard_id"]]["geometry"]).buffer(0.01)).area
    if outside > 0.5:
        anomalies["outside its block > 0.5 m2"].append((*tag, round(outside, 1)))
    near = [r for r in tile_rows[(p["tile"], p["vineyard_id"])] if r.distance(g) < 0.45]
    if len(near) < 2:
        anomalies["corner piece: fewer than 2 of its rows in the tile"].append((*tag, len(near)))

print(f"{len(inter)} inter-row pieces on {len({p['tile'] for _, p in inter})} tiles, {len(rows)} row pieces, {len(canopies)} canopies, {len(blocks)} plots")
print(f"width across rows: median {np.median(widths):.2f} m, p5 {np.percentile(widths, 5):.2f}, p95 {np.percentile(widths, 95):.2f}")
for name, items in anomalies.items():
    area = sum(item[2] for item in items)
    examples = ", ".join(sorted({item[0][7:16] for item in items})[:6])
    print(f"  {name}: {len(items)} pieces, {area:.1f} m2 | tiles e.g. {examples}")

one_row = [pid for pid, f in blocks.items() if f["properties"]["rows"] < 2]
print(f"row_structure: {dict(Counter(p['row_structure'] for _, p in rows))}; interrow_cover: {dict(Counter(p['interrow_cover'] for _, p in inter))}")
print(f"plots with fewer than 2 rows (no inter-rows possible): {one_row}")
no_inter = sorted({pid for pid in blocks} - {p["vineyard_id"] for _, p in inter})
print(f"plots without any inter-row: {no_inter}")

print("\nattributes per plot: rows regular/disrupted | inter-rows bare/mixed/vegetation/unassessable")
cover = defaultdict(Counter)
structure = defaultdict(Counter)
for _, p in inter:
    cover[p["vineyard_id"]][p["interrow_cover"]] += 1
for _, p in rows:
    structure[p["vineyard_id"]][p["row_structure"]] += 1
for pid in sorted(blocks):
    s, c = structure[pid], cover[pid]
    print(f"  {pid} spacing {blocks[pid]['properties']['row_spacing_m']:.2f} rows {blocks[pid]['properties']['rows']:3d}: "
          f"{s['regular']:3d}/{s['disrupted']:3d} | {c['bare_soil']:3d}/{c['mixed']:3d}/{c['vegetation']:3d}/{c['unassessable']:3d}")

# route: passable space is inter-rows plus passages minus forbidden zones
passable = unary_union([g for g, _ in inter] + [passages]).difference(forbidden)
parts = sorted(getattr(passable, "geoms", [passable]), key=lambda g: -g.area)
passage_parts = list(getattr(passages, "geoms", [passages]))
print(f"\nroute: passages {len(passage_parts)} parts; passable space (inter-rows + passages - forbidden) {len(parts)} parts, "
      f"largest {parts[0].area:.0f} m2; parts touching a passage {sum(p.intersects(passages) for p in parts)}")
bridged = [p for p in parts if sum(p.intersects(q) for q in passage_parts) >= 2]
print(f"  parts that join both passage components: {len(bridged)}")
merged = unary_union([g for g, _ in inter])
strips = list(getattr(merged, "geoms", [merged]))
gaps = np.array([s.distance(passages) for s in strips])
print(f"  merged inter-row strips: {len(strips)}; distance to the nearest passage: touching {np.mean(gaps < 0.01):.0%}, "
      f"median {np.median(gaps):.2f} m, p90 {np.percentile(gaps, 90):.2f} m, over 5 m {np.sum(gaps > 5)}")
json.dump({k: v for k, v in anomalies.items()}, (OUT / "sweep.json").open("w"), indent=0, default=str)


def render(name: str, tag: str) -> Path:
    """The tile at 1/4 resolution with rows (red), inter-rows (cyan), canopies (green) and passages (yellow)."""
    tile = tiles[name]
    with rasterio.open(tile.path) as source:
        rgb = source.read(out_shape=(3, TILE_PX // 4, TILE_PX // 4))
    image = Image.fromarray(np.moveaxis(rgb, 0, -1)).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    pix = lambda coords: [((x - tile.left) / PIXEL_M / 4, (tile.top - y) / PIXEL_M / 4) for x, y in coords]
    for g in getattr(passages.intersection(tile.bounds), "geoms", [passages.intersection(tile.bounds)]):
        if g.geom_type == "Polygon" and not g.is_empty:
            draw.polygon(pix(g.exterior.coords), fill=(255, 220, 0, 60), outline=(255, 220, 0, 255))
    for g, p in canopies:
        if g.intersects(tile.bounds):
            draw.polygon(pix(g.exterior.coords), outline=(0, 255, 0, 255))
    for g, p in inter:
        if p["tile"] == name:
            draw.polygon(pix(g.exterior.coords), fill=(0, 255, 255, 50), outline=(0, 255, 255, 255))
            draw.text(pix([g.representative_point().coords[0]])[0], p["interrow_cover"][:4], fill=(255, 255, 255, 255))
    for g, p in rows:
        if p["tile"] == name:
            draw.line(pix(g.coords), fill=(255, 0, 255, 255) if p["row_structure"] == "disrupted" else (255, 0, 0, 255), width=2)
    draw.text((4, 4), f"{name} {tag}", fill=(255, 255, 255, 255))
    path = OUT / f"{name[:-4]}_{tag}.jpg"
    image.save(path, quality=85)
    return path


if __name__ == "__main__":
    shown = set()
    for key, tag in (("giant: wider than 1.5x (spacing - 2 inset)", "giant"), ("narrow: under half the expected width", "narrow"),
                     ("overlaps canopy > 0.01 m2", "canopy_overlap"), ("corner piece: fewer than 2 of its rows in the tile", "corner"),
                     ("outside its block > 0.5 m2", "outside_block"), ("a row axis runs inside it", "row_inside")):
        for item in sorted(anomalies.get(key, []), key=lambda item: -item[2])[:2]:
            if item[0] not in shown:
                shown.add(item[0])
                print("rendered", render(item[0], tag))
