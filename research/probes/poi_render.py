"""Renders gap POIs over the native tiles: a contact sheet of random gaps and of the two example tiles.
Run from backend/: uv run --frozen python ../research/probes/poi_render.py [poi.geojson]"""

import json
import random
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.windows import from_bounds
from shapely import STRtree
from shapely.geometry import box, shape

from marcaj import poi
from marcaj.tiles import DATA_DIR, load_tiles

PX_M = 0.05
THUMB = 360
OUT = poi.POI_PATH.parent
TILES = load_tiles(DATA_DIR)


def crop(x0: float, y0: float, x1: float, y1: float) -> Image.Image:
    """RGB of a world box at PX_M from the native tiles."""
    w, h = round((x1 - x0) / PX_M), round((y1 - y0) / PX_M)
    canvas = np.zeros((3, h, w), np.uint8)
    area = box(x0, y0, x1, y1)
    for tile in TILES:
        part = tile.bounds.intersection(area)
        if part.is_empty or part.area < 1e-6:
            continue
        a, b, c, d = part.bounds
        with rasterio.open(tile.path) as source:
            window = from_bounds(a, b, c, d, source.transform)
            cw, ch = max(round((c - a) / PX_M), 1), max(round((d - b) / PX_M), 1)
            data = source.read(window=window, out_shape=(3, ch, cw))
        col, row = round((a - x0) / PX_M), round((y1 - d) / PX_M)
        canvas[:, row:row + ch, col:col + cw] = data[:, :h - row, :w - col]
    return Image.fromarray(np.moveaxis(canvas, 0, -1))


def draw_scene(image: Image.Image, bounds, features, tree, geoms, pois, highlight=None) -> Image.Image:
    x0, y0, x1, y1 = bounds
    to_px = lambda coords: [((x - x0) / PX_M, (y1 - y) / PX_M) for x, y in coords]
    draw = ImageDraw.Draw(image)
    for i in tree.query(box(*bounds)):
        f, g = features[i], geoms[i]
        label = f["properties"]["label"]
        if label == "vineyard":
            for part in getattr(g, "geoms", [g]):
                draw.line(to_px(part.exterior.coords), fill=(255, 230, 0), width=2)
        elif label == "row":
            draw.line(to_px(g.coords), fill=(0, 200, 255), width=1)
    for p in pois:
        props = p["properties"]
        if "gap_start" in props:
            colour = (255, 0, 0) if props is highlight or highlight is None else (255, 120, 120)
            draw.line(to_px([props["gap_start"], props["gap_end"]]), fill=colour, width=5)
        (cx, cy), = to_px([p["geometry"]["coordinates"]])
        draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], outline=(255, 0, 255), width=3)
        r = 2.0 / PX_M
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 0, 255), width=1)
    return image


def sheet(pois, features, name, cols=4, pad=6.0):
    geoms = [shape(f["geometry"]) for f in features]
    tree = STRtree(geoms)
    thumbs = []
    for p in pois:
        props = p["properties"]
        (a, b), (c, d) = props["gap_start"], props["gap_end"]
        cx, cy = (a + c) / 2, (b + d) / 2
        half = max(abs(c - a), abs(d - b), 12.0) / 2 + pad
        bounds = (cx - half, cy - half, cx + half, cy + half)
        image = draw_scene(crop(*bounds), bounds, features, tree, geoms, [q for q in pois if box(*bounds).contains(shape(q["geometry"]))], props)
        image = image.resize((THUMB, THUMB))
        ImageDraw.Draw(image).text((4, 4), f"{props['id']} {props['gap_m']} m", fill=(255, 255, 255))
        thumbs.append(image)
    rows = (len(thumbs) + cols - 1) // cols
    out = Image.new("RGB", (cols * THUMB, rows * THUMB), "white")
    for k, t in enumerate(thumbs):
        out.paste(t, ((k % cols) * THUMB, (k // cols) * THUMB))
    out.save(OUT / name, quality=85)
    print(OUT / name)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else poi.POI_PATH
    pois = [p for p in json.loads(path.read_text())["features"] if "gap_start" in p["properties"]]
    features = [f for f in json.loads(poi.PREDICTIONS_PATH.read_text())["features"] if f["properties"]["label"] in ("row", "vineyard")]
    random.seed(1)
    sheet(random.sample(pois, min(16, len(pois))), features, "gaps_random.jpg")


def examples(pois, features):
    """The two organizer tiles at 0.1 m: reference gaps >= 5 m (from the reference canopies) in white, POIs in magenta."""
    import zipfile

    from marcaj.cvat import read_cvat

    global PX_M
    by_name = {tile.name: tile for tile in TILES}
    with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
        reference = read_cvat(archive.read(next(n for n in archive.namelist() if n.endswith(".xml"))), by_name)
    names = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
    ref_gaps = poi.row_gaps(poi.sample_rows(reference, [by_name[n] for n in names]))
    geoms = [shape(f["geometry"]) for f in features]
    tree = STRtree(geoms)
    PX_M, images = 0.1, []
    for name in names:
        bounds = by_name[name].bounds.bounds
        image = draw_scene(crop(*bounds), bounds, features, tree, geoms, [p for p in pois if by_name[name].bounds.contains(shape(p["geometry"]))])
        draw = ImageDraw.Draw(image)
        x0, y0, x1, y1 = bounds
        for g in ref_gaps:
            if g["kind"] == "gap":
                draw.line([((x - x0) / PX_M, (y1 - y) / PX_M) for x, y in (g["start"], g["end"])], fill=(255, 255, 255), width=2)
        draw.text((4, 4), f"{name}: white = reference gaps >= 5 m, red = predicted gaps, magenta = POI + 2 m", fill=(255, 255, 255))
        images.append(image)
    out = Image.new("RGB", (sum(i.width for i in images), max(i.height for i in images)))
    for k, image in enumerate(images):
        out.paste(image, (k * images[0].width, 0))
    out.save(OUT / "gaps_examples.jpg", quality=85)
    PX_M = 0.05
    print(OUT / "gaps_examples.jpg")


def sentinel_zones(pois, features):
    """Each Sentinel low zone over the drone: 10 m pixels in white, rows and canopies, canopy-free stretches >= 2 m in
    red, the kept point in magenta."""
    import pickle  # rows.pkl is our own cache from poi_site.py, never outside data

    zones = json.loads((OUT / "sentinel_zones.geojson").read_text())["features"]
    report = {z["zone"]: z for z in json.loads((OUT / "sentinel_report.json").read_text())["zones"]}
    rows = pickle.loads((OUT / "rows.pkl").read_bytes())
    geoms = [shape(f["geometry"]) for f in features]
    tree = STRtree(geoms)
    thumbs = []
    for zone in zones:
        polygon = shape(zone["geometry"])
        c = polygon.centroid
        key = f"S-{zone['properties']['index']}-{zone['properties']['vineyard_id']}-{round(c.x) % 10000:04d}-{round(c.y) % 10000:04d}"
        x0, y0, x1, y1 = polygon.buffer(8).bounds
        half = max(x1 - x0, y1 - y0) / 2
        bounds = (c.x - half, c.y - half, c.x + half, c.y + half)
        mine = [p for p in pois if p["properties"]["id"] == key]
        image = draw_scene(crop(*bounds), bounds, features, tree, geoms, mine)
        draw = ImageDraw.Draw(image)
        to_px = lambda coords: [((x - bounds[0]) / PX_M, (bounds[3] - y) / PX_M) for x, y in coords]
        for part in getattr(polygon, "geoms", [polygon]):
            draw.line(to_px(part.exterior.coords), fill=(255, 255, 255), width=3)
        for row in rows:
            if row.vineyard_id != zone["properties"]["vineyard_id"]:
                continue
            empty = ~row.canopy & row.seen & ~row.dark
            for a, b in poi._stretches(empty, poi.SHORT_GAP_M):
                if a and b < row.n:
                    draw.line(to_px([row.xy[a], row.xy[b - 1]]), fill=(255, 0, 0), width=4)
        r = report[key]
        image = image.resize((THUMB * 2, THUMB * 2))
        ImageDraw.Draw(image).text((4, 4), f"{key} z {r['z']} {'KEPT' if r['kept'] else 'dropped'}: {r['why']}\n"
                                          f"canopy {r.get('canopy_share')} vs plot {r.get('plot_canopy_share')}, "
                                          f"gaps>=2 m/100 m {r.get('short_gaps_per_100m')} vs {r.get('plot_short_gaps_per_100m')}", fill=(255, 255, 255))
        thumbs.append(image)
    cols = 4
    out = Image.new("RGB", (cols * THUMB * 2, ((len(thumbs) + cols - 1) // cols) * THUMB * 2), "white")
    for k, t in enumerate(thumbs):
        out.paste(t, ((k % cols) * THUMB * 2, (k // cols) * THUMB * 2))
    out.save(OUT / "sentinel_zones.jpg", quality=85)
    print(OUT / "sentinel_zones.jpg")


def ndvi_map(features):
    """Latest chosen scene's NDVI at 10 m (x4), plots shrunk by 5 m in black, low zones in red."""
    from marcaj import sentinel

    scene = sentinel.scenes()[0]
    ndvi = scene.ndvi
    v = np.clip((np.nan_to_num(ndvi, nan=0) - 0.1) / 0.6, 0, 1)
    rgb = np.stack([255 * (1 - v), 255 * np.ones_like(v) * (0.4 + 0.6 * v), 80 * (1 - v)], -1).astype(np.uint8)
    rgb[np.isnan(ndvi)] = 0
    k = 4
    image = Image.fromarray(np.repeat(np.repeat(rgb, k, 0), k, 1))
    draw = ImageDraw.Draw(image)
    transform, _ = sentinel.grid()
    to_px = lambda coords: [((x - transform.c) / 10 * k, (transform.f - y) / 10 * k) for x, y in coords]
    for polygon in sentinel.plot_polygons(features).values():
        polygon = polygon.buffer(-sentinel.SHRINK_M)
        for part in getattr(polygon, "geoms", [polygon]):
            if part.geom_type == "Polygon":
                draw.line(to_px(part.exterior.coords), fill=(0, 0, 0), width=2)
    for zone in json.loads((OUT / "sentinel_zones.geojson").read_text())["features"]:
        g = shape(zone["geometry"])
        for part in getattr(g, "geoms", [g]):
            draw.line(to_px(part.exterior.coords), fill=(255, 0, 0), width=3)
    draw.text((4, 4), f"Sentinel-2 NDVI {scene.date} (0.1 red .. 0.7 green), plots -5 m black, low zones red", fill=(0, 0, 0))
    image.save(OUT / "sentinel_ndvi.jpg", quality=85)
    print(OUT / "sentinel_ndvi.jpg")


if __name__ == "__main__" and len(sys.argv) > 2:
    everything = json.loads(poi.POI_PATH.read_text())["features"]
    features = [f for f in json.loads(poi.PREDICTIONS_PATH.read_text())["features"] if f["properties"]["label"] in ("row", "vineyard", "block", "interrow_area")]
    examples([p for p in everything if p["properties"]["reason"] == "gap"], features)
    sentinel_zones(everything, features)
    ndvi_map(features)
