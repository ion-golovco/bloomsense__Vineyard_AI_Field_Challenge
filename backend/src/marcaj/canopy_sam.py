"""SAM 2.1 tiny point prompts per plant along the row, on the vine canopy of `marcaj.canopy` / `marcaj.canopy_net`.
Off by default: `marcaj.predict` does not call it. Model: Meta's `facebook/sam2.1-hiera-tiny` (Apache-2.0) through
Hugging Face transformers. Needs the `sam` dependency group.

`split_tile`: a canopy piece longer than `long_m` along the row is cut into n = round(length / planting distance)
plants (the rules: one polygon per plant, touching plants split at the in-row planting distance). Each plant gets a
positive point at its centre on the piece and negative points at its neighbours' centres; each pixel of the piece goes
to the plant whose SAM logit is highest (a plant may claim pixels at most one planting distance from its centre), so
the cuts follow the visible boundaries SAM finds instead of equal lengths. `mode="separate"` first asks SAM, with the
centre alone, whether two neighbouring plants are one object (its mask covers the neighbour's centre), and cuts only
between plants it sees apart. SAM only moves pixels between plants: the union stays the input piece. The planting
distance is the median centre spacing of consecutive short pieces (under `long_m`) on one row of one plot on one tile,
or `default_plant_m`.

Measured in research/probes/sam_v2_*.py (research/notes/canopy_sam.md)."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from rasterio.features import shapes
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import Polygon, mapping, shape

from marcaj import canopy
from marcaj.canopy import CanopyParams
from marcaj.tiles import PIXEL_M

LOWRES = 256  # SAM's mask logits for a 1024 px input
MODEL_ID = "facebook/sam2.1-hiera-tiny"


@dataclass(frozen=True)
class SamParams:
    long_m: float = 2.5          # pieces longer than this along the row are prompted per plant
    plant_m: float = 0.0         # planting distance; 0 = estimated per row of a plot on a tile
    default_plant_m: float = 1.7  # ...when fewer than `min_pairs` short-piece pairs give it
    min_pairs: int = 6
    mode: str = "assign"         # "assign": every long piece is n plants; "separate": cut only where SAM sees two objects; "equal": no SAM, equal lengths
    negatives: bool = True       # neighbours' centres as negative points
    min_part_m2: float = 0.2     # a part smaller than this joins its neighbour
    crop_px: int = 1024          # SAM window on the tile (upsampled to 1024 when smaller)
    model_id: str = MODEL_ID


class TileSam:
    """SAM on one tile: windows of `crop_px` at half-window steps, encoded when first used; each prompt goes to the
    window where its first point lies farthest from the edge."""

    def __init__(self, rgb: np.ndarray, crop_px: int = 1024, model_id: str = MODEL_ID) -> None:
        self.rgb, self.crop, self.model_id = rgb, crop_px, model_id
        height, width = rgb.shape[1:]
        starts = lambda n: sorted(set(list(range(0, n - crop_px + 1, crop_px // 2)) + [n - crop_px]))
        self.rows, self.cols = starts(height), starts(width)
        self._embeddings: dict[tuple[int, int], Any] = {}

    def origin(self, row: float, col: float) -> tuple[int, int]:
        margin = lambda start, p: min(p - start, start + self.crop - p)
        return max(self.rows, key=lambda r: margin(r, row)), max(self.cols, key=lambda c: margin(c, col))

    def _embed(self, origin: tuple[int, int]) -> Any:
        if origin not in self._embeddings:
            import torch

            processor, model, device = _model(self.model_id)
            r0, c0 = origin
            image = np.ascontiguousarray(self.rgb[:, r0:r0 + self.crop, c0:c0 + self.crop].transpose(1, 2, 0))
            with torch.inference_mode():
                self._embeddings[origin] = model.get_image_embeddings(processor(images=image, return_tensors="pt")["pixel_values"].to(device))
        return self._embeddings[origin]

    def decode(self, origin: tuple[int, int], points: np.ndarray, labels: np.ndarray, multimask: bool = False,
               batch: int = 64) -> tuple[np.ndarray, np.ndarray]:
        """Mask logits (objects, masks, 256, 256) and predicted IoU (objects, masks) for point prompts in tile pixel
        (col, row) coordinates, `points` (objects, points, 2), `labels` 1 / 0 / -1 (padding)."""
        import torch

        _, model, device = _model(self.model_id)
        embeddings = self._embed(origin)
        scale = 1024 / self.crop
        local = (points - [origin[1], origin[0]]) * scale
        logits, scores = [], []
        with torch.inference_mode():
            for start in range(0, len(points), batch):
                out = model(image_embeddings=embeddings,
                            input_points=torch.tensor(local[None, start:start + batch], dtype=torch.float32, device=device),
                            input_labels=torch.tensor(labels[None, start:start + batch], dtype=torch.int64, device=device),
                            multimask_output=multimask)
                logits.append(out.pred_masks[0].float().cpu().numpy())
                scores.append(out.iou_scores[0].float().cpu().numpy())
        return np.concatenate(logits), np.concatenate(scores)

    def sample(self, logit: np.ndarray, origin: tuple[int, int], rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        """`logit` (256, 256) of window `origin` at tile pixels; -inf outside the window."""
        r, c = rows - origin[0], cols - origin[1]
        inside = (r >= 0) & (r < self.crop) & (c >= 0) & (c < self.crop)
        out = np.full(len(rows), -np.inf, np.float32)
        f = LOWRES / self.crop
        out[inside] = ndimage.map_coordinates(logit, [(r[inside] + 0.5) * f - 0.5, (c[inside] + 0.5) * f - 0.5], order=1, mode="nearest")
        return out


@lru_cache(maxsize=1)
def _model(model_id: str = MODEL_ID) -> tuple[Any, Any, Any]:
    import torch
    from transformers import Sam2Model, Sam2Processor

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    # the weights must already be in the Hugging Face cache: prediction never downloads
    return (Sam2Processor.from_pretrained(model_id, local_files_only=True),
            Sam2Model.from_pretrained(model_id, local_files_only=True).to(device).eval(), device)


def direction(polygon: Polygon) -> np.ndarray:
    """Unit vector along the long side of the minimum rotated rectangle (the row direction for a long piece)."""
    corners = np.asarray(polygon.minimum_rotated_rectangle.exterior.coords)
    edge = max(corners[1] - corners[0], corners[2] - corners[1], key=lambda e: float(np.hypot(*e)))
    return edge / np.hypot(*edge)


def planting_distances(polygons: list[Polygon], keys: list[str], params: SamParams) -> list[float]:
    """Per polygon: the median centre spacing of consecutive pieces shorter than `long_m` on its row (same key, across-row
    positions within 0.6 m), spacings of 0.7-3.5 m only; `default_plant_m` with fewer than `min_pairs` of them."""
    if params.plant_m:
        return [params.plant_m] * len(polygons)
    out = [params.default_plant_m] * len(polygons)
    lengths = np.array([canopy.length_m(p) for p in polygons])
    centres = np.array([p.centroid.coords[0] for p in polygons]) if polygons else np.zeros((0, 2))
    for key in set(keys):
        members = np.array([i for i, k in enumerate(keys) if k == key])
        long = [polygons[i] for i in members if lengths[i] > 3.0]
        if not long:
            continue
        d = np.median([direction(p) * np.sign(direction(p)[0] or 1) for p in long], axis=0)
        d /= np.hypot(*d)
        u, v = centres[members] @ d, centres[members] @ [-d[1], d[0]]
        order = np.argsort(v)
        for row in np.split(order, np.flatnonzero(np.diff(v[order]) > 0.6) + 1):
            row = row[np.argsort(u[row])]
            short = lengths[members[row]] <= params.long_m
            gaps = np.diff(u[row])[short[:-1] & short[1:]]
            gaps = gaps[(gaps > 0.7) & (gaps < 3.5)]
            if len(gaps) >= params.min_pairs:
                for i in members[row]:
                    out[i] = float(np.median(gaps))
    return out


def _nearest_fill(labels: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Pixels of `mask` with label 0 take the label of the nearest labelled pixel."""
    if not (labels > 0).any():
        return labels
    index = ndimage.distance_transform_edt(labels == 0, return_distances=False, return_indices=True)
    return np.where(mask, labels[tuple(index)], 0)


def _clean(labels: np.ndarray, mask: np.ndarray, min_px: int) -> np.ndarray:
    """One connected part per label (the largest; the rest join their nearest neighbour), then labels under `min_px`
    pixels join their neighbour too."""
    out = np.zeros_like(labels)
    for value in np.unique(labels[labels > 0]):
        parts, count = ndimage.label(labels == value, canopy.EIGHT)
        if count:
            out[parts == 1 + int(np.argmax(ndimage.sum(np.ones_like(parts), parts, range(1, count + 1))))] = value
    out = _nearest_fill(out, mask)
    sizes = np.bincount(out.ravel())
    small = [v for v in np.unique(out[out > 0]) if sizes[v] < min_px]
    if small and len(small) < len(np.unique(out[out > 0])):
        out[np.isin(out, small)] = 0
        out = _nearest_fill(out, mask)
    return out


def split_piece(polygon: Polygon, tile_sam: TileSam | None, transform: Affine, shape_: tuple[int, int], plant_m: float,
                params: SamParams = SamParams(), canopy_params: CanopyParams = CanopyParams()) -> list[Polygon]:
    """`polygon` cut into plants (see the module docstring); the polygon itself when it is under 1.5 planting distances."""
    rows, cols = canopy._pixels(polygon, transform, shape_)
    if len(rows) < 10:
        return [polygon]
    x, y = transform * (cols + 0.5, rows + 0.5)
    d = direction(polygon)
    u, v = x * d[0] + y * d[1], -x * d[1] + y * d[0]
    u0, length = u.min(), u.max() - u.min()
    n = int(round(length / plant_m))
    if n < 2:
        return [polygon]
    step = length / n
    mids = u0 + step * (np.arange(n) + 0.5)
    centre = []
    for mid in mids:
        near = np.abs(u - mid) < 0.25 * step
        vm = np.median(v[near]) if near.any() else np.median(v)
        centre.append(int(np.argmin((u - mid) ** 2 + (v - vm) ** 2)))
    centre = np.array(centre)
    equal = np.clip(((u - u0) / step).astype(int), 0, n - 1)
    if params.mode == "equal" or tile_sam is None:
        labels = equal
    else:
        points = np.stack([cols[centre] + 0.5, rows[centre] + 0.5], 1)
        prompts = np.zeros((n, 3, 2), np.float32)
        tags = np.full((n, 3), -1, np.int64)
        prompts[:, 0], tags[:, 0] = points, 1
        if params.negatives:
            prompts[1:, 1], tags[1:, 1] = points[:-1], 0
            prompts[:-1, 2], tags[:-1, 2] = points[1:], 0
        logits = _plant_logits(tile_sam, prompts, tags, rows, cols, rows[centre], cols[centre])
        logits[np.abs(u[None] - mids[:, None]) > step] = -np.inf
        labels = np.where(np.isfinite(logits.max(0)), logits.argmax(0), equal)
        if params.mode == "separate":
            alone = _plant_logits(tile_sam, prompts[:, :1], tags[:, :1], rows, cols, rows[centre], cols[centre], multimask=True)
            same = (alone[np.arange(n - 1), centre[1:]] > 0) | (alone[np.arange(1, n), centre[:-1]] > 0)
            labels = np.concatenate([[0], np.cumsum(~same)])[labels]
    r0, c0 = rows.min(), cols.min()
    window = np.zeros((rows.max() - r0 + 1, cols.max() - c0 + 1), np.int32)
    window[rows - r0, cols - c0] = labels + 1
    window = _clean(window, window > 0, int(params.min_part_m2 / PIXEL_M**2))
    local = transform * Affine.translation(c0, r0)
    parts: dict[int, list] = {}
    for geometry, value in shapes(window, mask=window > 0, connectivity=8, transform=local):
        parts.setdefault(int(value), []).append(shape(geometry))
    out = [p for pieces in parts.values() for p in canopy._parts(pieces, 0.0, canopy_params)]
    return out or [polygon]


def _plant_logits(tile_sam: TileSam, prompts: np.ndarray, tags: np.ndarray, rows: np.ndarray, cols: np.ndarray,
                  prow: np.ndarray, pcol: np.ndarray, multimask: bool = False) -> np.ndarray:
    """(plants, pixels) SAM logits of each plant's prompt at the piece's pixels, each from its own window; with
    `multimask`, the mask SAM scores highest."""
    out = np.full((len(prompts), len(rows)), -np.inf, np.float32)
    groups: dict[tuple[int, int], list[int]] = {}
    for k in range(len(prompts)):
        groups.setdefault(tile_sam.origin(prow[k], pcol[k]), []).append(k)
    for origin, ks in groups.items():
        logit, score = tile_sam.decode(origin, prompts[ks], tags[ks], multimask)
        for j, k in enumerate(ks):
            out[k] = tile_sam.sample(logit[j, int(np.argmax(score[j]))], origin, rows, cols)
    return out


def split_tile(features: list[dict[str, Any]], rgb: np.ndarray, transform: Affine, params: SamParams = SamParams(),
               canopy_params: CanopyParams = CanopyParams()) -> list[dict[str, Any]]:
    """One tile's canopy features with every piece longer than `long_m` replaced by its plants (same properties)."""
    polygons = [shape(f["geometry"]) for f in features]
    long = [i for i, p in enumerate(polygons) if canopy.length_m(p) > params.long_m]
    if not long:
        return features
    plant = planting_distances(polygons, [f["properties"].get("vineyard_id", "") for f in features], params)
    tile_sam = None if params.mode == "equal" else TileSam(rgb, params.crop_px, params.model_id)
    out = [f for i, f in enumerate(features) if i not in set(long)]
    for i in long:
        parts = split_piece(polygons[i], tile_sam, transform, rgb.shape[1:], plant[i], params, canopy_params)
        out += [features[i]] if len(parts) == 1 else \
            [{**features[i], "geometry": mapping(p), "properties": {**features[i]["properties"], "sam": "split"}} for p in parts]
    return out


@dataclass(frozen=True)
class RecallParams:
    plant_m: float = 1.7         # prompt spacing along an uncovered stretch of a row axis
    min_run_m: float = 0.8       # stretches with no canopy within +-`tube_m` at least this long are prompted
    tube_m: float = 0.3
    negative_m: float = 0.9      # negative points this far to each side of the axis (the inter-row)
    min_m2: float = 0.1          # a SAM part in the tube, outside the current canopy, is kept from this area...
    max_m2: float = 2.0          # ...to this
    max_dark: float = 1.0        # ...when at most this share of it is darker than `dark_dn` (mean RGB); 1 = off
    dark_dn: float = 60.0
    min_excess: float = -255.0   # ...and its mean 2g - r - b is at least this
    crop_px: int = 1024
    model_id: str = MODEL_ID


def recall_tile(features: list[dict[str, Any]], axes: list[tuple[Any, str]], rgb: np.ndarray, transform: Affine,
                params: RecallParams = RecallParams(), canopy_params: CanopyParams = CanopyParams()) -> list[dict[str, Any]]:
    """New canopy features on one tile: SAM masks prompted at `plant_m` steps along the stretches of each row axis
    (LineString, vineyard_id) that no canopy covers, kept inside the +-`tube_m` tube and outside the current canopy."""
    from rasterio.features import rasterize

    shape_ = rgb.shape[1:]
    polygons = [shape(f["geometry"]) for f in features]
    covered = rasterize(polygons, out_shape=shape_, transform=transform).astype(bool) if polygons else np.zeros(shape_, bool)
    blocked = ndimage.binary_dilation(covered, iterations=2)
    inverse = ~transform
    prompts, windows = [], []
    for line, vineyard_id in axes:
        if line.length < params.min_run_m:
            continue
        t = np.arange(0.0, line.length, 0.05)
        (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
        d = np.array([x1 - x0, y1 - y0]) / line.length
        normal = np.array([-d[1], d[0]])
        base = np.array([x0, y0]) + t[:, None] * d
        hit = np.zeros(len(t), bool)
        for off in np.linspace(-params.tube_m, params.tube_m, 7):
            c, r = inverse * tuple((base + off * normal).T)
            r, c = np.clip(np.floor(r).astype(int), 0, shape_[0] - 1), np.clip(np.floor(c).astype(int), 0, shape_[1] - 1)
            hit |= covered[r, c]
        edges = np.flatnonzero(np.diff(np.concatenate([[1], hit.astype(int), [1]])))
        for start, stop in zip(edges[::2], edges[1::2]):
            run = (stop - start) * 0.05
            if run < params.min_run_m:
                continue
            n = max(1, int(round(run / params.plant_m)))
            step = run / n
            for k in range(n):
                u = t[start] + step * (k + 0.5)
                point = np.array([x0, y0]) + u * d
                ends = [point - (step / 2 + 0.1) * d, point + (step / 2 + 0.1) * d]
                window = Polygon([ends[0] - params.tube_m * normal, ends[1] - params.tube_m * normal,
                                  ends[1] + params.tube_m * normal, ends[0] + params.tube_m * normal])
                pts = [inverse * tuple(p) for p in (point, point + params.negative_m * normal, point - params.negative_m * normal)]
                prompts.append(pts)
                windows.append((window, vineyard_id))
    if not prompts:
        return []
    tile_sam = TileSam(rgb, params.crop_px, params.model_id)
    points = np.array(prompts, np.float32)
    tags = np.tile(np.array([1, 0, 0], np.int64), (len(points), 1))
    groups: dict[tuple[int, int], list[int]] = {}
    for k, p in enumerate(points):
        groups.setdefault(tile_sam.origin(p[0, 1], p[0, 0]), []).append(k)
    r_, g_, b_ = rgb.astype(np.float32)
    out = []
    for origin, ks in groups.items():
        logit, _ = tile_sam.decode(origin, points[ks], tags[ks])
        for j, k in enumerate(ks):
            window, vineyard_id = windows[k]
            rows, cols = canopy._pixels(window, transform, shape_)
            if not len(rows):
                continue
            keep = (tile_sam.sample(logit[j, 0], origin, rows, cols) > 0) & ~blocked[rows, cols]
            if not keep.any():
                continue
            rows, cols = rows[keep], cols[keep]
            r0, c0 = rows.min(), cols.min()
            image = np.zeros((rows.max() - r0 + 1, cols.max() - c0 + 1), bool)
            image[rows - r0, cols - c0] = True
            parts, count = ndimage.label(image, canopy.EIGHT)
            local = transform * Affine.translation(c0, r0)
            for value in range(1, count + 1):
                pr, pc = np.nonzero(parts == value)
                area = len(pr) * PIXEL_M**2
                if not params.min_m2 <= area <= params.max_m2:
                    continue
                pr, pc = pr + r0, pc + c0
                red, green, blue = r_[pr, pc], g_[pr, pc], b_[pr, pc]
                if ((red + green + blue) / 3 < params.dark_dn).mean() > params.max_dark or (2 * green - red - blue).mean() < params.min_excess:
                    continue
                geometries = [shape(g) for g, v in shapes((parts == value).astype(np.uint8), mask=parts == value, connectivity=8, transform=local) if v]
                out += [{"type": "Feature", "geometry": mapping(p), "properties": {"label": "vineyard", "source": "prediction", "vineyard_id": vineyard_id, "sam": "recall"}}
                        for p in canopy._parts(geometries, params.min_m2, canopy_params)]
    return out
