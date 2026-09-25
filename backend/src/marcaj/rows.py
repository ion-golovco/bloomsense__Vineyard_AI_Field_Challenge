"""Labels derived from the row axes: inter-row polygons between neighbouring rows, and the per-tile
attributes of rows and inter-rows, which the organizers judge per tile (docs/SPEC.md section 8)."""

from collections import defaultdict
from typing import Any

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import LineString, Polygon, mapping, shape

from marcaj.layers import exg
from marcaj.tiles import PIXEL_M, TILE_PX, Tile

INTERROW_INSET_M = 0.35   # reference inter-rows stop a median 0.30-0.38 m short of the row axis, at the canopy edge
GREEN_EXG = 0.11          # best canopy threshold on both reference tiles
TUBE_M = 0.3              # all reference canopy lies within 0.3 m of its row axis
GAP_M = 5.0               # organizer rule: `disrupted` is a gap of at least 5 m within the tile
COVER_BARE, COVER_VEGETATION = 0.25, 0.75


def _direction(line: LineString) -> np.ndarray:
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    d = np.array([x1 - x0, y1 - y0])
    return d / np.linalg.norm(d)


def interrow_areas(features: list[dict[str, Any]], exclusions) -> list[dict[str, Any]]:
    """One polygon between each pair of neighbouring rows of a plot, inset from both axes, minus `exclusions`."""
    by_plot: dict[str, list[LineString]] = defaultdict(list)
    for feature in features:
        if feature["properties"]["label"] == "row":
            by_plot[feature["properties"]["vineyard_id"]].append((feature["properties"]["row_id"], shape(feature["geometry"])))
    areas = []
    for plot_id, rows in by_plot.items():
        rows = [line for _, line in sorted(rows)]
        if not rows:
            continue
        along = _direction(rows[0])
        normal = np.array([-along[1], along[0]])
        for a, b in zip(rows[:-1], rows[1:]):
            ends = lambda line: sorted((np.asarray(p) for p in (line.coords[0], line.coords[-1])), key=lambda p: p @ along)
            side = np.sign((np.asarray(b.centroid.coords[0]) - np.asarray(a.centroid.coords[0])) @ normal)
            (a0, a1), (b0, b1) = ends(a), ends(b)
            shift = side * INTERROW_INSET_M * normal
            polygon = Polygon([a0 + shift, a1 + shift, b1 - shift, b0 - shift])
            if not polygon.is_valid:
                continue
            polygon = polygon.difference(exclusions)
            parts = [part for part in getattr(polygon, "geoms", [polygon]) if part.geom_type == "Polygon"]
            if parts:
                areas.append({"type": "Feature", "geometry": mapping(max(parts, key=lambda part: part.area)),
                              "properties": {"label": "interrow_area", "vineyard_id": plot_id, "interrow_cover": "bare_soil"}})
    return areas


def _row_structure(line: LineString, excess: np.ndarray, tile: Tile) -> str:
    """`disrupted` when the green in a 0.3 m tube around the axis has an interior gap of at least 5 m."""
    length = line.length
    if length < GAP_M:
        return "regular"
    along = np.arange(0, length, 0.05)
    points = np.array([line.interpolate(t).coords[0] for t in along])
    normal = _direction(line)[::-1] * [-1, 1]
    green = np.zeros(len(along), bool)
    for offset in np.linspace(-TUBE_M, TUBE_M, 7):
        x, y = (points + offset * normal).T
        values = ndimage.map_coordinates(excess, [(tile.top - y) / PIXEL_M - 0.5, (x - tile.left) / PIXEL_M - 0.5], order=0, cval=0)
        green |= values > GREEN_EXG
    # a vine canopy is at least a few centimetres of green; single pixels are weeds or noise
    green = ndimage.binary_opening(green, structure=np.ones(3, bool))
    where = np.flatnonzero(green)
    if len(where) < 2:
        return "regular"
    return "disrupted" if np.max(np.diff(where)) * 0.05 >= GAP_M else "regular"


def _cover(polygon, excess: np.ndarray, tile: Tile) -> str:
    transform = rasterio.transform.from_origin(tile.left, tile.top, PIXEL_M, PIXEL_M)
    inside = rasterize([polygon], out_shape=excess.shape, transform=transform).astype(bool)
    if not inside.any():
        return "unassessable"
    share = float((excess[inside] > GREEN_EXG).mean())
    return "bare_soil" if share < COVER_BARE else "vegetation" if share > COVER_VEGETATION else "mixed"


def per_tile(features: list[dict[str, Any]], tiles: list[Tile]) -> list[dict[str, Any]]:
    """Splits rows and inter-rows at tile edges (IDs kept) and sets each piece's attribute from that tile's pixels.
    Other features pass through unchanged."""
    split = [f for f in features if f["properties"]["label"] in ("row", "interrow_area")]
    out = [f for f in features if f["properties"]["label"] not in ("row", "interrow_area")]
    geometries = [shape(f["geometry"]) for f in split]
    for tile in tiles:
        bounds = tile.bounds
        hits = [i for i, g in enumerate(geometries) if g.intersects(bounds)]
        if not hits:
            continue
        with rasterio.open(tile.path) as source:
            excess, _ = exg(source.read())
        assert excess.shape == (TILE_PX, TILE_PX)
        for i in hits:
            piece = geometries[i].intersection(bounds)
            properties = dict(split[i]["properties"], tile=tile.name)
            if properties["label"] == "row":
                lines = [part for part in getattr(piece, "geoms", [piece]) if part.geom_type == "LineString" and part.length > 0]
                for line in lines:
                    out.append({"type": "Feature", "geometry": mapping(line), "properties": {**properties, "row_structure": _row_structure(line, excess, tile)}})
            else:
                polygons = [part for part in getattr(piece, "geoms", [piece]) if part.geom_type == "Polygon" and part.area > 0]
                for polygon in polygons:
                    out.append({"type": "Feature", "geometry": mapping(polygon), "properties": {**properties, "interrow_cover": _cover(polygon, excess, tile)}})
    return out
