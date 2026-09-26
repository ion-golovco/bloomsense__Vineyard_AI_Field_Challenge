"""Waste boxes (CVAT label `waste`) with no training: a colour-blob candidate generator and a hand-set verifier.

Method, per tile at the native 0.025 m:
1. Evidence pixels: white (darkest channel >= 200, chroma <= 45, >= 40 brighter than the 2 m median background;
   pale soil tops out near 184), blue (hue 185-265, saturation >= 0.25) and, for the scan only, vivid red/orange
   and very dark neutral pixels. No-data is kept 1 m away so the background never mixes it in.
2. Candidates: 8-connected blobs of evidence (pieces within 0.1 m merged), 0.004-8 m2, with shape, colour and
   surroundings: fill of the box, clipped share, luminance spread, green share of a 0.1-0.5 m ring, evidence
   density within 3 m, overlap with pale structures (>= 3 m2 of pale 0.2 m cells: roofs, walls, kerbs, concrete,
   tracks), distance to the nearest predicted row axis and to the organizers' forbidden zones (buildings).
3. `accept`: white blobs of 0.1-1.5 m2, bright and partly clipped but textured (not a smooth disc: concrete well
   lids), lying in vegetation, isolated, clear of pale structures, of buildings (10 m) and of row axes (0.5 m,
   vine tubes and stakes); blue blobs of 0.04-1.5 m2 with the same context and a crumpled or printed texture.
   Vivid and dark blobs are never accepted: in the scan they were roofs, machinery, flowers and shadows.
4. Touching boxes (across tile edges) are merged; `vineyard_id` is the block the box lies in, or the nearest
   predicted block within 10 m, otherwise empty (rules, section 3).

Measured (research/notes/waste.md): the scan of all 311 tiles gives 25,153 candidates in 84 s (0.6 GB peak) on
one core of an M4 Pro; the verifier keeps 29 boxes. By eye, 10 of them are likely litter (34%), 12 are unsure
(small white objects: bag, paper or stone cannot be told apart at 2.5 cm/px) and 7 are not litter (a concrete
well ring, stones, a wall end, a concrete base, a flowering shrub). The thresholds were set with those crops in
view, so this is in-sample. Boxes cover the white pixels only and can under-cover printed or multi-part items.
Control: nothing is kept on the two organizer example tiles, which have no waste; the best raw candidate
there (a pale object at the r006_c004 headland, score 0.92) is rejected by the building-distance rule.
Run: uv run --frozen python -m marcaj.waste (writes data/generated/work/waste/waste.geojson)."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from scipy import ndimage
from shapely import STRtree
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, Tile, load_tiles

WORK_DIR = REPO_ROOT / "data" / "generated" / "work" / "waste"
PREDICTIONS_PATH = REPO_ROOT / "data" / "generated" / "predictions.geojson"
BG_FACTOR = 8  # background statistics on a 0.2 m grid
EIGHT = np.ones((3, 3), bool)
KINDS = ("white", "blue", "vivid", "dark")
MIN_AREA_M2 = {"white": 0.01, "blue": 0.004, "vivid": 0.004, "dark": 0.12}


@dataclass(frozen=True)
class WasteParams:
    bg_m: float = 2.0             # median window for the local background
    white_min: float = 200.0      # darkest channel of a white pixel
    white_chroma: float = 45.0    # max - min channel of a white pixel
    bright_contrast: float = 40.0  # luminance over the local background
    dark_max: float = 30.0
    dark_contrast: float = 70.0
    vivid_s: float = 0.5
    blue_s: float = 0.25
    max_area_m2: float = 8.0
    merge_m: float = 0.10         # pieces closer than this form one object
    structure_min: float = 175.0  # darkest channel of a pale structure cell (0.2 m)
    structure_m2: float = 3.0
    # verifier
    white_area_m2: tuple[float, float] = (0.10, 1.5)
    blue_area_m2: tuple[float, float] = (0.04, 1.5)
    min_fill: float = 0.35
    min_clipped: float = 0.25     # share of pixels with every channel >= 235
    min_lum: float = 225.0
    white_texture: float = 9.5    # luminance std; smooth discs are well lids
    blue_texture: float = 20.0    # planters, pools and roofs are smooth
    min_ring_green: float = 0.4
    max_density: float = 0.03     # other evidence within 3 m
    building_m: float = 10.0
    row_m: float = 0.5
    pad_m: float = 0.025


def _hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = rgb
    mx, mn = rgb.max(0), rgb.min(0)
    c = mx - mn
    safe = np.maximum(c, 1e-6)
    h = np.where(mx == r, ((g - b) / safe) % 6, np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4)) * 60
    return np.where(c > 0, h, 0), c / np.maximum(mx, 1e-6), mx


def _coarse(values: np.ndarray) -> np.ndarray:
    return values.reshape(*values.shape[:-2], TILE_PX // BG_FACTOR, BG_FACTOR, TILE_PX // BG_FACTOR, BG_FACTOR).mean((-3, -1))


def _fine(values: np.ndarray) -> np.ndarray:
    return np.repeat(np.repeat(values, BG_FACTOR, 0), BG_FACTOR, 1)


def masks(rgb: np.ndarray, params: WasteParams) -> dict[str, np.ndarray]:
    """Per-pixel evidence at native resolution (white, blue, vivid, dark) and the helper layers `_lum`, `_exg`, `_valid`."""
    rgb = rgb.astype(np.float32)
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    valid = rgb.sum(0) > 30
    # no-data is black: keep 1 m clear of it so the background median never mixes it in
    coarse_valid = ndimage.binary_erosion(_coarse(valid) == 1, iterations=round(1.0 / (PIXEL_M * BG_FACTOR)), border_value=1)
    valid &= _fine(coarse_valid)
    contrast = lum - _fine(ndimage.median_filter(_coarse(lum), size=int(params.bg_m / (PIXEL_M * BG_FACTOR)) | 1))
    h, s, v = _hsv(rgb)
    mn = rgb.min(0)
    return {
        "white": valid & (mn >= params.white_min) & (v - mn <= params.white_chroma) & (contrast >= params.bright_contrast),
        "blue": valid & (h >= 185) & (h <= 265) & (s >= params.blue_s) & (rgb[2] - rgb[0] >= 25) & (v >= 60),
        "vivid": valid & (s >= params.vivid_s) & (v >= 90) & ((h >= 290) | (h <= 12) | ((h <= 45) & (s >= 0.7))),
        "dark": valid & (lum <= params.dark_max) & (contrast <= -params.dark_contrast) & (v - mn <= 30),
        "_lum": lum, "_exg": (2 * rgb[1] - rgb[0] - rgb[2]) / np.maximum(rgb.sum(0), 1), "_valid": valid,
    }


def tile_candidates(tile: Tile, params: WasteParams = WasteParams()) -> list[dict[str, Any]]:
    """Connected blobs of evidence with their shape, colour and surroundings, in tile pixels and EPSG:32635."""
    with rasterio.open(tile.path) as source:
        rgb = source.read()
    evidence = masks(rgb, params)
    any_mask = np.logical_or.reduce([evidence[kind] for kind in KINDS])
    # evidence share within 3 m: blossom, stone fields and roof clutter are dense, a lone bag is not
    density = ndimage.uniform_filter(_coarse(any_mask.astype(np.float32)), size=int(3.0 / (PIXEL_M * BG_FACTOR)) | 1, mode="constant")
    # roofs, walls, kerbs, concrete and pale tracks: pale regions of 3 m2 or more on the 0.2 m grid, grown 0.4 m
    pale = _coarse(rgb.astype(np.float32)).min(0) >= params.structure_min
    pale_labels, _ = ndimage.label(pale, EIGHT)
    sizes = np.bincount(pale_labels.ravel()) * (PIXEL_M * BG_FACTOR) ** 2
    structure = ndimage.binary_dilation((sizes >= params.structure_m2)[pale_labels] & pale, EIGHT, iterations=2)
    grown = ndimage.binary_dilation(any_mask, EIGHT, iterations=max(1, round(params.merge_m / PIXEL_M / 2)))
    labels, _ = ndimage.label(grown, EIGHT)
    labels[~any_mask] = 0
    found = []
    ring_px = round(0.5 / PIXEL_M)
    for index, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None:
            continue
        mask = labels[window] == index
        area = mask.sum() * PIXEL_M ** 2
        if not min(MIN_AREA_M2.values()) <= area <= params.max_area_m2:
            continue
        shares = {kind: float(evidence[kind][window][mask].mean()) for kind in KINDS}
        kind = max(shares, key=shares.get)
        if area < MIN_AREA_M2[kind]:
            continue
        ys, xs = np.nonzero(mask)
        r0, c0 = window[0].start + ys.min(), window[1].start + xs.min()
        r1, c1 = window[0].start + ys.max() + 1, window[1].start + xs.max() + 1
        big = (slice(max(r0 - ring_px, 0), min(r1 + ring_px, TILE_PX)), slice(max(c0 - ring_px, 0), min(c1 + ring_px, TILE_PX)))
        own = labels[big] == index
        ring = ndimage.binary_dilation(own, iterations=ring_px) & ~ndimage.binary_dilation(own, iterations=4) & evidence["_valid"][big]
        exg_ring = evidence["_exg"][big][ring]
        pix = rgb[:, window[0], window[1]][:, mask].astype(np.float32)
        lum = evidence["_lum"][window][mask]
        found.append({
            "tile": tile.name, "px": [int(c0), int(r0), int(c1), int(r1)], "bounds": list(tile.to_world(box(c0, r0, c1, r1)).bounds),
            "area_m2": float(area), "w_m": float((c1 - c0) * PIXEL_M), "h_m": float((r1 - r0) * PIXEL_M),
            "fill": float(mask.sum() / ((r1 - r0) * (c1 - c0))), "kind": kind, **shares,
            "lum": float(lum.mean()), "lum_std": float(lum.std()),
            "rgb": [float(x) for x in pix.mean(1)], "clipped": float((pix.min(0) >= 235).mean()),
            "ring_green": float((exg_ring > 0.05).mean()) if exg_ring.size else 0.0,
            "ring_lum": float(evidence["_lum"][big][ring].mean()) if ring.any() else 0.0,
            "density": float(density[(r0 + r1) // 2 // BG_FACTOR, (c0 + c1) // 2 // BG_FACTOR]),
            "structure": float(structure[r0 // BG_FACTOR:(r1 - 1) // BG_FACTOR + 1, c0 // BG_FACTOR:(c1 - 1) // BG_FACTOR + 1].mean()),
        })
    return found


@dataclass(frozen=True)
class Context:
    blocks: list[tuple[Any, str]]
    rows: list[Any]
    row_index: STRtree
    forbidden: Any
    passages: Any


def load_context(predictions: list[dict[str, Any]], data_dir: Path = DATA_DIR) -> Context:
    """Predicted blocks and row axes, and the organizers' forbidden zones and passages."""
    blocks = [(shape(f["geometry"]), f["properties"]["vineyard_id"]) for f in predictions if f["properties"].get("label") == "block"]
    rows = [shape(f["geometry"]) for f in predictions if f["properties"].get("label") == "row"]
    zones = {name: unary_union([shape(f["geometry"]) for f in json.loads((data_dir / "02_route" / f"{name}.geojson").read_text(encoding="utf-8"))["features"]])
             for name in ("forbidden", "passages")}
    return Context(blocks, rows, STRtree(rows), zones["forbidden"], zones["passages"])


def vineyard_id(geometry: Any, blocks: list[tuple[Any, str]], reach_m: float = 10.0) -> str:
    """The block the object lies in, or the nearest block within `reach_m`, otherwise empty (rules, section 3)."""
    distance, name = min(((polygon.distance(geometry), name) for polygon, name in blocks), default=(np.inf, ""))
    return name if distance <= reach_m else ""


def annotate(candidate: dict[str, Any], context: Context) -> dict[str, Any]:
    geometry = box(*candidate["bounds"])
    centre = geometry.centroid
    nearest = context.row_index.nearest(centre) if context.rows else None
    candidate["row_m"] = float(context.rows[nearest].distance(centre)) if nearest is not None else 99.0
    candidate["where"] = ("forbidden" if context.forbidden.intersects(centre) else "passage" if context.passages.intersects(centre)
                          else "block" if any(p.intersects(centre) for p, _ in context.blocks) else "outside")
    candidate["forbidden_m"] = float(context.forbidden.distance(centre))
    candidate["vineyard_id"] = vineyard_id(geometry, context.blocks)
    return candidate


def accept(c: dict[str, Any], params: WasteParams = WasteParams()) -> bool:
    """The verifier (module docstring, step 3). Needs `annotate` first."""
    area = c["area_m2"]
    context = (c["structure"] == 0 and c["fill"] >= params.min_fill and c["ring_green"] >= params.min_ring_green
               and c["density"] - area / 9 <= params.max_density and c["forbidden_m"] >= params.building_m and c["row_m"] >= params.row_m)
    if c["kind"] == "white":
        disc = c["fill"] >= 0.6 and max(c["w_m"], c["h_m"]) < 1.3 * min(c["w_m"], c["h_m"]) and c["lum_std"] < 10
        return (context and not disc and params.white_area_m2[0] <= area <= params.white_area_m2[1] and c["clipped"] >= params.min_clipped
                and c["lum"] >= params.min_lum and c["lum_std"] >= params.white_texture)
    if c["kind"] == "blue":
        return context and params.blue_area_m2[0] <= area <= params.blue_area_m2[1] and c["lum_std"] >= params.blue_texture
    return False


def detect(tiles: list[Tile], predictions: list[dict[str, Any]], data_dir: Path = DATA_DIR,
           params: WasteParams = WasteParams()) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Waste features (EPSG:32635 boxes, `source` prediction) and every annotated candidate, for review."""
    context = load_context(predictions, data_dir)
    candidates = [annotate(c, context) for tile in tiles for c in tile_candidates(tile, params)]
    kept = [c for c in candidates if accept(c, params)]
    merged = unary_union([box(*c["bounds"]).buffer(params.pad_m, join_style="mitre") for c in kept])
    features = []
    for part in getattr(merged, "geoms", [merged]) if kept else []:
        geometry = box(*part.bounds)
        members = [c for c in kept if geometry.intersects(box(*c["bounds"]))]
        features.append({"type": "Feature", "geometry": mapping(geometry), "properties": {
            "label": "waste", "source": "prediction", "vineyard_id": vineyard_id(geometry, context.blocks),
            "kind": members[0]["kind"], "area_m2": round(sum(c["area_m2"] for c in members), 3), "tiles": sorted({c["tile"] for c in members})}})
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
