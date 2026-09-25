import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds
from shapely.geometry import shape

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
from marcaj.tiles import DATA_DIR, REPO_ROOT

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "data" / "generated"
OUT.mkdir(parents=True, exist_ok=True)
DATA = DATA_DIR
study = shape(json.loads((DATA / "02_route/study_area.geojson").read_text())["features"][0]["geometry"])
minx, miny, maxx, maxy = (round(v / 10) * 10 for v in study.bounds)
maxx, maxy = maxx + 10, maxy + 10
W, H = int((maxx - minx) / 10), int((maxy - miny) / 10)
grid = from_origin(minx, maxy, 10, 10)

body = json.dumps({"collections": ["sentinel-2-c1-l2a"], "bbox": [28.695, 47.108, 28.728, 47.135],
                   "datetime": "2025-03-01T00:00:00Z/2025-10-31T23:59:59Z", "limit": 200,
                   "query": {"eo:cloud_cover": {"lt": 15}}}).encode()
request = urllib.request.Request("https://earth-search.aws.element84.com/v1/search", body, {"Content-Type": "application/json"})
items = sorted(json.load(urllib.request.urlopen(request, timeout=60))["features"], key=lambda i: i["properties"]["datetime"])
chosen, last = [], None
for item in items:
    day = item["properties"]["datetime"][:10]
    if last is None or (np.datetime64(day) - np.datetime64(last)).astype(int) >= 12:
        chosen.append(item)
        last = day


def read(href, resampling=Resampling.nearest):
    with rasterio.open(href) as src:
        out = np.zeros((H, W), np.float32)
        reproject(rasterio.band(src, 1), out, dst_transform=grid, dst_crs="EPSG:32635", resampling=resampling)
        return out


started = time.perf_counter()
series = []
for item in chosen:
    a = item["assets"]
    red, nir, scl = read(a["red"]["href"]), read(a["nir"]["href"]), read(a["scl"]["href"])
    clear = np.isin(scl, [4, 5, 6, 7]) & (red > 0) & (nir > 0)
    red, nir = red * 1e-4 - 0.1, nir * 1e-4 - 0.1
    ndvi = np.where(clear, (nir - red) / np.maximum(nir + red, 1e-6), np.nan)
    series.append((item["properties"]["datetime"][:10], ndvi))
print(f"{len(series)} dates read in {time.perf_counter() - started:.1f} s: {[d for d, _ in series]}")

inside = rasterize([study], out_shape=(H, W), transform=grid).astype(bool)
plots = [shape(f["geometry"]) for f in json.loads((REPO_ROOT / "data" / "generated" / "predictions.geojson").read_text())["features"]]
vine = rasterize([p.buffer(-5) for p in plots if not p.buffer(-5).is_empty], out_shape=(H, W), transform=grid).astype(bool) & inside
near = rasterize([p.buffer(15) for p in plots], out_shape=(H, W), transform=grid).astype(bool)
other = inside & ~near

names = sorted((DATA / "tiles").glob("*.tif"))
green = np.full((H, W), np.nan, np.float32)
for path in names:
    with rasterio.open(path) as src:
        rgb = src.read(out_shape=(3, 512, 512)).astype(np.float32)
        t = src.transform * src.transform.scale(4, 4)
    total = rgb.sum(0)
    chroma = rgb / np.maximum(total, 1)
    exg = 2 * chroma[1] - chroma[0] - chroma[2]
    frac = np.where(total > 30, (exg > 0.08).astype(np.float32), np.nan)
    dst = np.full((H, W), np.nan, np.float32)
    reproject(frac, dst, src_transform=t, src_crs="EPSG:32635", dst_transform=grid, dst_crs="EPSG:32635",
              resampling=Resampling.average, src_nodata=np.nan, dst_nodata=np.nan)
    green = np.where(np.isnan(green), dst, green)

print(f"pixels: vineyard core {vine.sum()}, other land {other.sum()}")
print("date        vine NDVI  other NDVI  gap   | corr(NDVI, drone green) in vineyard / all")
for day, ndvi in series:
    v, o = np.nanmedian(ndvi[vine]), np.nanmedian(ndvi[other])
    ok_v = vine & ~np.isnan(ndvi) & ~np.isnan(green)
    ok_a = inside & ~np.isnan(ndvi) & ~np.isnan(green)
    cv = np.corrcoef(ndvi[ok_v], green[ok_v])[0, 1] if ok_v.sum() > 30 else np.nan
    ca = np.corrcoef(ndvi[ok_a], green[ok_a])[0, 1] if ok_a.sum() > 30 else np.nan
    print(f"{day}  {v:8.3f}  {o:9.3f}  {v - o:+.3f} | {cv:+.2f} / {ca:+.2f}  (clear {np.mean(~np.isnan(ndvi[inside])):.0%})")
np.savez(OUT / "sentinel.npz", dates=[d for d, _ in series], ndvi=np.stack([n for _, n in series]), vine=vine, other=other, green=green, inside=inside)
