"""Organizer tile inventory and exact pixel <-> EPSG:32635 transforms."""

import os
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import rasterio
from shapely.affinity import affine_transform
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("MARCAJ_DATA_DIR", REPO_ROOT / "data" / "raw" / "marcaj")).expanduser()
CRS = "EPSG:32635"
TILE_PX = 2048
PIXEL_M = 0.025
EXPECTED_TILE_COUNT = 311


@dataclass(frozen=True)
class Tile:
    name: str
    part: str
    path: Path
    size: int
    crc: int
    left: float
    top: float

    @property
    def bounds(self) -> BaseGeometry:
        extent = TILE_PX * PIXEL_M
        return box(self.left, self.top - extent, self.left + extent, self.top)

    def to_world(self, geometry: BaseGeometry) -> BaseGeometry:
        return affine_transform(geometry, [PIXEL_M, 0, 0, -PIXEL_M, self.left, self.top])

    def to_pixel(self, geometry: BaseGeometry) -> BaseGeometry:
        return affine_transform(geometry, [1 / PIXEL_M, 0, 0, -1 / PIXEL_M, -self.left / PIXEL_M, self.top / PIXEL_M])


def _check_raster(path: Path) -> tuple[float, float]:
    with rasterio.open(path) as source:
        transform = source.transform
        if source.crs is None or source.crs.to_epsg() != 32635:
            raise ValueError(f"{path.name}: CRS {source.crs} is not {CRS}")
        if (source.width, source.height, source.count) != (TILE_PX, TILE_PX, 3):
            raise ValueError(f"{path.name}: {source.width}x{source.height}x{source.count}, expected {TILE_PX}x{TILE_PX}x3")
        if (transform.a, transform.b, transform.d, transform.e) != (PIXEL_M, 0.0, 0.0, -PIXEL_M):
            raise ValueError(f"{path.name}: unexpected pixel grid {transform}")
        return transform.c, transform.f


def load_tiles(data_dir: Path = DATA_DIR) -> list[Tile]:
    """Read the tile set from the organizer part ZIPs, extracting and verifying each tile byte-for-byte."""
    parts = sorted((data_dir / "01_tiles").glob("*part*of*.zip"))
    if not parts:
        raise FileNotFoundError(f"No tile part ZIPs in {data_dir / '01_tiles'}; unzip marcaj-data.zip into {data_dir}")
    tiles_dir = data_dir / "tiles"
    tiles: dict[str, Tile] = {}
    for part in parts:
        with zipfile.ZipFile(part) as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.endswith(".tif"):
                    continue
                name = Path(info.filename).name
                if name in tiles:
                    raise ValueError(f"{name} appears in both {tiles[name].part} and {part.name}")
                path = tiles_dir / name
                if not path.is_file():
                    tiles_dir.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(info))
                data = path.read_bytes()
                if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
                    raise ValueError(f"{path} differs from its original in {part.name}; delete it to re-extract")
                path.chmod(0o444)
                left, top = _check_raster(path)
                tiles[name] = Tile(name, part.name, path, info.file_size, info.CRC, left, top)
    if len(tiles) != EXPECTED_TILE_COUNT:
        raise ValueError(f"Found {len(tiles)} tiles, the organizers released {EXPECTED_TILE_COUNT}")
    return [tiles[name] for name in sorted(tiles)]
