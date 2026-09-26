"""Builds the review set for the local review tool (research tooling, never read by prediction code):
1. waste: high-recall candidates from marcaj.waste's candidate stage over every predicted block plus a 6 m headland
   ring, with no verifier, tagged interrow / block / headland, scored, deduped, merged with the manual checklists and
   the accepted boxes -> data/generated/work/waste/candidates_all.geojson (all) and review/waste.json (the review set);
2. gaps: every canopy-gap and missing-planting POI in data/generated/work/poi/poi.geojson -> review/gaps.json;
3. canopies: a length-stratified sample of data/generated/canopy_flags.csv -> review/canopies.json;
plus the crops of each under data/generated/work/review/crops/.
`site` appends the site-wide verifier's boxes and the best new candidates outside the blocks (after
`python -m marcaj.waste`) to an existing review set as S-* cards and re-flags every card; re-running `waste` renumbers
the candidate ids, so do not re-run it once answers exist.
Run from backend/: uv run --frozen python ../research/review/build.py [waste|gaps|canopies|site ...]"""

import csv
import json
import math
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from typing import Any

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely import STRtree
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import unary_union

from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, load_tiles
from marcaj.waste import WasteParams, accept, tile_candidates

GEN = REPO_ROOT / "data" / "generated"
WASTE_DIR = GEN / "work" / "waste"
REVIEW_DIR = GEN / "work" / "review"
CROPS = REVIEW_DIR / "crops"
GRID_LEFT, GRID_TOP, TILE_M = 628992.0, 5221222.4, TILE_PX * PIXEL_M
HEADLAND_M = 6.0
REVIEW_TOP = {"interrow": 320, "block": 100, "headland": 80}  # detector cards per location, besides every checklist item and accepted box
CLOSE_M, CONTEXT_M = 3.0, 12.0
PARAMS = WasteParams(min_candidate_m2=0.008)
warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)


def tile_name(x: float, y: float) -> str:
    return f"siret3_r{int((GRID_TOP - y) // TILE_M):03d}_c{int((x - GRID_LEFT) // TILE_M):03d}.tif"


@lru_cache(maxsize=12)
def _tile_rgb(name: str) -> np.ndarray | None:
    path = DATA_DIR / "tiles" / name
    if not path.is_file():
        return None
    with rasterio.open(path) as source:
        return source.read()


def read_world(left: float, top: float, width_px: int, height_px: int) -> np.ndarray:
    """Native 0.025 m RGB of a world window, stitched across tile edges (black outside the tiles)."""
    out = np.zeros((3, height_px, width_px), np.uint8)
    col0, row0 = round((left - GRID_LEFT) / PIXEL_M), round((GRID_TOP - top) / PIXEL_M)
    for tr in range(row0 // TILE_PX, (row0 + height_px - 1) // TILE_PX + 1):
        for tc in range(col0 // TILE_PX, (col0 + width_px - 1) // TILE_PX + 1):
            rgb = _tile_rgb(f"siret3_r{tr:03d}_c{tc:03d}.tif")
            if rgb is None:
                continue
            r0, c0 = max(row0, tr * TILE_PX), max(col0, tc * TILE_PX)
            r1, c1 = min(row0 + height_px, (tr + 1) * TILE_PX), min(col0 + width_px, (tc + 1) * TILE_PX)
            out[:, r0 - row0:r1 - row0, c0 - col0:c1 - col0] = rgb[:, r0 - tr * TILE_PX:r1 - tr * TILE_PX, c0 - tc * TILE_PX:c1 - tc * TILE_PX]
    return out


def crop(cx: float, cy: float, size_m: float, max_px: int, overlays: list[tuple[Any, tuple[int, int, int]]]) -> np.ndarray:
    """A square crop centred on (cx, cy), downsampled by an integer factor to at most `max_px`, with the overlay
    geometries (EPSG:32635) drawn as 1-px outlines in their colours."""
    native = max(8, round(size_m / PIXEL_M))
    factor = max(1, math.ceil(native / max_px))
    native = native // factor * factor
    left, top = cx - native * PIXEL_M / 2, cy + native * PIXEL_M / 2
    rgb = read_world(left, top, native, native)
    if factor > 1:
        rgb = rgb.reshape(3, native // factor, factor, native // factor, factor).mean((2, 4)).astype(np.uint8)
    size = native // factor
    transform = from_origin(left, top, PIXEL_M * factor, PIXEL_M * factor)
    for geometry, colour in overlays:
        lines = geometry.boundary if geometry.geom_type in ("Polygon", "MultiPolygon") else geometry
        if lines.is_empty:
            continue
        mask = rasterize([(lines, 1)], out_shape=(size, size), transform=transform, all_touched=True, dtype="uint8").astype(bool)
        for band, value in enumerate(colour):
            rgb[band][mask] = value
    return rgb


def save_jpeg(rgb: np.ndarray, name: str) -> str:
    path = CROPS / name
    with rasterio.open(path, "w", driver="JPEG", width=rgb.shape[2], height=rgb.shape[1], count=3, dtype="uint8", quality=88) as out:
        out.write(rgb)
    return f"crops/{name}"


def _load_predictions() -> list[dict[str, Any]]:
    return json.loads((GEN / "predictions.geojson").read_text(encoding="utf-8"))["features"]


# ---------------------------------------------------------------- waste

_REGIONS: list[tuple[Any, str]] = []
_ROWS: list[Any] = []


def _init(regions: list[tuple[Any, str]], rows: list[Any]) -> None:
    _REGIONS[:], _ROWS[:] = regions, rows


def _scan(tile: Any) -> list[dict[str, Any]]:
    return tile_candidates(tile, _REGIONS, _ROWS, PARAMS)


def score(c: dict[str, Any]) -> float:
    """Review ranking, not a verdict: bright neutral or vivid compact blobs away from the row axes rank high; lines
    (tubes, stakes), blobs hugging a row and shadows rank low."""
    size = min(3.0, math.sqrt(c["area_m2"] / 0.04))
    compact = 1.0 if c["length_m"] <= 3 * max(c["width_m"], 1e-3) else 0.35
    away = min(1.2, max(0.25, c["row_m"] / 0.8))
    if c["kind"] == "white":
        colour = min(1.5, max(0.05, (c["lum"] - 185) / 35)) * min(1.2, max(0.15, (35 - c["chroma"]) / 20))
    elif c["kind"] == "colour":
        colour = min(1.5, max(0.1, (c["chroma"] - 45) / 40)) * min(1.3, c["dev"] / 100)
    else:
        colour = 0.15 * min(1.0, c["area_m2"] / 0.08)
    return round(size * compact * away * colour * (2.0 if accept(c, PARAMS) else 1.0), 4)


def _checklist_items() -> list[dict[str, Any]]:
    items = []
    for origin in ("north", "south"):
        for i, entry in enumerate(json.loads((WASTE_DIR / f"checklist_{origin}.json").read_text(encoding="utf-8"))):
            w, h = max(entry.get("w_m", 0.3), 0.1), max(entry.get("h_m", 0.3), 0.1)
            e, n = entry["easting"], entry["northing"]
            items.append({"id": f"CL-{origin[0].upper()}{i + 1:02d}", "origin": f"checklist_{origin}", "tile": entry["tile"],
                          "box": [e - w / 2, n - h / 2, e + w / 2, n + h / 2], "prior": entry["verdict"], "reason": entry.get("reason", ""),
                          "vineyard_id": entry.get("vineyard_id", ""), "score": 0.0})
    accepted = json.loads((WASTE_DIR / "waste.geojson").read_text(encoding="utf-8"))["features"]
    for i, feature in enumerate(accepted):
        geometry = shape(feature["geometry"])
        c = geometry.centroid
        items.append({"id": f"ACC-{i + 1}", "origin": "accepted", "tile": tile_name(c.x, c.y), "box": list(geometry.bounds), "prior": "accepted",
                      "reason": "kept by the current verifier", "vineyard_id": feature["properties"].get("vineyard_id", ""), "score": 0.0})
    return items


def build_waste() -> None:
    if (REVIEW_DIR / "waste.json").is_file() and "--force" not in sys.argv:
        raise SystemExit("review/waste.json exists and its ids carry answers; pass --force to renumber anyway")
    started = time.perf_counter()
    predictions = _load_predictions()
    blocks = [(shape(f["geometry"]), f["properties"]["vineyard_id"]) for f in predictions if f["properties"]["label"] == "block"]
    interrows = [shape(f["geometry"]) for f in predictions if f["properties"]["label"] == "interrow_area"]
    rows = [shape(f["geometry"]) for f in predictions if f["properties"]["label"] == "row"]
    regions = [(polygon.buffer(HEADLAND_M), vid) for polygon, vid in blocks]
    reach = unary_union([polygon for polygon, _ in regions])
    tiles = [tile for tile in load_tiles() if tile.bounds.intersects(reach)]
    with ProcessPoolExecutor(6, initializer=_init, initargs=(regions, rows)) as pool:
        raw = [c for found in pool.map(_scan, tiles, chunksize=2) for c in found]
    print(f"{len(raw)} raw candidates on {len(tiles)} tiles in {time.perf_counter() - started:.0f} s")

    interrow_tree, block_tree = STRtree(interrows), STRtree([polygon for polygon, _ in blocks])
    for c in raw:
        centre = box(*c["box"]).centroid
        c["location"] = ("interrow" if len(interrow_tree.query(centre, predicate="within")) else
                         "block" if len(block_tree.query(centre, predicate="within")) else "headland")
        c["verifier"] = bool(accept(c, PARAMS)) and c["location"] == "interrow"
        c["score"] = score(c)
    # dedupe: blobs within 0.25 m of each other are one item, described by its best-scoring blob
    raw.sort(key=lambda c: -c["score"])
    boxes = [box(*c["box"]) for c in raw]
    tree = STRtree(boxes)
    group = [-1] * len(raw)
    merged = []
    for i, c in enumerate(raw):
        if group[i] >= 0:
            continue
        members = [j for j in tree.query(boxes[i].buffer(0.25)) if group[j] < 0]
        for j in members:
            group[j] = len(merged)
        bounds = unary_union([boxes[j] for j in members]).bounds
        merged.append({**c, "box": list(bounds), "blobs": len(members)})
    print(f"{len(merged)} items after dedupe")

    checklist = _checklist_items()
    merged_tree = STRtree([box(*c["box"]) for c in merged])
    items = []
    for i, c in enumerate(merged):
        e, n = (c["box"][0] + c["box"][2]) / 2, (c["box"][1] + c["box"][3]) / 2
        items.append({"id": f"W{i:06d}", "origin": "detector", "tile": c["tile"], "box": [round(v, 3) for v in c["box"]], "easting": round(e, 2),
                      "northing": round(n, 2), "score": c["score"], "rank": i + 1, "kind": c["kind"], "location": c["location"], "verifier": c["verifier"],
                      "vineyard_id": c["vineyard_id"], "area_m2": round(c["area_m2"], 4), "lum": round(c["lum"], 1), "chroma": round(c["chroma"], 1),
                      "hue": round(c["hue"], 1), "dev": round(c["dev"], 1), "rgb": c["rgb"], "width_m": round(c["width_m"], 3),
                      "length_m": round(c["length_m"], 3), "row_m": round(c["row_m"], 2), "blobs": c["blobs"], "prior": "", "reason": ""})
    for entry in checklist:
        near = [items[j] for j in merged_tree.query(box(*entry["box"]).buffer(0.3))]
        if near:  # the checklist item was also generated: keep the detector's stats, add the checklist's note
            best = min(near, key=lambda it: it["rank"])
            best["origin"] = f"{best['origin']}+{entry['origin']}"
            best["prior"], best["reason"] = entry["prior"], entry["reason"]
            best.setdefault("checklist_ids", []).append(entry["id"])
            continue
        e, n = (entry["box"][0] + entry["box"][2]) / 2, (entry["box"][1] + entry["box"][3]) / 2
        centre = Point(e, n)
        location = ("interrow" if len(interrow_tree.query(centre, predicate="within")) else
                    "block" if len(block_tree.query(centre, predicate="within")) else "headland" if reach.contains(centre) else "outside")
        items.append({**entry, "box": [round(v, 3) for v in entry["box"]], "easting": round(e, 2), "northing": round(n, 2), "rank": None,
                      "kind": "manual", "location": location, "verifier": entry["origin"] == "accepted"})
    for it in items:
        tile = it["tile"]
        left, top = GRID_LEFT + TILE_M * int(tile[13:16]), GRID_TOP - TILE_M * int(tile[8:11])
        it["x_px"], it["y_px"] = round((it["easting"] - left) / PIXEL_M), round((top - it["northing"]) / PIXEL_M)
        it["w_m"], it["h_m"] = round(it["box"][2] - it["box"][0], 2), round(it["box"][3] - it["box"][1], 2)

    features = [{"type": "Feature", "geometry": mapping(box(*it["box"])), "properties": {k: v for k, v in it.items() if k != "box"}} for it in items]
    (WASTE_DIR / "candidates_all.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": features}), encoding="utf-8")
    seen: dict[str, int] = {}
    for it in items:  # items are in score order; rank within the location type
        if it["rank"] is not None:
            seen[it["location"]] = it["location_rank"] = seen.get(it["location"], 0) + 1
    review = [it for it in items if it["origin"] != "detector" or it["location_rank"] <= REVIEW_TOP[it["location"]]]
    review.sort(key=lambda it: (it["tile"], it["y_px"]))
    for it in review:
        target = box(*it["box"])
        it["close"] = save_jpeg(crop(it["easting"], it["northing"], max(CLOSE_M, 2.5 * max(it["w_m"], it["h_m"])), 240, [(target.buffer(0.08, join_style="mitre"), (255, 0, 255))]), f"w_{it['id']}_close.jpg")
        it["context"] = save_jpeg(crop(it["easting"], it["northing"], CONTEXT_M, 480, [(target.buffer(0.3, join_style="mitre"), (255, 0, 255))]), f"w_{it['id']}_ctx.jpg")
    review.sort(key=lambda it: (it["origin"] == "detector", -(it["score"] or 0)))
    (REVIEW_DIR / "waste.json").write_text(json.dumps(review), encoding="utf-8")
    by_location: dict[str, int] = {}
    for it in items:
        by_location[it["location"]] = by_location.get(it["location"], 0) + 1
    print(f"{len(items)} items {by_location}; review set {len(review)}; {time.perf_counter() - started:.0f} s")


NEW_CARDS = {"outside": 150, "headland": 60}  # site-wide candidates added as cards, besides every site-wide verifier box


def rule_flags(it: dict[str, Any]) -> list[str]:
    """Likely conflicts with the rules' not-waste list (section 3), from the item's blob stats; empty when unknown."""
    if it.get("lum") is None:
        return []
    flags = []
    if it["area_m2"] < 0.02:
        flags.append("tiny scrap (< 0.02 m2): not clearly visible litter")
    if it["kind"] == "black":
        flags.append("dark blob: shadow, hole or dark soil")
    elif it["area_m2"] >= 0.15 and (it["lum"] < 215 or it["chroma"] > 22) and it["dev"] < 170:
        flags.append("large dull or tinted patch: pale or dry soil")
    elif it["kind"] == "white" and it["lum"] < 212 and it["dev"] < 170:
        flags.append("not bright (lum < 212): pale clod or stone range")
    if it["dev"] < 105:
        flags.append("low contrast with the ground")
    if it["row_m"] < 0.5 and it["location"] in ("interrow", "block"):
        flags.append("within 0.5 m of a row axis: vine tube, stake or post")
    if it["length_m"] > 3.5 * max(it["width_m"], 1e-3):
        flags.append("long thin shape: tube, stake, hose or wire")
    return flags


def _site_item(c: dict[str, Any], geometry: Any, origin: str) -> dict[str, Any]:
    e, n = (geometry.bounds[0] + geometry.bounds[2]) / 2, (geometry.bounds[1] + geometry.bounds[3]) / 2
    tile = tile_name(e, n)
    left, top = GRID_LEFT + TILE_M * int(tile[13:16]), GRID_TOP - TILE_M * int(tile[8:11])
    x, y = round((e - left) / PIXEL_M), round((top - n) / PIXEL_M)
    it = {"id": f"S-{tile[7:16]}-{x}-{y}", "origin": origin, "tile": tile, "box": [round(v, 3) for v in geometry.bounds], "easting": round(e, 2),
          "northing": round(n, 2), "x_px": x, "y_px": y, "w_m": round(geometry.bounds[2] - geometry.bounds[0], 2), "h_m": round(geometry.bounds[3] - geometry.bounds[1], 2),
          "score": c.get("score"), "rank": None, "location_rank": None, "kind": c["kind"], "location": c["location"], "verifier": origin.endswith("verifier"),
          "vineyard_id": c["vineyard_id"], "prior": "", "reason": ""}
    for key in ("area_m2", "lum", "chroma", "hue", "dev", "width_m", "length_m", "row_m", "clipped", "ring_green", "density", "forbidden_m"):
        if key in c:
            it[key] = round(c[key], 3)
    it["rgb"] = c["rgb"]
    return it


def append_site() -> None:
    """Adds site-wide cards to the review set without touching existing items (their ids carry the user's answers):
    every box of the site-wide verifier (work/waste/waste_sitewide.geojson) and the best-scoring new candidates
    outside the blocks (NEW_CARDS, from work/waste/candidates_site.json, both written by `python -m marcaj.waste`).
    Re-flags `verifier` and `rule_flags` on every item."""
    path = REVIEW_DIR / "waste.json"
    review = json.loads(path.read_text(encoding="utf-8"))
    found = [c for c in json.loads((WASTE_DIR / "candidates_site.json").read_text(encoding="utf-8"))]
    site_boxes = [shape(f["geometry"]) for f in json.loads((WASTE_DIR / "waste_sitewide.geojson").read_text(encoding="utf-8"))["features"]]
    box_tree = STRtree(site_boxes)
    ids = {it["id"] for it in review}
    existing = STRtree([box(*it["box"]) for it in review])
    taken: list[Any] = []
    added: dict[str, int] = {}
    for it in review:  # earlier site cards count toward NEW_CARDS, so a re-run only re-flags
        if it["id"].startswith("S-"):
            added[it["location"]] = added.get(it["location"], 0) + 1

    def add(it: dict[str, Any], geometry: Any) -> None:
        if it["id"] in ids or len(existing.query(geometry.buffer(0.25))) or any(g.distance(geometry) < 0.25 for g in taken):
            return
        target = box(*it["box"])
        it["close"] = save_jpeg(crop(it["easting"], it["northing"], max(CLOSE_M, 2.5 * max(it["w_m"], it["h_m"])), 240, [(target.buffer(0.08, join_style="mitre"), (255, 0, 255))]), f"w_{it['id']}_close.jpg")
        it["context"] = save_jpeg(crop(it["easting"], it["northing"], CONTEXT_M, 480, [(target.buffer(0.3, join_style="mitre"), (255, 0, 255))]), f"w_{it['id']}_ctx.jpg")
        new.append(it)
        ids.add(it["id"])
        taken.append(geometry)
        added[it["location"]] = added.get(it["location"], 0) + 1

    new: list[dict[str, Any]] = []
    candidate_tree = STRtree([box(*c["box"]) for c in found])
    for geometry in site_boxes:  # the verifier's boxes first, described by their largest member blob
        members = [found[i] for i in candidate_tree.query(geometry)]
        c = max(members, key=lambda c: c["area_m2"])
        add(_site_item({**c, "score": score(c)}, geometry, "site verifier"), geometry)
    rest = sorted(({**c, "score": score(c)} for c in found if c["location"] in NEW_CARDS), key=lambda c: -c["score"])
    for location, limit in NEW_CARDS.items():
        for c in [c for c in rest if c["location"] == location]:
            if added.get(location, 0) >= limit:
                break
            add(_site_item(c, box(*c["box"]), "site"), box(*c["box"]))
    review = new + review
    for it in review:
        it["verifier"] = bool(len(box_tree.query(box(*it["box"]).buffer(0.1))))
        it["rule_flags"] = rule_flags(it)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(review), encoding="utf-8")
    temporary.replace(path)
    print(f"{len(site_boxes)} site-wide boxes; added {len(new)} cards (site cards now {added}); {sum(it['verifier'] for it in review)} items flagged as verifier boxes, "
          f"{sum(bool(it['rule_flags']) for it in review)} with rule flags; {len(review)} cards")


# ---------------------------------------------------------------- canopy gaps and long canopies

def _canopies(predictions: list[dict[str, Any]]) -> tuple[list[Any], STRtree]:
    polygons = [shape(f["geometry"]) for f in predictions if f["properties"]["label"] == "vineyard"]
    return polygons, STRtree(polygons)


def build_gaps() -> None:
    predictions = _load_predictions()
    polygons, tree = _canopies(predictions)
    rows = {f["properties"]["row_id"]: shape(f["geometry"]) for f in predictions if f["properties"]["label"] == "row"}
    items = []
    for f in json.loads((GEN / "work" / "poi" / "poi.geojson").read_text(encoding="utf-8"))["features"]:
        p = f["properties"]
        if p.get("reason") not in ("gap", "planting"):
            continue
        a, b = p["gap_start"], p["gap_end"]
        segment = LineString([a, b])
        mid = segment.interpolate(0.5, normalized=True)
        size = min(64.0, max(10.0, segment.length + 6))
        window = box(mid.x - size / 2, mid.y - size / 2, mid.x + size / 2, mid.y + size / 2)
        near = [polygons[i] for i in tree.query(window)]
        overlays = [(g, (255, 0, 255)) for g in near] + [(segment.buffer(0.06), (255, 230, 0))]
        row = rows.get(p.get("row_id"))
        if row is not None:
            overlays.insert(0, (row.intersection(window), (0, 200, 255)))
        item = {"id": p["id"], "reason": p["reason"], "vineyard_id": p.get("vineyard_id", ""), "row_id": p.get("row_id", ""), "gap_m": p.get("gap_m"),
                "green_share": p.get("green_share"), "confidence": p.get("confidence"), "challenge": p.get("challenge"), "tile": tile_name(mid.x, mid.y),
                "easting": round(mid.x, 2), "northing": round(mid.y, 2), "gap_start": a, "gap_end": b, "geometry": mapping(segment)}
        item["context"] = save_jpeg(crop(mid.x, mid.y, size, 640, overlays), f"g_{p['id']}_ctx.jpg")
        item["close"] = save_jpeg(crop(mid.x, mid.y, 6.0, 240, [(g, (255, 0, 255)) for g in near] + [(segment.buffer(0.03), (255, 230, 0))]), f"g_{p['id']}_close.jpg")
        items.append(item)
    items.sort(key=lambda it: -(it["gap_m"] or 0))
    (REVIEW_DIR / "gaps.json").write_text(json.dumps(items), encoding="utf-8")
    print(f"{len(items)} gap items")


def build_canopies(sample: int = 150) -> None:
    predictions = _load_predictions()
    polygons, tree = _canopies(predictions)
    flags = sorted(csv.DictReader((GEN / "canopy_flags.csv").open(encoding="utf-8")), key=lambda r: float(r["length_m"]))
    picked = [flags[round(i * (len(flags) - 1) / (sample - 1))] for i in range(sample)] if len(flags) > sample else flags
    items = []
    for i, row in enumerate(picked):
        point = Point(float(row["easting"]), float(row["northing"]))
        hits = list(tree.query(point.buffer(0.5)))
        if not hits:
            continue
        target = polygons[min(hits, key=lambda j: polygons[j].distance(point))]
        c = target.centroid
        size = min(40.0, float(row["length_m"]) + 3)
        window = box(c.x - size / 2, c.y - size / 2, c.x + size / 2, c.y + size / 2)
        others = [(polygons[j], (255, 0, 255)) for j in tree.query(window) if polygons[j] is not target]
        cid = f"C{i:03d}-{row['tile'][7:16]}-{row['x_px']}-{row['y_px']}"
        item = {"id": cid, "tile": row["tile"], "x_px": int(row["x_px"]), "y_px": int(row["y_px"]), "length_m": float(row["length_m"]),
                "area_m2": float(row["area_m2"]), "vineyard_id": row["vineyard_id"], "easting": round(c.x, 2), "northing": round(c.y, 2),
                "geometry": mapping(target)}
        item["context"] = save_jpeg(crop(c.x, c.y, size, 640, others + [(target, (0, 255, 255))]), f"c_{cid}_ctx.jpg")
        item["close"] = save_jpeg(crop(c.x, c.y, 6.0, 240, others + [(target, (0, 255, 255))]), f"c_{cid}_close.jpg")
        items.append(item)
    (REVIEW_DIR / "canopies.json").write_text(json.dumps(items), encoding="utf-8")
    print(f"{len(items)} long-canopy items")


if __name__ == "__main__":
    CROPS.mkdir(parents=True, exist_ok=True)
    steps = {"waste": build_waste, "gaps": build_gaps, "canopies": build_canopies, "site": append_site}
    for step in [a for a in sys.argv[1:] if not a.startswith("--")] or ["waste", "gaps", "canopies"]:
        steps[step]()
