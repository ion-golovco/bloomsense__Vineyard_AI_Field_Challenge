"""Waste boxes (CVAT label `waste`) lying in the predicted inter-rows, with no training: a colour-anomaly candidate
generator and a hand-set verifier. Only litter whose box centre lies in a predicted `interrow_area` polygon is kept
(the user's decision: "we only care inter-row"), and it takes that inter-row's `vineyard_id`.

Method, per tile at the native 0.025 m, on the pixels of the predicted inter-row polygons (inset 0.30 m from the row
axes, so standing tubes, stakes, posts and canopies are outside):
1. Evidence: pixels that are neither soil nor vegetation against the 2 m median background: white (darkest channel
   >= 170 and >= 40 brighter than the background), coloured (chroma >= 60, hue outside 25-175: red, pink, blue,
   purple), or black and neutral (luminance <= 45, chroma <= 20, >= 60 darker than the background). Pieces within
   0.05 m form one blob.
2. Features per blob: area, colour class shares, luminance, chroma, hue, RGB distance from the background, the
   spread across its narrow axis and its length, and the distance to the nearest predicted row axis.
3. `accept`: white blobs of 0.03-1.5 m2 with luminance >= 220, chroma <= 15, distance >= 150, not a thin line
   (narrow spread >= 0.04 m, length <= 3x width) and >= 0.9 m from a row axis (leaning and lying white tubes and
   stakes stay within about 0.75 m of theirs); coloured blobs of >= 0.02 m2 with chroma >= 80 and distance >= 100;
   black blobs never (vine shadows fall into the inter-rows and look the same).

Measured (research/notes/waste.md): 97,215 candidates in the 1,727 inter-rows of 131 tiles in 86 s (1 GB peak, one
core of an M4 Pro). The verifier keeps 2 boxes: a white blob caught on the anchor wire of a P02 end post (r019_c013,
E 629675.3 N 5220198.9) and a crumpled white piece mid inter-row in P01 (r008_c003, E 629161.2 N 5220797.2), both
likely litter by eye. A looser first version (luminance 205, chroma 25, no row distance) kept 86 boxes, and by eye
they were silvery shrubs, pale clods and white tubes and stakes leaning or lying beside the rows, not litter.
Control: 0 boxes on the two organizer example tiles. The thresholds were set with these crops in view (in-sample).
Run: uv run --frozen python -m marcaj.waste (writes
data/generated/work/waste/waste.geojson)."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, Tile, load_tiles

WORK_DIR = REPO_ROOT / "data" / "generated" / "work" / "waste"
PREDICTIONS_PATH = REPO_ROOT / "data" / "generated" / "predictions.geojson"
BG_FACTOR = 8  # background statistics on a 0.2 m grid
EIGHT = np.ones((3, 3), bool)
KINDS = ("white", "colour", "black")


@dataclass(frozen=True)
class WasteParams:
    bg_m: float = 2.0              # median window for the local background
    white_min: float = 170.0       # darkest channel of a white pixel
    white_contrast: float = 40.0   # luminance over the background
    colour_chroma: float = 60.0    # max - min channel of a coloured pixel, outside the soil and vegetation hues
    black_max: float = 45.0
    black_contrast: float = 60.0
    merge_m: float = 0.05
    min_candidate_m2: float = 0.005
    # verifier
    area_m2: tuple[float, float] = (0.03, 1.5)
    white_lum: float = 220.0       # pale clods and stones on dark soil stay near 205-215
    white_chroma: float = 15.0     # silvery shrubs and weeds are tinted
    white_dev: float = 150.0
    white_row_m: float = 0.9       # leaning and lying tubes and stakes stay within about 0.75 m of their row axis
    min_width_m: float = 0.04      # spread across the narrow axis; lying tubes and stakes are thinner lines
    max_elongation: float = 3.0    # length over width spread; a tube or stake lying flat is a long line
    colour_area_m2: float = 0.02
    colour_min_chroma: float = 80.0
    colour_dev: float = 100.0
    pad_m: float = 0.025


def _coarse(values: np.ndarray) -> np.ndarray:
    return values.reshape(*values.shape[:-2], TILE_PX // BG_FACTOR, BG_FACTOR, TILE_PX // BG_FACTOR, BG_FACTOR).mean((-3, -1))


def _fine(values: np.ndarray) -> np.ndarray:
    return np.repeat(np.repeat(values, BG_FACTOR, 0), BG_FACTOR, 1)


def _hue(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb
    mx, mn = rgb.max(0), rgb.min(0)
    c = np.maximum(mx - mn, 1e-6)
    return np.where(mx == r, ((g - b) / c) % 6, np.where(mx == g, (b - r) / c + 2, (r - g) / c + 4)) * 60


def interrows(predictions: list[dict[str, Any]]) -> list[tuple[Any, str]]:
    return [(shape(f["geometry"]), f["properties"].get("vineyard_id", "")) for f in predictions if f["properties"].get("label") == "interrow_area"]


def evidence(rgb: np.ndarray, inside: np.ndarray, params: WasteParams = WasteParams()) -> dict[str, np.ndarray]:
    """Per-pixel white, colour and black evidence inside `inside`, plus the helper layers `_dev` and `_lum`."""
    size = int(params.bg_m / (PIXEL_M * BG_FACTOR)) | 1
    background = np.stack([_fine(ndimage.median_filter(_coarse(band), size=size)) for band in rgb])
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    bg_lum = 0.299 * background[0] + 0.587 * background[1] + 0.114 * background[2]
    mx, mn = rgb.max(0), rgb.min(0)
    hue = _hue(rgb)
    soil_or_green = (hue >= 25) & (hue <= 175)  # tan and brown soil, dry and green vegetation
    return {
        "white": inside & (mn >= params.white_min) & (lum - bg_lum >= params.white_contrast),
        "colour": inside & (mx - mn >= params.colour_chroma) & ~soil_or_green & (mx >= 80),
        "black": inside & (lum <= params.black_max) & (mx - mn <= 20) & (bg_lum - lum >= params.black_contrast),
        "_dev": np.sqrt(((rgb - background) ** 2).sum(0)), "_lum": lum, "_hue": hue,
    }


def tile_candidates(tile: Tile, polygons: list[tuple[Any, str]], rows: list[Any], params: WasteParams = WasteParams()) -> list[dict[str, Any]]:
    """Blobs of evidence inside the tile's inter-rows, with their features, in tile pixels and EPSG:32635."""
    hits = [(i, polygon) for i, (polygon, _) in enumerate(polygons) if polygon.intersects(tile.bounds)]
    if not hits:
        return []
    with rasterio.open(tile.path) as source:
        rgb, transform = source.read().astype(np.float32), source.transform
    owner = rasterize([(polygon, i + 1) for i, polygon in hits], out_shape=(TILE_PX, TILE_PX), transform=transform, dtype="int32")
    layers = evidence(rgb, owner > 0, params)
    near = [row for row in rows if row.intersects(tile.bounds.buffer(3.0))]
    any_mask = ndimage.binary_opening(np.logical_or.reduce([layers[kind] for kind in KINDS]), np.ones((2, 2), bool))
    labels, _ = ndimage.label(ndimage.binary_dilation(any_mask, EIGHT, iterations=max(1, round(params.merge_m / PIXEL_M / 2))), EIGHT)
    labels[~any_mask] = 0
    found = []
    for index, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None:
            continue
        mask = labels[window] == index
        area = mask.sum() * PIXEL_M ** 2
        if area < params.min_candidate_m2 or area > 3.0:
            continue
        ys, xs = np.nonzero(mask)
        r0, c0 = window[0].start + ys.min(), window[1].start + xs.min()
        r1, c1 = window[0].start + ys.max() + 1, window[1].start + xs.max() + 1
        spread = np.sqrt(np.maximum(np.linalg.eigvalsh(np.cov(np.vstack([xs, ys]).astype(float))), 0)) * PIXEL_M if len(xs) > 2 else np.zeros(2)
        pix = rgb[:, window[0], window[1]][:, mask]
        shares = {kind: float(layers[kind][window][mask].mean()) for kind in KINDS}
        geometry = tile.to_world(box(c0, r0, c1, r1))
        owners = owner[window][mask]
        found.append({
            "tile": tile.name, "px": [int(c0), int(r0), int(c1), int(r1)], "bounds": list(geometry.bounds), "box": list(geometry.bounds),
            "kind": max(shares, key=shares.get), **shares, "area_m2": float(area),
            "dev": float(layers["_dev"][window][mask].mean()), "lum": float(layers["_lum"][window][mask].mean()),
            "chroma": float((pix.max(0) - pix.min(0)).mean()), "hue": float(np.median(layers["_hue"][window][mask])),
            "rgb": [round(float(v)) for v in pix.mean(1)], "width_m": float(spread[0]), "length_m": float(spread[1]),
            "vineyard_id": polygons[np.bincount(owners[owners > 0]).argmax() - 1][1] if (owners > 0).any() else "",
            "row_m": float(min((row.distance(geometry.centroid) for row in near), default=99.0)),
        })
    return found


def accept(c: dict[str, Any], params: WasteParams = WasteParams()) -> bool:
    """The verifier (module docstring, step 3)."""
    if c["area_m2"] > params.area_m2[1] or c["length_m"] > params.max_elongation * max(c["width_m"], 1e-3):
        return False
    if c["kind"] == "white":
        return (c["area_m2"] >= params.area_m2[0] and c["width_m"] >= params.min_width_m and c["lum"] >= params.white_lum
                and c["chroma"] <= params.white_chroma and c["dev"] >= params.white_dev and c["row_m"] >= params.white_row_m)
    if c["kind"] == "colour":
        return c["area_m2"] >= params.colour_area_m2 and c["chroma"] >= params.colour_min_chroma and c["dev"] >= params.colour_dev
    return False


def detect(tiles: list[Tile], predictions: list[dict[str, Any]], data_dir: Path = DATA_DIR,
           params: WasteParams = WasteParams()) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Waste features (EPSG:32635 boxes, `source` prediction, the inter-row's `vineyard_id`) whose centre lies in a
    predicted inter-row, and every candidate, for review. `data_dir` is kept for the caller's signature."""
    polygons = interrows(predictions)
    rows = [shape(f["geometry"]) for f in predictions if f["properties"].get("label") == "row"]
    candidates = [c for tile in tiles for c in tile_candidates(tile, polygons, rows, params)]
    kept = [c for c in candidates if accept(c, params)]
    merged = unary_union([box(*c["box"]).buffer(params.pad_m, join_style="mitre") for c in kept])
    union = unary_union([polygon for polygon, _ in polygons]) if kept else None
    features = []
    for part in getattr(merged, "geoms", [merged]) if kept else []:
        geometry = box(*part.bounds)
        if not union.contains(geometry.centroid):
            continue
        members = [c for c in kept if geometry.intersects(box(*c["box"]))]
        features.append({"type": "Feature", "geometry": mapping(geometry), "properties": {
            "label": "waste", "source": "prediction", "vineyard_id": members[0]["vineyard_id"],
            "area_m2": round(sum(c["area_m2"] for c in members), 3), "tiles": sorted({c["tile"] for c in members})}})
    return features, candidates


def main() -> None:
    started = time.perf_counter()
    predictions = json.loads(PREDICTIONS_PATH.read_text(encoding="utf-8"))["features"]
    features, candidates = detect(load_tiles(), predictions)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
    path = WORK_DIR / "waste.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}), encoding="utf-8")
    print(f"{len(features)} waste boxes from {len(candidates)} candidates in {time.perf_counter() - started:.0f} s -> {path}")


if __name__ == "__main__":
    main()
