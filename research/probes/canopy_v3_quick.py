"""Canopy round three, quick loop: a variant on the four central-field tiles (V19-11, V21-13, V22-13) and the two
organizer tiles only, with per-tile row cover (share of visible row samples with canopy within 0.3 m, poi.sample_rows
style, on the frozen v4 rows) and the judge on the organizer tiles. Also the organizers' own row cover on their tiles
(reference rows + canopies), the target style. Evaluation only; network probabilities from the canopy_v2 cache.
Run from backend/: uv run --frozen python ../research/probes/canopy_v3_quick.py NAME..."""

import json
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
import canopy_recall_lib as crl  # noqa: E402
from canopy_v2_lib import BASE, block_of, frozen_plots, prob_path  # noqa: E402
from canopy_v3_variants import VARIANTS  # noqa: E402

from marcaj import canopy, canopy_net, poi  # noqa: E402
from marcaj.canopy import plot_rows  # noqa: E402
from marcaj.cvat import read_cvat  # noqa: E402
from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, load_tiles  # noqa: E402

WORK = REPO_ROOT / "data" / "generated" / "work" / "canopy_v3"
WORK.mkdir(parents=True, exist_ok=True)
FIELD_TILES = ["siret3_r019_c011.tif", "siret3_r021_c013.tif", "siret3_r022_c013.tif", "siret3_r020_c012.tif"]
TILES = FIELD_TILES + crl.NAMES
BY_NAME = {t.name: t for t in load_tiles()}


def run(params, names=TILES) -> list[dict]:
    rows = plot_rows(frozen_plots())
    blocks = block_of()
    out = []
    for name in names:
        tile = BY_NAME[name]
        rgb = canopy.read_rgb(tile)
        prob = np.load(prob_path(name)).astype(np.float32)[None] / 255.0
        found = canopy.tile_canopies(tile, rows, params, rgb, canopy_net.mask(rgb[0], None, params, 0.2, "and", False, prob))
        out += [dict(f, properties={**f["properties"], "vineyard_id": blocks.get(f["properties"]["vineyard_id"], f["properties"]["vineyard_id"]),
                                    "pattern_id": f["properties"]["vineyard_id"], "tile_run": name}) for f in found]
    return out


def resample(rows, canopies, names) -> None:
    """crl.resample_canopy on `names` only."""
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms) if geoms else None
    piece_rows = [(row, piece) for row in rows for piece in row.pieces]
    piece_tree = STRtree([piece for _, piece in piece_rows])
    offsets = np.linspace(-poi.TUBE_M, poi.TUBE_M, 7)
    for row in rows:
        row.canopy[:] = False
    for name in names:
        tile = BY_NAME[name]
        transform = from_origin(tile.left, tile.top, PIXEL_M, PIXEL_M)
        near = [geoms[i] for i in tree.query(tile.bounds, predicate="intersects")] if tree else []
        covered = rasterize(near, out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool) if near else np.zeros((TILE_PX, TILE_PX), bool)
        for i in piece_tree.query(tile.bounds, predicate="intersects"):
            row, piece = piece_rows[i]
            clipped = piece.intersection(tile.bounds)
            for line in getattr(clipped, "geoms", [clipped]):
                if line.geom_type != "LineString" or line.length < poi.SAMPLE_M:
                    continue
                t = np.arange(0, line.length, poi.SAMPLE_M)
                points = np.array([line.interpolate(d).coords[0] for d in t])
                (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
                normal = np.array([-(y1 - y0), x1 - x0]) / line.length
                hit = np.zeros(len(t), bool)
                for offset in offsets:
                    x, y = (points + offset * normal).T
                    hit |= covered[np.clip((tile.top - y) / PIXEL_M, 0, TILE_PX - 1).astype(int), np.clip((x - tile.left) / PIXEL_M, 0, TILE_PX - 1).astype(int)]
                row.canopy[row.index(points)] |= hit


def cover(rows, names) -> dict[str, float]:
    out = {}
    for name in names:
        b = BY_NAME[name].bounds
        seen, hit = [], []
        for r in rows:
            ok = ~np.isnan(r.xy[:, 0])
            inside = ok & (r.xy[:, 0] >= b.bounds[0]) & (r.xy[:, 0] < b.bounds[2]) & (r.xy[:, 1] >= b.bounds[1]) & (r.xy[:, 1] < b.bounds[3])
            vis = inside & r.seen & ~r.dark
            seen.append(vis.sum())
            hit.append((vis & r.canopy).sum())
        out[name[7:16]] = round(sum(hit) / max(sum(seen), 1), 3)
    return out


_OURS = None
_REF = None


def our_rows():
    global _OURS
    if _OURS is None:
        _OURS = poi.sample_rows([f for f in json.loads(BASE.read_text())["features"] if f["properties"]["label"] == "row"], [BY_NAME[n] for n in TILES])
    return _OURS


def reference():
    """(reference features, their rows sampled on the organizer tiles)."""
    global _REF
    if _REF is None:
        with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
            xml = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
        ref = read_cvat(xml, BY_NAME)
        _REF = (ref, poi.sample_rows(ref, [BY_NAME[n] for n in crl.NAMES]))
    return _REF


def metrics(tag: str, canopies: list[dict]) -> dict:
    rows = our_rows()
    resample(rows, canopies, TILES)
    ref, ref_rows = reference()
    resample(ref_rows, canopies, crl.NAMES)
    j = crl.judge_numbers([f for f in canopies if f["properties"]["tile_run"] in crl.NAMES])
    area = {n[7:16]: round(sum(shape(f["geometry"]).area for f in canopies if f["properties"]["tile_run"] == n)) for n in TILES}
    return {"tag": tag, "cover": cover(rows, TILES), "cover_on_ref_rows": cover(ref_rows, crl.NAMES), "area": area,
            "canopy": j["canopy"], "iou": j["canopy_iou"], "f1": j["canopy_f1"], "total": j["total"], "counts": j["counts"]}


def line(m: dict) -> str:
    return (f"{m['tag']:22s} judge canopy {m['canopy']:.4f} (IoU {m['iou']:.4f} F1 {m['f1']:.4f}) total {m['total']} counts {m['counts']} | "
            f"cover {m['cover']} | on ref rows {m['cover_on_ref_rows']} | area {m['area']}")


if __name__ == "__main__":
    if sys.argv[1:] == ["reference"]:
        ref, ref_rows = reference()
        print("organizer cover (reference rows + canopies):", cover(ref_rows, crl.NAMES))
        sys.exit()
    for name in sys.argv[1:]:
        started = time.perf_counter()
        found = run(VARIANTS[name])
        if name in ("defaults",) or name.startswith("save_"):
            (WORK / f"quick_{name}.json").write_text(json.dumps(found))
        text = line(metrics(name, found)) + f" | {time.perf_counter() - started:.0f} s"
        print(text, flush=True)
        with (WORK / "quick.txt").open("a") as handle:
            handle.write(text + "\n")
