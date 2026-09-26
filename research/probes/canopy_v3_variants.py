"""Canopy round-three variants (shared by canopy_v3_quick.py and canopy_v3_trials.py). Evaluation only."""

from dataclasses import replace

from marcaj.canopy import CanopyParams

D = CanopyParams(grass_ratio=0.0)  # the round-two defaults; round three ships grass_ratio 0.9 ("e90")
VARIANTS: dict[str, CanopyParams] = {
    "defaults": D,
    # finer weak stretches: fill between plants with 2g - r - b above weak_dn where a stretch has almost no full colour
    "w2_s10": replace(D, weak_window_m=2.0, weak_share=0.10),
    "w1_s05": replace(D, weak_window_m=1.0, weak_share=0.05),
    "w1_s05_t05": replace(D, weak_window_m=1.0, weak_share=0.05, tuft_m2=0.05),
    "w1_s10_t05": replace(D, weak_window_m=1.0, weak_share=0.10, tuft_m2=0.05),
    "w1_s10_d12_t05": replace(D, weak_window_m=1.0, weak_share=0.10, weak_dn=12, tuft_m2=0.05),
}
VARIANTS.update({
    "grow_a8": replace(D, grow_a=-8),
    "grow_a10": replace(D, grow_a=-10),
    "grow_a8_d15": replace(D, grow_a=-8, grow_dn=15),
})
VARIANTS.update({
    "grass85": replace(D, grass_ratio=0.85),
    "grass80": replace(D, grass_ratio=0.80),
    "grass88": replace(D, grass_ratio=0.88),
})
VARIANTS.update({
    "edge85": replace(D, grass_ratio=0.85),
    "edge90_f13": replace(D, grass_ratio=0.90, grass_flank=1.3),
})
VARIANTS.update({
    "edge85m": replace(D, grass_ratio=0.85),  # with the absolute flank floor 0.15
    "edge90m": replace(D, grass_ratio=0.90),
})
VARIANTS.update({
    "otsu": replace(D, otsu=True),
    "hyst_dn15": replace(D, grow_a=100, grow_dn=15),  # hysteresis: 2g - r - b above 15, 8-connected to the canopy, in the tube
    "hyst_dn20": replace(D, grow_a=100, grow_dn=20),
    "hyst_a4_dn10": replace(D, grow_a=-4, grow_dn=10),
    "tuft05": replace(D, tuft_m2=0.05),
    "tuft03": replace(D, tuft_m2=0.03),
    "min010": replace(D, min_area_m2=0.10),
})
E = replace(D, grass_ratio=0.90)
VARIANTS.update({
    "e90": E,
    "e90_hyst_a4": replace(E, grow_a=-4, grow_dn=10),
    "e90_hyst_dn20": replace(E, grow_a=100, grow_dn=20),
    "e90_tuft05": replace(E, tuft_m2=0.05),
    "e90_w1_t05": replace(E, weak_window_m=1.0, weak_share=0.10, tuft_m2=0.05),
})
VARIANTS.update({
    "hyst_a6_dn15": replace(D, grow_a=-6, grow_dn=15),
    "hyst_a5_dn12": replace(D, grow_a=-5, grow_dn=12),
    "hyst_a4_dn15": replace(D, grow_a=-4, grow_dn=15),
    "hyst_a3_dn18": replace(D, grow_a=-3, grow_dn=18),
    "e95": replace(D, grass_ratio=0.95),
})
VARIANTS.update({
    "e90_hyst_a5": replace(E, grow_a=-5, grow_dn=12),
})
VARIANTS.update({
    "hyst_a4_c15": replace(D, grow_a=-4, grow_dn=10, grow_core_m=0.15),
    "hyst_a4_c10": replace(D, grow_a=-4, grow_dn=10, grow_core_m=0.10),
    "hyst_dn15_c10": replace(D, grow_a=100, grow_dn=15, grow_core_m=0.10),
    "hyst_dn12_c05": replace(D, grow_a=100, grow_dn=12, grow_core_m=0.05),
})
VARIANTS.update({
    "e90_hyst_a4_c10": replace(E, grow_a=-4, grow_dn=10, grow_core_m=0.10),
    "hyst_a3_c10": replace(D, grow_a=-3, grow_dn=10, grow_core_m=0.10),
    "hyst_a4_c08": replace(D, grow_a=-4, grow_dn=10, grow_core_m=0.08),
    "hyst_a4_c12": replace(D, grow_a=-4, grow_dn=10, grow_core_m=0.12),
    "hyst_a3_c08": replace(D, grow_a=-3, grow_dn=10, grow_core_m=0.08),
})
VARIANTS.update({  # V08-04: grassed rows whose flanks are green too fail row_value 1.2 by a hair (1.0-1.19)
    "e90_rv11": replace(E, row_value=1.1),
    "e90_rv10": replace(E, row_value=1.0),
})
