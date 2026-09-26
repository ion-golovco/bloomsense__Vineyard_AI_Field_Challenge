"""Vine canopy network: a U-Net in plain torch, randomly initialised (no downloaded weights) and self-trained on the
rule-based canopy (`marcaj.canopy`). It predicts canopy per pixel at the tiles' native 0.025 m; a second, auxiliary
output (the band between two plants) is trained but unused. The mask goes through `canopy.canopy_mask(..., green=)`,
alone or ANDed with the rule's colour mask, and the rule post-processing (axis refit and filters, +-0.3 m tube,
closing, fill, neck split, minimum area, inset) runs unchanged. Needs the `sam` dependency group (torch).

Trained (research/probes/canopy_net_train.py, seed 0, Apple MPS) on the rule labels of 118 vineyard tiles, holding out
the two organizer tiles and their neighbours. The loss is masked beyond 1.5 m of any row axis, near the colour
threshold and on green outside the tube (grassed plots put the teacher's tube on inter-rows). Background between two
plants weighs 6x. Measured on the two organizer tiles (a sanity check, not a holdout; research/notes/canopy_net.md):
- Network alone: judge canopy 0.843 (union IoU 0.807, F1 0.896), 402 / 256 canopies against 399 / 251.
- `combine="and", threshold=0.2`: 0.855 (0.821, 0.905), a tie with the rules alone (0.855; 0.822, 0.905). With the
  recall rules of 26 September (`canopy` weak stretches, per-row young rule): 0.851, rules alone 0.852; thresholds
  0.05-0.3 give 0.851 (research/probes/canopy_recall_trials.py).
5 levels, 1,964,546 parameters, 6,000 steps x 16 crops of 256 px in 834 s. Seed spread about 0.003.
Inference on MPS takes 0.8 s per tile with 4 flips, 0.2 s without; without flips "and" still scores 0.855 and the
network alone 0.840. The full run over 131 vineyard tiles takes 174 s with flips, 62 s for the rules alone."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.transform import Affine

from marcaj import canopy
from marcaj.canopy import CanopyParams, RowSet
from marcaj.tiles import REPO_ROOT, Tile

WEIGHTS = REPO_ROOT / "models" / "canopy_net.pt"  # v14 of research/probes/canopy_net_train.py, 7.9 MB
CROP_PX = 1024  # inference window; each is read with MARGIN_PX of context on every side
MARGIN_PX = 64


def inputs(x: Any) -> Any:
    """(B, 3, H, W) float tensor of 0-255 RGB -> (B, 6, H, W): RGB scaled to about [-1, 1], then chromaticity r, g, b / (r + g + b)."""
    import torch

    return torch.cat([x / 127.5 - 1.0, (x / x.sum(1, keepdim=True).clamp(min=1.0) - 1 / 3) * 6.0], 1)


def build(width: int = 16, depth: int = 4, outputs: int = 2) -> Any:
    """U-Net: `depth` levels of two 3x3 conv + BatchNorm + ReLU, widths doubling from `width`, bilinear upsampling."""
    import torch
    from torch import nn

    def block(a: int, b: int) -> nn.Sequential:
        return nn.Sequential(nn.Conv2d(a, b, 3, padding=1, bias=False), nn.BatchNorm2d(b), nn.ReLU(inplace=True),
                             nn.Conv2d(b, b, 3, padding=1, bias=False), nn.BatchNorm2d(b), nn.ReLU(inplace=True))

    class UNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            widths = [width * 2**i for i in range(depth)]
            self.down = nn.ModuleList([block(6, widths[0])] + [block(a, b) for a, b in zip(widths, widths[1:])])
            self.up = nn.ModuleList([block(b + a, a) for a, b in zip(widths[:-1], widths[1:])])
            self.head = nn.Conv2d(widths[0], outputs, 1)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            skips = []
            for i, layer in enumerate(self.down):
                x = layer(x if i == 0 else nn.functional.max_pool2d(x, 2))
                skips.append(x)
            for layer, skip in zip(reversed(self.up), reversed(skips[:-1])):
                x = layer(torch.cat([nn.functional.interpolate(x, size=skip.shape[-2:], mode="bilinear", align_corners=False), skip], 1))
            return self.head(x)

    return UNet()


def device() -> Any:
    import torch

    return torch.device("mps" if torch.backends.mps.is_available() else "cpu")


@lru_cache(maxsize=2)
def load(path: Path = WEIGHTS) -> Any:
    import torch

    saved = torch.load(path, map_location="cpu", weights_only=True)
    model = build(**saved["config"])
    model.load_state_dict(saved["state_dict"])
    return model.to(device()).eval()


def probabilities(rgb: np.ndarray, model: Any, flips: bool = True) -> np.ndarray:
    """(outputs, H, W) float32 sigmoid probabilities for a (3, H, W) tile, in overlapping windows; with `flips`
    the mean over the four horizontal/vertical flips."""
    import torch

    height, width = rgb.shape[1:]
    padded = np.pad(rgb, ((0, 0), (MARGIN_PX, MARGIN_PX), (MARGIN_PX, MARGIN_PX)), mode="reflect")
    out = np.zeros((model.head.out_channels, height, width), np.float32)
    variants = [(), (-1,), (-2,), (-2, -1)] if flips else [()]
    with torch.inference_mode():
        for r0 in range(0, height, CROP_PX):
            for c0 in range(0, width, CROP_PX):
                r1, c1 = min(r0 + CROP_PX, height), min(c0 + CROP_PX, width)
                x = inputs(torch.from_numpy(padded[None, :, r0:r1 + 2 * MARGIN_PX, c0:c1 + 2 * MARGIN_PX]).to(device()).float())
                total = torch.zeros((1, out.shape[0], *x.shape[-2:]), device=x.device)
                for dims in variants:
                    y = torch.sigmoid(model(x.flip(dims) if dims else x))
                    total += y.flip(dims) if dims else y
                out[:, r0:r1, c0:c1] = (total / len(variants))[0, :, MARGIN_PX:-MARGIN_PX, MARGIN_PX:-MARGIN_PX].cpu().numpy()
    return out


def tile_canopies(tile: Tile, rows: list[RowSet], params: CanopyParams = CanopyParams(), model: Any = None,
                  threshold: float = 0.5, combine: str = "net", flips: bool = True,
                  rgb: tuple[np.ndarray, Affine] | None = None, prob: np.ndarray | None = None) -> list[dict[str, Any]]:
    """`canopy.tile_canopies` with the network's canopy mask in place of the colour threshold. `combine`: "net" uses
    canopy probability > `threshold` alone, "and" keeps the rule's colour pixels the network accepts, "or" takes either.
    `prob` reuses `probabilities` already computed; a tile no row crosses is not read."""
    if not any(axis.intersects(tile.bounds) for plot in rows for axis in plot.axes):
        return []
    image, transform = rgb or canopy.read_rgb(tile)
    return canopy.tile_canopies(tile, rows, params, (image, transform), mask(image, model, params, threshold, combine, flips, prob))


def mask(rgb: np.ndarray, model: Any = None, params: CanopyParams = CanopyParams(), threshold: float = 0.5,
         combine: str = "net", flips: bool = True, prob: np.ndarray | None = None) -> np.ndarray:
    """The per-pixel canopy mask for `canopy.canopy_mask(..., green=...)`: see `tile_canopies`."""
    prob = probabilities(rgb, model or load(), flips) if prob is None else prob
    green = prob[0] > threshold
    if combine == "net":
        return green
    colour = canopy.green_mask(rgb, params)
    return colour & green if combine == "and" else colour | green
