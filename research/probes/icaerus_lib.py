"""ICAERUS AI4Leafhopper vine segmentation (YOLOv9 gelan-c-seg, Zenodo record 14610756) run through ultralytics.
Research only, in its own venv (data/generated/work/icaerus/.venv: ultralytics AGPL-3.0, not in the project lock).
The checkpoint is pickled from the WongKinYiu/yolov9 code (`models.common`, `models.yolo`), which ultralytics refuses to
load. We never import or copy that code: a restricted unpickler turns every `models.*` class into a bare nn.Module
holding the pickled parameters, and the state_dict is loaded into ultralytics' own yolov9c-seg (same layer names,
`legacy` heads). Evaluation only: nothing here reads data/review or trains on Sireț3."""

import pickle
import sys
import zipfile
from pathlib import Path

import numpy as np
import torch
from torch import nn

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "data" / "generated" / "work" / "icaerus"
WEIGHTS = WORK / "best.pt"
TILES = REPO / "data" / "raw" / "marcaj" / "tiles"
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
GRID_LEFT, GRID_TOP, TILE_M, PX = 628992.0, 5221222.4, 51.2, 0.025


class _Stub(nn.Module):
    def __setstate__(self, state):
        self.__dict__.update(state)


class _Unpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module.startswith("models"):
            return type(name, (_Stub,), {})
        return super().find_class(module, name)


def _stub_state() -> tuple[dict, dict]:
    """(state_dict, checkpoint metadata) of the yolov9-repo checkpoint without its code."""
    unpickler = torch.serialization._load  # noqa: SLF001 - torch.load's zip reader with our pickle module
    mod = type(sys)("stub_pickle")
    mod.Unpickler, mod.load = _Unpickler, pickle.load
    mod.__dict__.update({k: getattr(pickle, k) for k in dir(pickle) if not k.startswith("__") and k not in ("Unpickler", "load")})
    ckpt = torch.load(WEIGHTS, map_location="cpu", pickle_module=mod, weights_only=False)
    model = ckpt.get("ema") or ckpt["model"]
    meta = {k: v for k, v in ckpt.items() if k not in ("model", "ema", "optimizer")}
    meta["names"] = getattr(model, "names", None)
    meta["yaml"] = getattr(model, "yaml", None)
    return model.float().state_dict(), meta


def load_model():
    """An ultralytics YOLO wrapper around yolov9c-seg carrying the ICAERUS weights (strict key and shape match)."""
    from ultralytics import YOLO
    from ultralytics.nn.modules.head import Detect
    from ultralytics.nn.tasks import SegmentationModel
    state, meta = _stub_state()
    Detect.legacy = True  # the yolov9-repo head: cv3 = Conv, Conv, Conv2d
    net = SegmentationModel("yolov9c-seg.yaml", nc=len(meta["names"]), verbose=False)
    net.model[-1].proto.upsample = nn.Upsample(scale_factor=2, mode="nearest")  # the yolov9-repo Proto, no ConvTranspose
    net.load_state_dict(state, strict=True)
    net.names = dict(meta["names"])
    model = YOLO("yolov9c-seg.yaml", task="segment", verbose=False)
    model.model = net
    model.overrides = {"task": "segment", "imgsz": 640}
    return model.to(DEVICE), meta


def read_tile(name: str) -> np.ndarray:
    """(2048, 2048, 3) RGB uint8 of a tile, read only."""
    import rasterio
    with rasterio.open(TILES / name) as source:
        return source.read().transpose(1, 2, 0)


def tile_origin(name: str) -> tuple[float, float]:
    r, c = int(name[8:11]), int(name[13:16])
    return GRID_LEFT + TILE_M * c, GRID_TOP - TILE_M * r


def detect(model, rgb: np.ndarray, zoom: float = 1.0, overlap_m: float = 2.0, conf: float = 0.1, batch: int = 16,
           max_det: int = 1000, iou: float = 0.5) -> list[tuple[np.ndarray, float]]:
    """Instances over a whole tile: windows of 640 / zoom native px (the network sees the ground at 2.5 / zoom cm/px),
    `overlap_m` apart at the edges; a detection is kept by the one window whose core (the window less half the overlap)
    holds its box centre, so no cross-window NMS is needed. Returns (polygon ring in tile px, confidence)."""
    size = rgb.shape[0]
    window = int(round(640 / zoom))
    margin = int(round(overlap_m / PX / 2))
    step = max(window - 2 * margin, 1)
    starts = list(range(0, max(size - window, 0) + 1, step))
    if starts[-1] + window < size:
        starts.append(size - window)
    crops = [(x, y) for y in starts for x in starts]
    out = []
    for k in range(0, len(crops), batch):
        chunk = crops[k:k + batch]
        images = [np.ascontiguousarray(rgb[y:y + window, x:x + window, ::-1]) for x, y in chunk]  # ultralytics wants BGR
        for (x, y), result in zip(chunk, model.predict(images, imgsz=640, conf=conf, iou=iou, max_det=max_det, verbose=False, retina_masks=False)):
            if result.masks is None:
                continue
            lo_x = margin if x else 0
            lo_y = margin if y else 0
            hi_x = window - margin if x + window < size else window
            hi_y = window - margin if y + window < size else window
            centres = result.boxes.xywh.cpu().numpy()[:, :2]
            for ring, score, (cx, cy) in zip(result.masks.xy, result.boxes.conf.cpu().numpy(), centres):
                if lo_x <= cx < hi_x and lo_y <= cy < hi_y and len(ring) >= 3:
                    out.append((ring + (x, y), float(score)))
    return out


def to_world(ring: np.ndarray, name: str) -> np.ndarray:
    left, top = tile_origin(name)
    return np.column_stack([left + ring[:, 0] * PX, top - ring[:, 1] * PX])


def save_instances(path: Path, found: list[tuple[np.ndarray, float]], seconds: float) -> None:
    rings = [ring.astype(np.float32) for ring, _ in found]
    np.savez_compressed(path, xy=np.concatenate(rings) if rings else np.zeros((0, 2), np.float32),
                        ends=np.cumsum([len(r) for r in rings]).astype(np.int64), conf=np.array([s for _, s in found], np.float32),
                        seconds=np.float32(seconds))


def load_instances(path: Path) -> tuple[list[np.ndarray], np.ndarray]:
    """(rings in tile px, confidences) as saved by icaerus_run.py."""
    data = np.load(path)
    return np.split(data["xy"], data["ends"][:-1]) if len(data["ends"]) else [], data["conf"]
