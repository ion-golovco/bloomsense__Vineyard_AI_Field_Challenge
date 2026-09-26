"""Shared harness for the canopy-recall probes. Evaluation only.

- Frozen rows: the blocks and rows of the uploaded data/generated/predictions.geojson, per-tile row pieces merged per
  `row_id` back into one axis (predict.py runs the canopy on the global axes), so trials don't move with plots.py edits.
- Network probabilities cached per tile as uint8 (no flips, as predict.py), so variants run on CPU in parallel.
- `site(variant)`: canopies on every tile a row crosses, in a process pool.
- `metrics(canopies)`: count, length distribution, POI gap candidates (marcaj.poi on the uploaded row pieces with
  these canopies), per-row canopy cover, and the judge on the two organizer tiles (full `marcaj.judge`).
"""

import json
import multiprocessing as mp
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely import STRtree
from shapely.geometry import LineString, mapping, shape

from marcaj import canopy, canopy_net, poi
from marcaj.canopy import CanopyParams, plot_rows
from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, load_tiles

WORK = REPO_ROOT / "data" / "generated" / "work" / "canopy_recall"
PROB = WORK / "prob"
UPLOADED = REPO_ROOT / "data" / "generated" / "predictions.geojson"
FROZEN = WORK / "plots_frozen.json"
NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
WORK.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Variant:
    name: str
    params: CanopyParams = CanopyParams()
    combine: str = "and"      # "and" / "or" / "net" with the network, "rules" without
    threshold: float = 0.2
    extra: dict[str, Any] = field(default_factory=dict)


def uploaded_features() -> list[dict[str, Any]]:
    return json.loads(UPLOADED.read_text())["features"]


def frozen_plots() -> list[dict[str, Any]]:
    """Blocks and one merged axis per row_id from the uploaded predictions (cached)."""
    if FROZEN.is_file():
        return json.loads(FROZEN.read_text())
    features = uploaded_features()
    pieces: dict[str, list] = {}
    meta: dict[str, dict] = {}
    for f in features:
        if f["properties"]["label"] == "row":
            pieces.setdefault(f["properties"]["row_id"], []).append(shape(f["geometry"]))
            meta[f["properties"]["row_id"]] = f["properties"]
    out = [f for f in features if f["properties"]["label"] == "block"]
    for row_id, lines in pieces.items():
        longest = max(lines, key=lambda line: line.length)
        (x0, y0), (x1, y1) = longest.coords[0], longest.coords[-1]
        d = np.array([x1 - x0, y1 - y0]) / longest.length
        pts = np.concatenate([np.asarray(line.coords) for line in lines])
        u = (pts - [x0, y0]) @ d
        a, b = np.array([x0, y0]) + u.min() * d, np.array([x0, y0]) + u.max() * d
        out.append({"type": "Feature", "geometry": mapping(LineString([a, b])),
                    "properties": {"label": "row", "vineyard_id": meta[row_id]["vineyard_id"], "row_id": row_id}})
    FROZEN.write_text(json.dumps(out))
    return out


def vine_tiles() -> list:
    rows = plot_rows(frozen_plots())
    return [t for t in load_tiles() if any(a.intersects(t.bounds) for p in rows for a in p.axes)]


def cache_probabilities() -> None:
    PROB.mkdir(exist_ok=True)
    todo = [t for t in vine_tiles() if not (PROB / f"{t.name[:-4]}.npy").is_file()]
    if not todo:
        return
    model = canopy_net.load()
    started = time.perf_counter()
    for tile in todo:
        image, _ = canopy.read_rgb(tile)
        prob = canopy_net.probabilities(image, model, flips=False)[0]
        np.save(PROB / f"{tile.name[:-4]}.npy", np.round(prob * 255).astype(np.uint8))
    print(f"cached {len(todo)} probability maps in {time.perf_counter() - started:.0f} s", flush=True)


_ROWS: list = []
_TILES: dict = {}


def _init() -> None:
    global _ROWS, _TILES
    _ROWS = plot_rows(frozen_plots())
    _TILES = {t.name: t for t in load_tiles()}


def tile_run(args: tuple[str, Variant]) -> list[dict[str, Any]]:
    name, variant = args
    tile = _TILES[name]
    rgb = canopy.read_rgb(tile)
    if variant.combine == "rules":
        return canopy.tile_canopies(tile, _ROWS, variant.params, rgb)
    prob = np.load(PROB / f"{tile.name[:-4]}.npy").astype(np.float32)[None] / 255.0
    green = canopy_net.mask(rgb[0], None, variant.params, variant.threshold, variant.combine, False, prob)
    return canopy.tile_canopies(tile, _ROWS, variant.params, rgb, green)


def site(variant: Variant, names: list[str] | None = None, processes: int = 6) -> list[dict[str, Any]]:
    names = names or [t.name for t in vine_tiles()]
    with mp.get_context("spawn").Pool(processes, initializer=_init) as pool:
        out = pool.map(tile_run, [(n, variant) for n in names], chunksize=1)
    return [dict(f, properties={**f["properties"], "tile_run": n}) for n, fs in zip(names, out) for f in fs]


def length_m(geometry) -> float:
    c = list(geometry.minimum_rotated_rectangle.exterior.coords)
    return max(np.hypot(c[0][0] - c[1][0], c[0][1] - c[1][1]), np.hypot(c[1][0] - c[2][0], c[1][1] - c[2][1]))


_SAMPLED: list | None = None


def sampled_rows() -> list:
    """poi.sample_rows on the uploaded row pieces (canopy flags come from the uploaded canopies), cached in memory."""
    global _SAMPLED
    if _SAMPLED is None:
        rows_only = [f for f in uploaded_features() if f["properties"]["label"] == "row"]
        _SAMPLED = poi.sample_rows(rows_only)
    return _SAMPLED


def resample_canopy(rows: list, canopies: list[dict[str, Any]]) -> None:
    """Sets each row's canopy flags from `canopies` exactly as poi.sample_rows does, without reading pixels."""
    tiles = load_tiles()
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    piece_rows = [(row, piece) for row in rows for piece in row.pieces]
    piece_tree = STRtree([piece for _, piece in piece_rows])
    offsets = np.linspace(-poi.TUBE_M, poi.TUBE_M, 7)
    for row in rows:
        row.canopy[:] = False
    for tile in tiles:
        bounds = tile.bounds
        hits = piece_tree.query(bounds, predicate="intersects")
        if not len(hits):
            continue
        transform = from_origin(tile.left, tile.top, PIXEL_M, PIXEL_M)
        near = [geoms[i] for i in tree.query(bounds, predicate="intersects")]
        covered = rasterize(near, out_shape=(TILE_PX, TILE_PX), transform=transform).astype(bool) if near else np.zeros((TILE_PX, TILE_PX), bool)
        for i in hits:
            row, piece = piece_rows[i]
            clipped = piece.intersection(bounds)
            for line in getattr(clipped, "geoms", [clipped]):
                if line.geom_type != "LineString" or line.length < poi.SAMPLE_M:
                    continue
                t = np.arange(0, line.length, poi.SAMPLE_M)
                points = np.array([line.interpolate(d).coords[0] for d in t])
                (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
                normal = np.array([-(y1 - y0), x1 - x0]) / line.length
                index = row.index(points)
                hit = np.zeros(len(t), bool)
                for offset in offsets:
                    x, y = (points + offset * normal).T
                    hit |= covered[np.clip((tile.top - y) / PIXEL_M, 0, TILE_PX - 1).astype(int), np.clip((x - tile.left) / PIXEL_M, 0, TILE_PX - 1).astype(int)]
                row.canopy[index] |= hit


def gap_stats(canopies: list[dict[str, Any]]) -> dict[str, Any]:
    rows = sampled_rows()
    resample_canopy(rows, canopies)
    pois = poi.gap_pois(rows)
    gaps = [p["properties"] for p in pois if p["properties"]["reason"] == "gap"]
    visible = np.concatenate([r.seen & ~r.dark for r in rows])
    planted = np.concatenate([r.canopy for r in rows])
    green = np.concatenate([r.green for r in rows])
    return {"poi_recall": poi_recall(pois), "gaps": len(gaps), "gaps_challenge": sum(g["challenge"] for g in gaps),
            "gaps_green": sum(g["green_share"] >= poi.MAX_GREEN / 2 for g in gaps),
            "gap_m": round(sum(g["gap_m"] for g in gaps)),
            "planting": sum(p["properties"]["reason"] == "planting" for p in pois),
            "row_cover": round(float(planted[visible].mean()), 4),
            "green_uncovered_share": round(float((green & ~planted & visible).sum() / max((green & visible).sum(), 1)), 4),
            "_pois": pois}


_TRUTH: list | None = None


def reference_gaps() -> list:
    """The organizers' own canopy-free stretches >= 5 m (reference rows + canopies on the two tiles), as segments."""
    global _TRUTH
    if _TRUTH is None:
        import zipfile

        from marcaj.cvat import read_cvat
        by_name = {t.name: t for t in load_tiles()}
        with zipfile.ZipFile(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip") as archive:
            xml = archive.read(next(n for n in archive.namelist() if n.endswith(".xml")))
        ref_rows = poi.sample_rows(read_cvat(xml, by_name), [by_name[n] for n in NAMES])
        _TRUTH = [LineString([g["start"], g["end"]]) for g in poi.row_gaps(ref_rows, green=False, min_gap_m=poi.GAP_M)]
    return _TRUTH


def poi_recall(pois: list[dict[str, Any]]) -> str:
    """Reference gaps with a challenge POI within 2 m, and challenge POIs on the two tiles."""
    from shapely.geometry import Point

    points = [Point(p["geometry"]["coordinates"]) for p in pois if p["properties"]["challenge"]]
    truth = reference_gaps()
    tiles = {t.name: t for t in load_tiles()}
    on = [q for q in points if any(tiles[n].bounds.contains(q) for n in NAMES)]
    return f"{sum(any(q.distance(t) <= 2 for q in points) for t in truth)}/{len(truth)} ({len(on)} POIs)"


def judge_numbers(canopies: list[dict[str, Any]]) -> dict[str, Any]:
    """Full marcaj.judge on the organizer tiles: the uploaded predictions with their canopies replaced."""
    from marcaj.cvat import build_scene
    from marcaj.judge import judge

    tiles = {t.name: t for t in load_tiles()}
    base = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [], tiles=list(tiles.values()))["features"]
    keep = [f for f in uploaded_features() if f["properties"]["label"] != "vineyard"]
    report = judge({"type": "FeatureCollection", "crs": "EPSG:32635", "features": base + keep + canopies})
    by_tile = {t["tile"]: t for t in report["tiles"]}
    out = {"total": report["points"], **{k: round(v, 4) for k, v in report["scores"].items() if v is not None},
           "canopy_iou": round(report["canopy_iou"], 4), "canopy_f1": round(report["canopy_f1"], 4)}
    out["counts_score"] = round(report["scores"]["counts"], 4)
    out["counts"] = {n[7:16]: by_tile[n]["counts"]["vineyard"] for n in NAMES if n in by_tile}
    return out


def metrics(tag: str, canopies: list[dict[str, Any]], judge: bool = True) -> dict[str, Any]:
    geoms = [shape(f["geometry"]) for f in canopies]
    lengths = np.array([length_m(g) for g in geoms]) if geoms else np.zeros(1)
    out: dict[str, Any] = {"tag": tag, "canopies": len(geoms), "area_m2": round(sum(g.area for g in geoms)),
                           "len_median": round(float(np.median(lengths)), 2), "len_p90": round(float(np.percentile(lengths, 90)), 2),
                           "over_2_5": round(float((lengths > 2.5).mean()), 4), "over_5": round(float((lengths > 5).mean()), 4),
                           "over_3_n": int((lengths > 3).sum())}
    out.update({k: v for k, v in gap_stats(canopies).items() if not k.startswith("_")})
    if judge:
        out["judge"] = judge_numbers(canopies)
    return out


def line(m: dict[str, Any]) -> str:
    j = m.get("judge", {})
    return (f"{m['tag']:34s} canopy {j.get('canopy', 0):.4f} (IoU {j.get('canopy_iou', 0):.4f} F1 {j.get('canopy_f1', 0):.4f}) "
            f"counts {j.get('counts')} total {j.get('total')} | n {m['canopies']} area {m['area_m2']} "
            f"len med {m['len_median']} p90 {m['len_p90']} >2.5 {m['over_2_5']:.3f} >5 {m['over_5']:.3f} | "
            f"gaps {m['gaps']} (challenge {m['gaps_challenge']}, green {m['gaps_green']}, {m['gap_m']} m) planting {m['planting']} "
            f"row cover {m['row_cover']:.4f} green uncovered {m['green_uncovered_share']:.4f} | ref gaps {m.get('poi_recall')} | counts {j.get('counts_score')}")


def replace_params(**kw: Any) -> CanopyParams:
    return replace(CanopyParams(), **kw)
