"""Waste boxes (CVAT label `waste`) anywhere on the tile, with no training: a colour-anomaly candidate generator and
hand-set verifiers. The rules (section 3) box clearly visible litter "within the vineyards and the land around them
(anywhere on the tile)" and leave out tubes, stakes, posts, wires, hoses, stones, bare or pale soil, flowering shrubs,
pruning residue, vehicles and machinery; "when in doubt, leave it out". `vineyard_id` is the block the box lies in, or
the nearest predicted block within 10 m, otherwise empty. `WasteParams.scope = "interrow"` restores the 10:20 rule
(inter-rows only).

Method, per tile at the native 0.025 m, in `params.workers` processes:
1. Evidence: pixels that are neither soil nor vegetation against the 2 m median background: white (darkest channel
   >= 170 and >= 40 brighter than the background), coloured (chroma >= 60, hue outside 25-175: red, pink, blue,
   purple), or black and neutral (luminance <= 45, chroma <= 20, >= 60 darker than the background). Pieces within
   0.05 m form one blob. No-data is kept 1 m away.
2. Blobs are taken twice: inside the predicted inter-row polygons (inset 0.30 m from the row axes, so standing tubes,
   stakes, posts and canopies are outside), and in the rest of the tile (>= 0.02 m2). Features: area, colour class
   shares, luminance, chroma, hue, RGB distance from the background, the spread across the narrow axis and the
   length, the distance to the nearest predicted row axis and to the organizers' forbidden zones (buildings,
   compounds); outside the inter-rows also the site-wide v1 surroundings: fill, luminance spread, clipped share,
   green share of a 0.1-0.5 m ring, evidence density within 3 m and overlap with pale structures (>= 3 m2 of pale
   0.2 m cells: roofs, walls, kerbs, concrete, tracks). `location` is interrow, block, headland (within 10 m of a
   block) or outside.
3. `accept` in the inter-rows: white blobs of 0.03-1.5 m2 with luminance >= 220, chroma <= 15, distance >= 150, not
   a thin line (narrow spread >= 0.04 m, length <= 3x width) and >= 0.9 m from a row axis (leaning and lying white
   tubes and stakes stay within about 0.75 m of theirs); or small bright white blobs of 0.02-0.15 m2 with luminance
   >= 220, chroma <= 20, distance >= 100, narrow spread >= 0.03 m, not a thin line and >= 0.55 m from a row axis;
   coloured blobs of >= 0.02 m2 with chroma >= 80 and distance >= 100; black blobs never (vine shadows).
   `accept_rest` elsewhere: white blobs of 0.03-1.5 m2, not a smooth disc (well lids), clipped share >= 0.05, chroma
   <= 25, luminance spread >= 9.5, lying in vegetation (ring green >= 0.5), clear of pale structures, >= 10 m from
   buildings and >= 0.55 m from a row axis (as the small tier); luminance >= 225 and density <= 0.03 outside the
   blocks' 10 m reach, or luminance >= 220 and density <= 0.10 in a block or its headland; blue blobs (hue 185-265)
   of 0.04-1.5 m2 with luminance spread >= 20 and the same surroundings. Vivid red and dark blobs never (roofs,
   machinery, flowers, shadows).

Labelled set (research/review/eval_waste.py, 19:55; in-sample, the thresholds were set with these labels in view):
the user's 136 waste and 563 not-waste review answers plus the earlier eye verdicts; labels on the two organizer
example tiles count as not waste (their reference has none). On the 90 waste answers without rule flags: 74 boxes,
45 on labelled waste, 14 on labelled not, 15 unlabelled, precision 0.76, recall 43/90, F1 0.56 (unlabelled boxes
count half) against 0.51 for the 18:50 verifier (60 boxes, recall 37/90). Split at northing 5220200: north F1 0.571
-> 0.564, south 0.456 -> 0.550. Boxes are tight: against a whitish region grown around each confirmed item the median
IoU is 0.79. Control: 0 boxes on the two organizer example tiles. 228,726 candidates on 311 tiles in 90 s with 2
processes (1.2 GB peak).
Run: uv run --frozen python -m marcaj.waste [--workers N] (writes data/generated/work/waste/waste_sitewide.geojson and
candidates_site.json)."""

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import shapely
from rasterio.features import rasterize
from scipy import ndimage
from shapely import STRtree
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
    scope: str = "interrow"        # "interrow": the predicted inter-rows plus margin_m inside the blocks (the user, 20:30); "site": the whole tile
    margin_m: float = 2.0          # scope "interrow": blobs inside a predicted block within this of an inter-row count too (row ends, canopy strips); every in-block waste label lies within 2 m of one
    bg_m: float = 2.0              # median window for the local background
    white_min: float = 170.0       # darkest channel of a white pixel
    white_contrast: float = 40.0   # luminance over the background
    colour_chroma: float = 60.0    # max - min channel of a coloured pixel, outside the soil and vegetation hues
    black_max: float = 45.0
    black_contrast: float = 60.0
    merge_m: float = 0.05
    min_candidate_m2: float = 0.005
    rest_min_m2: float = 0.02      # blobs outside the inter-rows below this are never accepted, so never measured
    # verifier in the inter-rows
    area_m2: tuple[float, float] = (0.03, 1.5)
    white_lum: float = 220.0       # pale clods and stones on dark soil stay near 205-215
    white_chroma: float = 15.0     # silvery shrubs and weeds are tinted
    white_dev: float = 150.0
    white_row_m: float = 0.9       # leaning and lying tubes and stakes stay within about 0.75 m of their row axis
    min_width_m: float = 0.04      # spread across the narrow axis; lying tubes and stakes are thinner lines
    max_elongation: float = 3.0    # length over width spread; a tube or stake lying flat is a long line
    # small bright tier: crumpled white scraps of 0.02-0.15 m2 are brighter than the pale clods (lum 199-216) but
    # smaller than the strict tier's 0.03 m2 or nearer a row than its 0.9 m (research/notes/waste.md, 13:50)
    small_area_m2: tuple[float, float] = (0.02, 0.15)
    small_lum: float = 220.0
    small_chroma: float = 20.0
    small_dev: float = 100.0
    row_m: float = 0.55            # white blobs nearer a row axis are tubes, stakes or posts: the small tier and everywhere outside the inter-rows
    small_min_width_m: float = 0.03
    # pile tier: heaps of pale stones or dusty sheeting of >= 0.15 m2 are dimmer and more tinted than the scraps
    # (luminance 208-222, chroma 24-31) but hold a solid core; pale soil patches that size are dull (distance < 110)
    pile_area_m2: tuple[float, float] = (0.15, 1.5)
    pile_lum: float = 208.0
    pile_chroma: float = 32.0
    pile_dev: float = 110.0
    pile_min_width_m: float = 0.10
    pile_row_m: float = 0.7
    pile_hue_max: float = 50.0     # stone and soil tan (hue 24-51); silvery shrubs and dry grass at that brightness are greener (55-106)
    colour_area_m2: float = 0.02
    colour_min_chroma: float = 80.0
    colour_dev: float = 100.0
    # verifier outside the inter-rows (canopy strips, headlands, surrounding land): the site-wide v1 context tests
    structure_min: float = 175.0   # darkest channel of a pale structure cell (0.2 m): roofs, walls, kerbs, concrete, tracks
    structure_m2: float = 3.0
    rest_area_m2: tuple[float, float] = (0.03, 1.5)
    rest_blue_area_m2: tuple[float, float] = (0.04, 1.5)
    rest_lum: float = 225.0
    rest_chroma: float = 25.0
    rest_clipped: float = 0.05     # share of pixels with every channel >= 235
    rest_texture: float = 9.5      # luminance std; smooth discs are well lids, smooth pale patches are soil or stone
    rest_blue_texture: float = 20.0  # planters, pools and roofs are smooth
    rest_fill: float = 0.35
    rest_ring_green: float = 0.5   # litter lies in vegetation; pale soil, tracks and yards do not
    rest_density: float = 0.03     # other evidence within 3 m: blossom, stone fields and roof clutter are dense
    building_m: float = 10.0       # distance to the organizers' forbidden zones (buildings, compounds)
    # in a block or within reach_m of one (headlands, field edges) the white tests are looser: litter is likelier there
    near_lum: float = 220.0
    near_density: float = 0.10
    reach_m: float = 10.0          # vineyard_id: the block it lies in, or the nearest block within 10 m (rules, section 3)
    pad_m: float = 0.025
    workers: int = 6


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
    """Per-pixel white, colour and black evidence inside `inside`, plus the helper layers `_dev`, `_lum` and `_hue`."""
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


def _blobs(tile: Tile, rgb: np.ndarray, layers: dict[str, np.ndarray], mask_in: np.ndarray, params: WasteParams,
           owner: np.ndarray | None = None, polygons: list[tuple[Any, str]] = (), context: dict[str, np.ndarray] | None = None,
           min_m2: float = 0.0) -> list[dict[str, Any]]:
    """Blobs of evidence inside `mask_in` with their features. With `owner` (inter-row index + 1 per pixel) a blob takes
    its inter-row's vineyard_id; with `context` it also gets the surroundings tests of the outside verifier."""
    kinds = {kind: layers[kind] & mask_in for kind in KINDS}
    any_mask = ndimage.binary_opening(np.logical_or.reduce(list(kinds.values())), np.ones((2, 2), bool))
    labels, _ = ndimage.label(ndimage.binary_dilation(any_mask, EIGHT, iterations=max(1, round(params.merge_m / PIXEL_M / 2))), EIGHT)
    labels[~any_mask] = 0
    ring_px = round(0.5 / PIXEL_M)
    found = []
    for index, window in enumerate(ndimage.find_objects(labels), start=1):
        if window is None:
            continue
        mask = labels[window] == index
        area = mask.sum() * PIXEL_M ** 2
        if area < max(params.min_candidate_m2, min_m2) or area > 3.0:
            continue
        ys, xs = np.nonzero(mask)
        r0, c0 = window[0].start + ys.min(), window[1].start + xs.min()
        r1, c1 = window[0].start + ys.max() + 1, window[1].start + xs.max() + 1
        spread = np.sqrt(np.maximum(np.linalg.eigvalsh(np.cov(np.vstack([xs, ys]).astype(float))), 0)) * PIXEL_M if len(xs) > 2 else np.zeros(2)
        pix = rgb[:, window[0], window[1]][:, mask]
        shares = {kind: float(kinds[kind][window][mask].mean()) for kind in KINDS}
        geometry = tile.to_world(box(c0, r0, c1, r1))
        lum = layers["_lum"][window][mask]
        c = {
            "tile": tile.name, "px": [int(c0), int(r0), int(c1), int(r1)], "bounds": list(geometry.bounds), "box": list(geometry.bounds),
            "kind": max(shares, key=shares.get), **shares, "area_m2": float(area),
            "dev": float(layers["_dev"][window][mask].mean()), "lum": float(lum.mean()),
            "chroma": float((pix.max(0) - pix.min(0)).mean()), "hue": float(np.median(layers["_hue"][window][mask])),
            "rgb": [round(float(v)) for v in pix.mean(1)], "width_m": float(spread[0]), "length_m": float(spread[1]), "vineyard_id": "",
        }
        if owner is not None:
            owners = owner[window][mask]
            c["vineyard_id"] = polygons[np.bincount(owners[owners > 0]).argmax() - 1][1] if (owners > 0).any() else ""
        if context is not None:
            big = (slice(max(r0 - ring_px, 0), min(r1 + ring_px, TILE_PX)), slice(max(c0 - ring_px, 0), min(c1 + ring_px, TILE_PX)))
            own = labels[big] == index
            ring = ndimage.binary_dilation(own, iterations=ring_px) & ~ndimage.binary_dilation(own, iterations=4) & context["valid"][big]
            exg = context["exg"][big][ring]
            c.update({
                "w_m": float((c1 - c0) * PIXEL_M), "h_m": float((r1 - r0) * PIXEL_M), "fill": float(mask.sum() / ((r1 - r0) * (c1 - c0))),
                "lum_std": float(lum.std()), "clipped": float((pix.min(0) >= 235).mean()),
                "ring_green": float((exg > 0.05).mean()) if exg.size else 0.0,
                "density": float(context["density"][(r0 + r1) // 2 // BG_FACTOR, (c0 + c1) // 2 // BG_FACTOR]),
                "structure": float(context["structure"][r0 // BG_FACTOR:(r1 - 1) // BG_FACTOR + 1, c0 // BG_FACTOR:(c1 - 1) // BG_FACTOR + 1].mean()),
            })
        found.append(c)
    return found


def _row_distance(found: list[dict[str, Any]], rows: list[Any]) -> None:
    centres = shapely.centroid(shapely.box(*np.array([c["box"] for c in found]).T)) if found else []
    for c, centre in zip(found, centres):
        c["row_m"] = float(shapely.distance(np.array(rows), centre).min()) if rows else 99.0


def tile_candidates(tile: Tile, polygons: list[tuple[Any, str]], rows: list[Any], params: WasteParams = WasteParams()) -> list[dict[str, Any]]:
    """Blobs of evidence inside the tile's inter-rows, with their features, in tile pixels and EPSG:32635."""
    hits = [(i, polygon) for i, (polygon, _) in enumerate(polygons) if polygon.intersects(tile.bounds)]
    if not hits:
        return []
    with rasterio.open(tile.path) as source:
        rgb, transform = source.read().astype(np.float32), source.transform
    owner = rasterize([(polygon, i + 1) for i, polygon in hits], out_shape=(TILE_PX, TILE_PX), transform=transform, dtype="int32")
    layers = evidence(rgb, owner > 0, params)
    found = _blobs(tile, rgb, layers, owner > 0, params, owner, polygons)
    _row_distance(found, [row for row in rows if row.intersects(tile.bounds.buffer(3.0))])
    return found


@dataclass(frozen=True)
class Context:
    interrows: list[tuple[Any, str]]
    rows: list[Any]
    blocks: list[tuple[Any, str]]
    forbidden: Any


def load_context(predictions: list[dict[str, Any]], data_dir: Path = DATA_DIR) -> Context:
    """Predicted inter-rows, row axes and blocks, and the organizers' forbidden zones (buildings, compounds)."""
    forbidden = json.loads((data_dir / "02_route" / "forbidden.geojson").read_text(encoding="utf-8"))["features"]
    return Context(interrows(predictions), [shape(f["geometry"]) for f in predictions if f["properties"].get("label") == "row"],
                   [(shape(f["geometry"]), f["properties"]["vineyard_id"]) for f in predictions if f["properties"].get("label") == "block"],
                   unary_union([shape(f["geometry"]) for f in forbidden]))


def vineyard_id(geometry: Any, blocks: list[tuple[Any, str]], reach_m: float = 10.0) -> str:
    """The block the object lies in, or the nearest block within `reach_m`, otherwise empty (rules, section 3)."""
    distance, name = min(((polygon.distance(geometry), name) for polygon, name in blocks), default=(np.inf, ""))
    return name if distance <= reach_m else ""


def zone(interrow_polygons: list[Any], blocks: list[Any], margin_m: float) -> Any:
    """Scope "interrow": the inter-rows, plus the parts of the predicted blocks within `margin_m` of one (row ends,
    canopy strips, piles straddling an inter-row edge)."""
    union = unary_union(interrow_polygons)
    return union.union(union.buffer(margin_m).intersection(unary_union(blocks))) if margin_m > 0 and blocks else union


def _tile_context(rgb: np.ndarray, layers: dict[str, np.ndarray], valid: np.ndarray, params: WasteParams) -> dict[str, np.ndarray]:
    any_mask = np.logical_or.reduce([layers[kind] for kind in KINDS])
    density = ndimage.uniform_filter(_coarse(any_mask.astype(np.float32)), size=int(3.0 / (PIXEL_M * BG_FACTOR)) | 1, mode="constant")
    pale = _coarse(rgb).min(0) >= params.structure_min
    pale_labels, _ = ndimage.label(pale, EIGHT)
    sizes = np.bincount(pale_labels.ravel()) * (PIXEL_M * BG_FACTOR) ** 2
    structure = ndimage.binary_dilation((sizes >= params.structure_m2)[pale_labels] & pale, EIGHT, iterations=2)
    exg = (2 * rgb[1] - rgb[0] - rgb[2]) / np.maximum(rgb.sum(0), 1)
    return {"density": density, "structure": structure, "exg": exg, "valid": valid}


def site_tile_candidates(tile: Tile, context: Context, params: WasteParams = WasteParams()) -> list[dict[str, Any]]:
    """Every candidate of one tile: inter-row blobs exactly as `tile_candidates`, and with scope "site" the blobs of
    the rest of the tile with their surroundings. Each gets `location` (interrow, block, headland within `reach_m` of a
    block, outside), `row_m`, `forbidden_m` and the 10 m `vineyard_id`."""
    bounds = tile.bounds
    hits = [(i, polygon) for i, (polygon, _) in enumerate(context.interrows) if polygon.intersects(bounds)]
    if params.scope != "site" and not hits:
        return []
    with rasterio.open(tile.path) as source:
        rgb, transform = source.read().astype(np.float32), source.transform
    # no-data is black (0.2 m cells averaging under 10 over the three bands; deep vine shadows stay above): keep 1 m
    # clear of it so the background median never mixes it in
    valid = _fine(ndimage.binary_erosion(_coarse(rgb.sum(0)) > 10, iterations=round(1.0 / (PIXEL_M * BG_FACTOR)), border_value=1))
    owner = (rasterize([(polygon, i + 1) for i, polygon in hits], out_shape=(TILE_PX, TILE_PX), transform=transform, dtype="int32")
             if hits else np.zeros((TILE_PX, TILE_PX), np.int32))
    layers = evidence(rgb, valid, params)
    found = [{**c, "location": "interrow"} for c in _blobs(tile, rgb, layers, owner > 0, params, owner, context.interrows)] if hits else []
    near_blocks = [(p, name) for p, name in context.blocks if p.intersects(bounds.buffer(params.reach_m + 3))]
    rest_mask = valid & (owner == 0)
    if params.scope != "site":
        margin = zone([p for _, p in hits], [p for p, _ in near_blocks], params.margin_m).intersection(bounds)
        rest_mask &= (rasterize([margin], out_shape=(TILE_PX, TILE_PX), transform=transform, dtype="uint8") > 0
                      if params.margin_m > 0 and not margin.is_empty else False)
    if rest_mask.any():
        rest = _blobs(tile, rgb, layers, rest_mask, params, context=_tile_context(rgb, layers, valid, params), min_m2=params.rest_min_m2)
        found += [{**c, "location": "rest"} for c in rest]
    _row_distance(found, [row for row in context.rows if row.intersects(bounds.buffer(3.0))])
    forbidden = context.forbidden.intersection(bounds.buffer(params.building_m + 3))
    for c in found:
        geometry = box(*c["box"])
        centre = geometry.centroid
        c["vineyard_id"] = vineyard_id(geometry, near_blocks, params.reach_m)
        c["forbidden_m"] = float(forbidden.distance(centre)) if not forbidden.is_empty else 99.0
        if c["location"] == "rest":
            c["location"] = ("block" if any(p.contains(centre) for p, _ in near_blocks) else "headland" if c["vineyard_id"] else "outside")
    return found


def accept(c: dict[str, Any], params: WasteParams = WasteParams()) -> bool:
    """The verifier (module docstring, step 3). A candidate without `location` is an inter-row one."""
    if c["area_m2"] > params.area_m2[1] or c["length_m"] > params.max_elongation * max(c["width_m"], 1e-3):
        return False
    if c.get("location", "interrow") != "interrow":
        return accept_rest(c, params)
    if c["kind"] == "white":
        return _white(c, params)
    if c["kind"] == "colour":
        return c["area_m2"] >= params.colour_area_m2 and c["chroma"] >= params.colour_min_chroma and c["dev"] >= params.colour_dev
    return False


def _white(c: dict[str, Any], params: WasteParams, pile: bool = True) -> bool:
    """The inter-row white tiers: strict, small bright and (with `pile`) pale piles."""
    strict = (c["area_m2"] >= params.area_m2[0] and c["width_m"] >= params.min_width_m and c["lum"] >= params.white_lum
              and c["chroma"] <= params.white_chroma and c["dev"] >= params.white_dev and c["row_m"] >= params.white_row_m)
    small = (params.small_area_m2[0] <= c["area_m2"] <= params.small_area_m2[1] and c["width_m"] >= params.small_min_width_m
             and c["lum"] >= params.small_lum and c["chroma"] <= params.small_chroma and c["dev"] >= params.small_dev
             and c["row_m"] >= params.row_m)
    pile_ok = (params.pile_area_m2[0] <= c["area_m2"] <= params.pile_area_m2[1] and c["width_m"] >= params.pile_min_width_m
               and c["lum"] >= params.pile_lum and c["chroma"] <= params.pile_chroma and c["dev"] >= params.pile_dev
               and c["row_m"] >= params.pile_row_m and c["hue"] <= params.pile_hue_max)
    return strict or small or (pile and pile_ok)


def accept_rest(c: dict[str, Any], params: WasteParams = WasteParams()) -> bool:
    """Outside the inter-rows: bright textured white or crumpled blue blobs lying in vegetation, isolated, clear of
    pale structures, of buildings and of row axes (module docstring, step 3)."""
    area = c["area_m2"]
    near = c["location"] in ("block", "headland")
    # in a block (row ends, canopy strips) the ground is dry grass or soil like the inter-rows', not green: the
    # inter-row scrap tiers apply there too, clear of pale structures and buildings (in-block labels at P02, 20:55)
    if (c["location"] == "block" and c["kind"] == "white" and c["structure"] == 0 and c["forbidden_m"] >= params.building_m
            and _white(c, params, pile=False)):
        return True
    context = (c["structure"] == 0 and c["fill"] >= params.rest_fill and c["ring_green"] >= params.rest_ring_green
               and c["density"] - area / 9 <= (params.near_density if near else params.rest_density) and c["forbidden_m"] >= params.building_m
               and c["row_m"] >= params.row_m)
    if not context:
        return False
    if c["kind"] == "white":
        disc = c["fill"] >= 0.6 and max(c["w_m"], c["h_m"]) < 1.3 * min(c["w_m"], c["h_m"]) and c["lum_std"] < 10
        return (not disc and params.rest_area_m2[0] <= area <= params.rest_area_m2[1] and c["clipped"] >= params.rest_clipped
                and c["lum"] >= (params.near_lum if near else params.rest_lum) and c["chroma"] <= params.rest_chroma and c["lum_std"] >= params.rest_texture)
    if c["kind"] == "colour":
        return (185 <= c["hue"] <= 265 and params.rest_blue_area_m2[0] <= area <= params.rest_blue_area_m2[1]
                and c["lum_std"] >= params.rest_blue_texture)
    return False


_CONTEXT: list[Any] = []


def _init(context: Context, params: WasteParams) -> None:
    _CONTEXT[:] = [context, params]


def _scan(tile: Tile) -> list[dict[str, Any]]:
    return site_tile_candidates(tile, *_CONTEXT)


def candidates(tiles: list[Tile], predictions: list[dict[str, Any]], data_dir: Path = DATA_DIR,
               params: WasteParams = WasteParams()) -> list[dict[str, Any]]:
    """Every candidate of `tiles` (in `params.workers` processes)."""
    context = load_context(predictions, data_dir)
    if params.workers <= 1:
        return [c for tile in tiles for c in site_tile_candidates(tile, context, params)]
    with ProcessPoolExecutor(params.workers, initializer=_init, initargs=(context, params)) as pool:
        return [c for found in pool.map(_scan, tiles, chunksize=2) for c in found]


def boxes(found: list[dict[str, Any]], predictions: list[dict[str, Any]], params: WasteParams = WasteParams(), verifier=accept) -> list[dict[str, Any]]:
    """Accepted candidates, padded and merged into one box per object, with the 10 m `vineyard_id`. With scope
    "interrow" only boxes whose centre lies in a predicted inter-row are kept."""
    kept = [c for c in found if verifier(c, params)]
    if not kept:
        return []
    merged = unary_union([box(*c["box"]).buffer(params.pad_m, join_style="mitre") for c in kept])
    blocks = [(shape(f["geometry"]), f["properties"]["vineyard_id"]) for f in predictions if f["properties"].get("label") == "block"]
    union = zone([p for p, _ in interrows(predictions)], [p for p, _ in blocks], params.margin_m) if params.scope != "site" else None
    tree = STRtree([box(*c["box"]) for c in kept])
    features = []
    for part in getattr(merged, "geoms", [merged]):
        geometry = box(*part.bounds)
        if union is not None and not union.contains(geometry.centroid):
            continue
        members = [kept[i] for i in tree.query(geometry)]
        features.append({"type": "Feature", "geometry": mapping(geometry), "properties": {
            "label": "waste", "source": "prediction", "vineyard_id": vineyard_id(geometry, blocks, params.reach_m),
            "location": min((c["location"] for c in members), key=["interrow", "block", "headland", "outside"].index),
            "area_m2": round(sum(c["area_m2"] for c in members), 3), "tiles": sorted({c["tile"] for c in members})}})
    return features


def detect(tiles: list[Tile], predictions: list[dict[str, Any]], data_dir: Path = DATA_DIR,
           params: WasteParams = WasteParams()) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Waste features (EPSG:32635 boxes, `source` prediction, the 10 m `vineyard_id`, `location`) and every
    candidate, for review."""
    found = candidates(tiles, predictions, data_dir, params)
    return boxes(found, predictions, params), found


def main() -> None:
    started = time.perf_counter()
    predictions = json.loads(PREDICTIONS_PATH.read_text(encoding="utf-8"))["features"]
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else WasteParams.workers
    features, found = detect(load_tiles(), predictions, params=WasteParams(workers=workers))
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "candidates_site.json").write_text(json.dumps([c for c in found if c["area_m2"] >= 0.02]), encoding="utf-8")
    path = WORK_DIR / "waste_sitewide.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}), encoding="utf-8")
    by_location: dict[str, int] = {}
    for feature in features:
        by_location[feature["properties"]["location"]] = by_location.get(feature["properties"]["location"], 0) + 1
    print(f"{len(features)} waste boxes {by_location} from {len(found)} candidates in {time.perf_counter() - started:.0f} s -> {path}")


if __name__ == "__main__":
    main()
