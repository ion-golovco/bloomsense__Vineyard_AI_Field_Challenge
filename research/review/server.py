"""Local review tool (research tooling, not the jury client): label waste candidates, canopy gaps and long canopies,
relabel and correct the hand-drawn plot outlines over the cadastral parcels (Plots tab),
and mark missed waste or canopies on the map. Every answer is recorded through marcaj.review into
data/review/verdicts.json (evaluation evidence only; prediction code never reads it). Confirmed waste is exported to
data/generated/work/waste/confirmed.csv (and confirmed_rule_conform.csv, without rule-flagged answers) after every
waste answer or mark.
Build the review set first (research/review/build.py), then from backend/:
uv run --frozen python ../research/review/server.py   ->   http://127.0.0.1:8010"""

import csv
import json
import shutil
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import transform, unary_union

from marcaj import review
from marcaj.tiles import PIXEL_M, REPO_ROOT, TILE_PX

HERE = Path(__file__).parent
GEN = REPO_ROOT / "data" / "generated"
REVIEW_DIR = GEN / "work" / "review"
BACKUP = REVIEW_DIR / "verdicts_backup.json"
CONFIRMED = GEN / "work" / "waste" / "confirmed.csv"
GRID_LEFT, GRID_TOP, TILE_M = 628992.0, 5221222.4, TILE_PX * PIXEL_M
TO_LONLAT = Transformer.from_crs("EPSG:32635", "EPSG:4326", always_xy=True).transform
TO_WORLD = Transformer.from_crs("EPSG:4326", "EPSG:32635", always_xy=True).transform
# answer key -> (object verdict, reason) per tab
ANSWERS = {
    "waste": {"waste": ("right", "waste"), "not_waste": ("wrong", "not waste"), "unsure": ("unsure", "unsure")},
    "gaps": {"real_gap": ("right", "real gap"), "vines_present": ("wrong", "vines present (missed canopy)"), "partly": ("partly", "partly a gap")},
    "canopies": {"one_plant": ("right", "one plant"), "several": ("wrong", "several touching plants"), "not_vine": ("wrong", "not a vine")},
}
LABEL = {"waste": ("waste", "waste_candidate"), "gaps": ("inspection", "poi"), "canopies": ("vineyard", "prediction")}

app = FastAPI(title="Marcaj review tool")


def items(tab: str) -> list[dict[str, Any]]:
    path = REVIEW_DIR / f"{tab}.json"
    if tab not in ANSWERS or not path.is_file():
        raise HTTPException(404, f"no review set for {tab}; run research/review/build.py")
    return json.loads(path.read_text(encoding="utf-8"))


def answers() -> dict[str, dict[str, Any]]:
    """Latest review-tool answer per item id."""
    latest: dict[str, dict[str, Any]] = {}
    for verdict in review.load_verdicts():
        properties = verdict.get("properties") or {}
        if verdict["kind"] == "object" and "review_id" in properties:
            latest[properties["review_id"]] = {"answer": properties["review_answer"], "verdict_id": verdict["id"], "reviewer": verdict.get("reviewer", "")}
    return latest


def _backup() -> None:
    if not BACKUP.exists() and review.REVIEW_PATH.is_file():
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(review.REVIEW_PATH, BACKUP)


def _pixel(e: float, n: float) -> tuple[str, int, int]:
    r, c = int((GRID_TOP - n) // TILE_M), int((e - GRID_LEFT) // TILE_M)
    return f"siret3_r{r:03d}_c{c:03d}.tif", round((e - GRID_LEFT - c * TILE_M) / PIXEL_M), round((GRID_TOP - r * TILE_M - n) / PIXEL_M)


CONFORM = GEN / "work" / "waste" / "confirmed_rule_conform.csv"
EXPORT_FIELDS = ["source", "id", "tile", "x_px", "y_px", "easting", "northing", "w_m", "h_m", "location", "vineyard_id", "rule_flags", "reviewer", "note"]


def export_confirmed() -> int:
    """confirmed.csv: every "waste" answer and "waste here" mark; confirmed_rule_conform.csv: the same without the
    answers whose card carries rule flags (build.rule_flags: tiny scraps, pale patches, stones, tubes). vineyard_id
    follows the rules' 10 m rule against the current predicted blocks."""
    from marcaj.waste import vineyard_id  # the rules' 10 m rule, shared with the detector
    blocks = _cached(GEN / "predictions.geojson", _blocks)
    by_id = {it["id"]: it for it in items("waste")}
    rows = []
    for item_id, answer in answers().items():
        it = by_id.get(item_id)
        if it and answer["answer"] == "waste":
            rows.append({"source": "candidate", "id": item_id, "tile": it["tile"], "x_px": it["x_px"], "y_px": it["y_px"], "easting": it["easting"],
                         "northing": it["northing"], "w_m": it["w_m"], "h_m": it["h_m"], "location": it["location"], "vineyard_id": vineyard_id(box(*it["box"]), blocks),
                         "rule_flags": "; ".join(it.get("rule_flags", [])), "reviewer": answer["reviewer"], "note": it.get("reason", "")})
    for verdict in review.load_verdicts():
        if verdict["kind"] == "missed" and verdict["label"] == "waste":
            point = shape(verdict["geometry"]).centroid
            tile, x, y = _pixel(point.x, point.y)
            rows.append({"source": "map mark", "id": verdict["id"], "tile": tile, "x_px": x, "y_px": y, "easting": round(point.x, 2), "northing": round(point.y, 2),
                         "w_m": "", "h_m": "", "location": "", "vineyard_id": vineyard_id(point, blocks), "rule_flags": "",
                         "reviewer": verdict.get("reviewer", ""), "note": verdict.get("note", "")})
    rows.sort(key=lambda row: (row["tile"], row["y_px"]))
    CONFIRMED.parent.mkdir(parents=True, exist_ok=True)
    for path, chosen in ((CONFIRMED, rows), (CONFORM, [row for row in rows if not row["rule_flags"]])):
        with path.open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=EXPORT_FIELDS)
            writer.writeheader()
            writer.writerows(chosen)
    return len(rows)


class Answer(BaseModel):
    tab: str
    id: str
    answer: str  # a key of ANSWERS[tab], or "" to clear
    reviewer: str = ""


class Mark(BaseModel):
    label: str  # "waste" or "vineyard" (a missed canopy)
    lon: float
    lat: float
    note: str = ""
    reviewer: str = ""


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/items/{tab}")
def get_items(tab: str) -> list[dict[str, Any]]:
    latest = answers()
    return [{**{k: v for k, v in it.items() if k != "geometry"}, "answer": latest.get(it["id"], {}).get("answer", "")} for it in items(tab)]


@app.post("/api/answer")
def post_answer(body: Answer) -> dict[str, Any]:
    if body.tab not in ANSWERS or (body.answer and body.answer not in ANSWERS[body.tab]):
        raise HTTPException(400, f"answer must be one of {sorted(ANSWERS.get(body.tab, {}))}")
    it = next((it for it in items(body.tab) if it["id"] == body.id), None)
    if it is None:
        raise HTTPException(404, f"no item {body.id} in {body.tab}")
    _backup()
    previous = answers().get(body.id)
    if previous:
        review.delete_verdict(previous["verdict_id"])
    record = None
    if body.answer:
        verdict, reason = ANSWERS[body.tab][body.answer]
        label, source = LABEL[body.tab]
        geometry = it.get("geometry") or mapping(box(*it["box"]))
        properties = {k: v for k, v in it.items() if k not in ("geometry", "close", "context", "box")}
        feature = {"geometry": geometry, "properties": {**properties, "label": label, "source": source, "review_tab": body.tab, "review_id": body.id, "review_answer": body.answer}}
        record = review.object_verdict(feature, verdict, reason, body.reviewer)
    confirmed = export_confirmed() if body.tab == "waste" else None
    return {"id": body.id, "answer": body.answer, "verdict_id": record["id"] if record else None, "confirmed": confirmed}


@app.post("/api/mark")
def post_mark(body: Mark) -> dict[str, Any]:
    if body.label not in ("waste", "vineyard"):
        raise HTTPException(400, "label must be waste or vineyard")
    _backup()
    e, n = TO_WORLD(body.lon, body.lat)
    tile, _, _ = _pixel(e, n)
    record = review.missed(body.label, {"type": "Point", "coordinates": [round(e, 3), round(n, 3)]}, tile, body.note or "review tool map", body.reviewer)
    confirmed = export_confirmed() if body.label == "waste" else None
    return {"verdict_id": record["id"], "tile": tile, "easting": round(e, 2), "northing": round(n, 2), "confirmed": confirmed}


@app.delete("/api/verdict/{verdict_id}")
def delete(verdict_id: str) -> dict[str, Any]:
    if not review.delete_verdict(verdict_id):
        raise HTTPException(404, f"no verdict {verdict_id}")
    return {"deleted": verdict_id, "confirmed": export_confirmed()}


@app.get("/api/export")
def export(conform: bool = False) -> FileResponse:
    """confirmed.csv, or with ?conform=1 only the rule-conform items (confirmed_rule_conform.csv)."""
    export_confirmed()
    path = CONFORM if conform else CONFIRMED
    return FileResponse(path, media_type="text/csv", filename=path.name)


def _lonlat(geometry: dict[str, Any]) -> dict[str, Any]:
    return mapping(transform(TO_LONLAT, shape(geometry)))


@app.get("/api/map")
def map_layers() -> dict[str, Any]:
    latest = answers()
    feature = lambda geometry, **properties: {"type": "Feature", "geometry": _lonlat(geometry), "properties": properties}
    candidates = [feature({"type": "Point", "coordinates": [it["easting"], it["northing"]]}, id=it["id"], answer=latest.get(it["id"], {}).get("answer", ""),
                          score=it.get("score"), location=it["location"], origin=it["origin"], prior=it.get("prior", ""), tile=it["tile"],
                          x_px=it["x_px"], y_px=it["y_px"], close=it["close"]) for it in items("waste")]
    accepted = [feature(mapping(box(*it["box"])), id=it["id"]) for it in items("waste") if it.get("verifier")]
    marks = [feature(v["geometry"], id=v["id"], label=v["label"], tile=v.get("tile", ""), note=v.get("note", ""))
             for v in review.load_verdicts() if v["kind"] == "missed" and v["label"] in ("waste", "vineyard")]
    predictions = json.loads((GEN / "predictions.geojson").read_text(encoding="utf-8"))["features"]
    blocks = [feature(f["geometry"], vineyard_id=f["properties"]["vineyard_id"]) for f in predictions if f["properties"]["label"] == "block"]
    scene = GEN / "scene.json"
    start = [feature(f["geometry"]) for f in json.loads(scene.read_text(encoding="utf-8"))["features"] if f["properties"]["label"] == "start"] if scene.is_file() else []
    collection = lambda features: {"type": "FeatureCollection", "features": features}
    return {"candidates": collection(candidates), "accepted": collection(accepted), "marks": collection(marks), "blocks": collection(blocks), "start": collection(start)}


# ---------------------------------------------------------------- plots tab: correct the hand-drawn plot outlines
# An edit is a new plot_outline record plus delete_verdict of the old one; `replaces` keeps the first version's id
# (the one in verdicts_backup_plots.json). Storage stays EPSG:32635; the browser only sees lon/lat.

PLOTS_BACKUP = REVIEW_DIR / "verdicts_backup_plots.json"
PREDICTED = {"new": GEN / "predictions.geojson", "uploaded": GEN / "work" / "uploaded_1054" / "predictions.geojson"}
_files: dict[tuple[Path, Any], tuple[float, Any]] = {}  # per (file, builder): one file can feed several builders


def _cached(path: Path, build: Any) -> Any:
    """`build(path)`, rebuilt when the file changes; a half-written file keeps the last good copy."""
    mtime = path.stat().st_mtime if path.is_file() else -1.0
    hit = _files.get((path, build))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        value = build(path) if mtime >= 0 else []
    except json.JSONDecodeError:
        if hit:
            return hit[1]
        raise
    _files[(path, build)] = (mtime, value)
    return value


def _blocks(path: Path) -> list[tuple[Any, str]]:
    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    return [(make_valid(shape(f["geometry"])), f["properties"].get("vineyard_id", "")) for f in features if f["properties"].get("label") == "block"]


def _parcels() -> list[Any]:
    from marcaj.cadastre import PARCELS, load_parcels  # lazy: cadastre.py is edited elsewhere and must not stop the tool
    return _cached(PARCELS, load_parcels)


def _backup_plots() -> None:
    if not PLOTS_BACKUP.exists() and review.REVIEW_PATH.is_file():
        PLOTS_BACKUP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(review.REVIEW_PATH, PLOTS_BACKUP)


def _plot(verdict_id: str) -> dict[str, Any] | None:
    return next((v for v in review.load_verdicts() if v["id"] == verdict_id and v["kind"] == "plot"), None)


def _outline(v: dict[str, Any]) -> dict[str, Any]:
    polygon = shape(v["geometry"])
    out = {"id": v["id"], "label": v["label"], "note": v.get("note", ""), "reviewer": v.get("reviewer", ""), "created": v.get("created", ""),
           "replaces": v.get("replaces", ""), "area_m2": round(polygon.area), "utm": v["geometry"],
           "ring": [list(TO_LONLAT(x, y)) for x, y in polygon.exterior.coords[:-1]]}
    for run, path in PREDICTED.items():
        best = max(((b.intersection(polygon).area / b.union(polygon).area, vid) for b, vid in _cached(path, _blocks) if b.intersects(polygon)), default=(0.0, ""))
        out[f"iou_{run}"], out[f"match_{run}"] = round(best[0], 3), best[1]
    return out


def _world_ring(ring: list[list[float]]) -> dict[str, Any]:
    return mapping(Polygon([(round(e, 3), round(n, 3)) for e, n in (TO_WORLD(lon, lat) for lon, lat in ring)]))


class PlotSave(BaseModel):
    label: str
    ring: list[list[float]] | None = None  # lon/lat vertices, not closed
    utm: dict[str, Any] | None = None  # or the EPSG:32635 geometry as stored (undo)
    edit: str = ""  # the outline this one corrects; it is deleted once the new record is stored
    replaces: str = ""  # explicit first-version id (undo of a delete)
    note: str | None = None
    reviewer: str = ""


class ParcelUnion(BaseModel):
    parcels: list[int]
    clip: list[list[float]] | None = None  # lon/lat ring to intersect with


@app.get("/api/plots")
def plot_outlines() -> list[dict[str, Any]]:
    return [_outline(v) for v in review.load_verdicts() if v["kind"] == "plot"]


@app.get("/api/plots/layers")
def plot_layers() -> dict[str, Any]:
    feature = lambda geometry, **properties: {"type": "Feature", "geometry": mapping(transform(TO_LONLAT, geometry)), "properties": properties}
    collection = lambda features: {"type": "FeatureCollection", "features": features}
    layers = {"parcels": collection([feature(p, id=i, area_m2=round(p.area)) for i, p in enumerate(_parcels())])}
    for run, path in PREDICTED.items():
        layers[f"blocks_{run}"] = collection([feature(b, vineyard_id=vid) for b, vid in _cached(path, _blocks)])
    scene = GEN / "scene.json"
    start = [shape(f["geometry"]) for f in _cached(scene, lambda p: json.loads(p.read_text(encoding="utf-8"))["features"]) if f["properties"]["label"] == "start"] if scene.is_file() else []
    layers["start"] = collection([feature(g) for g in start])
    return layers


@app.post("/api/plots")
def save_plot(body: PlotSave) -> dict[str, Any]:
    old = _plot(body.edit) if body.edit else None
    if body.edit and old is None:
        raise HTTPException(404, f"no plot outline {body.edit}")
    geometry = body.utm or (_world_ring(body.ring) if body.ring else old["geometry"] if old else None)
    if geometry is None:
        raise HTTPException(400, "give ring, utm or edit")
    _backup_plots()
    try:
        record = review.plot_outline(body.label, geometry, old.get("note", "") if body.note is None and old else body.note or "",
                                     body.reviewer or (old.get("reviewer", "") if old else ""),
                                     replaces=body.replaces or ((old.get("replaces") or old["id"]) if old else ""))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if old:
        review.delete_verdict(old["id"])
    return {"created": _outline(record), "deleted": _outline(old) if old else None}


@app.delete("/api/plots/{verdict_id}")
def delete_plot(verdict_id: str) -> dict[str, Any]:
    old = _plot(verdict_id)
    if old is None:
        raise HTTPException(404, f"no plot outline {verdict_id}")
    _backup_plots()
    review.delete_verdict(verdict_id)
    return {"deleted": _outline(old)}


@app.post("/api/plots/union")
def parcel_union(body: ParcelUnion) -> dict[str, Any]:
    """One outline from the union of parcels (0.3 m closing bridges the slivers between neighbours), optionally
    intersected with a drawn polygon; the largest part, holes filled, simplified at 5 cm."""
    parcels = _parcels()
    if not body.parcels or any(not 0 <= i < len(parcels) for i in body.parcels):
        raise HTTPException(400, f"parcels must be indices 0..{len(parcels) - 1}")
    merged = unary_union([parcels[i].buffer(0.3, join_style="mitre") for i in body.parcels]).buffer(-0.3, join_style="mitre")
    if body.clip:
        merged = merged.intersection(make_valid(shape(_world_ring(body.clip))))
    parts = sorted((g for g in getattr(merged, "geoms", [merged]) if g.geom_type == "Polygon" and g.area > 1), key=lambda g: -g.area)
    if not parts:
        raise HTTPException(400, "the parcels and the drawn polygon do not overlap")
    outline = Polygon(parts[0].exterior).simplify(0.05)
    return {"ring": [list(TO_LONLAT(x, y)) for x, y in outline.exterior.coords[:-1]], "area_m2": round(outline.area), "parts": len(parts)}


# ---------------------------------------------------------------- plots tab: predicted plots without an outline
# Blocks of data/generated/predictions.geojson (the "new run" layer; plus the plots verify_plots dropped, beside it) whose
# area is under 50% on vineyard outlines, with their vine evidence (plots.vine_evidence, computed here when the run does
# not carry it) and what plots.looks_like_vineyard says. An answer is an object verdict on the block (label "block",
# source "prediction"): right = vineyard, wrong = not a vineyard, unsure. Answers follow a block across runs by geometry
# (IoU >= 0.5), not by its id.

CANDIDATE_RUN = GEN / "predictions.geojson"
DROPPED = "dropped_plots.geojson"
CANDIDATES_BACKUP = REVIEW_DIR / "verdicts_backup_candidates.json"
BLOCK_ANSWERS = {"vineyard": ("right", "vineyard"), "not_vineyard": ("wrong", "not a vineyard"), "unsure": ("unsure", "unsure")}
EVIDENCE_KEYS = ("cover", "rows_any", "per_10m", "len_med", "long_share", "contrast", "on_exg", "off_exg", "canopy_share", "spacing_cv")


def _run() -> Path:
    return CANDIDATE_RUN


def _candidate_features(path: Path) -> list[dict[str, Any]]:
    """The run's blocks and the dropped ones beside it, each with `vine_evidence` (its largest pattern's) and `rule`
    (plots.looks_like_vineyard over its patterns: "" keeps, else the reason to drop)."""
    from marcaj import plots  # lazy: heavy, and plots.py is edited elsewhere
    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    blocks = [f for f in features if f["properties"].get("label") == "block"]
    dropped = path.parent / DROPPED
    blocks += json.loads(dropped.read_text(encoding="utf-8"))["features"] if dropped.is_file() else []
    evidence = plots.vine_evidence(features, plots.load_excess()) if any("vine_evidence" not in f["properties"] for f in blocks) else {}
    out = []
    for f in blocks:
        p = f["properties"]
        patterns = sorted(p.get("patterns") or [p], key=lambda q: -q.get("area_m2", 0))
        found = [evidence[q["pattern_id"]] for q in patterns if q.get("pattern_id") in evidence]
        main = p.get("vine_evidence") or (found[0] if found else {})
        checks = [plots.looks_like_vineyard(e) for e in (found or ([main] if main else []))]
        rule = "" if not checks or any(ok for ok, _ in checks) else checks[0][1]
        out.append({**f, "properties": {**p, "vine_evidence": main, "rule": p.get("dropped") or rule}})
    return out


def _block_answers() -> list[tuple[Any, dict[str, Any]]]:
    return [(make_valid(shape(v["geometry"])), v) for v in review.load_verdicts()
            if v["kind"] == "object" and (v.get("properties") or {}).get("review_tab") == "plots"]


def _same(a, b) -> bool:
    return a.intersects(b) and a.intersection(b).area >= 0.5 * a.union(b).area


def candidates() -> list[dict[str, Any]]:
    path = _run()
    outlines = [(make_valid(shape(v["geometry"])), v["label"]) for v in review.load_verdicts() if v["kind"] == "plot"]
    union = {label: unary_union([g for g, l in outlines if l == label]) for label in ("vineyard", "orchard", "overgrown")}
    answered = _block_answers()
    out = []
    for f in _cached(path, _candidate_features):
        p, g = f["properties"], make_valid(shape(f["geometry"]))
        share = {label: round(g.intersection(u).area / g.area, 2) if not u.is_empty else 0.0 for label, u in union.items()}
        if share["vineyard"] >= 0.5:
            continue
        latest = [v for a, v in answered if _same(a, g)]
        evidence = p.get("vine_evidence") or {}
        polygon = max(getattr(g, "geoms", [g]), key=lambda part: part.area)
        out.append({"id": f"{p['vineyard_id']}{':dropped' if p.get('dropped') else ''}", "vineyard_id": p["vineyard_id"], "dropped": p.get("dropped", ""), "rule": p.get("rule", ""),
                    "area_m2": round(g.area), "rows": p.get("rows", 0), "row_spacing_m": p.get("row_spacing_m"), "on": share,
                    "evidence": {k: evidence[k] for k in EVIDENCE_KEYS if k in evidence},
                    "answer": latest[-1]["properties"].get("review_answer", "") if latest else "", "verdict_id": latest[-1]["id"] if latest else "",
                    "ring": [list(TO_LONLAT(x, y)) for x, y in polygon.exterior.coords[:-1]], "utm": mapping(g)})
    return sorted(out, key=lambda c: (bool(c["dropped"]), -c["area_m2"]))


class BlockAnswer(BaseModel):
    id: str
    answer: str  # a key of BLOCK_ANSWERS, or "" to clear
    reviewer: str = ""
    reason: str = ""  # replaces the answer's default reason


@app.get("/api/plots/candidates")
def get_candidates() -> dict[str, Any]:
    return {"run": str(_run().relative_to(REPO_ROOT)), "items": [{k: v for k, v in c.items() if k != "utm"} for c in candidates()]}


@app.post("/api/plots/candidates")
def answer_candidate(body: BlockAnswer) -> dict[str, Any]:
    if body.answer and body.answer not in BLOCK_ANSWERS:
        raise HTTPException(400, f"answer must be one of {sorted(BLOCK_ANSWERS)}")
    item = next((c for c in candidates() if c["id"] == body.id), None)
    if item is None:
        raise HTTPException(404, f"no predicted plot {body.id} without an outline")
    if not CANDIDATES_BACKUP.exists() and review.REVIEW_PATH.is_file():
        CANDIDATES_BACKUP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(review.REVIEW_PATH, CANDIDATES_BACKUP)
    g = shape(item["utm"])
    for a, v in _block_answers():
        if _same(a, g):
            review.delete_verdict(v["id"])
    record = None
    if body.answer:
        verdict, reason = BLOCK_ANSWERS[body.answer]
        properties = {"label": "block", "source": "prediction", "vineyard_id": item["vineyard_id"], "dropped": item["dropped"], "rule": item["rule"], "area_m2": item["area_m2"],
                      "vine_evidence": item["evidence"], "run": str(_run().relative_to(REPO_ROOT)), "review_tab": "plots", "review_id": body.id, "review_answer": body.answer}
        record = review.object_verdict({"geometry": item["utm"], "properties": properties}, verdict, body.reason or reason, body.reviewer)
    return {"id": body.id, "answer": body.answer, "verdict_id": record["id"] if record else ""}


@app.get("/imagery/{z}/{x}/{y}.png")
def imagery(z: int, x: int, y: int) -> Response:
    """Fallback when the client app on :8000 is not running (it is never started from here)."""
    from marcaj.imagery import render_tile
    return Response(render_tile(z, x, y), media_type="image/png", headers={"Cache-Control": "max-age=86400"})


app.mount("/crops", StaticFiles(directory=REVIEW_DIR / "crops"), name="crops")
LEAFLET = REPO_ROOT / "web" / "node_modules" / "leaflet" / "dist"
if LEAFLET.is_dir():
    app.mount("/leaflet", StaticFiles(directory=LEAFLET), name="leaflet")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8010)
