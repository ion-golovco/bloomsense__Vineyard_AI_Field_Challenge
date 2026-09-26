"""ICAERUS vine instances as canopies, alone and combined with ours (v4, data/generated/scene.json, read only).
Evaluation only: the verdicts are read to score, never to predict. Run from backend/:
  uv run --frozen python ../research/probes/icaerus_canopy_eval.py ZOOM [tile,tile,...]
Instances come from icaerus_run.py (inst_z<zoom>/<tile>.npz). Variants, per tile, all at native 2.5 cm:
  model_c<c>   instances with confidence >= c inside our predicted blocks (highest confidence owns a pixel)
  gt_c<c>      ...cut to our green (2g - r - b > 25) and the +-0.3 m tube of our rows
  split_c<c>   our canopy pixels relabelled by the instance that covers them (nearest within 0.3 m), our long pieces
               (> 2 m along the row) only; fragments under 0.1 m2 go back to their neighbour
  fill_c<c>    ours plus gt instances that overlap no canopy of ours (under 10% of their area), >= 0.1 m2
  both_c<c>    split then fill
Metrics: marcaj.judge on the two organizer tiles (predicted canopies swapped on the processed tiles), row cover on the
central tiles (share of each row's length under a canopy within 0.6 m, as plots.vine_evidence), and the canopy / gap
verdicts on the processed tiles."""

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
import rasterio
from rasterio.features import rasterize, shapes
from scipy import ndimage
from shapely import STRtree, make_valid
from shapely.geometry import LineString, Polygon, box, mapping, shape
from shapely.ops import unary_union

from marcaj import canopy
from marcaj.canopy import CanopyParams
from marcaj.judge import judge
from marcaj.review import load_verdicts
from marcaj.scene import load_projected_scene
from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT

WORK = REPO_ROOT / "data" / "generated" / "work" / "icaerus"
CENTRAL = ["siret3_r019_c011.tif", "siret3_r021_c013.tif", "siret3_r022_c013.tif", "siret3_r020_c012.tif"]
REFERENCE = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
CONFS = (0.15, 0.25, 0.35)
LONG_M = 2.0


def polygons(labels: np.ndarray, transform, min_m2: float) -> list[Polygon]:
    out = []
    for geometry, value in shapes(labels.astype(np.int32), mask=labels > 0, transform=transform, connectivity=8):
        polygon = make_valid(shape(geometry)).buffer(0).simplify(0.02).buffer(-0.01)
        for part in getattr(polygon, "geoms", [polygon]):
            if part.geom_type == "Polygon" and part.area >= min_m2:
                out.append(Polygon(part.exterior))
    return out


def burn(geometries: list, transform, size: int) -> np.ndarray:
    """Label image: geometry i gets i + 1, later ones on top."""
    if not geometries:
        return np.zeros((size, size), np.int32)
    return rasterize(((g, i + 1) for i, g in enumerate(geometries)), out_shape=(size, size), transform=transform, dtype="int32")


def nearest_fill(key: np.ndarray, todo: np.ndarray, group: np.ndarray, reach_px: float) -> np.ndarray:
    """Pixels in `todo` take the key of the nearest pixel with a key (> 0) within reach and of the same group."""
    if not todo.any() or not (key > 0).any():
        return key
    distance, (iy, ix) = ndimage.distance_transform_edt(key == 0, return_indices=True)
    take = todo & (distance <= reach_px) & (group[iy, ix] == group)
    out = key.copy()
    out[take] = key[iy[take], ix[take]]
    return out


def along_length(polygon: Polygon, angle_deg: float) -> float:
    d = np.array([np.cos(np.radians(angle_deg)), np.sin(np.radians(angle_deg))])
    u = np.asarray(polygon.exterior.coords) @ d
    return float(u.max() - u.min())


def tile_variants(name: str, zoom: float, scene_index) -> dict[str, list[Polygon]]:
    ours_tree, ours, rows_by_tile, blocks = scene_index
    with rasterio.open(DATA_DIR / "tiles" / name) as source:
        rgb, transform = source.read(), source.transform
    size = rgb.shape[1]
    bounds = box(transform.c, transform.f - size * PIXEL_M, transform.c + size * PIXEL_M, transform.f)
    mine = [ours[i].intersection(bounds) for i in ours_tree.query(bounds)]
    mine = [g for g in mine if g.area > 0]
    rows = rows_by_tile.get(name, [])
    axes = [shape(f["geometry"]) for f in rows]
    angle = float(np.median([np.degrees(np.arctan2(a.coords[-1][1] - a.coords[0][1], a.coords[-1][0] - a.coords[0][0])) for a in axes])) if axes else 0.0
    green = canopy.green_mask(rgb, CanopyParams())
    tube = canopy.tube(axes, transform, (size, size), 0.30) if axes else np.zeros((size, size), bool)
    in_block = rasterize([(b, 1) for b in blocks if b.intersects(bounds)], out_shape=(size, size), transform=transform, dtype="uint8").astype(bool) if blocks else np.zeros((size, size), bool)
    data = np.load(WORK / f"inst_z{zoom:g}" / (name[:-4] + ".npz"))
    rings = np.split(data["xy"], data["ends"][:-1]) if len(data["ends"]) else []
    conf = data["conf"]
    ours_lab = burn(mine, transform, size)
    long_ids = {i + 1 for i, g in enumerate(mine) if along_length(g.minimum_rotated_rectangle if g.geom_type == "Polygon" else g.convex_hull, angle) > LONG_M}
    out: dict[str, list[Polygon]] = {"ours": mine}
    for c in CONFS:
        order = np.argsort(conf)
        order = order[conf[order] >= c]
        inst = [Polygon(np.column_stack([transform.c + rings[k][:, 0] * PIXEL_M, transform.f - rings[k][:, 1] * PIXEL_M])).buffer(0) for k in order]
        lab = burn(inst, transform, size)
        out[f"model_c{c}"] = polygons(np.where(in_block, lab, 0), transform, 0.05)
        gt = np.where(green & tube, lab, 0)
        out[f"gt_c{c}"] = polygons(gt, transform, 0.05)
        # split: our long pieces relabelled by instance
        long_mask = np.isin(ours_lab, list(long_ids)) if long_ids else np.zeros_like(green)
        key = np.where(long_mask & (lab > 0), ours_lab.astype(np.int64) * 100000 + lab, 0)
        key = nearest_fill(key, long_mask & (key == 0), ours_lab, 0.3 / PIXEL_M)
        key = np.where(long_mask & (key == 0), ours_lab.astype(np.int64) * 100000, key)
        # fragments under 0.1 m2 back to a neighbouring region of the same piece
        values, counts = np.unique(key[key > 0], return_counts=True)
        small = values[counts * PIXEL_M ** 2 < 0.1]
        if len(small):
            tiny = np.isin(key, small)
            key = nearest_fill(np.where(tiny, 0, key), tiny, ours_lab, 1.0 / PIXEL_M)
        _, dense = np.unique(key, return_inverse=True)
        dense = dense.reshape(key.shape)
        split = polygons(np.where(long_mask, dense, 0), transform, 0.05) + [g for i, g in enumerate(mine) if i + 1 not in long_ids]
        out[f"split_c{c}"] = split
        mine_union = unary_union(mine) if mine else Polygon()
        new = [g for g in out[f"gt_c{c}"] if g.area >= 0.1 and g.intersection(mine_union).area < 0.1 * g.area]
        new = [g.difference(mine_union) for g in new]
        new = [p for g in new for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and p.area >= 0.1]
        out[f"fill_c{c}"] = mine + new
        out[f"both_c{c}"] = split + new
    return out


def row_cover(canopies: list[Polygon], axes: list[LineString]) -> float:
    if not axes:
        return float("nan")
    tree = STRtree(canopies) if canopies else None
    covered = total = 0.0
    for axis in axes:
        total += axis.length
        if tree is None:
            continue
        (x0, y0), (x1, y1) = axis.coords[0], axis.coords[-1]
        d = np.array([x1 - x0, y1 - y0]) / max(axis.length, 1e-9)
        band = axis.buffer(0.6)
        spans = []
        for i in tree.query(band):
            part = canopies[i].intersection(band)
            if part.is_empty:
                continue
            u = (shapely.get_coordinates(part) - (x0, y0)) @ d
            spans.append((max(u.min(), 0.0), min(u.max(), axis.length)))
        end = -np.inf
        for s, e in sorted(spans):
            covered += max(0.0, e - max(s, end))
            end = max(end, e)
    return covered / total


def verdict_metrics(canopies: list[Polygon], verdicts: list[dict]) -> dict[str, float]:
    tree = STRtree(canopies) if canopies else None
    pieces = lambda g: [canopies[i] for i in tree.query(g)] if tree is not None else []
    acc: dict[str, list] = defaultdict(list)
    for v in verdicts:
        g = shape(v["geometry"])
        reason = v.get("reason")
        if v["label"] == "vineyard":
            inside = [p for p in pieces(g) if p.intersection(g).area >= 0.5 * p.area and p.area >= 0.05]
            union = unary_union(pieces(g)) if pieces(g) else Polygon()
            if reason == "several touching plants":
                acc["touching_split"].append(len(inside) >= 2)
            elif reason == "one plant":
                acc["one_kept_one"].append(len(inside) == 1)
            elif reason == "not a vine":
                acc["notvine_cover"].append(g.intersection(union).area / g.area)
        elif v["label"] == "inspection":
            near = unary_union([p.buffer(0.3) for p in pieces(g.buffer(0.3))]) if pieces(g.buffer(0.3)) else Polygon()
            share = g.intersection(near).length / g.length if g.length else 0.0
            if reason == "vines present (missed canopy)":
                acc["vines_present_cover"].append(share)
            elif reason == "real gap":
                acc["real_gap_cover"].append(share)
    return {k: (round(float(np.mean(x)), 3), len(x)) for k, x in acc.items()}


def main() -> None:
    zoom = float(sys.argv[1])
    scene = load_projected_scene()
    features = scene["features"]
    pred = lambda label: [f for f in features if f["properties"].get("source") == "prediction" and f["properties"].get("label") == label]
    ours = [shape(f["geometry"]) for f in pred("vineyard")]
    rows_by_tile = defaultdict(list)
    for f in pred("row"):
        rows_by_tile[f["properties"]["tile"]].append(f)
    blocks = [shape(f["geometry"]) for f in pred("block")]
    index = (STRtree(ours), ours, rows_by_tile, blocks)
    available = {p.stem + ".tif" for p in (WORK / f"inst_z{zoom:g}").glob("*.npz")}
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else sorted(available & ({v["tile"] for v in load_verdicts() if v["kind"] == "object" and v.get("label") in ("vineyard", "inspection")} | set(REFERENCE) | set(CENTRAL)))
    names = [n for n in names if n in available]
    started = time.perf_counter()
    per_tile = {}
    for name in names:
        per_tile[name] = tile_variants(name, zoom, index)
    print(f"{len(names)} tiles in {time.perf_counter() - started:.0f} s", flush=True)
    variants = list(next(iter(per_tile.values())))
    verdicts = [v for v in load_verdicts() if v["kind"] == "object" and v.get("tile") in per_tile and v.get("label") in ("vineyard", "inspection")]
    report = {}
    geo = []
    processed = set(names)
    tile_boxes = unary_union([shape(f["geometry"]) for f in features if f["properties"].get("label") == "tile" and f["properties"].get("tile") in processed])
    swapped = [f for f in features if not (f["properties"].get("source") == "prediction" and f["properties"].get("label") == "vineyard")]
    outside = []
    for f in pred("vineyard"):
        g = shape(f["geometry"])
        if g.intersects(tile_boxes):
            g = g.difference(tile_boxes)
            if g.is_empty:
                continue
            f = {"type": "Feature", "geometry": mapping(g), "properties": f["properties"]}
        outside.append(f)
    for variant in variants:
        canopies = [g for name in names for g in per_tile[name][variant]]
        rows = {}
        if all(n in per_tile for n in REFERENCE):
            # ours elsewhere, the variant on the processed tiles ('ours' there is ours clipped to them)
            mine = [{"type": "Feature", "geometry": mapping(g), "properties": {"label": "vineyard", "source": "prediction", "vineyard_id": "V"}} for g in canopies]
            r = judge({**scene, "features": swapped + outside + mine})
            rows = {"judge_canopy": round(r["scores"]["canopy"], 4), "iou": round(r["canopy_iou"], 4), "f1": round(r["canopy_f1"], 4),
                    "points": r["points"], "counts": {t["tile"][7:16]: t["counts"]["vineyard"] for t in r["tiles"]}}
        cover = {n[7:16]: round(row_cover(per_tile[n][variant], [shape(f["geometry"]) for f in rows_by_tile.get(n, [])]), 3) for n in CENTRAL if n in per_tile}
        report[variant] = {**rows, "row_cover": cover, "canopies": len(canopies), "verdicts": verdict_metrics(canopies, verdicts)}
        print(variant, json.dumps(report[variant]), flush=True)
    (WORK / f"canopy_eval_z{zoom:g}.json").write_text(json.dumps(report, indent=1))
    for variant in ("model_c0.25", "gt_c0.25", "split_c0.25", "both_c0.25"):
        geo += [{"type": "Feature", "geometry": mapping(g), "properties": {"variant": variant, "tile": n}} for n in names if n in REFERENCE + CENTRAL for g in per_tile[n].get(variant, [])]
    (WORK / f"canopies_z{zoom:g}.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": "EPSG:32635", "features": geo}))


if __name__ == "__main__":
    main()
