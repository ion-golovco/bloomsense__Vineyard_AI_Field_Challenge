"""Trains `marcaj.canopy_net` by self-training on the rule-based canopy snapshot (canopy_net_labels.py) and scores it on
the two organizer reference tiles next to the rule baseline (canopy_net_eval.py). Results: research/notes/canopy_net.md.

Targets per pixel: canopy = the rule `canopy_mask`; second output = the cut band between two rule polygons (a window of
--cut-reach px holds two), or with --instance centre the centre-ness (distance to the edge over its maximum) of teacher
canopies up to 1 m2. The canopy loss is dropped:
- where the colour index is within --doubt of its threshold, within 0.6 m of an axis;
- on green between the kept tube and 0.6 m, or (--no-grass) on all green outside the tube;
- on mask pieces the teacher dropped (under 0.2 m2);
- beyond --zone (1.5 m) of every predicted axis, where rows the teacher missed would count as background.
--gap-weight: background inside the cut band weighs (1 + w) times. Crops of 256 px at native 0.025 m, 70% centred near a
teacher canopy; D4 flips, brightness gain +-jitter (per channel +-jitter/3), contrast, blur and noise.

--teacher W: noisy student, the labels come from network W (through the same rule post-processing) instead of the rules.
--diagnostic: DIAGNOSTIC ONLY, NEVER SHIPPED (CLAUDE.md forbids a model trained on hand-drawn Sirets3 geometry): 2-fold
cross-tile, trained on one organizer tile's hand-drawn canopies and scored on the other.
--evaluate W: the variant grid for saved weights.
Shipped weights (v14): run from backend/:
uv run --frozen --group sam python ../research/probes/canopy_net_train.py --name v14_deep --labels ff679923 --no-grass \
    --gap-weight 5 --cut-reach 9 --jitter 0.2 --doubt 0.1 --depth 5 --steps 6000"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from rasterio.features import shapes
from rasterio.transform import Affine
from scipy import ndimage
from shapely import make_valid
from shapely.geometry import Polygon, mapping, shape
from torch import nn
from torchvision.transforms.functional import gaussian_blur

import canopy_net_eval as ev
from marcaj import canopy, canopy_net
from marcaj.canopy import CanopyParams, excess_green, green_mask, tube
from marcaj.tiles import PIXEL_M

WORK = ev.WORK
CANOPY, IGNORE, CUT, LARGE, OUTSIDE = 1, 2, 4, 8, 16  # label bits; LARGE: in a teacher canopy over SINGLE_M2, no centre
# target; OUTSIDE: beyond --zone of every predicted axis, no loss at all (rows the teacher missed would be false negatives)
SINGLE_M2 = 1.0  # teacher canopies up to this area count as one plant for the centre target (reference p90 1.15-1.87 m2)


def cut_band(instance: np.ndarray, reach: int = 5) -> np.ndarray:
    """Pixels whose `reach` x `reach` window holds two different instance ids."""
    high = ndimage.maximum_filter(instance, reach)
    low = ndimage.minimum_filter(np.where(instance > 0, instance, np.iinfo(instance.dtype).max), reach)
    return (high > 0) & (low < np.iinfo(instance.dtype).max) & (high != low)


def centre(instance: np.ndarray, single_m2: float = SINGLE_M2) -> tuple[np.ndarray, np.ndarray]:
    """Per canopy, distance to its edge (or to a touching canopy) over its largest such distance, as uint8 0-250; and the
    pixels of canopies larger than SINGLE_M2, which get no centre target (the teacher often merges plants there)."""
    cut = cut_band(instance, 3)
    distance = ndimage.distance_transform_edt((instance > 0) & ~cut)
    count = int(instance.max())
    if not count:
        return np.zeros(instance.shape, np.uint8), np.zeros(instance.shape, bool)
    peak = np.concatenate([[1.0], np.maximum(ndimage.maximum(distance, instance, np.arange(1, count + 1)), 1.0)])
    area = np.bincount(instance.ravel(), minlength=count + 1) * PIXEL_M**2
    return (250 * distance / peak[instance]).astype(np.uint8), (area[instance] > single_m2) & (instance > 0)


def encode(image: np.ndarray, instance: np.ndarray, mask: np.ndarray, band: np.ndarray | None, near: np.ndarray | None,
           ignore: bool, zone: np.ndarray | None = None, single_m2: float = SINGLE_M2, grass: bool = True, cut_reach: int = 5,
           doubt_share: float = 0.2) -> np.ndarray:
    """(2, H, W) uint8: label bits, centre target."""
    value, large = centre(instance, single_m2)
    lab = np.where(mask, CANOPY, 0).astype(np.uint8) | np.where(cut_band(instance, cut_reach), CUT, 0).astype(np.uint8) | np.where(large, LARGE, 0).astype(np.uint8)
    if zone is not None:
        lab |= np.where(zone, 0, OUTSIDE).astype(np.uint8)
    if ignore:
        params = CanopyParams()
        index, _ = excess_green(image, params)
        threshold = params.green_dn or params.exg_min
        dropped = mask & ~ndimage.binary_dilation(instance > 0, iterations=3)
        doubt = near & (np.abs(index - threshold) < doubt_share * threshold) | (near if grass else True) & ~band & (index > threshold) | dropped
        lab |= np.where(doubt, IGNORE, 0).astype(np.uint8)
    return np.stack([lab, value])


def crops(image: np.ndarray, lab: np.ndarray, count: int, size: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """`count` crops, 70% centred near a random canopy pixel, the rest on any pixel with a loss."""
    ys, xs = np.nonzero(lab[0] & CANOPY)
    ay, ax = np.nonzero(~lab[0] & OUTSIDE)
    height, width = lab.shape[1:]
    out_rgb, out_lab = [], []
    for k in range(count):
        if len(ys) and k < 0.7 * count:
            i = rng.integers(len(ys))
            cy, cx = ys[i] + rng.integers(-size // 4, size // 4), xs[i] + rng.integers(-size // 4, size // 4)
        else:
            i = rng.integers(len(ay))
            cy, cx = ay[i], ax[i]
        y0, x0 = int(np.clip(cy - size // 2, 0, height - size)), int(np.clip(cx - size // 2, 0, width - size))
        out_rgb.append(image[:, y0:y0 + size, x0:x0 + size])
        out_lab.append(lab[:, y0:y0 + size, x0:x0 + size])
    return np.stack(out_rgb), np.stack(out_lab)


def teacher_labels(image: np.ndarray, transform, name: str, model, params: CanopyParams, combine: str, threshold: float) -> dict[str, np.ndarray]:
    """Noisy-student labels: the rule pipeline with the teacher network's mask (alone, or and/or the colour mask) in place of colour."""
    from canopy_net_labels import tile_labels

    tile = ev.tiles[name]
    crossing = [(p, [a for a in p.axes if a.intersects(tile.bounds)]) for p in ev.rows]
    green = canopy_net.probabilities(image, model)[0] > threshold
    if combine != "net":
        green = green & green_mask(image, params) if combine == "and" else green | green_mask(image, params)
    return tile_labels(image, transform, [(p, axes) for p, axes in crossing if axes], params, green)


def pseudo_bank(per_tile: int, size: int, ignore: bool, seed: int, teacher: Path | None, zone_m: float, version: str,
                grass: bool = True, cut_reach: int = 5, doubt_share: float = 0.2, teacher_mode: tuple[str, float] = ("net", 0.5)) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    meta = json.loads((WORK / f"meta_{version}.json").read_text())
    model = canopy_net.load(teacher) if teacher else None
    rgb, lab = [], []
    for entry in meta["tiles"]:
        image, transform = ev.read_rgb(ev.tiles[entry["tile"]])
        z = teacher_labels(image, transform, entry["tile"], model, CanopyParams(), *teacher_mode) if model else np.load(WORK / f"labels_{version}" / f"{entry['tile'][:-4]}.npz")
        tile = ev.tiles[entry["tile"]]
        zone = tube([a for p in ev.rows for a in p.axes if a.intersects(tile.bounds)], transform, image.shape[1:], zone_m) if zone_m else None
        a, b = crops(image, encode(image, z["instance"], z["mask"], z["band"], z["near"], ignore, zone, grass=grass, cut_reach=cut_reach, doubt_share=doubt_share), per_tile, size, rng)
        rgb.append(a)
        lab.append(b)
    return np.concatenate(rgb), np.concatenate(lab)


def reference_bank(name: str, count: int, size: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """DIAGNOSTIC ONLY: crops of one organizer tile labelled with its hand-drawn canopies."""
    from rasterio.features import rasterize

    image, transform = ev.images[name]
    polygons = ev.ref_polygons(name)
    instance = rasterize([(p, i + 1) for i, p in enumerate(polygons)], out_shape=(2048, 2048), transform=transform, dtype=np.uint16)
    return crops(image, encode(image, instance, instance > 0, None, None, False, single_m2=np.inf), count, size, np.random.default_rng(seed))


def augment(rgb: np.ndarray, lab: np.ndarray, rng: np.random.Generator, dev, jitter: float = 0.3) -> tuple[torch.Tensor, torch.Tensor]:
    k, flip = rng.integers(4), rng.integers(2)
    rgb, lab = np.rot90(rgb, k, (2, 3)), np.rot90(lab, k, (2, 3))
    if flip:
        rgb, lab = rgb[..., ::-1], lab[..., ::-1]
    x = torch.from_numpy(np.ascontiguousarray(rgb)).to(dev).float()
    y = torch.from_numpy(np.ascontiguousarray(lab)).to(dev)
    n = x.shape[0]
    gain = torch.empty(n, 1, 1, 1, device=dev).uniform_(1 - jitter, 1 + jitter) * torch.empty(n, 3, 1, 1, device=dev).uniform_(1 - jitter / 3, 1 + jitter / 3)
    mean = x.mean((1, 2, 3), keepdim=True)
    x = (x * gain - mean) * torch.empty(n, 1, 1, 1, device=dev).uniform_(0.8, 1.2) + mean
    if rng.random() < 0.3:
        x = gaussian_blur(x, 5, float(rng.uniform(0.5, 1.5)))
    x = (x + torch.randn_like(x) * 3.0).clamp(0, 255)
    return canopy_net.inputs(x), y


def train(bank: tuple[np.ndarray, np.ndarray], config: dict, steps: int, batch: int, lr: float, seed: int,
          instance: str = "cut", init: Path | None = None, log_every: int = 250, gap_weight: float = 0.0, jitter: float = 0.3) -> nn.Module:
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    dev = canopy_net.device()
    model = canopy_net.build(**config)
    if init:
        model.load_state_dict(torch.load(init, map_location="cpu", weights_only=True)["state_dict"])
    model = model.to(dev).train()
    optimiser = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimiser, lr, total_steps=steps, pct_start=0.1)
    rgb, lab = bank
    started, running = time.perf_counter(), 0.0
    for step in range(1, steps + 1):
        pick = rng.integers(len(rgb), size=batch)
        x, y = augment(rgb[pick], lab[pick], rng, dev, jitter)
        z = model(x)
        bits = y[:, 0]
        target, weight = (bits & CANOPY).float(), 1.0 - (bits & (IGNORE | OUTSIDE)).bool().float()
        if gap_weight:  # background between two teacher canopies weighs more (U-Net's separation weight map, simplified)
            weight = weight * (1.0 + gap_weight * ((bits & CUT).bool() & ~(bits & CANOPY).bool()).float())
        bce = nn.functional.binary_cross_entropy_with_logits(z[:, 0], target, weight=weight, reduction="sum") / weight.sum().clamp(min=1)
        p = torch.sigmoid(z[:, 0]) * weight
        dice = 1 - (2 * (p * target).sum() + 1) / (p.sum() + (target * weight).sum() + 1)
        if instance == "cut":
            keep = 1.0 - (bits & OUTSIDE).bool().float()
            second = nn.functional.binary_cross_entropy_with_logits(z[:, 1], (bits & CUT).bool().float(), weight=keep,
                                                                    pos_weight=torch.tensor(5.0, device=dev), reduction="sum") / keep.sum().clamp(min=1)
        else:
            keep = 1.0 - (bits & (LARGE | OUTSIDE)).bool().float()
            second = nn.functional.binary_cross_entropy_with_logits(z[:, 1], y[:, 1].float() / 250, weight=keep, reduction="sum") / keep.sum().clamp(min=1)
        loss = bce + dice + second
        optimiser.zero_grad(set_to_none=True)
        loss.backward()
        optimiser.step()
        schedule.step()
        running += loss.item()
        if step % log_every == 0:
            print(f"  step {step} loss {running / log_every:.4f} ({time.perf_counter() - started:.0f} s)", flush=True)
            running = 0.0
    return model.eval()


def save(model: nn.Module, config: dict, path: Path, info: dict) -> None:
    torch.save({"config": config, "state_dict": {k: v.cpu() for k, v in model.state_dict().items()}, "info": info}, path)


def label_polygons(labels: np.ndarray, transform: Affine, params: CanopyParams) -> list[Polygon]:
    """One simplified single-ring polygon per label of at least `min_area_m2`, as `canopy.canopy_polygons` makes them (without its neck split)."""
    labels = labels.copy()
    labels[np.bincount(labels.ravel())[labels] * PIXEL_M**2 < params.min_area_m2] = 0
    parts: dict[int, list] = {}
    for geometry, value in shapes(labels.astype(np.int32), mask=labels > 0, connectivity=8, transform=transform):
        parts.setdefault(int(value), []).append(shape(geometry))
    largest = lambda g: max((p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"), key=lambda p: p.area, default=None)
    inset = getattr(params, "inset_m", 0.0)
    polygons = []
    for pieces in parts.values():
        best = largest(make_valid(max(pieces, key=lambda p: p.area).simplify(params.simplify_m)))
        if best is not None and best.area >= params.min_area_m2:
            best = largest(make_valid(Polygon(best.exterior).buffer(-inset, join_style="mitre"))) if inset else best
            if best is not None:
                polygons.append(Polygon(best.exterior))
    return polygons


def split_at_cuts(mask: np.ndarray, cut: np.ndarray, min_core_px: int = 0) -> np.ndarray:
    """Labels: 8-connected pieces of `mask` without the `cut` pixels (pieces under `min_core_px` dropped), then every
    other pixel of `mask` given to its nearest piece. A component with no piece left is kept whole."""
    labels, count = ndimage.label(mask & ~cut, canopy.EIGHT)
    if min_core_px and count:
        labels[np.bincount(labels.ravel())[labels] < min_core_px] = 0
    whole, _ = ndimage.label(mask, canopy.EIGHT)
    orphans = mask & ~np.isin(whole, np.unique(whole[labels > 0]))
    labels, count = ndimage.label((labels > 0) | orphans, canopy.EIGHT)
    if not count:
        return labels
    _, (rows, cols) = ndimage.distance_transform_edt(labels == 0, return_indices=True)
    return np.where(mask, labels[rows, cols], 0)


def second_output_split(name: str, model: nn.Module, prob: np.ndarray, threshold: float, combine: str, split: str, value: float,
                        min_core_m2: float = 0.02) -> list[dict]:
    """Plants split by the network's second output instead of the rule necks: "cut" where it exceeds `value`, "centre"
    grown from the cores where centre-ness reaches `value`. Measured below the neck split in every run, so not in canopy_net."""
    image, transform = ev.images[name]
    params = CanopyParams()
    green = canopy_net.mask(image, model, params, threshold, combine, prob=prob)
    cut = prob[1] > value if split == "cut" else prob[1] < value
    tile = ev.tiles[name]
    crossing = [(plot, [a for a in plot.axes if a.intersects(tile.bounds)]) for plot in ev.rows]
    return [{"type": "Feature", "geometry": mapping(polygon), "properties": {"label": "vineyard", "source": "prediction", "vineyard_id": plot.vineyard_id}}
            for plot, axes in crossing if axes
            for polygon in label_polygons(split_at_cuts(canopy.canopy_mask(image, transform, axes, params, green, canopy.row_spacing(plot.axes)), cut,
                                                        int(min_core_m2 / PIXEL_M**2)), transform, params)]


def evaluate(tag: str, model: nn.Module, instance: str = "cut", names: list[str] | None = None) -> list[dict]:
    """The network through the canopy hook: alone at thresholds 0.4-0.6 or combined with the rule's colour mask,
    split at the rule necks or by its second output."""
    names = names or ev.NAMES
    started = time.perf_counter()
    probs = {name: canopy_net.probabilities(ev.images[name][0], model) for name in names}
    seconds = (time.perf_counter() - started) / len(names)
    splits = [("neck", 0.0), ("cut", 0.5), ("cut", 0.3)] if instance == "cut" else [("neck", 0.0), ("centre", 0.3), ("centre", 0.5)]
    masks = [("net", 0.4), ("net", 0.5), ("net", 0.6), ("and", 0.2), ("and", 0.5), ("or", 0.5)]
    results = []
    for combine, threshold in masks:
        for split, value in splits:
            found = {name: canopy_net.tile_canopies(ev.tiles[name], ev.rows, model=model, threshold=threshold, rgb=ev.images[name],
                                                    prob=probs[name], combine=combine) if split == "neck" else
                     second_output_split(name, model, probs[name], threshold, combine, split, value) for name in names}
            label = f"{'net' if combine == 'net' else 'colour ' + combine + ' net'}>{threshold}, {'rule neck split' if split == 'neck' else f'net {split} {value}'}"
            results.append(ev.score(f"{tag} {label}", found, seconds, explain=(combine, threshold, split) in (("net", 0.5, "neck"), ("and", 0.2, "neck"))))
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="")
    parser.add_argument("--evaluate", type=Path, help="score saved weights instead of training")
    parser.add_argument("--steps", type=int, default=3000)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--lr", type=float, default=3e-3)
    parser.add_argument("--width", type=int, default=16)
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--per-tile", type=int, default=32)
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-ignore", action="store_true")
    parser.add_argument("--no-grass", action="store_true", help="no loss on green outside the kept tube (grass is never a negative)")
    parser.add_argument("--cut-reach", type=int, default=5, help="cut target: pixels whose window of this size holds two teacher canopies")
    parser.add_argument("--jitter", type=float, default=0.3, help="brightness gain range +-; per-channel gain +- a third of it")
    parser.add_argument("--doubt", type=float, default=0.2, help="no canopy loss where the colour index is within this share of its threshold")
    parser.add_argument("--gap-weight", type=float, default=0.0, help="extra canopy-loss weight on background inside the cut band")
    parser.add_argument("--teacher", type=Path)
    parser.add_argument("--init", type=Path, help="start from these weights (fine-tuning) instead of random initialisation")
    parser.add_argument("--teacher-combine", default="net", choices=["net", "and", "or"])
    parser.add_argument("--teacher-threshold", type=float, default=0.5)
    parser.add_argument("--labels", default=max(WORK.glob("meta_*.json"), key=lambda p: p.stat().st_mtime).stem[5:] if WORK.is_dir() else "",
                        help="pseudo-label version (canopy.py sha256[:8]); default the newest snapshot")
    parser.add_argument("--zone", type=float, default=1.5, help="loss only within this many metres of a predicted axis; 0 = whole tile")
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--instance", choices=["cut", "centre"], default="cut", help="what the second output learns")
    args = parser.parse_args()
    if args.evaluate:
        info = torch.load(args.evaluate, map_location="cpu", weights_only=True)["info"]
        evaluate(args.evaluate.stem, canopy_net.load(args.evaluate), info.get("instance", "cut"))
        return
    config = {"width": args.width, "depth": args.depth, "outputs": 2}
    out_dir = WORK / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.diagnostic:
        for train_on, test_on in (ev.NAMES, ev.NAMES[::-1]):
            bank = reference_bank(train_on, 1024, args.size, args.seed)
            started = time.perf_counter()
            model = train(bank, config, args.steps, args.batch, args.lr, args.seed, args.instance, init=args.init, gap_weight=args.gap_weight,
                          jitter=args.jitter)
            print(f"DIAGNOSTIC trained on {train_on} hand-drawn canopies, {time.perf_counter() - started:.0f} s")
            save(model, config, out_dir / f"DIAGNOSTIC_{args.name}_{train_on[7:16]}.pt", {"diagnostic": True, "trained_on": train_on})
            evaluate(f"DIAG {args.name} {train_on[7:16]}->{test_on[7:16]}", model, args.instance, [train_on, test_on])
        return
    started = time.perf_counter()
    bank = pseudo_bank(args.per_tile, args.size, not args.no_ignore, args.seed, args.teacher, args.zone, args.labels, not args.no_grass, args.cut_reach, args.doubt,
                       (args.teacher_combine, args.teacher_threshold))
    bank_seconds = time.perf_counter() - started
    bits = bank[1][:, 0]
    print(f"bank {bank[0].shape[0]} crops of {args.size} px in {bank_seconds:.0f} s; canopy {np.mean(bits & CANOPY):.3f}, ignored "
          f"{np.mean((bits & IGNORE) > 0):.3f}, outside {np.mean((bits & OUTSIDE) > 0):.3f}, cut {np.mean((bits & CUT) > 0):.4f}, large {np.mean((bits & LARGE) > 0):.3f}", flush=True)
    started = time.perf_counter()
    model = train(bank, config, args.steps, args.batch, args.lr, args.seed, args.instance, init=args.init, gap_weight=args.gap_weight,
                  jitter=args.jitter)
    train_seconds = time.perf_counter() - started
    del bank
    parameters = sum(p.numel() for p in model.parameters())
    print(f"{args.name}: {parameters:,} parameters, trained {args.steps} steps x {args.batch} in {train_seconds:.0f} s", flush=True)
    info = {**vars(args), "teacher": str(args.teacher) if args.teacher else None, "parameters": parameters,
            "train_seconds": round(train_seconds), "bank_seconds": round(bank_seconds)}
    save(model, config, out_dir / f"{args.name}.pt", info)
    results = evaluate(args.name, model, args.instance)
    (out_dir / f"{args.name}.json").write_text(json.dumps({"info": info, "results": results}, indent=1))


if __name__ == "__main__":
    main()
