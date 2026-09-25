"""Pack the tiles and pre-annotations into Marcaj upload ZIPs, then re-open and verify every ZIP."""

import argparse
import json
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from pathlib import Path

from marcaj.cvat import check_cvat, document, image_elements
from marcaj.tiles import DATA_DIR, REPO_ROOT, Tile, load_tiles

MAX_ZIP_BYTES = 88_000_000
ZIP_ENTRY_OVERHEAD = 200


def _groups(tiles: list[Tile], fragments: dict[str, str]) -> list[list[Tile]]:
    """Greedy in file-name order, so neighbouring tiles stay in one part, as the organizers' parts are."""
    empty = len(document([]))
    groups: list[list[Tile]] = [[]]
    used = empty
    for tile in tiles:
        cost = tile.size + len(zlib.compress(fragments[tile.name].encode())) + ZIP_ENTRY_OVERHEAD
        if groups[-1] and used + cost > MAX_ZIP_BYTES:
            groups.append([])
            used = empty
        groups[-1].append(tile)
        used += cost
    return groups


def verify(paths: list[Path], tiles: list[Tile]) -> list[str]:
    expected = {tile.name: tile for tile in tiles}
    seen: dict[str, str] = {}
    problems = []
    for path in paths:
        if path.stat().st_size > MAX_ZIP_BYTES:
            problems.append(f"{path.name}: {path.stat().st_size:,} bytes, over the {MAX_ZIP_BYTES:,} budget")
        with zipfile.ZipFile(path) as archive:
            if (broken := archive.testzip()) is not None:
                problems.append(f"{path.name}: {broken} fails its CRC")
            images = {}
            for info in archive.infolist():
                if info.filename.startswith("images/"):
                    images[info.filename.removeprefix("images/")] = info
                elif info.filename != "annotations.xml":
                    problems.append(f"{path.name}: unexpected entry {info.filename}")
            for name, info in images.items():
                tile = expected.get(name)
                if tile is None or (info.file_size, info.CRC) != (tile.size, tile.crc):
                    problems.append(f"{path.name}: images/{name} is not an unchanged organizer tile")
                if name in seen:
                    problems.append(f"{name} is in both {seen[name]} and {path.name}")
                seen[name] = path.name
            problems.extend(f"{path.name}: {problem}" for problem in check_cvat(archive.read("annotations.xml"), set(images)))
    if missing := sorted(set(expected) - set(seen)):
        problems.append(f"{len(missing)} tiles in no ZIP, e.g. {missing[:3]}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Marcaj upload ZIPs (CVAT for images 1.1)")
    parser.add_argument("--scene", type=Path, help="EPSG:32635 scene with pre-annotations; omit for tiles only")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "output" / "upload")
    args = parser.parse_args()
    started = time.perf_counter()
    tiles = load_tiles(args.data_dir)
    features = json.loads(args.scene.read_text(encoding="utf-8"))["features"] if args.scene else []
    fragments = image_elements(features, tiles)
    groups = _groups(tiles, fragments)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for stale in args.output_dir.glob("siret3_upload_part*.zip"):
        stale.unlink()
    paths = []
    for number, group in enumerate(groups, start=1):
        path = args.output_dir / f"siret3_upload_part{number}of{len(groups)}.zip"
        xml = document([fragments[tile.name] for tile in group])
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("annotations.xml", xml, compress_type=zipfile.ZIP_DEFLATED)
            for tile in group:
                archive.write(tile.path, f"images/{tile.name}", compress_type=zipfile.ZIP_STORED)
        counts = Counter(element.get("label") for element in ET.fromstring(xml).iter() if element.get("label"))
        print(f"{path.name}: {len(group)} tiles, {path.stat().st_size / 1e6:.1f} MB, {dict(sorted(counts.items()))}")
        paths.append(path)
    problems = verify(paths, tiles)
    for problem in problems[:50]:
        print(f"problem: {problem}")
    print(f"{len(paths)} ZIPs, {sum(len(group) for group in groups)} tiles, {len(problems)} problems, {time.perf_counter() - started:.1f} s")
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
