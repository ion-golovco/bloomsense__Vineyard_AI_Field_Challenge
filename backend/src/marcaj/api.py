"""Thin API for the challenge map; processing remains in Python modules."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from marcaj.scene import browser_scene, load_projected_scene

app = FastAPI(title="Marcaj Vineyard Map", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/scene")
def get_scene() -> dict:
    return browser_scene(load_projected_scene())


_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if _WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="web")
