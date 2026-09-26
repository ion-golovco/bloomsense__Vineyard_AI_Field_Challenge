"""Cadastral land parcels ("terenuri cadastrale") over the study area, from the public WMS of I.P. Cadastrul Bunurilor
Imobile (map.cadastru.md; capabilities: Fees NONE, AccessConstraints NONE; no reuse licence found, research/notes/fields.md;
the WFS answers 403 and is not used).
One GetMap in EPSG:4026 (MOLDREF99 / Moldova TM) rendered as SVG gives every parcel as a closed path in pixel space at
0.25 m/px, so the parcels are exact vectors, not a traced image. Fetched 26 Sep 2026: 2,121 paths, 2.5 MB.
Parcels are land ownership, not planting: use them to place edges, never to create a plot.

Run from backend/: uv run --frozen python -m marcaj.cadastre [--fetch]"""

import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy import ndimage
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import transform, unary_union

from marcaj.tiles import REPO_ROOT

CADASTRE_DIR = REPO_ROOT / "data" / "raw" / "external" / "cadastre"
PARCELS = CADASTRE_DIR / "parcels_32635.geojson"
WMS = "https://map.cadastru.md/geoserver/ows"
BBOX_4026 = (222760.0, 219530.0, 224600.0, 221410.0)  # the study area plus about 30 m
PX_M = 0.25


def _query() -> str:
    minx, miny, maxx, maxy = BBOX_4026
    width, height = round((maxx - minx) / PX_M), round((maxy - miny) / PX_M)
    return (f"{WMS}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=w_cbi:cad_terenuri&STYLES=&SRS=EPSG:4026"
            f"&BBOX={minx:.0f},{miny:.0f},{maxx:.0f},{maxy:.0f}&WIDTH={width}&HEIGHT={height}&FORMAT=image/svg%2Bxml")


def fetch(svg: Path = CADASTRE_DIR / "cad_terenuri_4026.svg") -> Path:
    svg.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(_query(), headers={"User-Agent": "marcaj-hackathon-research/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        svg.write_bytes(response.read())
    return svg


def svg_parcels(svg: Path) -> list[Polygon]:
    """Parcel polygons in EPSG:32635. Parcels are the unfilled paths; label glyphs are filled. Paths that touch the
    render's clip frame (23 px outside the bbox) are cut parcels and are kept as cut."""
    minx, miny, maxx, maxy = BBOX_4026
    to_utm = Transformer.from_crs(4026, 32635, always_xy=True).transform
    parcels = []
    for attributes in re.findall(r"<path ([^>]*)>", svg.read_text(encoding="utf-8")):
        if 'fill="none"' not in attributes or "url(#clipPath1)" not in attributes:
            continue
        numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", re.search(r'd="([^"]*)"', attributes).group(1))]
        ring = [(minx + px * PX_M, maxy - py * PX_M) for px, py in zip(numbers[::2], numbers[1::2])]
        polygon = Polygon(ring).buffer(0)
        if polygon.area > 1:
            parcels.append(transform(to_utm, polygon))
    return parcels


def load_parcels(path: Path = PARCELS) -> list[Polygon]:
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing: run `uv run --frozen python -m marcaj.cadastre --fetch` from backend/")
    return [shape(f["geometry"]) for f in json.loads(path.read_text(encoding="utf-8"))["features"]]


def edge_distance(path: Path = PARCELS, px_m: float = 0.25):
    """A sampler of the distance in metres from EPSG:32635 points (N, 2) to the nearest parcel edge; far outside is 99."""
    parcels = load_parcels(path)
    x0, y0, x1, y1 = np.array(unary_union(parcels).bounds) + [-20, -20, 20, 20]
    width, height = int((x1 - x0) / px_m), int((y1 - y0) / px_m)
    lines = rasterize([p.boundary for p in parcels], out_shape=(height, width), transform=from_origin(x0, y1, px_m, px_m), all_touched=True)
    distance = (ndimage.distance_transform_edt(~lines.astype(bool)) * px_m).astype(np.float32)

    def at(points: np.ndarray) -> np.ndarray:
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        return ndimage.map_coordinates(distance, [(y1 - points[:, 1]) / px_m - 0.5, (points[:, 0] - x0) / px_m - 0.5], order=1, cval=99.0)
    return at


if __name__ == "__main__":
    svg = fetch() if "--fetch" in sys.argv else CADASTRE_DIR / "cad_terenuri_4026.svg"
    parcels = svg_parcels(svg)
    PARCELS.write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "source": _query(),
                                   "features": [{"type": "Feature", "geometry": mapping(p), "properties": {"area_m2": round(p.area)}}
                                                for p in parcels]}))
    print(f"{len(parcels)} parcels, {sum(p.area for p in parcels) / 1e4:.1f} ha -> {PARCELS}")
