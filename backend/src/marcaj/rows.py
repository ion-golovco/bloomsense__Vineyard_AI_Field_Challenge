"""Labels derived from the row axes: inter-row polygons between neighbouring rows, and the per-tile
attributes of rows and inter-rows, which the organizers judge per tile (docs/SPEC.md section 8).
Measured by research/probes/interrow_probe.py on the two organizer tiles (a sanity check, not a holdout; notes in
research/notes/interrows.md): inter-row F1 0.979 / 0.941, attributes 0.974, axes 0.980 / 0.962."""

from collections import defaultdict
from typing import Any

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine, from_origin
from scipy import ndimage
from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from marcaj.layers import exg
from marcaj.tiles import PIXEL_M, TILE_PX, Tile

INTERROW_INSET_M = 0.30   # every vertex of the 49 reference inter-rows lies 0.298-0.302 m from its row axis
GREEN_EXG = 0.11          # best canopy threshold on both reference tiles
TUBE_M = 0.3              # all reference canopy lies within 0.3 m of its row axis
GAP_M = 5.0               # organizer rule: `disrupted` is a gap of at least 5 m within the tile
SAMPLE_M = 0.05
VINE_RUN_M = 0.55         # middle of the 0.45-0.65 m plateau on the reference tiles (see _row_structure)
COVER_BARE, COVER_VEGETATION = 0.25, 0.75
STRAY_SHARE = 0.7         # of the plot's row spacing; 0 turns the stray-row filter off
EXTEND_M = 1.5            # 0 turns the extension off
MIN_PIECE_M2 = 0.1        # tile-edge slivers; the smallest reference inter-row is 7.5 m2


def _direction(line: LineString) -> np.ndarray:
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    d = np.array([x1 - x0, y1 - y0])
    return d / np.linalg.norm(d)


def _pattern(properties: dict[str, Any]) -> str:
    """The row lattice a row or plot belongs to (`marcaj.plots`): `pattern_id`, or the vineyard_id before blocks."""
    return properties.get("pattern_id") or properties["vineyard_id"]


def kept_rows(features: list[dict[str, Any]]) -> dict[str, list[tuple[str, LineString]]]:
    """Each row pattern's (row_id, axis) in order across the rows, without stray rows: a row closer than `STRAY_SHARE`
    of the pattern's spacing to both neighbours, which are themselves about one spacing apart, is a grass strip or weed
    line between two vine rows (neighbours 0.7-1.4 spacings apart). 14 of 648 rows on the site; on r006 they are the 3
    predicted rows 1.1-1.4 m off any reference row, which split 3 reference inter-rows into 6 slivers. Keyed by pattern,
    not block: a block (vineyard_id) may hold several row lattices, and only one lattice's rows are neighbours."""
    spacing = {}
    for feature in features:
        if feature["properties"]["label"] == "block":
            for pattern in feature["properties"].get("patterns", [feature["properties"]]):
                spacing[_pattern(pattern)] = pattern["row_spacing_m"]
    by_pattern: dict[str, list[tuple[str, LineString]]] = defaultdict(list)
    for feature in features:
        if feature["properties"]["label"] == "row":
            by_pattern[_pattern(feature["properties"])].append((feature["properties"]["row_id"], shape(feature["geometry"])))
    out = {}
    for pattern_id, rows in by_pattern.items():
        rows = sorted(rows, key=lambda row: row[0])
        along = _direction(rows[0][1])
        across = np.array([-along[1], along[0]])
        v = [float(np.asarray(line.centroid.coords[0]) @ across) for _, line in rows]
        step = spacing.get(pattern_id, 0.0)
        kept = [0]
        for k in range(1, len(rows) - 1):
            left, right = abs(v[k] - v[kept[-1]]), abs(v[k + 1] - v[k])
            if not (max(left, right) < STRAY_SHARE * step and 0.7 * step <= left + right <= 1.4 * step):
                kept.append(k)
        out[pattern_id] = [rows[k] for k in kept + ([len(rows) - 1] if len(rows) > 1 else [])]
    return out


def interrow_areas(features: list[dict[str, Any]], exclusions) -> list[dict[str, Any]]:
    """One polygon between each pair of neighbouring rows of a row pattern (`kept_rows`): inset `INTERROW_INSET_M` from
    both axes, ending where the shorter row ends (the rules), minus `exclusions`. An end within `EXTEND_M` of an
    exclusion is carried onto it: the plot's road setback stops rows 0.95 m short of the passages at 547 of 640 row ends,
    where the vines visibly run on into the passage polygon, and without this no inter-row touches a passage. Each takes
    its rows' vineyard_id and pattern_id."""
    block = {_pattern(f["properties"]): f["properties"]["vineyard_id"] for f in features if f["properties"]["label"] == "row"}
    areas = []
    for pattern_id, rows in kept_rows(features).items():
        along = _direction(rows[0][1])
        across = np.array([-along[1], along[0]])
        frame = lambda u, w: along * u + across * w
        for (_, a), (_, b) in zip(rows[:-1], rows[1:]):
            (ua0, ua1), (ub0, ub1) = (sorted(np.asarray(line.coords) @ along) for line in (a, b))
            va, vb = sorted(float(np.asarray(line.centroid.coords[0]) @ across) for line in (a, b))
            u0, u1 = max(ua0, ub0), min(ua1, ub1)
            v0, v1 = va + INTERROW_INSET_M, vb - INTERROW_INSET_M
            if u1 <= u0 or v1 <= v0:
                continue
            box = lambda lo, hi: Polygon([frame(lo, v0), frame(hi, v0), frame(hi, v1), frame(lo, v1)])
            if EXTEND_M:
                u0 -= EXTEND_M * box(u0 - EXTEND_M, u0).intersects(exclusions)
                u1 += EXTEND_M * box(u1, u1 + EXTEND_M).intersects(exclusions)
            polygon = box(u0, u1).difference(exclusions)
            parts = [part for part in getattr(polygon, "geoms", [polygon]) if part.geom_type == "Polygon"]
            if parts:
                areas.append({"type": "Feature", "geometry": mapping(max(parts, key=lambda part: part.area)),
                              "properties": {"label": "interrow_area", "vineyard_id": block[pattern_id], "pattern_id": pattern_id, "interrow_cover": "bare_soil"}})
    return areas


def _row_structure(line: LineString, excess: np.ndarray, tile: Tile, edge: BaseGeometry) -> str:
    """`disrupted` when the row has a stretch of at least 5 m without vine green in a 0.3 m tube around the axis:
    between two vines, or between a vine and the `edge` of the visible tile that the row runs into (the reference
    marks both; a row end inside the tile is its last vine). Green runs shorter than `VINE_RUN_M` along the row are
    weeds, not vines. On the two reference tiles this marks 5 of 5 disrupted and 0 of 44 regular rows (runs
    0.45-0.65 m all do; 0.15 m, or without the edge stretches, finds 2-4 of 5). A row of 5 m or more with no vine
    green at all cannot be made out: `unassessable` (73 pieces on the site, mostly plastic mulch and bare fields)."""
    along = np.arange(0, line.length, SAMPLE_M)
    if len(along) < 2:
        return "regular"
    points = np.array([line.interpolate(t).coords[0] for t in along])
    normal = _direction(line)[::-1] * [-1, 1]
    green = np.zeros(len(along), bool)
    for offset in np.linspace(-TUBE_M, TUBE_M, 7):
        x, y = (points + offset * normal).T
        values = ndimage.map_coordinates(excess, [(tile.top - y) / PIXEL_M - 0.5, (x - tile.left) / PIXEL_M - 0.5], order=0, cval=0)
        green |= values > GREEN_EXG
    green = ndimage.binary_opening(green, structure=np.ones(round(VINE_RUN_M / SAMPLE_M), bool))
    where = np.flatnonzero(green)
    if not len(where):
        return "unassessable" if line.length >= GAP_M else "regular"
    open_ends = [where[0] if edge.distance(Point(line.coords[0])) < 0.01 else 0,
                 len(along) - 1 - where[-1] if edge.distance(Point(line.coords[-1])) < 0.01 else 0]
    longest = max(open_ends + ([int(np.max(np.diff(where)))] if len(where) > 1 else []))
    return "disrupted" if longest * SAMPLE_M >= GAP_M else "regular"


def _transform(tile: Tile) -> Affine:
    return from_origin(tile.left, tile.top, PIXEL_M, PIXEL_M)


def _cover(polygon, excess: np.ndarray, tile: Tile) -> str:
    inside = rasterize([polygon], out_shape=excess.shape, transform=_transform(tile)).astype(bool)
    if not inside.any():
        return "unassessable"
    share = float((excess[inside] > GREEN_EXG).mean())
    return "bare_soil" if share < COVER_BARE else "vegetation" if share > COVER_VEGETATION else "mixed"


def per_tile(features: list[dict[str, Any]], tiles: list[Tile]) -> list[dict[str, Any]]:
    """Splits rows and inter-rows at tile edges (IDs kept) and sets each piece's attribute from that tile's pixels.
    Stray rows (`kept_rows`) are dropped; other features pass through unchanged."""
    kept = {row_id for rows in kept_rows(features).values() for row_id, _ in rows}
    split = [f for f in features if f["properties"]["label"] == "interrow_area" or f["properties"]["label"] == "row" and f["properties"]["row_id"] in kept]
    out = [f for f in features if f["properties"]["label"] not in ("row", "interrow_area")]
    geometries = [shape(f["geometry"]) for f in split]
    for tile in tiles:
        bounds = tile.bounds
        hits = [i for i, g in enumerate(geometries) if g.intersects(bounds)]
        if not hits:
            continue
        with rasterio.open(tile.path) as source:
            excess, valid = exg(source.read())
        assert excess.shape == (TILE_PX, TILE_PX)
        # nothing is annotated on the orthomosaic's no-data margin (656 m2 of inter-row and 586 m of row lay on it);
        # dark pixels inside the image are shadows, not no-data
        nodata = ndimage.binary_opening(~ndimage.binary_fill_holes(valid)[::8, ::8], iterations=3)  # at 0.2 m, specks under ~1 m go
        visible = bounds if not nodata.any() else bounds.intersection(unary_union(
            [shape(g) for g, _ in shapes(np.uint8(~nodata), mask=~nodata, transform=_transform(tile) * Affine.scale(8))]).simplify(0.2))
        edge = visible.boundary
        for i in hits:
            piece = geometries[i].intersection(visible)
            properties = dict(split[i]["properties"], tile=tile.name)
            if properties["label"] == "row":
                lines = [part for part in getattr(piece, "geoms", [piece]) if part.geom_type == "LineString" and part.length > 0]
                for line in lines:
                    out.append({"type": "Feature", "geometry": mapping(line), "properties": {**properties, "row_structure": _row_structure(line, excess, tile, edge)}})
            else:
                polygons = [part for part in getattr(piece, "geoms", [piece]) if part.geom_type == "Polygon" and part.area >= MIN_PIECE_M2]
                for polygon in polygons:
                    out.append({"type": "Feature", "geometry": mapping(polygon), "properties": {**properties, "interrow_cover": _cover(polygon, excess, tile)}})
    return out
