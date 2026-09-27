"""Thin API for the challenge map; processing remains in Python modules."""

import json
import threading
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping
from shapely.ops import transform, unary_union

from marcaj import points
from marcaj import route as solver
from marcaj.export import ORGANIZER_CRS
from marcaj.imagery import render_tile
from marcaj.routing import _geometries
from marcaj.scene import _TO_DISPLAY, OVERLAYS, browser_scene, load_projected_scene, scene_path

def _version(path: Path) -> tuple[int, int]:
    """Cache key of the route world: the scene file and the inspection points file (`OVERLAYS[0]`), whose targets the
    plan is built on; either changing means a new world."""
    return path.stat().st_mtime_ns, OVERLAYS[0].stat().st_mtime_ns if OVERLAYS[0].is_file() else 0


def _warm_routes() -> None:
    """Load (or build, about 5 min for 290 targets) the scene's route plan with row hops, so the first route request
    is answered in seconds; a failure only means the first request builds its plan itself."""
    path = scene_path()
    try:
        # hops only: the client always asks with hops on; the no-hop plan is the export's and takes minutes more
        _solve(str(path), _version(path), None, None, "site", None, True)
    except Exception as error:  # noqa: BLE001 -- a warm-up must never stop the app
        print(f"route warm-up failed: {error!r}")


@asynccontextmanager
async def _lifespan(_: FastAPI):
    threading.Thread(target=_warm_routes, name="route-warm-up", daemon=True).start()
    yield


app = FastAPI(title="Marcaj Vineyard Map", version="0.1.0", lifespan=_lifespan)
_FROM_DISPLAY = Transformer.from_crs("EPSG:4326", "EPSG:32635", always_xy=True)
MAX_ROUTE_BODY = 2048  # bytes; a route request is a few coordinates
STUDY_MARGIN_M = 5.0


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/scene")
def get_scene() -> dict:
    return browser_scene(load_projected_scene())


@app.get("/api/imagery/{z}/{x}/{y}.png")
def imagery_tile(z: int, x: int, y: int) -> Response:
    if not 0 <= z <= 24 or not 0 <= x < 2**z or not 0 <= y < 2**z:
        raise HTTPException(status_code=404)
    return Response(render_tile(z, x, y), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


Finite = Annotated[float, Field(allow_inf_nan=False)]


class Projected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    easting: Finite
    northing: Finite


class RouteRequest(BaseModel):
    """`start` / `end`: [lon, lat] (WGS84, as the map shows) or {easting, northing} (EPSG:32635); a missing start
    is the organizer START, a missing end returns to the start."""
    model_config = ConfigDict(extra="forbid")
    start: tuple[Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)], Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]] | Projected | None = None
    end: tuple[Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)], Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]] | Projected | None = None
    scope: Annotated[str, Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_-]+$")] = "site"
    min_confidence: Literal[0.5, 0.7] | None = None
    hops: bool = True
    # the farmer's route: only these target kinds (`canopy` is missing canopy, gaps and planting), and with
    # `open_only` none a status has closed; the challenge route keeps the defaults
    kinds: tuple[Literal["waste", "canopy"], ...] | None = None
    open_only: bool = False


@lru_cache(maxsize=2)
def _world(path: str, modified: tuple[int, int]) -> dict[str, Any]:
    """The route world of the scene file at `path` (see `route.planning_features`), its targets, the field ids and
    the plan cache token."""
    features, name = solver.planning_features(load_projected_scene())
    targets = solver.route_target_features(features) + (solver.poi_targets(OVERLAYS[0]) if OVERLAYS[0].is_file() else [])
    fields = {item["properties"].get("vineyard_id") for item in features if item["properties"].get("label") == "block"}
    return {
        "features": features, "targets": targets, "name": name, "fields": ({item["vineyard_id"] for item in targets} | fields) - {"", None},
        "study": unary_union(_geometries(features, "study_area")), "token": f"{path}:{Path(path).stat().st_size}:{modified}:{name}",
    }


def _projected(value: tuple[float, float] | Projected | None) -> tuple[float, float] | None:
    if value is None:
        return None
    if isinstance(value, Projected):
        return value.easting, value.northing
    return _FROM_DISPLAY.transform(*value)


@lru_cache(maxsize=16)
def _solve(path: str, modified: tuple[int, int], start: tuple[float, float] | None, end: tuple[float, float] | None, scope: str, cutoff: float | None,
           hops: bool, kinds: frozenset[str] | None = None, closed: frozenset[str] = frozenset()) -> dict[str, Any]:
    """The route for one normalised request on the scene file `path` as of `modified`, cached so the download
    after a display request is instant. `kinds` keeps only `waste` and/or `canopy` targets; `closed` ids are left out."""
    world = _world(path, modified)
    for name, value in (("start", start), ("end", end)):
        if value is not None and not world["study"].buffer(STUDY_MARGIN_M).contains(Point(value)):
            raise ValueError(f"The {name} point is outside the study area")
    if scope != "site" and scope not in world["fields"]:
        raise ValueError(f"Unknown field {scope!r}; known fields: {', '.join(sorted(world['fields']))}")
    targets = world["targets"]
    include = {index for index, target in enumerate(targets) if (scope == "site" or target.get("vineyard_id") == scope)
               and (cutoff is None or target.get("confidence") is None or target["confidence"] >= cutoff)
               and (kinds is None or ("waste" if target["label"] == "waste" else "canopy") in kinds) and target["id"] not in closed}
    started = time.perf_counter()
    line, rows, report = solver.request_route(
        world["features"], targets, world["token"], Point(start) if start else None, Point(end) if end else None,
        include, solver.HOP_PENALTY_M if hops else None)
    return {"line": line, "rows": rows, "report": report, "world": world["name"], "compute_s": round(time.perf_counter() - started, 2)}


async def _read_body(request: Request, what: str, limit: int = MAX_ROUTE_BODY) -> bytes:
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(status_code=413, detail=f"A {what} request is at most {limit} bytes")
    return body


async def _request(request: Request) -> tuple[RouteRequest, dict[str, Any]]:
    body = await _read_body(request, "route")
    try:
        query = RouteRequest.model_validate_json(body or b"{}")
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False, include_context=False, include_input=False)) from error
    try:
        path = scene_path()
        solved = await run_in_threadpool(_solve, str(path), _version(path), _projected(query.start), _projected(query.end),
                                         query.scope, query.min_confidence, query.hops, frozenset(query.kinds) if query.kinds else None,
                                         points.closed_ids() if query.open_only else frozenset())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return query, solved


def _point(point: Point, snapped_m: float) -> dict[str, float]:
    lon, lat = _TO_DISPLAY.transform(point.x, point.y)
    return {"easting": round(point.x, 3), "northing": round(point.y, 3), "lon": round(lon, 7), "lat": round(lat, 7), "snapped_m": round(snapped_m, 2)}


@app.post("/api/route")
async def post_route(request: Request) -> dict[str, Any]:
    """A route on request: from `start` to `end` (default: the organizer START, closed) over the site's or one
    field's targets at a POI confidence cutoff, with or without row hops. The line is in lon/lat for display;
    every metre is measured in EPSG:32635. 422 for an invalid request or an endpoint off the walkable space."""
    query, solved = await _request(request)
    line, rows, report = solved["line"], solved["rows"], solved["report"]
    coords = list(line.coords)
    summary = {
        "length_m": round(line.length, 2), "targets": report["targets"], "visited": report["visited"],
        "unreachable": sum(item["status"] == "unreachable" for item in rows), "over_budget": sum(item["status"] == "over_budget" for item in rows),
        "outside_share": round(report["outside_share"], 5), "robust_outside_share": round(report["robust_outside_share"], 5),
        "hops": report["hops"], "hop_m": round(report["hop_m"], 2), "forbidden_m": round(report["forbidden_m"], 3),
        "canopy_m": round(report["canopy_m"], 3), "legal": bool(report["legal"]), "scores": bool(report["scores"]),
        "closed": bool(report["closed"]), "open": report["open"],
        "outside_budget_m": round(report["budget"] * line.length, 1), "robust_outside_m": round(report["robust_outside_m"], 1),
    }
    return {
        "route": {"type": "Feature", "geometry": mapping(transform(_TO_DISPLAY.transform, line)),
                  "properties": {"label": "route", "source": "request", "scope": "site" if query.scope == "site" else "field",
                                 "vineyard_id": "" if query.scope == "site" else query.scope, "min_confidence": query.min_confidence, **summary}},
        **summary,
        "start": _point(Point(coords[0]), report["start_snap_m"]), "end": _point(Point(coords[-1]), report["end_snap_m"]),
        "scope": query.scope, "min_confidence": query.min_confidence, "hop_penalty_m": report["hop_penalty"], "world": solved["world"],
        "compute_s": solved["compute_s"],
        # visited / over_budget (needs_outside_m: what reaching it would add outside the lanes) / unreachable
        "target_status": [{key: item[key] for key in ("id", "status", "distance_m", "needs_outside_m", "reason")} for item in rows],
    }


@app.post("/api/route/geojson")
async def post_route_geojson(request: Request) -> Response:
    """The same route as /api/route in the official route.geojson format: one LineString in EPSG:32635 with
    `length_m`, and the organizers' `crs` member."""
    _, solved = await _request(request)
    line = LineString([(round(x, 3), round(y, 3)) for x, y in solved["line"].coords])
    collection = {"type": "FeatureCollection", "crs": ORGANIZER_CRS,
                  "features": [{"type": "Feature", "geometry": mapping(line), "properties": {"length_m": round(line.length, 3)}}]}
    return Response(json.dumps(collection, indent=2) + "\n", media_type="application/geo+json",
                    headers={"Content-Disposition": 'attachment; filename="route.geojson"'})


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: points.Role
    status: points.Status
    note: Annotated[str, Field(max_length=500)] = ""


@app.get("/api/points")
def get_points() -> dict[str, Any]:
    """Every visit point's status record (a point with none is `open`) and every field's score; one field is one farmer."""
    return points.summary()


@app.post("/api/points/{point_id}/status")
async def post_point_status(point_id: str, request: Request) -> dict[str, Any]:
    """Append a status event. A farmer claims (`in_progress`, `fixed`, `false_positive`, `open`); an inspector's
    event is a review: the current status approves it, `open` rejects a claim. No authentication: the role is
    whatever the client says (a single-site demo). 404 for an unknown point."""
    try:
        query = StatusRequest.model_validate_json(await _read_body(request, "status") or b"{}")
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False, include_context=False, include_input=False)) from error
    try:
        return await run_in_threadpool(points.set_status, point_id, query.role, query.status, query.note)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"Unknown point {point_id!r}") from error


_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if _WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="web")
