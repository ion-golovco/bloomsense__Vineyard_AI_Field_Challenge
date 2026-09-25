"""CVAT for images 1.1: per-tile pixel shapes <-> one EPSG:32635 scene with global IDs."""

import argparse
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from shapely import STRtree, make_valid, set_precision
from shapely.errors import GEOSException
from shapely.geometry import LineString, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, unary_union

from marcaj.routing import load_constraints
from marcaj.scene import DEFAULT_SCENE, PREDICTION
from marcaj.tiles import CRS, DATA_DIR, TILE_PX, Tile, load_tiles

SHAPE_TAGS = {"vineyard": "polygon", "waste": "box", "row": "polyline", "interrow_area": "polygon"}
ATTRIBUTES = {
    "vineyard": ("vineyard_id",),
    "waste": ("vineyard_id",),
    "row": ("vineyard_id", "row_id", "row_structure"),
    "interrow_area": ("vineyard_id", "interrow_cover"),
}
CHOICES = {
    "row_structure": {"regular", "disrupted", "unassessable"},
    "interrow_cover": {"bare_soil", "vegetation", "mixed", "unassessable"},
}
MIN_AREA_PX = 1.0
MIN_LENGTH_PX = 1.0

META = """<meta><task><name>Vineyard AI Field Challenge</name><labels>
<label><name>vineyard</name><type>polygon</type><attributes>
  <attribute><name>vineyard_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute></attributes></label>
<label><name>waste</name><type>rectangle</type><attributes>
  <attribute><name>vineyard_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute></attributes></label>
<label><name>row</name><type>polyline</type><attributes>
  <attribute><name>vineyard_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute>
  <attribute><name>row_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute>
  <attribute><name>row_structure</name><mutable>False</mutable><input_type>select</input_type><default_value>regular</default_value><values>regular
disrupted
unassessable</values></attribute></attributes></label>
<label><name>interrow_area</name><type>polygon</type><attributes>
  <attribute><name>vineyard_id</name><mutable>False</mutable><input_type>text</input_type><default_value></default_value><values></values></attribute>
  <attribute><name>interrow_cover</name><mutable>False</mutable><input_type>select</input_type><default_value>bare_soil</default_value><values>bare_soil
vegetation
mixed
unassessable</values></attribute></attributes></label>
</labels></task></meta>"""


def _points(text: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in (pair.split(",") for pair in text.split(";"))]


def _pixel_shape(element: ET.Element) -> BaseGeometry:
    if element.tag == "box":
        return box(*(float(element.get(key)) for key in ("xtl", "ytl", "xbr", "ybr")))
    if element.tag == "polygon":
        polygon = Polygon(_points(element.get("points", "")))
        return polygon if polygon.is_valid else make_valid(polygon)
    if element.tag == "polyline":
        return LineString(_points(element.get("points", "")))
    raise ValueError(f"Unsupported CVAT shape <{element.tag}> with label {element.get('label')!r}")


def _annotations_xml(path: Path) -> bytes:
    if path.suffix != ".zip":
        return path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        return archive.read(next(name for name in archive.namelist() if name.endswith("annotations.xml")))


def read_cvat(data: bytes, tiles: dict[str, Tile], source: str = "reference") -> list[dict[str, Any]]:
    features = []
    for image in ET.fromstring(data).iter("image"):
        name = Path(image.get("name", "")).name
        if name not in tiles:
            raise KeyError(f"CVAT image {name!r} is not one of the organizer tiles")
        tile = tiles[name]
        for element in image:
            label = element.get("label")
            attributes = {item.get("name"): (item.text or "") for item in element.findall("attribute")}
            geometry = tile.to_world(_pixel_shape(element))
            features.append({
                "type": "Feature",
                "geometry": mapping(geometry),
                "properties": {"label": label, "tile": name, "source": source, **attributes},
            })
    return features


def _snap(polygon: BaseGeometry) -> BaseGeometry:
    """Snaps to the 0.01 px grid `_format` writes, so rounding never makes a written polygon cross itself.
    A polygon GEOS cannot snap (a hole outside its shell) is repaired first; CVAT drops holes anyway."""
    try:
        return set_precision(polygon, 0.01)
    except GEOSException:
        repaired = make_valid(polygon)
        shells = [Polygon(p.exterior) for p in getattr(repaired, "geoms", [repaired]) if p.geom_type == "Polygon"]
        return set_precision(unary_union(shells), 0.01) if shells else Polygon()


def _pieces(geometry: BaseGeometry, tag: str) -> list[BaseGeometry]:
    parts = list(getattr(geometry, "geoms", [geometry]))
    if tag == "polyline":
        lines = [part for part in parts if part.geom_type in {"LineString", "MultiLineString"}]
        merged = linemerge(lines) if lines else LineString()
        return [line for line in getattr(merged, "geoms", [merged]) if line.length >= MIN_LENGTH_PX]
    polygons = [
        polygon for part in parts if part.geom_type in {"Polygon", "MultiPolygon"}
        for snapped in [_snap(part) if tag == "polygon" else part]
        for polygon in getattr(snapped, "geoms", [snapped]) if polygon.geom_type == "Polygon" and polygon.area >= MIN_AREA_PX
    ]
    if tag == "box":
        return [box(*polygon.bounds) for polygon in polygons]
    return polygons


def _format(coordinates: list[tuple[float, ...]]) -> str:
    return ";".join(f"{x:.2f},{y:.2f}" for x, y, *_ in coordinates)


def image_elements(features: list[dict[str, Any]], tiles: list[Tile]) -> dict[str, str]:
    """Clip every scene object to every tile it touches; IDs and attributes carry over to each piece."""
    shapes = [feature for feature in features if feature.get("properties", {}).get("label") in SHAPE_TAGS]
    geometries = [make_valid(shape(feature["geometry"])) for feature in shapes]
    index = STRtree(geometries)
    fragments: dict[str, str] = {}
    dropped_holes = 0
    for tile in tiles:
        image = ET.Element("image", id="0", name=tile.name, width=str(TILE_PX), height=str(TILE_PX))
        for position in sorted(index.query(tile.bounds, predicate="intersects")):
            properties = shapes[position]["properties"]
            label = properties["label"]
            tag = SHAPE_TAGS[label]
            clipped = tile.to_pixel(geometries[position].intersection(tile.bounds))
            for piece in _pieces(clipped, tag):
                element = ET.SubElement(image, tag, label=label, source="manual", occluded="0")
                if tag == "box":
                    for key, value in zip(("xtl", "ytl", "xbr", "ybr"), piece.bounds):
                        element.set(key, f"{value:.2f}")
                elif tag == "polygon":
                    dropped_holes += len(piece.interiors)
                    element.set("points", _format(list(piece.exterior.coords)[:-1]))
                else:
                    element.set("points", _format(list(piece.coords)))
                element.set("z_order", "0")
                for name in ATTRIBUTES[label]:
                    ET.SubElement(element, "attribute", name=name).text = str(properties.get(name) or "")
        fragments[tile.name] = ET.tostring(image, encoding="unicode")
    if dropped_holes:
        print(f"warning: CVAT polygons cannot hold holes; filled {dropped_holes} hole(s)")
    return fragments


def document(fragments: list[str]) -> bytes:
    images = [fragment.replace('id="0"', f'id="{index}"', 1) for index, fragment in enumerate(fragments)]
    body = "\n".join(['<?xml version="1.0" encoding="utf-8"?>', "<annotations>", "<version>1.1</version>", META, *images, "</annotations>"])
    return (body + "\n").encode("utf-8")


def check_cvat(data: bytes, expected_names: set[str]) -> list[str]:
    """Problems Marcaj would reject or score as wrong; an empty list means the upload is clean."""
    root = ET.fromstring(data)
    problems = []
    if root.findtext("version") != "1.1":
        problems.append(f"version is {root.findtext('version')!r}, expected '1.1'")
    labels = {label.findtext("name") for label in root.iter("label")}
    if labels != set(SHAPE_TAGS):
        problems.append(f"label definitions {sorted(labels)} differ from {sorted(SHAPE_TAGS)}")
    seen: set[str] = set()
    for image in root.iter("image"):
        name = image.get("name", "")
        if name in seen:
            problems.append(f"{name}: listed twice")
        seen.add(name)
        for element in image:
            label = element.get("label", "")
            where = f"{name} {element.tag} {label}"
            if SHAPE_TAGS.get(label) != element.tag:
                problems.append(f"{where}: label/shape mismatch")
                continue
            attributes = {item.get("name"): item.text or "" for item in element.findall("attribute")}
            if set(attributes) != set(ATTRIBUTES[label]):
                problems.append(f"{where}: attributes {sorted(attributes)}")
            for key, allowed in CHOICES.items():
                if key in attributes and attributes[key] not in allowed:
                    problems.append(f"{where}: {key}={attributes[key]!r}")
            if label != "waste" and not attributes.get("vineyard_id"):
                problems.append(f"{where}: empty vineyard_id")
            if label == "row" and not attributes.get("row_id"):
                problems.append(f"{where}: empty row_id")
            try:
                geometry = Polygon(_points(element.get("points", ""))) if element.tag == "polygon" else _pixel_shape(element)
            except (ValueError, TypeError, GEOSException) as error:
                problems.append(f"{where}: unreadable geometry ({error})")
                continue
            if not geometry.is_valid or geometry.is_empty:
                problems.append(f"{where}: invalid geometry")
            if not box(0, 0, TILE_PX, TILE_PX).buffer(1e-6).contains(geometry):
                problems.append(f"{where}: outside the {TILE_PX} px tile")
    if seen != expected_names:
        problems.append(f"images missing {sorted(expected_names - seen)[:5]}, unexpected {sorted(seen - expected_names)[:5]}")
    return problems


def build_scene(cvat_paths: list[Path], predictions: list[dict[str, Any]], data_dir: Path = DATA_DIR, tiles: list[Tile] | None = None) -> dict[str, Any]:
    tiles = tiles or load_tiles(data_dir)
    by_name = {tile.name: tile for tile in tiles}
    features = [feature for path in cvat_paths for feature in read_cvat(_annotations_xml(path), by_name)]
    features.extend({**feature, "properties": {**feature["properties"], "source": PREDICTION}} for feature in predictions)
    features.extend(load_constraints(data_dir / "02_route"))
    features.extend({"type": "Feature", "geometry": mapping(tile.bounds), "properties": {"label": "tile", "tile": tile.name, "source": "organizer"}} for tile in tiles)
    return {"type": "FeatureCollection", "crs": CRS, "source": ", ".join(path.name for path in cvat_paths) + (" + predictions" if predictions else ""), "features": features}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an EPSG:32635 scene from CVAT 1.1 annotations (Marcaj export or examples)")
    parser.add_argument("--cvat", type=Path, action="append", default=[], help="annotations.xml or a ZIP containing it: the Marcaj export or the organizer examples")
    parser.add_argument("--predictions", type=Path, action="append", default=[], help="EPSG:32635 GeoJSON of model output, shown for review and judging only")
    parser.add_argument("--output", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    predictions = [feature for path in args.predictions for feature in json.loads(path.read_text(encoding="utf-8"))["features"]]
    scene = build_scene(args.cvat, predictions, args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scene), encoding="utf-8")
    print(f"Wrote {len(scene['features'])} features to {args.output}")


if __name__ == "__main__":
    main()
