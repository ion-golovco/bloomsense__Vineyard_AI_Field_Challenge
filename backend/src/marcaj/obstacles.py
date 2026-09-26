"""Obstacles in and at the edge of vine blocks: buildings, sheds and tree crowns, found on the 0.2 m mosaic.

A vine canopy is under 1 m wide; a tree crown is 2-4 m and a shed more. So an obstacle is a region of one colour class
that survives a binary opening with a disk `OPEN_M` wide, which no vine row does:
- `tree`: ExG (2g - r - b, pixel values) averaged over 1 m above `TREE_EXG` (crowns 45-77, a vine row under 20 there);
- `building`: roof colours, never soil or vine: blue-green or grey-blue (b > r), red, or white.
Passages and forbidden zones are cut out first (roads are concrete-white and never an obstacle in a block). Each keeps
the share of deep shadow (mean RGB under `SHADOW_DN`) in a ring out to `SHADOW_M`: a tall object casts one, a weed
patch or bright soil does not, and `confidence` rises with it.

Used by `rows.interrow_areas` (the rules: "Holes: cut out trees and buildings standing in the inter-row") and by
`poi.gap_pois` (a row stretch that runs up to an obstacle is not missing planting).
Run: uv run --frozen python -m marcaj.obstacles [--scene S] [--output O]"""

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine
from rasterio.windows import from_bounds
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from marcaj.mosaic import MOSAIC_PATH, MOSAIC_PX_M
from marcaj.tiles import DATA_DIR, REPO_ROOT

OBSTACLES_PATH = REPO_ROOT / "data" / "generated" / "work" / "obstacles" / "obstacles.geojson"
PREDICTIONS_PATH = REPO_ROOT / "data" / "generated" / "predictions.geojson"
REACH_M = 4.0        # obstacles within this of a block count: a tree at the block edge ends its rows
OPEN_M = 2.2         # opening disk diameter: a vine canopy (median 0.6 m wide) never survives it
TREE_EXG = 35.0
SHADOW_DN = 45.0
SHADOW_M = 3.0
MIN_AREA_M2 = 3.0
CROWN_TEXTURE = 17.0
TALL_SHARE = 0.12    # deep shadow in or around a crown: a tree is tall, a weed or grass patch is not
CROWN_BLUE_GREEN = 0.56  # median b/g of a tree crown is 0.31-0.55; grass and weeds 0.57-0.69


def _disk(radius_px: int) -> np.ndarray:
    y, x = np.mgrid[-radius_px:radius_px + 1, -radius_px:radius_px + 1]
    return np.hypot(y, x) <= radius_px


def _masks(rgb: np.ndarray) -> dict[str, np.ndarray]:
    red, green, blue = rgb.astype(np.float32)
    bright = (red + green + blue) / 3
    chroma = np.max(rgb, axis=0).astype(np.float32) - np.min(rgb, axis=0)
    size = round(1.0 / MOSAIC_PX_M)
    excess = ndimage.uniform_filter(2 * green - red - blue, size)
    # a crown is lit and self-shadowed leaf by leaf: brightness s.d. over 1 m is 25-35 there, 13-20 on grass and weeds
    texture = np.sqrt(np.maximum(ndimage.uniform_filter(bright ** 2, size) - ndimage.uniform_filter(bright, size) ** 2, 0))
    roof = ((blue - red > 15) & (chroma > 20) & (bright > 60)) | ((red - np.maximum(green, blue) > 40) & (red > 110)) | (bright > 200)
    return {"green": excess > TREE_EXG, "crown": (excess > TREE_EXG) & (texture > CROWN_TEXTURE), "building": roof, "shadow": (bright < SHADOW_DN) & (bright > 0),
            "blue_green": blue / np.maximum(green, 1), "roof_colour": roof & (bright <= 200)}


def detect(features: list[dict[str, Any]], exclusions, mosaic_path: Path = MOSAIC_PATH) -> list[dict[str, Any]]:
    """`obstacle` polygons (EPSG:32635) within `REACH_M` of any `block` feature, with `obstacle_type`, `area_m2`,
    `width_m` / `length_m` (rotated rectangle), `shadow_share` (ring) and `dark_share` (inside), `in_block` (share of
    it inside a block) and `confidence`."""
    blocks = [shape(f["geometry"]) for f in features if f["properties"].get("label") == "block"]
    if not blocks:
        return []
    reach = unary_union([block.buffer(REACH_M) for block in blocks])
    radius = round(OPEN_M / MOSAIC_PX_M / 2)
    out = []
    with rasterio.open(mosaic_path) as source:
        for area in getattr(reach, "geoms", [reach]):
            window = from_bounds(*area.buffer(SHADOW_M + 1).bounds, transform=source.transform).round_offsets().round_lengths()
            rgb = source.read(window=window)
            transform = source.window_transform(window)
            keep = rasterize([area.difference(exclusions)], out_shape=rgb.shape[1:], transform=transform).astype(bool)
            masks = _masks(rgb)
            ring = _disk(round(SHADOW_M / MOSAIC_PX_M))
            for kind in ("building", "tree"):
                back = _disk(round(1.0 / MOSAIC_PX_M))
                if kind == "building":  # grown back 1 m over the roof colour: the opening eats a narrow eave
                    found = masks["building"] & keep & ndimage.binary_dilation(ndimage.binary_opening(masks["building"] & keep, _disk(radius)), back)
                else:  # textured crown cores, grown back 1 m over the green (grass next to a tree stays out)
                    core = ndimage.binary_opening(masks["crown"] & keep, _disk(radius - 1))
                    grown = masks["green"] & keep & ndimage.binary_dilation(core, back)
                    found = ndimage.binary_opening(grown, _disk(radius)) & ~ndimage.binary_dilation(masks["building"], iterations=3)  # a teal roof has ExG 99
                labels, _ = ndimage.label(ndimage.binary_fill_holes(found))
                pad = ring.shape[0] // 2 + 1
                for index, piece in enumerate(ndimage.find_objects(labels), start=1):
                    if piece is None:
                        continue
                    cut = tuple(slice(max(part.start - pad, 0), part.stop + pad) for part in piece)
                    one = labels[cut] == index
                    if one.sum() * MOSAIC_PX_M ** 2 < MIN_AREA_M2:
                        continue
                    around = ndimage.binary_dilation(one, ring) & ~one
                    shadow = float(masks["shadow"][cut][around].mean())
                    dark = float(masks["shadow"][cut][one].mean())
                    tall = max(shadow, dark)
                    if kind == "tree" and (tall < TALL_SHARE or np.median(masks["blue_green"][cut][one]) >= CROWN_BLUE_GREEN):
                        continue
                    if kind == "building" and masks["roof_colour"][cut][one].mean() < 0.5 and shadow < TALL_SHARE:
                        continue  # white without a shadow is bare sand, mulch or concrete
                    at = transform * Affine.translation(cut[1].start, cut[0].start)
                    polygon = unary_union([shape(g) for g, v in shapes(one.astype(np.uint8), mask=one, transform=at) if v]).buffer(0)
                    with np.errstate(all="ignore"):  # collinear hull points warn inside shapely
                        corners = np.asarray(polygon.minimum_rotated_rectangle.exterior.coords)
                    sides = sorted(np.hypot(*np.diff(corners[:3], axis=0).T))
                    polygon = polygon.simplify(0.1)
                    out.append({"type": "Feature", "geometry": mapping(polygon), "properties": {
                        "label": "obstacle", "source": "prediction", "obstacle_type": kind, "area_m2": round(polygon.area, 1),
                        "width_m": round(float(sides[0]), 1), "length_m": round(float(sides[-1]), 1),
                        "shadow_share": round(shadow, 2), "dark_share": round(dark, 2),
                        "in_block": round(sum(block.intersection(polygon).area for block in blocks) / polygon.area, 2),
                        "confidence": round(min(1.0, 0.5 + tall / (2 * 0.25)), 2) if kind == "tree" else 1.0 if masks["roof_colour"][cut][one].mean() >= 0.5 else 0.7}})
    return out


def write(obstacles: list[dict[str, Any]], path: Path = OBSTACLES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": obstacles}), encoding="utf-8")
    return path


if __name__ == "__main__":
    import argparse

    from marcaj.plots import exclusions

    parser = argparse.ArgumentParser(description="Obstacle polygons near the predicted blocks")
    parser.add_argument("--scene", type=Path, default=PREDICTIONS_PATH, help="EPSG:32635 GeoJSON with block features")
    parser.add_argument("--output", type=Path, default=OBSTACLES_PATH)
    args = parser.parse_args()
    started = time.perf_counter()
    found = detect(json.loads(args.scene.read_text(encoding="utf-8"))["features"], exclusions(DATA_DIR))
    counts: dict[str, int] = {}
    for feature in found:
        counts[feature["properties"]["obstacle_type"]] = counts.get(feature["properties"]["obstacle_type"], 0) + 1
    print(f"{counts} in {time.perf_counter() - started:.1f} s -> {write(found, args.output)}")
