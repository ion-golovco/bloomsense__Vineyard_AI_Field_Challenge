"""Thin API for the challenge map; processing remains in Python modules."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles

from marcaj.imagery import render_tile
from marcaj.scene import browser_scene, load_projected_scene

app = FastAPI(title="Marcaj Vineyard Map", version="0.1.0")


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


_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if _WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="web")
