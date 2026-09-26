"""Point status and farmer scores for the client (app-only records, never Marcaj labels; docs/SPEC.md section 12).

A point is what the client lists as a visit point: a waste box, a missing-canopy inspection point (`reason` `gap` or
`planting`) or a Sentinel-2 point. One field (`vineyard_id`) is one farmer. Statuses are an append-only event log in
`data/app/point_status.json`; a point's last event wins. A farmer's claim (`fixed`, `false_positive`) counts in the
score at once; an inspector's event is a review: re-posting the current status approves it, posting `open` rejects a
claim. Only waste and missing canopy are scored; Sentinel points are 10 m signals, not findings.

Run `uv run --frozen python -m marcaj.points` for a self-check of the event fold and the score."""

import json
import os
import threading
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from shapely.geometry import shape

from marcaj.scene import OVERLAYS, load_projected_scene, overlay_features, scene_path, waste_id
from marcaj.tiles import REPO_ROOT

STATUS_PATH = Path(os.environ.get("MARCAJ_STATUS_PATH", REPO_ROOT / "data" / "app" / "point_status.json")).expanduser()
Role = Literal["farmer", "inspector"]
Status = Literal["open", "in_progress", "fixed", "false_positive"]
CLOSED = {"fixed", "false_positive"}  # no longer penalised; `in_progress` still is: the problem is still in the field
MISSING = {"gap", "planting"}
FULL_MISSING = 0.15  # share of row length missing that costs the whole canopy penalty; the fields span 0-24% (P23)
_LOCK = threading.Lock()


def point_id(feature: dict[str, Any]) -> str | None:
    properties = feature["properties"]
    if properties.get("label") == "waste":
        return waste_id(feature)
    return str(properties["id"]) if properties.get("label") == "inspection" and properties.get("id") else None


def _kind(properties: dict[str, Any]) -> str:
    return "waste" if properties["label"] == "waste" else str(properties.get("reason", ""))


def point_features(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """The client's visit points: every waste box, and the inspection points `browser_scene` shows (route targets
    and Sentinel points)."""
    return [feature for feature in scene["features"] + overlay_features(scene)
            if feature["properties"].get("label") == "waste"
            or (feature["properties"].get("label") == "inspection" and point_id(feature))]


@lru_cache(maxsize=2)
def _facts(key: tuple[tuple[str, float], ...]) -> dict[str, Any]:
    """Per field: area and row length (m, EPSG:32635), and its points; cached per scene and overlay file version."""
    scene = load_projected_scene()
    fields: dict[str, dict[str, Any]] = defaultdict(lambda: {"area_m2": 0.0, "row_m": 0.0})
    for feature in scene["features"]:
        properties = feature["properties"]
        field = properties.get("vineyard_id")
        if field and properties.get("label") == "block":
            fields[field]["area_m2"] += float(properties.get("area_m2") or shape(feature["geometry"]).area)
        elif field and properties.get("label") == "row":
            fields[field]["row_m"] += shape(feature["geometry"]).length
    points = {}
    for feature in point_features(scene):
        properties = feature["properties"]
        points[point_id(feature)] = {"vineyard_id": properties.get("vineyard_id") or "", "kind": _kind(properties),
                                     "missing_m": float(properties.get("gap_m") or 0.0)}
    return {"fields": dict(fields), "points": points}


def facts() -> dict[str, Any]:
    paths = [scene_path(), *OVERLAYS]
    return _facts(tuple((str(path), path.stat().st_mtime if path.is_file() else 0.0) for path in paths))


def _events(path: Path = STATUS_PATH) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["events"]


def fold(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each point's current record from its events, oldest first: the last event's status, who set it and when, and
    `approved` when that was the inspector."""
    records: dict[str, dict[str, Any]] = {}
    for event in events:
        record = records.setdefault(event["id"], {"history": []})
        record.update(status=event["status"], by=event["role"], at=event["at"], note=event.get("note", ""),
                      approved=event["role"] == "inspector")
        record["history"].append({key: event[key] for key in ("role", "status", "at", "note") if key in event})
    return records


def field_score(area_m2: float, row_m: float, waste_open: int, missing_open_m: float) -> int:
    """0-100, 100 = a clean field. Missing canopy is weighed as a share of the field's row length and costs up to 60
    points, reached at FULL_MISSING of the row length; each open waste item costs 10, up to 40."""
    # ponytail: fixed hand-set weights, no per-hectare waste density; tune with the jury's or the farmers' ranking
    canopy = 60.0 * min(1.0, missing_open_m / row_m / FULL_MISSING) if row_m > 0 else (60.0 if missing_open_m else 0.0)
    return round(100.0 - canopy - min(40.0, 10.0 * waste_open))


def scores(fields: dict[str, dict[str, Any]], points: dict[str, dict[str, Any]], records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per field: `score` with the current statuses, `model_score` as if every point were open, the open and total
    counts, and `to_review`: farmer claims the inspector has not reviewed."""
    out = {}
    for field, fact in fields.items():
        mine = [(pid, item) for pid, item in points.items() if item["vineyard_id"] == field and (item["kind"] == "waste" or item["kind"] in MISSING)]
        is_open = {pid: records.get(pid, {}).get("status", "open") not in CLOSED for pid, _ in mine}
        waste = [pid for pid, item in mine if item["kind"] == "waste"]
        missing = {pid: item["missing_m"] for pid, item in mine if item["kind"] in MISSING}
        waste_open = sum(is_open[pid] for pid in waste)
        missing_open = sum(length for pid, length in missing.items() if is_open[pid])
        out[field] = {
            "score": field_score(fact["area_m2"], fact["row_m"], waste_open, missing_open),
            "model_score": field_score(fact["area_m2"], fact["row_m"], len(waste), sum(missing.values())),
            "waste_open": waste_open, "waste": len(waste), "missing_open": sum(is_open[pid] for pid in missing),
            "missing": len(missing), "missing_open_m": round(missing_open, 1), "missing_m": round(sum(missing.values()), 1),
            "row_m": round(fact["row_m"], 1), "area_m2": round(fact["area_m2"]),
            "to_review": sum(records.get(pid, {}).get("by") == "farmer" and records[pid]["status"] != "open" for pid, _ in mine),
        }
    return out


def summary() -> dict[str, Any]:
    """GET /api/points: every point's record (points without events are `open`) and every field's score."""
    known = facts()
    records = {pid: record for pid, record in fold(_events()).items() if pid in known["points"]}
    return {"records": records, "fields": scores(known["fields"], known["points"], records)}


def closed_ids() -> frozenset[str]:
    return frozenset(pid for pid, record in fold(_events()).items() if record["status"] in CLOSED)


def set_status(pid: str, role: Role, status: Status, note: str = "", path: Path = STATUS_PATH) -> dict[str, Any]:
    """Append one status event and return the point's record and its field's score. KeyError for an unknown point."""
    known = facts()
    if pid not in known["points"]:
        raise KeyError(pid)
    event = {"id": pid, "role": role, "status": status, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "note": note.strip()}
    with _LOCK:  # ponytail: one process, one lock; a database once more than one server writes
        events = _events(path) + [event]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"events": events}, indent=1) + "\n", encoding="utf-8")
        temporary.replace(path)  # atomic: a crash mid-write never leaves a half-written log
    records = fold(events)
    field = known["points"][pid]["vineyard_id"]
    return {"id": pid, "record": records[pid], "field": field,
            "score": scores({field: known["fields"][field]}, known["points"], records).get(field) if field in known["fields"] else None}


def _demo() -> None:
    at = "2026-09-26T12:00:00+00:00"
    events = [{"id": "W1", "role": "farmer", "status": "fixed", "at": at}, {"id": "G1", "role": "farmer", "status": "false_positive", "at": at},
              {"id": "G1", "role": "inspector", "status": "open", "at": at}, {"id": "G2", "role": "inspector", "status": "open", "at": at}]
    records = fold(events)
    assert records["W1"] == {"history": [{"role": "farmer", "status": "fixed", "at": at}], "status": "fixed", "by": "farmer", "at": at, "note": "", "approved": False}
    assert records["G1"]["status"] == "open" and records["G1"]["approved"] and len(records["G1"]["history"]) == 2
    fields = {"F": {"area_m2": 10_000.0, "row_m": 4000.0}}
    points = {"W1": {"vineyard_id": "F", "kind": "waste", "missing_m": 0.0}, "G1": {"vineyard_id": "F", "kind": "gap", "missing_m": 100.0},
              "G2": {"vineyard_id": "F", "kind": "gap", "missing_m": 0.0}, "S1": {"vineyard_id": "F", "kind": "sentinel_low_ndvi", "missing_m": 0.0}}
    score = scores(fields, points, records)["F"]
    # 100 m of 4000 m is 2.5% missing: 10 points; W1's fixed claim takes the waste penalty off at once
    assert (score["score"], score["model_score"], score["waste_open"], score["missing_open"], score["to_review"]) == (90, 80, 0, 2, 1), score
    assert field_score(1.0, 0.0, 0, 0.0) == 100 and field_score(1.0, 100.0, 9, 15.0) == 0
    print("points self-check passed")


if __name__ == "__main__":
    _demo()
