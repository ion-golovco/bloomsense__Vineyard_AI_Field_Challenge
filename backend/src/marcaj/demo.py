"""Deterministic, fictional geometry used until the organizer releases Sireț3."""

from typing import Any

EASTING = 500_000.0
NORTHING = 5_200_000.0


def _point(x: float, y: float) -> list[float]:
    return [EASTING + x, NORTHING + y]


def _polygon(x1: float, y1: float, x2: float, y2: float) -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [[
            _point(x1, y1), _point(x2, y1), _point(x2, y2),
            _point(x1, y2), _point(x1, y1),
        ]],
    }


def _feature(label: str, geometry: dict[str, Any], **properties: Any) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": {"label": label, **properties}}


def build_demo_scene() -> dict[str, Any]:
    features = [
        _feature("block", _polygon(0, 10, 120, 60), vineyard_id="V-001"),
        _feature("passage", _polygon(5, 23, 12, 47)),
        _feature("passage", _polygon(108, 23, 115, 47)),
        _feature("forbidden", _polygon(60, 55, 68, 59)),
        _feature("interrow_area", _polygon(8, 23, 112, 32), vineyard_id="V-001", interrow_cover="vegetation"),
        _feature("interrow_area", _polygon(8, 38, 112, 47), vineyard_id="V-001", interrow_cover="bare_soil"),
    ]
    for index, y in enumerate((20, 35, 50), start=1):
        row_id = f"V-001-R{index:03d}"
        features.append(_feature(
            "row", {"type": "LineString", "coordinates": [_point(10, y), _point(110, y)]},
            vineyard_id="V-001", row_id=row_id,
            row_structure="disrupted" if index == 2 else "regular",
        ))
        for x in (14, 37, 60, 83):
            features.append(_feature("vineyard", _polygon(x, y - 3, x + 17, y + 3), vineyard_id="V-001"))
    features.extend([
        _feature("waste", _polygon(79, 40.5, 81, 42.5), vineyard_id="V-001", target_id="W-001"),
        _feature("inspection", {"type": "Point", "coordinates": _point(55, 27.5)},
                 vineyard_id="V-001", row_id="V-001-R001", target_id="I-001", reason="visible_gap"),
        _feature("route", {"type": "LineString", "coordinates": [
            _point(8, 27.5), _point(112, 27.5), _point(112, 42.5),
            _point(8, 42.5), _point(8, 27.5),
        ]}),
    ])
    return {
        "type": "FeatureCollection",
        "crs": "EPSG:32635",
        "source": "synthetic demo — not Sireț3",
        "features": features,
    }
