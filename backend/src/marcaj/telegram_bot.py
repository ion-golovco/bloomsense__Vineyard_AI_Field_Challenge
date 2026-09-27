"""Telegram bot: a field's precomputed inspection walk as one picture and one checklist, where farmers already are.

`/start` lists the fields; `/field V06-03` (or just `V06-03`) and `/site` (the whole farm) answer with a PNG of the drone
imagery (data/generated/mosaic_20cm.tif, one window read) with the route, START and the stops numbered in walking
order, then a text checklist in the same order with a Google Maps link per stop. Routes come from
data/generated/routes.geojson (marcaj-export, POI confidence cutoff null); the bot never plans a route. Visiting order is
where the route line first passes each visited target. Stdlib Bot API over urllib (long polling), no new dependencies.

    uv run --frozen python -m marcaj.telegram_bot --render V06-03 --out out.png   # offline: PNG + printed message
    uv run --frozen python -m marcaj.telegram_bot --check                        # every route, order and picture checks
    TELEGRAM_BOT_TOKEN=... uv run --frozen python -m marcaj.telegram_bot          # the bot
"""

import argparse
import http.client
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import warnings
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.features import rasterize
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.windows import Window
from rasterio.windows import bounds as window_bounds
from rasterio.windows import from_bounds as window_from_bounds
from shapely.geometry import LineString, Point, box, shape

from marcaj.mosaic import MOSAIC_PATH
from marcaj.scene import load_projected_scene, scene_path, waste_id
from marcaj.tiles import REPO_ROOT

ROUTES_PATH = REPO_ROOT / "data" / "generated" / "routes.geojson"
POI_PATH = REPO_ROOT / "data" / "generated" / "work" / "poi" / "poi.geojson"
SAMPLES = REPO_ROOT / "data" / "generated" / "work" / "telegram"
LONG_SIDE = 1280           # Telegram shows photos at up to 1280 px
PHOTO_LIMIT = 10_000_000   # Telegram's sendPhoto limit
TEXT_LIMIT = 4000          # Telegram's message limit is 4096 characters
WALK_KMH = 4.0
ROUTE_RGB, MISSING_RGB, WASTE_RGB = (255, 221, 0), (230, 45, 45), (0, 160, 255)
START_RGB, SKIPPED_RGB, BLACK = (255, 255, 255), (150, 150, 150), (0, 0, 0)
_TO_WGS84 = Transformer.from_crs("EPSG:32635", "EPSG:4326", always_xy=True)
log = logging.getLogger("marcaj.telegram_bot")

# 5x7 bitmap font, five column bytes per glyph (bit 0 is the top row): no font library is a dependency
FONT = {
    " ": "0000000000", "(": "001C224100", ")": "0041221C00", ",": "0050300000", "-": "0808080808", ".": "0060600000",
    "/": "2010080402", ":": "0036360000", "?": "0201510906",
    "0": "3E5149453E", "1": "00427F4000", "2": "4261514946", "3": "2141454B31", "4": "1814127F10",
    "5": "2745454539", "6": "3C4A494930", "7": "0171090503", "8": "3649494936", "9": "064949291E",
    "A": "7E1111117E", "B": "7F49494936", "C": "3E41414122", "D": "7F4141221C", "E": "7F49494941", "F": "7F09090901",
    "G": "3E4149497A", "H": "7F0808087F", "I": "00417F4100", "J": "2040413F01", "K": "7F08142241", "L": "7F40404040",
    "M": "7F020C027F", "N": "7F0408107F", "O": "3E4141413E", "P": "7F09090906", "Q": "3E4151215E", "R": "7F09192946",
    "S": "4649494931", "T": "01017F0101", "U": "3F4040403F", "V": "1F2040201F", "W": "3F4038403F", "X": "6314081463",
    "Y": "0708700807", "Z": "6151494543",
}


# ---- data -------------------------------------------------------------------------------------------------------

def _read(path: Path) -> dict[str, Any]:
    collection = json.loads(path.read_text(encoding="utf-8"))
    if collection.get("crs") != "EPSG:32635":
        raise ValueError(f"{path} must be an EPSG:32635 FeatureCollection")
    return collection


def _stamp() -> tuple[float, ...]:
    """File versions the answers depend on, so a re-exported route is picked up without a restart."""
    return tuple(path.stat().st_mtime if path.is_file() else 0.0 for path in (ROUTES_PATH, POI_PATH, scene_path(), MOSAIC_PATH))


@lru_cache(maxsize=2)
def _data(stamp: tuple[float, ...]) -> dict[str, Any]:
    """Routes, every possible stop by target id (as marcaj.route keys them: inspection points by `id`, waste boxes by
    `waste_id` at their centroid, with the nearest row of their field) and the field outlines."""
    scene = load_projected_scene()
    rows: dict[str, list[tuple[Any, str]]] = defaultdict(list)
    blocks: dict[str, list[Any]] = defaultdict(list)
    for feature in scene["features"]:
        properties = feature["properties"]
        if properties.get("label") == "row":
            rows[properties.get("vineyard_id") or ""].append((shape(feature["geometry"]), properties.get("row_id", "")))
        elif properties.get("label") == "block" and properties.get("vineyard_id"):
            blocks[properties["vineyard_id"]].append(shape(feature["geometry"]))
    points: dict[str, dict[str, Any]] = {}
    for feature in _read(POI_PATH)["features"] if POI_PATH.is_file() else []:
        properties = feature["properties"]
        if properties.get("label") == "inspection" and properties.get("id"):
            points[str(properties["id"])] = {"kind": properties.get("reason", ""), "point": shape(feature["geometry"]),
                                             "vineyard_id": properties.get("vineyard_id") or "", "row_id": properties.get("row_id", ""),
                                             "gap_m": properties.get("gap_m")}
    for feature in scene["features"]:
        properties = feature["properties"]
        if properties.get("label") == "waste":
            centre = shape(feature["geometry"]).centroid
            field = properties.get("vineyard_id") or ""
            _, row_id = min(rows.get(field, []), key=lambda item: item[0].distance(centre), default=(None, ""))
            points[waste_id(feature)] = {"kind": "waste", "point": centre, "vineyard_id": field, "row_id": row_id, "gap_m": None}
    return {"routes": [f for f in _read(ROUTES_PATH)["features"] if f["properties"].get("min_confidence") is None],
            "points": points, "blocks": dict(blocks)}


def fields() -> list[str]:
    """Fields with a precomputed route."""
    return sorted(f["properties"]["vineyard_id"] for f in _data(_stamp())["routes"] if f["properties"].get("scope") == "field")


def match_field(text: str) -> str | None:
    return {field.upper(): field for field in fields()}.get(text.strip().upper())


def _first_pass(coords: np.ndarray, point: Point, reach: float) -> float:
    """Distance along the route (m) where it first comes within `reach` of `point`; nan if it never does."""
    a, d = coords[:-1], np.diff(coords, axis=0)
    lengths = np.hypot(d[:, 0], d[:, 1])
    p = np.array([point.x, point.y])
    t = np.clip(((p - a) * d).sum(axis=1) / np.maximum(lengths ** 2, 1e-12), 0.0, 1.0)
    near = np.flatnonzero(np.hypot(*(a + t[:, None] * d - p).T) <= reach)
    if not near.size:
        return float("nan")
    return float(np.r_[0.0, np.cumsum(lengths)][near[0]] + t[near[0]] * lengths[near[0]])


def walk(field: str | None) -> dict[str, Any]:
    """The route of `field` (None: the whole farm), its visited targets in walking order and the skipped ones.
    KeyError for a field without a route."""
    data = _data(_stamp())
    route = next((f for f in data["routes"] if (f["properties"].get("scope") == "site" if field is None
                                                 else f["properties"].get("scope") == "field" and f["properties"].get("vineyard_id") == field)), None)
    if route is None:
        raise KeyError(field)
    line = shape(route["geometry"])
    coords = np.asarray(line.coords)
    stops, skipped, unknown = [], [], []
    for item in route["properties"].get("target_status", []):
        if item["id"] not in data["points"]:
            unknown.append(item["id"])  # poi.geojson rebuilt after the route: re-run marcaj-export
            continue
        # the solver reports the closest pass; the first pass that close is where the walker gets there
        stop = {**data["points"][item["id"]], "id": item["id"], "reach": float(item["distance_m"] or 0.0) + 0.5}
        if item["status"] == "visited":
            stops.append({**stop, "at_m": _first_pass(coords, stop["point"], stop["reach"])})
        else:
            skipped.append(stop)
    if unknown:
        log.warning("%d route targets of %s are not in the POI or scene files: %s", len(unknown), field or "site", ", ".join(unknown[:5]))
    stops.sort(key=lambda stop: stop["at_m"])
    return {"field": field, "properties": route["properties"], "line": line, "stops": stops, "skipped": skipped,
            "unknown": unknown, "blocks": data["blocks"].get(field, []) if field else []}


# ---- picture ----------------------------------------------------------------------------------------------------

def _glyphs(text: str, scale: int) -> np.ndarray:
    """`text` in the 5x7 font as a boolean mask, one blank column between letters."""
    columns: list[int] = []
    for char in text.upper():
        columns += list(bytes.fromhex(FONT.get(char, FONT["?"]))) + [0]
    bits = (np.array(columns[:-1] or [0], dtype=np.uint8)[None, :] >> np.arange(7)[:, None]) & 1
    return np.kron(bits, np.ones((scale, scale), dtype=np.uint8)).astype(bool)


def _paste(rgb: np.ndarray, mask: np.ndarray, x: int, y: int, colour: tuple[int, int, int]) -> None:
    """Paint `mask` with its top-left corner at column x, row y, clipped to the image."""
    x0, y0, x1, y1 = max(0, x), max(0, y), min(rgb.shape[2], x + mask.shape[1]), min(rgb.shape[1], y + mask.shape[0])
    if x0 < x1 and y0 < y1:
        rgb[:, y0:y1, x0:x1][:, mask[y0 - y:y1 - y, x0 - x:x1 - x]] = np.array(colour, dtype=np.uint8)[:, None]


def _disc(rgb: np.ndarray, cx: float, cy: float, radius: float, colour: tuple[int, int, int]) -> None:
    size = int(np.ceil(radius)) * 2 + 1
    yy, xx = np.mgrid[:size, :size] - size // 2
    _paste(rgb, (xx + 0.5 + round(cx) - cx) ** 2 + (yy + 0.5 + round(cy) - cy) ** 2 <= radius ** 2, round(cx) - size // 2, round(cy) - size // 2, colour)


def _square(rgb: np.ndarray, cx: float, cy: float, half: int, colour: tuple[int, int, int]) -> None:
    _paste(rgb, np.ones((2 * half + 1, 2 * half + 1), dtype=bool), round(cx) - half, round(cy) - half, colour)


def _text(rgb: np.ndarray, x: int, y: int, text: str, scale: int, colour: tuple[int, int, int] = START_RGB, centred: bool = False) -> None:
    mask = _glyphs(text, scale)
    if centred:
        x, y = round(x - mask.shape[1] / 2), round(y - mask.shape[0] / 2)
    _paste(rgb, mask, x, y, colour)


def _panel(rgb: np.ndarray, x: int, y: int, width: int, height: int) -> None:
    rgb[:, max(0, y):max(0, y + height), max(0, x):max(0, x + width)] //= 4


def _frame(geometries: list[Any], pad: float = 15.0, min_side: float = 80.0) -> tuple[float, float, float, float]:
    b = np.array([g.bounds for g in geometries])
    left, bottom, right, top = b[:, 0].min() - pad, b[:, 1].min() - pad, b[:, 2].max() + pad, b[:, 3].max() + pad
    grow_x, grow_y = max(0.0, min_side - (right - left)) / 2, max(0.0, min_side - (top - bottom)) / 2
    return left - grow_x, bottom - grow_y, right + grow_x, top + grow_y


def _side(frame: tuple[float, float, float, float]) -> float:
    return max(frame[2] - frame[0], frame[3] - frame[1])


def _distance(metres: float) -> str:
    return f"{metres / 1000:.1f} KM" if metres >= 1000 else f"{round(metres, -1):.0f} M"


def render(result: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    """RGB (3, H, W) uint8 of the walk and what the check needs: the pixel transform and the imagery's non-black
    share. A field is framed on its outline and stops; the approach from START is shown whole only when it does not
    shrink the field (otherwise the route's entry is marked with the walking distance to START)."""
    line, stops, skipped, field = result["line"], result["stops"], result["skipped"], result["field"]
    points = [stop["point"] for stop in stops + skipped]
    focus = _frame(result["blocks"] + points or [line])
    whole = _frame([line] + result["blocks"] + points)
    frame = whole if field is None or _side(whole) <= 1.6 * _side(focus) else focus
    margin = 0.03 * _side(frame)  # room for the markers at the edges
    frame = (frame[0] - margin, frame[1] - margin, frame[2] + margin, frame[3] + margin)
    with rasterio.open(MOSAIC_PATH) as mosaic:  # one window, read at the output size (from the overviews when smaller)
        window = window_from_bounds(*frame, transform=mosaic.transform).round_offsets().round_lengths()
        window = window.intersection(Window(0, 0, mosaic.width, mosaic.height))
        frame = window_bounds(window, mosaic.transform)
        zoom = LONG_SIDE / max(window.width, window.height)
        height, width = max(1, round(window.height * zoom)), max(1, round(window.width * zoom))
        rgb = mosaic.read((1, 2, 3), window=window, out_shape=(3, height, width),
                          resampling=Resampling.average if zoom < 1 else Resampling.bilinear)
    imagery = float((rgb.max(axis=0) > 0).mean())
    transform = from_bounds(*frame, width, height)
    px = (frame[2] - frame[0]) / width

    def pixel(point: Point) -> tuple[float, float]:
        return ~transform * (point.x, point.y)

    if line.length > 0:
        for grow, colour in ((3.5, BLACK), (2.0, ROUTE_RGB)):
            mask = rasterize([line.buffer(grow * px)], out_shape=(height, width), transform=transform, dtype="uint8").astype(bool)
            rgb[:, mask] = np.array(colour, dtype=np.uint8)[:, None]
    scale = 2 if field else 1
    for stop in skipped:
        x, y = pixel(stop["point"])
        _disc(rgb, x, y, 4 * scale + 2, BLACK)
        _disc(rgb, x, y, 4 * scale, SKIPPED_RGB)
    for number, stop in enumerate(stops, start=1):
        x, y = pixel(stop["point"])
        radius = max(len(str(number)) * 6 * scale, 7 * scale) / 2 + 2 * scale
        _disc(rgb, x, y, radius + 2, BLACK)
        _disc(rgb, x, y, radius, WASTE_RGB if stop["kind"] == "waste" else MISSING_RGB)
        _text(rgb, round(x), round(y), str(number), scale, centred=True)
    coords = np.asarray(line.coords)
    left, bottom, right, top = frame
    inside = (coords[:, 0] >= left) & (coords[:, 0] <= right) & (coords[:, 1] >= bottom) & (coords[:, 1] <= top)
    start, first = Point(coords[0]), int(np.argmax(inside))
    if inside[0]:
        x, y = pixel(start)
        _square(rgb, x, y, 7 * scale + 2, BLACK)
        _square(rgb, x, y, 7 * scale, START_RGB)
        _text(rgb, round(x), round(y), "S", scale, BLACK, centred=True)
    elif inside.any():  # START is off the picture: mark where the route comes in, with the walk from START
        crossing = LineString(coords[first - 1:first + 1]).intersection(box(*frame).boundary)
        entry = crossing if crossing.geom_type == "Point" else Point(coords[first])
        along = float(np.hypot(*np.diff(coords[:first], axis=0).T).sum()) + Point(coords[first - 1]).distance(entry)
        x, y = pixel(entry)
        x, y = min(max(x, 10 * scale), width - 10 * scale), min(max(y, 10 * scale), height - 10 * scale)
        _square(rgb, x, y, 7 * scale + 2, BLACK)
        _square(rgb, x, y, 7 * scale, START_RGB)
        _text(rgb, round(x), round(y), "S", scale, BLACK, centred=True)
        label = _glyphs(f"START {_distance(along)} THIS WAY", 2)
        lx = round(x + 12 * scale) if x + 12 * scale + label.shape[1] + 8 < width else round(x - 12 * scale - label.shape[1] - 8)
        ly = min(max(round(y - label.shape[0] / 2) - 4, 0), height - label.shape[0] - 8)
        if y > height - 60:  # at the bottom edge: above the marker, clear of the scale bar
            ly = round(y - 7 * scale - 12 - label.shape[0] - 8)
        _panel(rgb, lx, ly, label.shape[1] + 8, label.shape[0] + 8)
        _paste(rgb, label, lx + 4, ly + 4, START_RGB)
    _legend(rgb, result, px)
    return rgb, {"transform": transform, "imagery": imagery}


def _legend(rgb: np.ndarray, result: dict[str, Any], px: float) -> None:
    length = result["properties"].get("length_m", 0.0)
    walk_text = f"{length / 1000:.2f} KM  {round(length / (WALK_KMH * 1000 / 60))} MIN" if result["stops"] else "NO ROUTE"
    title = f"{result['field'] or 'WHOLE FARM'}  {walk_text}"
    rows = [("route", "ROUTE"), ("start", "START"), ("missing", "MISSING VINES"), ("waste", "WASTE")]
    rows += [("skipped", "SKIPPED")] if result["skipped"] else []
    height = 28 + 22 * (len(rows) + 1)
    width = 16 + max(_glyphs(title, 2).shape[1], 34 + max(_glyphs(text, 2).shape[1] for _, text in rows))
    # top left or top right, whichever covers less of the picture (drone imagery and markers; no-data is black)
    x0 = min((8, rgb.shape[2] - width - 8), key=lambda x: (rgb[:, 8:8 + height, max(0, x):x + width].max(axis=0) > 0).mean())
    _panel(rgb, x0, 8, width, height)
    _text(rgb, x0 + 8, 16, title, 2)
    for k, (kind, text) in enumerate(rows, start=1):
        y = 16 + 22 * k
        if kind == "route":
            rgb[:, y + 3:y + 11, x0 + 8:x0 + 34] = np.array(ROUTE_RGB, dtype=np.uint8)[:, None, None]
        elif kind == "start":
            _square(rgb, x0 + 21, y + 7, 7, START_RGB)
            _text(rgb, x0 + 21, y + 7, "S", 1, BLACK, centred=True)
        else:
            _disc(rgb, x0 + 21, y + 7, 7, {"missing": MISSING_RGB, "waste": WASTE_RGB, "skipped": SKIPPED_RGB}[kind])
        _text(rgb, x0 + 42, y, text, 2)
    _text(rgb, x0 + 8, 16 + 22 * (len(rows) + 1), "AGROCONTROL BY BLOOMSENSE", 1, (200, 200, 200))
    # scale bar along the bottom
    height_px, width_px = rgb.shape[1:]
    metres = max((m for m in (5, 10, 20, 50, 100, 200, 500) if m / px <= width_px / 5), default=5)
    bar = round(metres / px)
    _panel(rgb, 8, height_px - 40, bar + 80, 32)
    rgb[:, height_px - 22:height_px - 16, 16:16 + bar] = 255
    _text(rgb, 24 + bar, height_px - 31, f"{metres} M", 2)


def to_png(rgb: np.ndarray) -> bytes:
    with warnings.catch_warnings(), MemoryFile() as memory:
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with memory.open(driver="PNG", width=rgb.shape[2], height=rgb.shape[1], count=3, dtype="uint8") as png:
            png.write(rgb)
        return memory.read()


# ---- message ----------------------------------------------------------------------------------------------------

def describe(stop: dict[str, Any]) -> str:
    """What to look for at a stop."""
    row = stop["row_id"] or "?"
    stretch = f"~{stop['gap_m']:.0f} m " if stop.get("gap_m") else ""
    if stop["kind"] == "gap":
        return f"Row {row}: {stretch}without vines, check for missing or dead vines"
    if stop["kind"] == "planting":
        return f"Row {row}: {stretch}at the row end without vines, check if it was never planted or the vines died"
    if stop["kind"] == "waste":
        return f"Waste pile near row {row}: remove" if stop["row_id"] else f"Waste pile in field {stop['vineyard_id']}: remove"
    return f"Point {stop['id']}: check"


def _maps(point: Point) -> str:
    lon, lat = _TO_WGS84.transform(point.x, point.y)
    return f"https://maps.google.com/?q={lat:.6f},{lon:.6f}"


def message(result: dict[str, Any]) -> str:
    field, stops, skipped = result["field"], result["stops"], result["skipped"]
    length = result["properties"].get("length_m", 0.0)
    lines = [f"Agrocontrol inspection walk: {f'field {field}' if field else 'whole farm'}"]
    if stops:
        lines += [f"Route {length / 1000:.2f} km, about {round(length / (WALK_KMH * 1000 / 60))} min walking at {WALK_KMH:.0f} km/h, from START and back.",
                  f"{len(stops)} stop{'s' if len(stops) > 1 else ''} in walking order (numbers match the map):"]
        items = [f"{number}. {describe(stop)}\n{_maps(stop['point'])}" for number, stop in enumerate(stops, start=1)]
    else:
        lines += ["No route inside the inter-rows and passages reaches this field's points. Check them on foot (grey on the map):"]
        items = [f"- {describe(stop)}\n{_maps(stop['point'])}" for stop in skipped]
    tail = []
    if stops and skipped:
        tail.append(f"{len(skipped)} point{'s' if len(skipped) > 1 else ''} skipped: reaching {'them' if len(skipped) > 1 else 'it'} would leave the inter-rows and passages (grey on the map).")
    tail.append("Map: yellow = route, S = start, red = missing vines, blue = waste" + (", grey = skipped." if skipped else "."))
    text, shown = "\n".join(lines), 0
    for item in items:
        if len(text) + len(item) + sum(len(t) + 1 for t in tail) + 40 > TEXT_LIMIT:
            break
        text, shown = text + "\n" + item, shown + 1
    if shown < len(items):
        text += f"\n…and {len(items) - shown} more on the map"
    return text + "\n\n" + "\n".join(tail)


# ---- check ------------------------------------------------------------------------------------------------------

def check(result: dict[str, Any], rgb: np.ndarray, meta: dict[str, Any], text: str) -> None:
    """Fails (AssertionError) if the checklist is not in route order, a visited target is missing from it, the message
    numbers differ from it, or the picture has no imagery or no route on it. The order is re-derived independently of
    `_first_pass`, from the route densified every 0.25 m."""
    line, stops = result["line"], result["stops"]
    visited = sorted(item["id"] for item in result["properties"].get("target_status", []) if item["status"] == "visited")
    assert sorted(stop["id"] for stop in stops) == sorted(set(visited) - set(result["unknown"])), "checklist and visited targets differ"
    if stops:
        dense = np.asarray(line.segmentize(0.25).coords)
        along = np.r_[0.0, np.cumsum(np.hypot(*np.diff(dense, axis=0).T))]
        positions = []
        a, d = dense[:-1], np.diff(dense, axis=0)
        for stop in stops:
            # exact distance to each densified segment, at the same reach as the walk: a looser radius picks up an earlier near-pass
            p = np.array([stop["point"].x, stop["point"].y])
            t = np.clip(((p - a) * d).sum(axis=1) / np.maximum((d ** 2).sum(axis=1), 1e-12), 0.0, 1.0)
            near = np.flatnonzero(np.hypot(*(a + t[:, None] * d - p).T) <= stop["reach"] + 1e-6)
            assert near.size, f"route never passes {stop['id']}"
            positions.append(along[near[0]] + t[near[0]] * np.hypot(*d[near[0]]))
        assert all(a <= b + 0.5 for a, b in zip(positions, positions[1:])), "checklist is not in the order the route passes the stops"
        at = -1
        for number, stop in enumerate(stops, start=1):
            key = f"\n{number}. {describe(stop)}\n"
            if key not in text[at + 1:]:
                assert "more on the map" in text, f"stop {number} missing from the message"
                break
            at = text.index(key, at + 1)
    assert len(text) <= 4096, "message over Telegram's 4096 characters"
    assert meta["imagery"] > 0.1 and rgb.std() > 5, "picture has no imagery"  # the site frame is 27% drone tiles
    if stops:
        samples = [line.interpolate(k / 40, normalized=True) for k in range(40)]
        cols_rows = [~meta["transform"] * (p.x, p.y) for p in samples]
        in_view = [(int(r), int(c)) for c, r in cols_rows if 0 <= r < rgb.shape[1] and 0 <= c < rgb.shape[2]]
        # samples under a stop marker or its digits don't count: on the farm picture 200+ markers cover much of the line
        markers = {MISSING_RGB, WASTE_RGB, SKIPPED_RGB, START_RGB, BLACK}
        free = [(r, c) for r, c in in_view if tuple(rgb[:, r, c]) not in markers]
        on_route = sum(tuple(rgb[:, r, c]) == ROUTE_RGB for r, c in free)
        assert free and on_route >= len(free) / 3, "route line is not on the picture"


@lru_cache(maxsize=64)
def _answer(field: str | None, stamp: tuple[float, ...]) -> tuple[bytes, str, str]:
    result = walk(field)
    rgb, meta = render(result)
    text = message(result)
    check(result, rgb, meta, text)
    png = to_png(rgb)
    assert len(png) < PHOTO_LIMIT, f"PNG is {len(png)} bytes, over Telegram's photo limit"
    length = result["properties"].get("length_m", 0.0)
    caption = f"{f'Field {field}' if field else 'Whole farm'}: {length / 1000:.2f} km, {len(result['stops'])} stop{'s' if len(result['stops']) != 1 else ''}"
    return png, caption, text


def answer(field: str | None) -> tuple[bytes, str, str]:
    """PNG, photo caption and checklist message for a field (None: the whole farm)."""
    return _answer(field, _stamp())


def intro() -> str:
    return ("Agrocontrol by BloomSense: your field's inspection walk from the drone survey, as a map and a checklist.\n"
            "Send a field id (for example V06-03) or /site for the whole farm.\n"
            f"Fields: {', '.join(fields())}")


def self_check() -> None:
    """Every route through the render path (its own checks), plus two controls that must fail."""
    routes = [None, *fields()]
    for field in routes:
        png, _, text = answer(field)
        result = walk(field)
        log.info("%s: %d stops, %d skipped, %d KB PNG, %d characters", field or "site", len(result["stops"]), len(result["skipped"]), len(png) // 1024, len(text))
    result = walk("V06-03")
    rgb, meta = render(result)
    text = message(result)
    for name, control in (("reversed checklist", lambda: check({**result, "stops": result["stops"][::-1]}, rgb, meta, text)),
                          ("blank picture", lambda: check(result, np.zeros_like(rgb), {**meta, "imagery": 0.0}, text))):
        try:
            control()
        except AssertionError:
            continue
        raise AssertionError(f"control '{name}' passed the check; the check is broken")
    print(f"telegram_bot self-check passed: {len(routes)} routes, both controls fail as they must")


# ---- bot --------------------------------------------------------------------------------------------------------

class TelegramError(RuntimeError):
    def __init__(self, code: int, description: str, retry_after: float | None = None) -> None:
        super().__init__(f"{code}: {description}")
        self.code, self.retry_after = code, retry_after


def _call(token: str, method: str, fields: dict[str, Any] | None = None, photo: bytes | None = None, timeout: float = 30) -> Any:
    """One Bot API call: JSON body, or multipart form data when sending a photo. Raises TelegramError or OSError."""
    fields = {key: value for key, value in (fields or {}).items() if value is not None}
    if photo is None:
        body, content_type = json.dumps(fields).encode(), "application/json"
    else:
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode() for key, value in fields.items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="route.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + photo + b"\r\n")
        body, content_type = b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as error:  # Telegram explains itself in the JSON body
        try:
            reply = json.loads(error.read())
        except ValueError:
            reply = {"ok": False, "error_code": error.code, "description": str(error.reason)}
    if not reply.get("ok"):
        raise TelegramError(int(reply.get("error_code", 0)), str(reply.get("description", "")), (reply.get("parameters") or {}).get("retry_after"))
    return reply["result"]


NETWORK_ERRORS = (OSError, http.client.HTTPException, ValueError)  # URLError, timeouts, resets, truncated JSON


def _send(token: str, method: str, fields: dict[str, Any], photo: bytes | None = None) -> None:
    """A send, retried three times on network errors and rate limits."""
    for attempt in range(3):
        try:
            _call(token, method, fields, photo, timeout=60)
            return
        except TelegramError as error:
            if error.code != 429 or attempt == 2:
                raise
            time.sleep(float(error.retry_after or 2))
        except NETWORK_ERRORS:
            if attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))


def reply(text: str) -> tuple[str | None, bytes | None, str, str]:
    """What to answer a message: (field or None, PNG or None, caption, text)."""
    command, _, rest = text.strip().partition(" ")
    command = command.split("@")[0].lower()  # "/site@AgrocontrolBot" in group chats
    if command == "/site":
        return None, *answer(None)
    wanted = rest.strip() if command == "/field" else text if not command.startswith("/") else ""
    if not wanted:
        return None, None, "", intro()
    field = match_field(wanted)
    if field is None:
        return None, None, "", f"Unknown field {wanted.strip()[:40]}.\n{intro()}"
    return field, *answer(field)


def handle(token: str, update: dict[str, Any]) -> None:
    message_ = update.get("message") or {}
    chat, text = (message_.get("chat") or {}).get("id"), message_.get("text") or ""
    if chat is None or not text.strip():
        return
    try:
        _, png, caption, body = reply(text)
    except Exception:
        log.exception("could not build the answer to %r", text[:60])
        _send(token, "sendMessage", {"chat_id": chat, "text": "Sorry, this route could not be prepared right now. Try another field or /site."})
        return
    if png is not None:
        try:
            _send(token, "sendPhoto", {"chat_id": chat, "caption": caption}, photo=png)
        except (TelegramError, *NETWORK_ERRORS) as error:
            log.warning("photo to chat %s failed (%s: %s); sending the checklist anyway", chat, type(error).__name__, error)
    _send(token, "sendMessage", {"chat_id": chat, "text": body, "link_preview_options": {"is_disabled": True}})
    log.info("answered chat %s: %s", chat, text.strip()[:40])


def run(token: str) -> None:
    """Long polling: one getUpdates at a time, each update handled on its own; network failures back off up to 60 s."""
    offset, failures = None, 0
    while True:
        try:
            updates = _call(token, "getUpdates", {"timeout": 50, "offset": offset, "allowed_updates": ["message"]}, timeout=65)
            if failures:
                log.info("connected again")
            failures = 0
        except TelegramError as error:
            if error.code == 401:
                sys.exit("Telegram rejected TELEGRAM_BOT_TOKEN (401 Unauthorized): copy the token again from @BotFather")
            failures += 1
            wait = float(error.retry_after or min(60, 2 ** failures))
            log.warning("getUpdates failed (%s); retrying in %.0f s", error, wait)  # 409: another bot process or a webhook is using this token
            time.sleep(wait)
            continue
        except NETWORK_ERRORS as error:
            failures += 1
            wait = min(60, 2 ** failures)
            log.warning("network error (%s: %s); retrying in %d s", type(error).__name__, str(error).replace(token, "<token>"), wait)
            time.sleep(wait)
            continue
        for update in updates:
            offset = update["update_id"] + 1  # acknowledged even when handling fails, so one bad message can't loop
            try:
                handle(token, update)
            except Exception as error:
                log.exception("update %s failed (%s)", update["update_id"], str(error).replace(token, "<token>"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram bot sending a field's precomputed inspection route as a picture and a checklist")
    parser.add_argument("--render", metavar="FIELD", help="offline: write FIELD's PNG (or 'site') to --out and print the message; no network")
    parser.add_argument("--out", type=Path, help=f"PNG path for --render (default {SAMPLES}/<field>.png)")
    parser.add_argument("--check", action="store_true", help="render every route in memory and check the order and the picture")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.check:
        self_check()
        return
    if args.render:
        field = None if args.render.strip().lower() == "site" else match_field(args.render)
        if field is None and args.render.strip().lower() != "site":
            sys.exit(f"No route for field {args.render!r}. Fields: {', '.join(fields())}")
        png, caption, text = answer(field)
        out = args.out or SAMPLES / f"{field or 'site'}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
        print(f"[photo {out}, {len(png) // 1024} KB, caption: {caption}]\n\n{text}")
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather in Telegram (/newbot), then run:\n"
                 "  TELEGRAM_BOT_TOKEN=<token> uv run --frozen python -m marcaj.telegram_bot")
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token):
        sys.exit("TELEGRAM_BOT_TOKEN does not look like a BotFather token (<digits>:<35 letters>)")
    log.info("warming up: %d field routes", len(fields()))
    try:
        log.info("running as @%s; press Ctrl+C to stop", _call(token, "getMe").get("username"))
    except TelegramError as error:
        sys.exit(f"Telegram rejected TELEGRAM_BOT_TOKEN ({error}): copy the token again from @BotFather")
    except NETWORK_ERRORS as error:
        log.warning("Telegram is not reachable yet (%s); polling will retry", type(error).__name__)
    try:
        run(token)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
