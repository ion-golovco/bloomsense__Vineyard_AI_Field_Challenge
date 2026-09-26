"""Shared harness for the SAM round-two probes (sam_v2_*.py). Evaluation only; probes may read data/review/.

- Base: a snapshot of the 18:13 predictions (work/v4, judge 44.87, canopy 0.8512) at work/sam_v2/base_predictions.geojson.
- `by_tile()`: base canopies grouped by the tile their representative point lies in (they are cut per tile).
- `judge_canopies(canopies)`: full marcaj.judge on the organizer tiles with the base's canopies replaced.
- `label_metrics(canopies)`: the user's long-canopy verdicts (review_tab "canopies").
- `row_cover(canopies, vineyard_ids)`: share of row-axis samples (every 5 cm, base rows) with canopy within +-0.3 m.
"""

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))

from marcaj.tiles import DATA_DIR, PIXEL_M, REPO_ROOT, TILE_PX, load_tiles  # noqa: E402

WORK = REPO_ROOT / "data" / "generated" / "work" / "sam_v2"
BASE = WORK / "base_predictions.geojson"
VERDICTS = REPO_ROOT / "data" / "review" / "verdicts.json"
NAMES = ["siret3_r021_c012.tif", "siret3_r006_c004.tif"]
MID = ["siret3_r019_c011.tif", "siret3_r021_c013.tif", "siret3_r022_c013.tif", "siret3_r020_c012.tif"]
MID_FIELDS = ["V19-11", "V21-13", "V22-13"]
EXTENT = TILE_PX * PIXEL_M


def base_features() -> list[dict[str, Any]]:
    return json.loads(BASE.read_text())["features"]


def tile_of(geometry) -> str:
    point = geometry.representative_point()
    r, c = math.floor((5221222.4 - point.y) / EXTENT), math.floor((point.x - 628992.0) / EXTENT)
    return f"siret3_r{r:03d}_c{c:03d}.tif"


def by_tile(features: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for f in features:
        if f["properties"]["label"] == "vineyard":
            out.setdefault(tile_of(shape(f["geometry"])), []).append(f)
    return out


_SCENE: list | None = None


def judge_canopies(canopies: list[dict[str, Any]]) -> dict[str, Any]:
    from marcaj.cvat import build_scene
    from marcaj.judge import judge

    global _SCENE
    if _SCENE is None:
        tiles = load_tiles()
        _SCENE = build_scene([DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"], [f for f in base_features() if f["properties"]["label"] != "vineyard"], tiles=tiles)["features"]
    report = judge({"type": "FeatureCollection", "crs": "EPSG:32635", "features": _SCENE + [{**f, "properties": {**f["properties"], "source": "prediction"}} for f in canopies]})
    tiles_ = {t["tile"]: t for t in report["tiles"]}
    return {"points": report["points"], "canopy": round(report["scores"]["canopy"], 4), "iou": round(report["canopy_iou"], 4),
            "f1": round(report["canopy_f1"], 4), "counts": {n[7:16]: tiles_[n]["counts"]["vineyard"] for n in NAMES}}


def labels(tab: str = "canopies") -> list[dict[str, Any]]:
    return [v for v in json.loads(VERDICTS.read_text()) if v.get("properties", {}).get("review_tab") == tab]


def length_m(g) -> float:
    c = np.asarray(g.minimum_rotated_rectangle.exterior.coords)
    return float(max(np.hypot(*(c[1] - c[0])), np.hypot(*(c[2] - c[1]))))


def label_metrics(canopies: list[dict[str, Any]], plant_max_m: float = 2.6) -> dict[str, Any]:
    """several: split into >= 2 parts of >= 0.2 m2 inside the label, and all such parts plant-sized (<= `plant_max_m`);
    one_plant: one such part covering >= 50%; not_vine: < 20% covered; vine_lost: several/one under 20% covered."""
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    rows = []
    for lab in labels():
        g = shape(lab["geometry"])
        hits = [int(i) for i in tree.query(g, predicate="intersects") if geoms[i].intersection(g).area >= 0.1 * min(geoms[i].area, g.area)]
        big = [i for i in hits if geoms[i].intersection(g).area >= 0.2]
        rows.append({"answer": lab["properties"]["review_answer"], "covered": sum(geoms[i].intersection(g).area for i in hits) / g.area,
                     "big": len(big), "max_len": max((length_m(geoms[i].intersection(g)) for i in big), default=0.0)})
    sel = lambda a: [r for r in rows if r["answer"] == a]
    sev, one, nv = sel("several"), sel("one_plant"), sel("not_vine")
    return {"several_split": f"{sum(r['big'] >= 2 for r in sev)}/{len(sev)}",
            "several_plant_sized": f"{sum(r['big'] >= 2 and r['max_len'] <= plant_max_m for r in sev)}/{len(sev)}",
            "one_plant_whole": f"{sum(r['big'] == 1 and r['covered'] >= 0.5 for r in one)}/{len(one)}",
            "not_vine_absent": f"{sum(r['covered'] < 0.2 for r in nv)}/{len(nv)}",
            "vine_lost": f"{sum(r['covered'] < 0.2 for r in sev + one)}/{len(sev) + len(one)}"}


def label_tiles() -> list[str]:
    return sorted({lab["properties"]["tile"] for lab in labels()})


def rows_of(features: list[dict[str, Any]], vineyard_ids: list[str] | None = None) -> list[dict[str, Any]]:
    return [f for f in features if f["properties"]["label"] == "row" and (vineyard_ids is None or f["properties"]["vineyard_id"] in vineyard_ids)]


def row_cover(canopies: list[dict[str, Any]], row_features: list[dict[str, Any]], tube_m: float = 0.3, step_m: float = 0.05) -> dict[str, float]:
    """Per vineyard_id: share of samples along the row pieces with a canopy within +-`tube_m` across (7 offsets)."""
    tiles = {t.name: t for t in load_tiles()}
    geoms = [shape(f["geometry"]) for f in canopies]
    tree = STRtree(geoms)
    hit: dict[str, list[int]] = {}
    for f in row_features:
        tile = tiles[f["properties"]["tile"]]
        line = shape(f["geometry"])
        near = [geoms[i] for i in tree.query(tile.bounds, predicate="intersects")]
        covered = rasterize(near, out_shape=(TILE_PX, TILE_PX), transform=from_origin(tile.left, tile.top, PIXEL_M, PIXEL_M)).astype(bool) if near else np.zeros((TILE_PX, TILE_PX), bool)
        t = np.arange(0, line.length, step_m)
        pts = np.array([line.interpolate(d).coords[0] for d in t])
        (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
        normal = np.array([-(y1 - y0), x1 - x0]) / max(line.length, 1e-9)
        ok = np.zeros(len(t), bool)
        for off in np.linspace(-tube_m, tube_m, 7):
            x, y = (pts + off * normal).T
            ok |= covered[np.clip((tile.top - y) / PIXEL_M, 0, TILE_PX - 1).astype(int), np.clip((x - tile.left) / PIXEL_M, 0, TILE_PX - 1).astype(int)]
        s = hit.setdefault(f["properties"]["vineyard_id"], [0, 0])
        s[0] += int(ok.sum())
        s[1] += len(ok)
    return {k: round(a / max(b, 1), 4) for k, (a, b) in sorted(hit.items())}
