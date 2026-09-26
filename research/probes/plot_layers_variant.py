"""Builds a variant of the plot layers under data/generated/work/plots/ (the shared cache stays untouched).
Run from backend/: uv run --frozen python ../research/probes/plot_layers_variant.py tophat_m=1.6 [smooth_m=2.0]"""

import sys
import time

import numpy as np

from marcaj.layers import compute_layers
from marcaj.mosaic import load_mosaic
from marcaj.tiles import REPO_ROOT

options = {k: float(v) for k, v in (a.split("=") for a in sys.argv[1:])}
path = REPO_ROOT / "data" / "generated" / "work" / "plots" / f"layers_{'_'.join(f'{k}{v:g}' for k, v in options.items()) or 'base'}.npz"
started = time.perf_counter()
layers = compute_layers(*load_mosaic(), **options)
np.savez_compressed(path, vine_over_orchard=layers.vine_over_orchard, orchard_energy=layers.orchard_energy, row_angle=layers.row_angle,
                    green_share=layers.green_share, valid=layers.valid, transform=np.array(layers.transform[:6]))
print(f"{path} in {time.perf_counter() - started:.1f} s")
