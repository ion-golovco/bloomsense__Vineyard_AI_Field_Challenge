"""Shared harness for the canopy round-two probes (not-a-vine precision, split policy). Evaluation only.

- Frozen rows: the detect_plots output behind the 15:43 predictions (work/rows_refit/plots_new.json), copied once.
- Baseline: a snapshot of data/generated/predictions.geojson (15:43), so other agents' re-runs don't move the numbers.
- Network probabilities cached per tile as uint8 (no flips, as predict.py): canopy_recall/prob, missing tiles in ours.
- `site(params)`: canopies on every tile a row crosses, in a process pool, relabelled to block ids like assign_blocks.
- `labels()`: the user's long-canopy verdicts (review_tab "canopies"), read fresh each call. Probes only.
- `match(labels, canopies)`: per labelled piece, the current canopies overlapping it.
- `metrics`: canopy_recall_lib's (judge on the two organizer tiles, gaps, lengths) on our baseline.
"""

import json
import multiprocessing as mp
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, mapping, shape

sys.path.insert(0, str(Path(__file__).parent))
import canopy_recall_lib as crl  # noqa: E402

from marcaj import canopy, canopy_net  # noqa: E402
from marcaj.canopy import CanopyParams, plot_rows  # noqa: E402
from marcaj.tiles import REPO_ROOT, load_tiles  # noqa: E402

GEN = REPO_ROOT / "data" / "generated"
WORK = GEN / "work" / "canopy_v2"
WORK.mkdir(parents=True, exist_ok=True)
PROB = WORK / "prob"
VERDICTS = REPO_ROOT / "data" / "review" / "verdicts.json"
NAMES = crl.NAMES
# which run the rows and the baseline come from: "1543" (rows_refit/plots_new.json) or "v4" (work/v4, 18:13; rows rebuilt
# per pattern from its exported rows, merged per row_id, and blocks split back into their patterns)
RUN = os.environ.get("CV2_RUN", "v4")
FROZEN = WORK / ("plots_frozen.json" if RUN == "1543" else f"plots_frozen_{RUN}.json")
BASE = WORK / ("base_predictions.geojson" if RUN == "1543" else f"base_{RUN}.geojson")


def _freeze_v4() -> None:
    features = json.loads((GEN / "work" / "v4" / "predictions.geojson").read_text())["features"]
    out = []
    for f in features:
        if f["properties"]["label"] == "block":
            for pattern in f["properties"].get("patterns") or [f["properties"]]:
                pid = pattern.get("pattern_id", f["properties"]["vineyard_id"])
                out.append({"type": "Feature", "geometry": f["geometry"], "properties": {
                    "label": "block", "vineyard_id": pid, "pattern_id": pid, "block_id": f["properties"]["vineyard_id"],
                    "row_angle": pattern["row_angle"], "row_spacing_m": pattern["row_spacing_m"], "rows": pattern["rows"], "area_m2": pattern["area_m2"]}})
    pieces: dict[str, list] = {}
    meta: dict[str, dict] = {}
    for f in features:
        if f["properties"]["label"] == "row":
            pieces.setdefault(f["properties"]["row_id"], []).append(shape(f["geometry"]))
            meta[f["properties"]["row_id"]] = f["properties"]
    for row_id, lines in pieces.items():
        longest = max(lines, key=lambda line: line.length)
        (x0, y0), (x1, y1) = longest.coords[0], longest.coords[-1]
        d = np.array([x1 - x0, y1 - y0]) / longest.length
        pts = np.concatenate([np.asarray(line.coords) for line in lines])
        u = (pts - [x0, y0]) @ d
        pid = meta[row_id].get("pattern_id", meta[row_id]["vineyard_id"])
        out.append({"type": "Feature", "geometry": mapping(LineString([np.array([x0, y0]) + u.min() * d, np.array([x0, y0]) + u.max() * d])),
                    "properties": {"label": "row", "vineyard_id": pid, "pattern_id": pid, "block_id": meta[row_id]["vineyard_id"],
                                   "row_id": row_id, "row_structure": meta[row_id].get("row_structure", "regular")}})
    FROZEN.write_text(json.dumps(out))


if not FROZEN.is_file():
    if RUN == "1543":
        shutil.copy(GEN / "work" / "rows_refit" / "plots_new.json", FROZEN)
    else:
        _freeze_v4()
if not BASE.is_file():
    shutil.copy(GEN / "work" / RUN / "predictions.geojson", BASE)
crl.UPLOADED = BASE  # judge / gaps / sampled rows use our snapshot


def frozen_plots() -> list[dict[str, Any]]:
    return json.loads(FROZEN.read_text())


def block_of() -> dict[str, str]:
    return {f["properties"]["vineyard_id"]: f["properties"]["block_id"] for f in frozen_plots() if f["properties"]["label"] == "block"}


def vine_tiles() -> list:
    rows = plot_rows(frozen_plots())
    return [t for t in load_tiles() if any(a.intersects(t.bounds) for p in rows for a in p.axes)]


def prob_path(name: str) -> Path:
    old = crl.PROB / f"{name[:-4]}.npy"
    return old if old.is_file() else PROB / f"{name[:-4]}.npy"


def cache_probabilities() -> None:
    PROB.mkdir(exist_ok=True)
    todo = [t for t in vine_tiles() if not prob_path(t.name).is_file()]
    if not todo:
        return
    model = canopy_net.load()
    for tile in todo:
        image, _ = canopy.read_rgb(tile)
        np.save(PROB / f"{tile.name[:-4]}.npy", np.round(canopy_net.probabilities(image, model, flips=False)[0] * 255).astype(np.uint8))
    print(f"cached {len(todo)} probability maps", flush=True)


_ROWS: list = []
_TILES: dict = {}


def _init() -> None:
    global _ROWS, _TILES
    _ROWS = plot_rows(frozen_plots())
    _TILES = {t.name: t for t in load_tiles()}


def tile_run(args: tuple[str, CanopyParams, str]) -> list[dict[str, Any]]:
    name, params, combine = args
    tile = _TILES[name]
    rgb = canopy.read_rgb(tile)
    if combine == "rules":
        return canopy.tile_canopies(tile, _ROWS, params, rgb)
    prob = np.load(prob_path(name)).astype(np.float32)[None] / 255.0
    return canopy.tile_canopies(tile, _ROWS, params, rgb, canopy_net.mask(rgb[0], None, params, 0.2, combine, False, prob))


def site(params: CanopyParams = CanopyParams(), names: list[str] | None = None, combine: str = "and", processes: int = 2) -> list[dict[str, Any]]:
    names = names or [t.name for t in vine_tiles()]
    started = time.perf_counter()
    with mp.get_context("spawn").Pool(processes, initializer=_init) as pool:
        out = pool.map(tile_run, [(n, params, combine) for n in names], chunksize=1)
    blocks = block_of()
    found = [dict(f, properties={**f["properties"], "vineyard_id": blocks.get(f["properties"]["vineyard_id"], f["properties"]["vineyard_id"]),
                                 "pattern_id": f["properties"]["vineyard_id"], "tile_run": n}) for n, fs in zip(names, out) for f in fs]
    print(f"site: {len(found)} canopies on {len(names)} tiles in {time.perf_counter() - started:.0f} s", flush=True)
    return found


def labels() -> list[dict[str, Any]]:
    verdicts = json.loads(VERDICTS.read_text())
    return [v for v in verdicts if v.get("properties", {}).get("review_tab") == "canopies"]


def match(labelled: list[dict[str, Any]], canopies: list[dict[str, Any]], min_cover: float = 0.1) -> list[dict[str, Any]]:
    """Per labelled piece: the canopies covering at least `min_cover` of their own area inside it or of the labelled
    area; `covered` is the share of the labelled polygon they cover, `parts` their count, `lengths` their lengths."""
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    out = []
    for label in labelled:
        g = shape(label["geometry"])
        hits = []
        for i in tree.query(g, predicate="intersects"):
            inter = geoms[i].intersection(g).area
            if inter >= min_cover * min(geoms[i].area, g.area):
                hits.append(int(i))
        covered = sum(geoms[i].intersection(g).area for i in hits) / g.area
        out.append({"id": label["id"], "answer": label["properties"]["review_answer"], "tile": label["properties"]["tile"],
                    "length_m": label["properties"]["length_m"], "area_m2": g.area, "parts": len(hits), "covered": covered,
                    "hits": hits, "lengths": sorted(round(crl.length_m(geoms[i]), 2) for i in hits),
                    "big_parts": sum(geoms[i].intersection(g).area >= 0.2 for i in hits)})
    return out


def label_summary(matched: list[dict[str, Any]]) -> dict[str, Any]:
    """several: pieces now split into >= 2 parts of >= 0.2 m2 inside the label; not_vine: removed (< 20% covered);
    one_plant: still one part, >= 50% covered."""
    by = lambda a: [m for m in matched if m["answer"] == a]
    sev, nv, one = by("several"), by("not_vine"), by("one_plant")
    return {"several_split": f"{sum(m['big_parts'] >= 2 for m in sev)}/{len(sev)}",
            "several_max_len_med": round(float(np.median([max(m["lengths"] or [0]) for m in sev])), 2) if sev else 0,
            "not_vine_removed": f"{sum(m['covered'] < 0.2 for m in nv)}/{len(nv)}",
            "one_plant_kept_whole": f"{sum(m['big_parts'] == 1 and m['covered'] >= 0.5 for m in one)}/{len(one)}",
            "vine_lost": f"{sum(m['covered'] < 0.2 for m in sev + one)}/{len(sev) + len(one)}"}


def refit_pois(canopies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """POIs as predict.py + marcaj.poi would give them for these canopies: the frozen rows moved onto them (refit_rows),
    stray rows dropped (rows.kept_rows), sampled against the tiles (no per-tile nodata clip)."""
    from marcaj import poi, rows as rows_mod

    plots = frozen_plots()
    moved = canopy.refit_rows(plots, canopies)[0]
    kept = {row_id for rows_ in rows_mod.kept_rows(moved).values() for row_id, _ in rows_}
    row_features = [f for f in moved if f["properties"]["label"] == "row" and f["properties"]["row_id"] in kept]
    sampled = poi.sample_rows(row_features + canopies)
    return poi.gap_pois(sampled), row_features, sampled


FIELDS = ("V19-11", "V21-13", "V22-13")


def field_cover(sampled: list, fields: tuple[str, ...] = FIELDS) -> dict[str, float]:
    """Share of visible row length with canopy within 0.3 m, per block (pattern rows mapped to their block)."""
    blocks = block_of()
    out = {}
    for field in fields + ("all",):
        rs = [r for r in sampled if field == "all" or blocks.get(r.vineyard_id, r.vineyard_id) == field]
        if rs:
            seen = np.concatenate([r.seen & ~r.dark for r in rs])
            out[field] = round(float(np.concatenate([r.canopy for r in rs])[seen].mean()), 3)
    return out


def on_row(labelled: list[dict[str, Any]], row_features: list[dict[str, Any]], reach_m: float = 0.5) -> list[bool]:
    """Whether a current row still runs along each labelled gap line (within `reach_m` over half its length)."""
    rows_ = [shape(f["geometry"]) for f in row_features]
    tree = STRtree(rows_)
    out = []
    for lab in labelled:
        line = shape(lab["geometry"])
        near = [rows_[i] for i in tree.query(line.buffer(reach_m))]
        out.append(any(line.intersection(r.buffer(reach_m)).length >= 0.5 * line.length for r in near))
    return out


def gap_label_summary(pois: list[dict[str, Any]], row_features: list[dict[str, Any]]) -> dict[str, str]:
    """The user's gap labels (challenge POIs for gaps): flagged within 2 m (the lead's count), and, of those whose row
    still runs along the labelled line, flagged within 0.8 m (on that row, not its neighbour)."""
    from canopy_v2_gaps import gap_labels, still_flagged

    labelled = gap_labels()
    kept = [p for p in pois if p["properties"]["challenge"] or p["properties"]["reason"] == "planting"]
    near2 = still_flagged(labelled, kept)
    near08 = still_flagged(labelled, kept, 0.8)
    row = on_row(labelled, row_features)
    out = {}
    for reason in ("gap", "planting"):
        for answer in ("vines_present", "real_gap"):
            idx = [i for i, lab in enumerate(labelled) if lab["properties"]["reason"] == reason and lab["properties"]["review_answer"] == answer]
            here = [i for i in idx if row[i]]
            out[f"{reason}/{answer}"] = f"2m {sum(near2[i] for i in idx)}/{len(idx)}; on row {sum(near08[i] for i in here)}/{len(here)}"
    return out


def metrics(tag: str, canopies: list[dict[str, Any]], judge: bool = True, refit: bool = True) -> dict[str, Any]:
    m = crl.metrics(tag, canopies, judge)
    m["labels"] = label_summary(match(labels(), canopies))
    if refit:
        pois, row_features, sampled = refit_pois(canopies)
        m["field_cover"] = field_cover(sampled)
        m["refit_gaps"] = {"challenge_gaps": sum(p["properties"]["reason"] == "gap" and p["properties"]["challenge"] for p in pois),
                           "planting": sum(p["properties"]["reason"] == "planting" for p in pois),
                           "planting_challenge": sum(p["properties"]["reason"] == "planting" and p["properties"]["challenge"] for p in pois)}
        m["gap_labels"] = gap_label_summary(pois, row_features)
    return m


def line(m: dict[str, Any]) -> str:
    return crl.line(m) + f" | labels {m['labels']} | refit POIs {m.get('refit_gaps')} | gap labels still flagged {m.get('gap_labels')} | field cover {m.get('field_cover')}"
