"""Human verdicts on model output and hand-drawn plot outlines, recorded from the lab notebook. They are
evaluation evidence, never annotation: manual annotation of Sireț3 happens only in Marcaj, so nothing
here may flow into the prediction pipeline or the upload."""

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from marcaj.tiles import REPO_ROOT

REVIEW_PATH = REPO_ROOT / "data" / "review" / "verdicts.json"
OBJECT_VERDICTS = {"right", "wrong"}
TILE_VERDICTS = {"vineyard", "no_vineyard"}
MISSED_LABELS = {"block", "vineyard", "row", "interrow_area", "waste"}
PLOT_CLASSES = {"vineyard", "overgrown", "orchard", "other"}
_lock = threading.Lock()


def load_verdicts(path: Path = REVIEW_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def _save(verdicts: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(verdicts, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _append(record: dict[str, Any], path: Path, replaces_tile: bool = False) -> dict[str, Any]:
    record = {"id": uuid.uuid4().hex[:10], "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **record}
    with _lock:
        verdicts = load_verdicts(path)
        if replaces_tile:
            verdicts = [item for item in verdicts if not (item["kind"] == "tile" and item["tile"] == record["tile"])]
        verdicts.append(record)
        _save(verdicts, path)
    return record


def object_verdict(feature: dict[str, Any], verdict: str, reason: str = "", reviewer: str = "", path: Path = REVIEW_PATH) -> dict[str, Any]:
    """`feature` is a scene feature in EPSG:32635; its geometry is kept so later model runs can be checked against it."""
    if verdict not in OBJECT_VERDICTS:
        raise ValueError(f"object verdict must be one of {sorted(OBJECT_VERDICTS)}")
    properties = feature["properties"]
    return _append({
        "kind": "object", "verdict": verdict, "reason": reason, "reviewer": reviewer,
        "label": properties.get("label", ""), "source": properties.get("source", ""), "tile": properties.get("tile", ""),
        "properties": properties, "geometry": feature["geometry"],
    }, path)


def tile_verdict(tile: str, verdict: str, reviewer: str = "", path: Path = REVIEW_PATH) -> dict[str, Any]:
    if verdict not in TILE_VERDICTS:
        raise ValueError(f"tile verdict must be one of {sorted(TILE_VERDICTS)}")
    return _append({"kind": "tile", "verdict": verdict, "tile": tile, "reviewer": reviewer}, path, replaces_tile=True)


def missed(label: str, geometry: dict[str, Any], tile: str = "", note: str = "", reviewer: str = "", path: Path = REVIEW_PATH) -> dict[str, Any]:
    """A point, line or polygon in EPSG:32635 where the model should have found `label`."""
    if label not in MISSED_LABELS:
        raise ValueError(f"missed label must be one of {sorted(MISSED_LABELS)}")
    return _append({"kind": "missed", "verdict": "missed", "label": label, "tile": tile, "note": note, "reviewer": reviewer, "geometry": geometry}, path)


def plot_outline(label: str, geometry: dict[str, Any], note: str = "", reviewer: str = "", path: Path = REVIEW_PATH) -> dict[str, Any]:
    """A hand-drawn plot polygon in EPSG:32635: `vineyard` (bare-soil inter-rows), `overgrown` vineyard,
    `orchard`, or `other` (field, garden, grass). The reference for what the plot detector must separate."""
    if label not in PLOT_CLASSES:
        raise ValueError(f"plot class must be one of {sorted(PLOT_CLASSES)}")
    outline = shape(geometry)
    if outline.geom_type != "Polygon" or not outline.area:
        raise ValueError("draw a polygon to outline a plot")
    if not outline.is_valid:
        raise ValueError("the outline crosses itself; redraw it")
    return _append({"kind": "plot", "verdict": label, "label": label, "note": note, "reviewer": reviewer, "geometry": geometry}, path)


def delete_verdict(verdict_id: str, path: Path = REVIEW_PATH) -> bool:
    with _lock:
        verdicts = load_verdicts(path)
        kept = [item for item in verdicts if item["id"] != verdict_id]
        if len(kept) == len(verdicts):
            return False
        _save(kept, path)
        return True
