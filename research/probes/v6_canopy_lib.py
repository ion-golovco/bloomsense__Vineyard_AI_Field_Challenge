"""v6 canopy-and-gaps harness: everything derived from rows (canopy, inter-rows, attributes, gap POIs) run on the team's
hand-corrected Marcaj rows (data/generated/work/v6/reference, the integration agent's extraction), then scored:
- judge canopy (0.6 union IoU + 0.4 F1 at IoU 0.5, after the CVAT per-tile round trip) on the two organizer tiles;
- row cover along the reference rows (share of visible row samples with canopy within 0.3 m, as `poi` samples it) and
  canopy area off-row (beyond 0.35 m of every reference row);
- the user's canopy labels and gap labels in data/review/verdicts.json;
- gap POIs against the organizers' own gaps on the example tiles.
Evaluation only: this probe reads the reference and the review labels; prediction code never does.
Network probabilities come from the per-tile caches (no flips, as predict.py); missing tiles are computed into OUT/prob."""

import json
import multiprocessing as mp
import os
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import unary_union

from marcaj import canopy, canopy_net, poi
from marcaj.canopy import CanopyParams, RowSet
from marcaj.cvat import _annotations_xml, document, image_elements, read_cvat
from marcaj.judge import _f1, _iou, _match
from marcaj.scene import PREDICTION
from marcaj.tiles import DATA_DIR, REPO_ROOT, load_tiles

GEN = REPO_ROOT / "data" / "generated"
REF = GEN / "work" / "v6" / "reference"
OUT = GEN / "work" / "v6" / "canopy"
PROB = OUT / "prob"
OUT.mkdir(parents=True, exist_ok=True)
VERDICTS = REPO_ROOT / "data" / "review" / "verdicts.json"
NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
CENTRAL = ["siret3_r019_c011.tif", "siret3_r021_c013.tif", "siret3_r022_c013.tif", "siret3_r020_c012.tif"]
CACHES = [GEN / "work" / "canopy_recall" / "prob", GEN / "work" / "canopy_v2" / "prob", PROB]
TILES = {t.name: t for t in load_tiles()}


# ---------------------------------------------------------------- reference rows
def ref_pieces() -> list[dict[str, Any]]:
    """Per-tile reference row pieces (label row, tile, row_id, vineyard_id = field)."""
    assert (REF / "READY").is_file(), "reference not READY"
    return [f for f in json.loads((REF / "rows_pieces.geojson").read_text())["features"] if f["properties"].get("label", "row") == "row"]


def _chord(line: LineString) -> LineString:
    return LineString([line.coords[0], line.coords[-1]])


def plot_features() -> list[dict[str, Any]]:
    """Block (one per row lattice of REF/blocks.geojson) and row (REF/rows.geojson parts, every drawn vertex) features in
    the shape `marcaj.plots.detect_plots` gives them, keyed by pattern (`<vineyard_id>` or `<vineyard_id>~<k>`): for
    canopy.plot_rows, canopy.refit_rows, rows.interrow_areas / per_tile. Rows keep their row_id (parts share it)."""
    blocks = json.loads((REF / "blocks.geojson").read_text())["features"]
    pattern, out = {}, []
    for b in blocks:
        vid = b["properties"]["vineyard_id"]
        pats = b["properties"]["patterns"]
        for k, p in enumerate(pats):
            pid = vid if len(pats) == 1 else f"{vid}~{k}"
            pattern.update({row_id: pid for row_id in p["row_ids"]})
            out.append({"type": "Feature", "geometry": b["geometry"], "properties": {
                "label": "block", "vineyard_id": pid, "pattern_id": pid, "block_id": vid, "row_angle": p["row_angle"],
                "row_spacing_m": p["row_spacing_m"], "rows": p["rows"], "area_m2": p["area_m2"]}})
    for f in json.loads((REF / "rows.geojson").read_text())["features"]:
        q = f["properties"]
        pid = pattern.get(q["row_id"], q["vineyard_id"])
        out.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "label": "row", "vineyard_id": pid, "pattern_id": pid, "block_id": q["vineyard_id"], "row_id": q["row_id"],
            "part": q.get("part", 1), "n_parts": q.get("n_parts", 1)}})
    return out


def rowsets(plots: list[dict[str, Any]], pieces: list[dict[str, Any]] | None = None) -> list[RowSet]:
    """canopy.plot_rows of `plots`; with `pieces`, each pattern's axes are the reference per-tile pieces as straight chords
    (first to last vertex) instead of the global row lines."""
    sets = canopy.plot_rows(plots)
    if pieces is None:
        return sets
    pattern_of = {f["properties"]["row_id"]: f["properties"]["pattern_id"] for f in plots if f["properties"]["label"] == "row"}
    axes: dict[str, list] = defaultdict(list)
    for f in pieces:
        g = shape(f["geometry"])
        for part in getattr(g, "geoms", [g]):
            if part.length > 0.05:
                axes[pattern_of.get(f["properties"]["row_id"], "")].append(_chord(part))
    return [RowSet(s.vineyard_id, s.angle_deg, axes.get(s.vineyard_id, [])) for s in sets]


def spacing_of(plots: list[dict[str, Any]]) -> dict[str, float]:
    return {f["properties"]["vineyard_id"]: f["properties"]["row_spacing_m"] for f in plots if f["properties"]["label"] == "block"}


# ---------------------------------------------------------------- canopy runs
def prob_path(name: str) -> Path | None:
    for cache in CACHES:
        path = cache / f"{name[:-4]}.npy"
        if path.is_file():
            return path
    return None


def cache_probabilities(names: list[str]) -> None:
    todo = [n for n in names if prob_path(n) is None]
    if not todo:
        return
    PROB.mkdir(exist_ok=True)
    model = canopy_net.load()
    for name in todo:
        image, _ = canopy.read_rgb(TILES[name])
        np.save(PROB / f"{name[:-4]}.npy", np.round(canopy_net.probabilities(image, model, flips=False)[0] * 255).astype(np.uint8))
    print(f"cached {len(todo)} probability maps", flush=True)


_STATE: dict[str, Any] = {}


def _init(plots: list[dict[str, Any]], pieces: list[dict[str, Any]] | None) -> None:
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    _STATE["sets"] = rowsets(plots, pieces)
    _STATE["spacing"] = spacing_of(plots)


def tile_canopies(name: str, params: CanopyParams, sets: list[RowSet], spacing: dict[str, float], combine: str = "and") -> list[dict[str, Any]]:
    """canopy.tile_canopies with each plot's spacing given (so per-tile piece axes work) and the cached network mask."""
    tile = TILES[name]
    crossing = [(s, [a for a in s.axes if a.intersects(tile.bounds)]) for s in sets]
    crossing = [(s, axes) for s, axes in crossing if axes]
    if not crossing:
        return []
    image, transform = canopy.read_rgb(tile)
    green = None
    if combine != "rules":
        prob = np.load(prob_path(name)).astype(np.float32)[None] / 255.0
        green = canopy_net.mask(image, None, params, 0.2, combine, False, prob)
    out = []
    for s, axes in crossing:
        mask, weak = canopy.canopy_mask(image, transform, axes, params, green, spacing.get(s.vineyard_id, 0.0), with_weak=True)
        out += [{"type": "Feature", "geometry": mapping(p), "properties": {"label": "vineyard", "source": "prediction",
                                                                             "vineyard_id": s.vineyard_id, "tile_run": name}}
                for p in canopy.canopy_polygons(mask, transform, s.angle_deg, params, weak) if not canopy.in_vegetation(p, image, transform, params)]
    return out


def _run(args: tuple[str, CanopyParams, str]) -> list[dict[str, Any]]:
    return tile_canopies(args[0], args[1], _STATE["sets"], _STATE["spacing"], args[2])


def vine_tiles(plots: list[dict[str, Any]]) -> list[str]:
    lines = [shape(f["geometry"]) for f in plots if f["properties"]["label"] == "row"]
    tree = STRtree(lines)
    return [n for n, t in TILES.items() if len(tree.query(t.bounds, predicate="intersects"))]


def run(params: CanopyParams, plots: list[dict[str, Any]], names: list[str], pieces: list[dict[str, Any]] | None = None,
        combine: str = "and", processes: int = 2) -> list[dict[str, Any]]:
    started = time.perf_counter()
    if processes <= 1 or len(names) <= 4:
        _init(plots, pieces)
        out = [_run((n, params, combine)) for n in names]
    else:
        with mp.get_context("spawn").Pool(processes, initializer=_init, initargs=(plots, pieces)) as pool:
            out = pool.map(_run, [(n, params, combine) for n in names], chunksize=2)
    found = [f for fs in out for f in fs]
    print(f"  canopy: {len(found)} on {len(names)} tiles in {time.perf_counter() - started:.0f} s", flush=True)
    return found


# ---------------------------------------------------------------- metrics
_ORG: dict[str, Any] = {}


def organizer() -> list[dict[str, Any]]:
    if "features" not in _ORG:
        _ORG["features"] = read_cvat(_annotations_xml(DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"), TILES)
    return _ORG["features"]


def judge_canopy(canopies: list[dict[str, Any]]) -> dict[str, Any]:
    """The judge's canopy score on the organizer tiles after the CVAT round trip, with per-tile IoU / F1 and counts."""
    marked = [{**f, "properties": {**f["properties"], "source": PREDICTION}} for f in canopies]
    tiles = [TILES[n] for n in NAMES]
    near = [f for f in marked if any(shape(f["geometry"]).intersects(t.bounds) for t in tiles)]
    uploaded = read_cvat(document(list(image_elements(near, tiles).values())), TILES, source=PREDICTION) if near else []
    inter = union = tp = n_pred = n_ref = 0.0
    per = {}
    for name in NAMES:
        ref = [(shape(f["geometry"]), f["properties"]) for f in organizer() if f["properties"]["label"] == "vineyard" and f["properties"]["tile"] == name]
        pred = [(shape(f["geometry"]), f["properties"]) for f in uploaded if f["properties"]["label"] == "vineyard" and f["properties"]["tile"] == name]
        pairs = _match(pred, ref, _iou, 0.5)
        ru, pu = unary_union([g for g, _ in ref]), unary_union([g for g, _ in pred])
        i, u = ru.intersection(pu).area, ru.union(pu).area
        inter, union, tp, n_pred, n_ref = inter + i, union + u, tp + len(pairs), n_pred + len(pred), n_ref + len(ref)
        per[name[7:16]] = {"iou": round(i / u, 4), "f1": round(_f1(len(pairs), len(pred), len(ref)), 4), "n": [len(pred), len(ref)],
                           "area": [round(pu.area, 1), round(ru.area, 1)]}
    iou, f1 = inter / union, _f1(int(tp), int(n_pred), int(n_ref))
    return {"canopy": round(0.6 * iou + 0.4 * f1, 4), "iou": round(iou, 4), "f1": round(f1, 4), "tiles": per}


def sampled(pieces: list[dict[str, Any]], canopies: list[dict[str, Any]], names: list[str] | None = None) -> list:
    """poi.sample_rows on the reference row pieces (vineyard_id = pattern) with these canopies."""
    tiles = [TILES[n] for n in names] if names else None
    return poi.sample_rows(pieces + canopies, tiles)


def cover(rows: list, by: dict[str, str] | None = None) -> dict[str, float]:
    """Visible-row canopy cover overall and per block (`by`: row_id -> block)."""
    out = {}
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups["all"].append(r)
        if by:
            groups[by.get(r.row_id, "?")].append(r)
    for key, rs in groups.items():
        seen = np.concatenate([r.seen & ~r.dark for r in rs])
        if seen.any():
            out[key] = round(float(np.concatenate([r.canopy for r in rs])[seen].mean()), 4)
    return out


def off_row(canopies: list[dict[str, Any]], pieces: list[dict[str, Any]], reach_m: float = 0.35) -> float:
    """Canopy area beyond `reach_m` of every reference row piece (m2)."""
    band = unary_union([shape(f["geometry"]).buffer(reach_m, cap_style="flat") for f in pieces])
    geoms = [shape(f["geometry"]) for f in canopies]
    return round(sum(g.area for g in geoms) - sum(g.intersection(band).area for g in geoms), 1)


def verdicts() -> list[dict[str, Any]]:
    return json.loads(VERDICTS.read_text())


def canopy_labels(canopies: list[dict[str, Any]]) -> dict[str, str]:
    """User canopy labels: `several` split into >= 2 parts of >= 0.2 m2 inside the label, `not_vine` removed (< 20% covered),
    `one_plant` kept as one part >= 50% covered, and vines lost (several / one_plant < 20% covered)."""
    labels = [v for v in verdicts() if v.get("properties", {}).get("review_tab") == "canopies"]
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    res = defaultdict(list)
    for lab in labels:
        g = shape(lab["geometry"])
        hits = [i for i in tree.query(g, predicate="intersects") if geoms[i].intersection(g).area >= 0.1 * min(geoms[i].area, g.area)]
        covered = sum(geoms[i].intersection(g).area for i in hits) / g.area
        big = sum(geoms[i].intersection(g).area >= 0.2 for i in hits)
        res[lab["properties"]["review_answer"]].append((covered, big))
    sev, nv, one = res["several"], res["not_vine"], res["one_plant"]
    return {"several_split": f"{sum(b >= 2 for _, b in sev)}/{len(sev)}", "not_vine_removed": f"{sum(c < 0.2 for c, _ in nv)}/{len(nv)}",
            "one_plant_whole": f"{sum(b == 1 and c >= 0.5 for c, b in one)}/{len(one)}",
            "vine_lost": f"{sum(c < 0.2 for c, _ in sev + one)}/{len(sev) + len(one)}"}


def gap_label_lines() -> list[tuple[LineString, str, str]]:
    seen = {}
    for v in verdicts():
        p = v.get("properties", {})
        if p.get("review_tab") == "gaps":
            seen[p["review_id"]] = (LineString(v["geometry"]["coordinates"]), p["review_answer"], p.get("reason", ""))
    return list(seen.values())


def label_of(line: LineString, labels: list, reach: float = 1.5) -> str | None:
    """poi_dev_eval.label_of: the label whose line lies within `reach` of this stretch's midpoint (or vice versa)."""
    best = None
    for other, answer, _ in labels:
        d = min(Point(line.interpolate(0.5, normalized=True)).distance(other), Point(other.interpolate(0.5, normalized=True)).distance(line))
        if d <= reach and (best is None or d < best[0]):
            best = (d, answer)
    return best and best[1]


def gap_metrics(pois: list[dict[str, Any]]) -> dict[str, Any]:
    """Challenge targets (gap / planting) against the user's gap labels: per answer the targets on a labelled stretch,
    precision (real + partly) / labelled, and the share of each answer's labels a target still covers (within 1.5 m)."""
    labels = gap_label_lines()
    targets = [p["properties"] for p in pois if p["properties"]["challenge"]]
    got = Counter(label_of(LineString([p["gap_start"], p["gap_end"]]), labels) for p in targets)
    by_reason = Counter((p["reason"], label_of(LineString([p["gap_start"], p["gap_end"]]), labels) or "none") for p in targets)
    good, bad = got["real_gap"] + got["partly"], got["vines_present"] + got["not_a_row"]
    tree = STRtree([LineString([p["gap_start"], p["gap_end"]]) for p in targets]) if targets else None
    lines = [LineString([p["gap_start"], p["gap_end"]]) for p in targets]
    flagged = Counter()
    total = Counter()
    for line, answer, _ in labels:
        total[answer] += 1
        mid = Point(line.interpolate(0.5, normalized=True))
        near = tree.query(line.buffer(1.5)) if tree is not None else []
        flagged[answer] += any(min(mid.distance(lines[i]), Point(lines[i].interpolate(0.5, normalized=True)).distance(line)) <= 1.5 for i in near)
    return {"targets": len(targets), "gap_targets": sum(p["reason"] == "gap" for p in targets),
            "on_label": {k or "none": v for k, v in got.items()}, "precision": round(good / max(good + bad, 1), 3),
            "labels_flagged": {a: f"{flagged[a]}/{total[a]}" for a in ("real_gap", "partly", "vines_present", "not_a_row")},
            "target_m": round(sum(p["gap_m"] for p in targets)), "by_reason": {f"{a}/{b}": n for (a, b), n in sorted(by_reason.items())}}


def organizer_gaps() -> list[LineString]:
    """The organizers' own >= 5 m canopy-free stretches (their rows + canopies on the two tiles)."""
    if "gaps" not in _ORG:
        rows = poi.sample_rows(organizer(), [TILES[n] for n in NAMES])
        _ORG["gaps"] = [LineString([g["start"], g["end"]]) for g in poi.row_gaps(rows, min_gap_m=poi.GAP_M)]
    return _ORG["gaps"]


def example_recall(pois: list[dict[str, Any]]) -> str:
    pts = [Point(p["geometry"]["coordinates"]) for p in pois if p["properties"]["challenge"]]
    truth = organizer_gaps()
    on = [q for q in pts if any(TILES[n].bounds.contains(q) for n in NAMES)]
    return f"{sum(any(q.distance(t) <= 2 for q in pts) for t in truth)}/{len(truth)} ({len(on)} targets on the tiles)"


def obstacles() -> list[dict[str, Any]]:
    path = GEN / "work" / "v5" / "predictions.geojson"
    return [f for f in json.loads(path.read_text())["features"] if f["properties"]["label"] == "obstacle"]


def heavy_lock(owner: str = "v6-canopy") -> Path:
    """mkdir lock shared with the other agents; stale after 25 min."""
    lock = GEN / "work" / ".heavy_lock"
    while True:
        try:
            lock.mkdir()
            (lock / "owner").write_text(f"{owner} {time.strftime('%H:%M:%S')} pid {os.getpid()}\n")
            return lock
        except FileExistsError:
            age = time.time() - lock.stat().st_mtime
            if age > 25 * 60:
                print(f"stale lock ({age / 60:.0f} min): {(lock / 'owner').read_text() if (lock / 'owner').is_file() else '?'}; taking it", flush=True)
                for p in lock.iterdir():
                    p.unlink()
                lock.rmdir()
                continue
            print(f"lock held by {(lock / 'owner').read_text().strip() if (lock / 'owner').is_file() else '?'}; waiting", flush=True)
            time.sleep(30)


def release(lock: Path) -> None:
    for p in lock.iterdir():
        p.unlink()
    lock.rmdir()
