"""Canopy outlines refined by SAM 2.1 tiny: Meta's published `facebook/sam2.1-hiera-tiny` weights (Apache-2.0)
through Hugging Face transformers, prompted with a box around each rule-based canopy from `marcaj.canopy`.
Needs the `sam` dependency group: uv sync --inexact --group sam.

Not used by `marcaj.predict`: on the reference tiles it scores below the rule-based canopy it refines
(best 0.555 against 0.668, `research/probes/sam_probe.py`), because SAM outlines plant and cast shadow as one
object (median 1.5x the area). About 14 s per vineyard tile on Apple MPS, 36 s with three masks."""

from functools import lru_cache
from typing import Any

import numpy as np
from rasterio.features import shapes
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

from marcaj.tiles import PIXEL_M

MODEL_ID = "facebook/sam2.1-hiera-tiny"
CROP_PX = 1024  # SAM encodes 1024 px, so a 2048 px tile is four crops at the native 0.025 m


@lru_cache(maxsize=1)
def _model() -> tuple[Any, Any, Any]:
    import torch
    from transformers import Sam2Model, Sam2Processor

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    return Sam2Processor.from_pretrained(MODEL_ID), Sam2Model.from_pretrained(MODEL_ID).to(device).eval(), device


def _polygon(mask: np.ndarray, transform: Affine, simplify_m: float) -> Polygon | None:
    labels, count = ndimage.label(mask)
    if not count:
        return None
    biggest = labels == 1 + int(np.argmax(ndimage.sum(mask, labels, range(1, count + 1))))
    filled = ndimage.binary_fill_holes(biggest)
    pieces = [shape(g) for g, value in shapes(filled.astype(np.uint8), mask=filled, transform=transform) if value]
    outline = unary_union(pieces).simplify(simplify_m)
    parts = [p for p in getattr(outline, "geoms", [outline]) if p.geom_type == "Polygon"]
    return Polygon(max(parts, key=lambda p: p.area).exterior) if parts else None


def refine(rgb: np.ndarray, transform: Affine, polygons: list[Polygon], margin_m: float = 0.1, simplify_m: float = 0.025,
           batch: int = 64, pick_closest: bool = False) -> list[Polygon | None]:
    """SAM's mask for each polygon's box (expanded by `margin_m`), kept inside that box. None where SAM finds nothing.
    With `pick_closest`, SAM returns its three candidate masks and the one closest (IoU) to the prompt polygon is kept:
    the single mask tends to take the plant's cast shadow too."""
    import torch

    processor, model, device = _model()
    margin = margin_m / PIXEL_M
    out: list[Polygon | None] = [None] * len(polygons)
    height, width = rgb.shape[1:]
    for r0 in range(0, height, CROP_PX):
        for c0 in range(0, width, CROP_PX):
            crop_transform = transform * Affine.translation(c0, r0)
            inverse = ~crop_transform
            members = []
            for i, polygon in enumerate(polygons):
                col, row = inverse * polygon.centroid.coords[0]
                if 0 <= col < CROP_PX and 0 <= row < CROP_PX:
                    xs, ys = zip(*(inverse * xy for xy in polygon.exterior.coords))
                    members.append((i, [max(min(xs) - margin, 0), max(min(ys) - margin, 0),
                                        min(max(xs) + margin, CROP_PX - 1), min(max(ys) + margin, CROP_PX - 1)]))
            if not members:
                continue
            image = np.ascontiguousarray(rgb[:, r0:r0 + CROP_PX, c0:c0 + CROP_PX].transpose(1, 2, 0))
            with torch.inference_mode():
                embeddings = model.get_image_embeddings(processor(images=image, return_tensors="pt")["pixel_values"].to(device))
                for start in range(0, len(members), batch):
                    chunk = members[start:start + batch]
                    inputs = processor(images=image, input_boxes=[[box for _, box in chunk]], return_tensors="pt")
                    outputs = model(image_embeddings=embeddings, input_boxes=inputs["input_boxes"].to(device), multimask_output=pick_closest)
                    masks = processor.post_process_masks(outputs.pred_masks.cpu(), inputs["original_sizes"])[0].numpy()
                    for (i, (x0, y0, x1, y1)), options in zip(chunk, masks):
                        inside = np.zeros(options.shape[1:], dtype=bool)
                        inside[int(y0):int(np.ceil(y1)) + 1, int(x0):int(np.ceil(x1)) + 1] = True
                        outlines = [o for o in (_polygon(mask & inside, crop_transform, simplify_m) for mask in options) if o is not None]
                        closeness = lambda o: o.intersection(polygons[i]).area / o.union(polygons[i]).area
                        out[i] = max(outlines, key=closeness) if outlines else None
    return out
