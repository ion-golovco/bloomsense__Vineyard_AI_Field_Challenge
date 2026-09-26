"""Low-threshold waste candidates in the predicted inter-rows of the southern tiles (tile row r >= 24), for review by eye.
Per tile at native 0.025 m, inside that tile's `interrow_area` polygons only: pixels whose RGB distance from the 2 m median
background is >= DEV_MIN, not green, plus colour cues that soil and grass do not have (white, blue, vivid red/orange/yellow,
very dark). Pieces within 0.05 m form one blob, kept from 0.03 m2 to 4 m2. Writes south_candidates.json (ranked).
Run from backend/:
  uv run --frozen python ../research/probes/waste_south.py scan
  uv run --frozen python ../research/probes/waste_south.py sheet KIND FIRST COUNT [RANKED.json]   # south_<kind>_NN.jpg"""

import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.features import rasterize
from rasterio.windows import Window
from scipy import ndimage
from shapely.geometry import box, shape

from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, load_tiles
from marcaj.waste import BG_FACTOR, EIGHT, _coarse, _fine

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
CANDIDATES = W / "south_candidates.json"
DEV_MIN = 55.0
MIN_M2, MAX_M2 = 0.03, 4.0


def south_interrows() -> dict[str, list]:
    features = json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]
    by_tile: dict[str, list] = {}
    for f in features:
        p = f["properties"]
        if p["label"] == "interrow_area" and int(re.search(r"_r(\d+)_", p["tile"]).group(1)) >= 24:
            by_tile.setdefault(p["tile"], []).append((shape(f["geometry"]), p["vineyard_id"]))
    return by_tile


CUE_WEIGHT = {"speck": 1.0, "white": 3.0, "neutral": 2.0, "blue": 4.0, "vivid": 4.0, "glint": 2.0, "dark": 1.0, "dev": 1.0}
CUE_MAX_M2 = {"speck": 0.03}
CUE_MIN_M2 = {"speck": 0.003, "white": 0.01, "neutral": 0.01, "blue": 0.004, "vivid": 0.004, "glint": 0.002, "dark": 0.05, "dev": MIN_M2}


def cue_masks(rgb: np.ndarray, deviation: np.ndarray, inside: np.ndarray, bg_br: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Per-pixel evidence by kind. white: neutral bright (pale soil is yellower, b - r < -18; silver shrubs are greener,
    g - (r + b) / 2 > 8); blue; vivid non-green colour; glint: near-saturated; dark: very dark and far from the background;
    dev: any strong departure from the background that is not green."""
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    chroma = rgb.max(0) - rgb.min(0)
    exg = (2 * g - r - b) / np.maximum(rgb.sum(0), 1)
    masks = {
        "white": (lum >= 200) & (chroma <= 35) & (g - (r + b) / 2 <= 8) & (b - r >= -18),
        # soil is warm (b - r near -38 in most tiles, but some tiles are cooler): grey or dirty-white plastic, metal and
        # concrete are bluer than the local background, and not greener than it
        "neutral": (lum >= 120) & (lum < 200) & (chroma <= 40) & (g - (r + b) / 2 <= 8)
                   & ((b - r) - (bg_br if bg_br is not None else -38.0) >= 22) & (b - r >= -15),
        "blue": (b - np.maximum(r, g) >= 10) & (b >= 80),
        "vivid": (chroma >= 80) & (exg < 0) & ~((r > g) & (g > b) & (chroma < 110)),  # soil and dry leaves are r > g > b at low chroma
        "glint": lum >= 245,
        # small pale items (a can, a cup, a scrap of paper or film), with no opening: the white rule loosened toward
        # cream, and clearly brighter than the local background
        "speck": (lum >= 185) & (chroma <= 45) & (g - (r + b) / 2 <= 8) & (b - r >= -25) & (deviation >= 60)
                 & ((b - r) - (bg_br if bg_br is not None else -38.0) >= 12),
        "dark": (lum <= 35) & (deviation >= DEV_MIN),
        "dev": (deviation >= DEV_MIN) & (exg < 0.08) & (lum >= 50),
    }
    return {k: ndimage.binary_opening(inside & m, EIGHT) if k in ("white", "neutral", "dev", "dark") else inside & m for k, m in masks.items()}


def scan(rest: bool = False) -> None:
    """rest=False: the inter-row pixels. rest=True: the rest of the predicted blocks on the same tiles (canopy strips,
    headlands, unpredicted inter-rows), with the colour cues only; writes south_rest_candidates.json."""
    started = time.perf_counter()
    interrows = south_interrows()
    tiles = {t.name: t for t in load_tiles()}
    blocks = [(shape(f["geometry"]), f["properties"]["vineyard_id"]) for f in json.loads(
        (REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"] if f["properties"]["label"] == "block"] if rest else []
    found = []
    for n, name in enumerate(sorted(interrows)):
        tile = tiles[name]
        with rasterio.open(tile.path) as source:
            rgb, transform = source.read().astype(np.float32), source.transform
        zones = [(p, v) for p, v in blocks if p.intersects(tile.bounds)] if rest else interrows[name]
        block_of = rasterize([(p, i + 1) for i, (p, _) in enumerate(zones)], out_shape=(TILE_PX, TILE_PX), transform=transform, dtype="int32")
        nodata = ndimage.binary_dilation(rgb.max(0) <= 2, EIGHT, iterations=4)
        inside = (block_of > 0) & ~nodata
        if rest:
            inside &= ~rasterize([p for p, _ in interrows[name]], out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool)
        background = np.stack([_fine(ndimage.median_filter(_coarse(band), size=11)) for band in rgb])
        deviation = np.sqrt(((rgb - background) ** 2).sum(0))
        lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        bg_lum = 0.299 * background[0] + 0.587 * background[1] + 0.114 * background[2]
        bg_br = background[2] - background[0]
        green = (2 * rgb[1] - rgb[0] - rgb[2]) / np.maximum(rgb.sum(0), 1) > 0.05
        del background
        for kind, mask_all in cue_masks(rgb, deviation, inside, bg_br).items():
            if rest and kind in ("dev", "dark", "speck"):
                continue
            labels, _ = ndimage.label(ndimage.binary_dilation(mask_all, EIGHT, iterations=1), EIGHT)
            labels[~mask_all] = 0
            for index, window in enumerate(ndimage.find_objects(labels), start=1):
                if window is None:
                    continue
                mask = labels[window] == index
                area = float(mask.sum()) * PIXEL_M ** 2
                if not CUE_MIN_M2[kind] <= area <= CUE_MAX_M2.get(kind, MAX_M2):
                    continue
                ys, xs = np.nonzero(mask)
                r0, c0 = window[0].start + ys.min(), window[1].start + xs.min()
                r1, c1 = window[0].start + ys.max() + 1, window[1].start + xs.max() + 1
                spread = (np.sqrt(np.maximum(np.linalg.eigvalsh(np.cov(np.vstack([xs, ys]).astype(float))), 0)) * PIXEL_M
                          if len(xs) > 2 else np.zeros(2))
                pix = rgb[:, window[0], window[1]][:, mask]
                mr, mg, mb = (float(v) for v in pix.mean(1))
                dev = float(deviation[window][mask].mean())
                dlum = float((lum[window][mask] - bg_lum[window][mask]).mean())
                contrast = float(np.abs(lum[window][mask] - bg_lum[window][mask]).mean())
                d_br = float((pix[2] - pix[0]).mean() - bg_br[window][mask].mean())
                # shadows fall to the upper left (W/N) of what casts them: green on the E, SE or S side marks a shadow
                h, w = r1 - r0, c1 - c0
                sides = [green[max(r0 + dy * h, 0):r1 + dy * h, max(c0 + dx * w, 0):c1 + dx * w] for dx, dy in ((1, 0), (1, 1), (0, 1))]
                sun_green = max((float(side.mean()) for side in sides if side.size), default=0.0)
                weight = CUE_WEIGHT[kind] * (0.4 if spread[0] < 0.03 and spread[1] > 0.15 else 1.0)  # a line: lying tube, stake, wire
                world = tile.to_world(box(c0, r0, c1, r1))
                block = np.bincount(block_of[window][mask]).argmax()
                found.append({
                    "tile": name, "px": [int(c0), int(r0), int(c1), int(r1)], "bounds": [round(v, 3) for v in world.bounds],
                    "kind": kind, "area_m2": round(area, 4), "dev": round(dev, 1), "contrast": round(contrast, 1), "dlum": round(dlum, 1), "d_br": round(d_br, 1), "sun_green": round(sun_green, 2),
                    "rgb": [round(mr), round(mg), round(mb)], "width_m": round(float(spread[0]), 3), "length_m": round(float(spread[1]), 3),
                    "fill": round(float(mask.mean()), 2), "vineyard_id": zones[block - 1][1] if block else "",
                    "score": round(area ** 0.5 * (dev + contrast) / 100 * weight, 3),
                })
        if n % 10 == 9:
            print(f"{n + 1} tiles, {len(found)} candidates, {time.perf_counter() - started:.0f} s", flush=True)
    found.sort(key=lambda c: -c["score"])
    path = CANDIDATES.with_name("south_rest_candidates.json") if rest else CANDIDATES
    path.write_text(json.dumps(found))
    print(f"{len(found)} candidates on {len(interrows)} tiles in {time.perf_counter() - started:.0f} s -> {path}")


CLOSE_M, CONTEXT_M, CELL, COLS, ROWS = 2.0, 8.0, 160, 4, 6


def _crop(path: Path, cx: float, cy: float, size_m: float) -> Image.Image:
    half = size_m / 2 / PIXEL_M
    with rasterio.open(path) as source:
        rgb = source.read(window=Window(cx - half, cy - half, 2 * half, 2 * half), boundless=True, fill_value=0, out_shape=(3, CELL, CELL))
    return Image.fromarray(np.moveaxis(rgb, 0, -1))


def cell(c: dict, number: int) -> Image.Image:
    c0, r0, c1, r1 = c["px"]
    cx, cy = (c0 + c1) / 2, (r0 + r1) / 2
    image = Image.new("RGB", (2 * CELL, CELL + 22), "white")
    close_m = 1.2 if c["kind"] == "speck" else CLOSE_M
    for i, size_m in enumerate((max(close_m, 2.2 * max(c1 - c0, r1 - r0) * PIXEL_M), CONTEXT_M)):
        crop = _crop(DATA_DIR / "tiles" / c["tile"], cx, cy, size_m)
        s = CELL / (size_m / PIXEL_M)
        pad = 3
        ImageDraw.Draw(crop).rectangle([CELL / 2 + (c0 - cx) * s - pad, CELL / 2 + (r0 - cy) * s - pad,
                                        CELL / 2 + (c1 - cx) * s + pad, CELL / 2 + (r1 - cy) * s + pad], outline=(255, 0, 255), width=1)
        image.paste(crop, (i * CELL, 0))
    ImageDraw.Draw(image).text((2, CELL + 1), f"#{number} {c['tile'][8:-4]} x{int(cx)} y{int(cy)} {c['kind'][:2]} {c['area_m2']:.2f}", fill="black")
    ImageDraw.Draw(image).text((2, CELL + 11), f"w{c['width_m']:.2f} L{c['length_m']:.2f} {c['vineyard_id']} s{c['score']:.2f}", fill="black")
    return image


def sheet(items: list[tuple[int, dict]], path: Path) -> None:
    rows = (len(items) - 1) // COLS + 1
    out = Image.new("RGB", (COLS * 2 * CELL + (COLS - 1) * 6, rows * (CELL + 26)), (70, 70, 70))
    for i, (number, c) in enumerate(items):
        out.paste(cell(c, number), ((i % COLS) * (2 * CELL + 6), (i // COLS) * (CELL + 26)))
    out.save(path, quality=90)


if __name__ == "__main__":
    if sys.argv[1] == "scan":
        scan(rest="--rest" in sys.argv)
    elif sys.argv[1] == "tiles":  # tiles [NAME ...]: each tile at 0.05 m/px, outside the inter-rows dimmed, grid every 256 native px
        interrows = south_interrows()
        for name in sys.argv[2:] or sorted(interrows):
            name = name if name.endswith(".tif") else f"siret3_{name}.tif"
            with rasterio.open(DATA_DIR / "tiles" / name) as source:
                rgb, transform = source.read(out_shape=(3, TILE_PX // 2, TILE_PX // 2)), source.transform
            inside = rasterize([p for p, _ in interrows[name]], out_shape=(TILE_PX // 2, TILE_PX // 2), transform=transform * transform.scale(2, 2)).astype(bool)
            image = np.moveaxis(rgb, 0, -1).copy()
            image[~inside] = image[~inside] // 3
            out = Image.fromarray(image)
            draw = ImageDraw.Draw(out)
            for k in range(0, TILE_PX // 2, 128):
                draw.line([(k, 0), (k, TILE_PX // 2)], fill=(0, 255, 255), width=1)
                draw.line([(0, k), (TILE_PX // 2, k)], fill=(0, 255, 255), width=1)
                draw.text((k + 2, 2), str(2 * k), fill=(0, 255, 255))
                draw.text((2, k + 2), str(2 * k), fill=(0, 255, 255))
            draw.text((900, 1010), name[7:-4], fill=(255, 0, 255))
            out.save(W / f"south_tile_{name[7:-4]}.jpg", quality=88)
        print(len(sys.argv[2:]) or len(interrows), "tile views")
    elif sys.argv[1] == "zoom":  # zoom NAME TILE:X:Y[:SIZE_M] ...: native crops at 3x, SIZE_M default 3 m, into south_zoom_NAME.jpg
        crops = []
        for spec in sys.argv[3:]:
            tile, x, y, *size = spec.split(":")
            size_m = float(size[0]) if size else 3.0
            half = size_m / 2 / PIXEL_M
            with rasterio.open(DATA_DIR / "tiles" / f"siret3_{tile}.tif") as source:
                rgb = source.read(window=Window(float(x) - half, float(y) - half, 2 * half, 2 * half), boundless=True, fill_value=0)
            crop = Image.fromarray(np.moveaxis(rgb, 0, -1)).resize((360, 360), Image.NEAREST)
            ImageDraw.Draw(crop).text((3, 3), f"{tile} x{x} y{y} {size_m:g}m", fill=(255, 0, 255))
            crops.append(crop)
        out = Image.new("RGB", (min(len(crops), 4) * 364, ((len(crops) - 1) // 4 + 1) * 364), (70, 70, 70))
        for i, crop in enumerate(crops):
            out.paste(crop, ((i % 4) * 364, (i // 4) * 364))
        out.save(W / f"south_zoom_{sys.argv[2]}.jpg", quality=92)
        print(W / f"south_zoom_{sys.argv[2]}.jpg")
    elif sys.argv[1] == "sheet":  # sheet KIND FIRST COUNT [JSON]: KIND filters the ranked list ("all" keeps it whole)
        kind, first, count = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
        ranked = json.loads(Path(sys.argv[5] if len(sys.argv) > 5 else CANDIDATES).read_text())
        ranked = [c for c in ranked if kind == "all" or c["kind"] == kind]
        per = COLS * ROWS
        for start in range(first, min(first + count, len(ranked)), per):
            chunk = [(k, ranked[k]) for k in range(start, min(start + per, first + count, len(ranked)))]
            path = W / f"south_{kind}_{start // per + 1:02d}.jpg"
            sheet(chunk, path)
            print(path)
