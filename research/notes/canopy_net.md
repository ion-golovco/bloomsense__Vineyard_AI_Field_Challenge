# Canopy network (`marcaj.canopy_net`): a self-trained U-Net for the brief's neural-network requirement

26 September 2026. Scores are the judge's canopy formula (0.6 × union IoU + 0.4 × one-to-one F1 at IoU 0.5) on the two
organizer tiles, r021_c012 (399 canopies) and r006_c004 (251). They are a sanity check, not a holdout: every choice
below was made with these two tiles in view, so differences under about 0.01 are noise (see the seed check). All runs
use one frozen `detect_plots()` output (`data/generated/work/canopy_net/plots.json`, byte-identical to
`canopy_rules/plots.json`), so the rule baseline and every network see the same rows.

**Result.** The rules win or tie.
- The shipped network (v14) scores **0.843** on its own (IoU 0.807, F1 0.896; 402 / 256 canopies against 399 / 251).
  The final rules score **0.855** (0.822, 0.905; 402 / 259).
- The same network as a filter on the rule's colour mask ("colour AND net > 0.2") scores **0.855** (0.8547 against
  0.8552), a tie.
- v15 scores 0.847 on its own, but over-counts (411 / 266). v14 is shipped because its counts are the closest to the
  reference of any variant, rules included.
- No variant beats the rules by more than noise. The highest number, 0.8559 for the noisy student's "and" (v16), is
  +0.0007 against a seed spread of 0.003. Self-training can only copy its teacher: where the teacher is wrong,
  the network is wrong too, and it cannot flag those pixels (AUC 0.53, below).
- One tile of hand labels does not beat the rules on the other tile either (0.65–0.77, below).

## Architecture and data

- **U-Net in plain torch, randomly initialised, no downloaded weights.** Each level has two 3×3 conv + BatchNorm + ReLU
  layers; widths double from 16; max-pool down, bilinear up. Best net: 5 levels, **1,964,546 parameters** (4 levels:
  488,450). Input is 6 channels: RGB scaled to ±1, plus chromaticity r, g, b / (r + g + b). There are 2 outputs: canopy,
  and an auxiliary "cut" head that learns the band between two teacher plants. The cut head is not used at inference.
  Everything runs at native 0.025 m.
- **Teacher = the rules.** `canopy_mask` (closed, filled, inside the kept tube) gives the canopy target. Instance ids
  come from the final rule polygons. Labels are snapshotted once per canopy.py version:
  `data/generated/work/canopy_net/labels_<sha8>/`, `meta_<sha8>.json`, `canopy_snapshot_<sha8>.py`.
  - `bba41472`: the ExG rules (0.668).
  - `580a4c8a`: the intermediate DN rules.
  - **`ff679923`: the final rules (0.855).** 118 tiles, 9,305 canopies.
  Every vineyard tile is used except the two reference tiles and their 8 neighbours (13 vineyard tiles held out). The
  labels never read `data/review/`.
- **Loss masking** (the step that decides the result):
  - No loss beyond 1.5 m of any predicted axis (`--zone`). Rows the teacher missed would otherwise be background.
  - No loss where the DN index is within 10–20% of its threshold, within 0.6 m of an axis (`--doubt`).
  - No loss on mask pieces the teacher dropped (under 0.2 m²).
  - **No loss on green pixels outside the kept tube** (`--no-grass`).
- **Separation weight** (`--gap-weight 5 --cut-reach 9`). Background pixels between two teacher plants less than
  about 20 cm apart weigh 6× in the canopy loss. This is a simplified form of the original U-Net weight map.
- **Training.**
  - 3,776 crops of 256 px per run (32 per tile, 70% centred near a teacher canopy), held in RAM (about 1 GB). Building
    them takes 61 s.
  - Augmentation: D4 flips and rotations; brightness gain ±`jitter` with per-channel gain ±jitter/3; contrast ±20%;
    blur with p = 0.3; noise σ = 3.
  - BCE + soft Dice on canopy, BCE (pos_weight 5) on cut. AdamW with one-cycle LR 3e-3, batch 16, seed 0, Apple MPS
    (M4 Pro, shared with four other agents).
  - 3,000 steps take 350–400 s at 4 levels; 6,000 steps take 834 s at 5 levels.
- **Inference.** 1,024 px windows with a 64 px reflected margin, averaged over 4 flips. The rule post-processing runs
  unchanged through the `green=` hook: twice-refitted axes, row_gap and row_value filters, ±0.3 m tube, 5 cm closing,
  fill, neck split, 0.2 m² minimum, 1 cm inset. The network takes 0.8 s per tile on MPS.

## Every variant

Canopy score (union IoU, F1), then per tile score and count (reference 399 / 251). "net" = network mask alone at 0.5;
"and" = colour AND net > 0.2; "or" = colour OR net > 0.5. Every run, all thresholds and combinations are in
`data/generated/work/canopy_net/runs/<name>.log|.json`.

| Run | Labels | Change | net | r021 / r006 (n) | and | or |
|---|---|---|---|---|---|---|
| **rule, ExG** | – | the old rules | **0.668** (0.664, 0.674) | 0.657 (374) / 0.680 (260) | | |
| v1 | bba41472 | loss on the whole tile | 0.627 (0.605, 0.661) | 0.614 (355) / 0.649 (265) | | |
| v2 | bba41472 | + loss only within 1.5 m of axes | 0.646 (0.623, 0.681) | 0.640 (374) / 0.658 (274) | | **0.677** ExG-or-net |
| **rule, final** | – | the final rules | **0.855** (0.822, 0.905) | 0.862 (402) / 0.850 (259) | | |
| v4 | 580a4c8a | v2 on DN labels | 0.788 (0.750, 0.844) | 0.821 (396) / 0.755 (265) | 0.822 | 0.841 |
| v5 | 580a4c8a | + no grass negatives | 0.811 (0.789, 0.843) | 0.833 (376) / 0.784 (241) | 0.851 | 0.816 |
| v6 | 580a4c8a | v4, but no loss near low-contrast rows | 0.793 (0.754, 0.851) | 0.826 (399) / 0.761 (269) | 0.825 | 0.845 |
| v9 | ff679923 | v5 on the final labels | 0.825 (0.797, 0.867) | 0.846 (377) / 0.801 (248) | 0.848 | 0.836 |
| v10 | ff679923 | + separation weight | 0.832 (0.800, 0.881) | 0.842 (397) / 0.824 (265) | 0.851 | 0.841 |
| v11 | ff679923 | v9 with a centre-ness head | 0.829 (0.800, 0.873) | 0.843 (394) / 0.813 (251) | 0.851 | 0.835 |
| v13 | ff679923 | v10 + jitter 0.1, doubt 0.1 | 0.832 (0.805, 0.871) | 0.830 (417) / 0.838 (265) | 0.846 | 0.852 |
| **v14 (shipped)** | ff679923 | v10 + 5 levels, 6,000 steps, jitter 0.2, doubt 0.1 | **0.843** (0.807, 0.896) | 0.845 (402) / 0.842 (256) | **0.855** | 0.846 |
| v15 | ff679923 | v14 with jitter 0.1 | 0.847 (0.816, 0.894) | 0.852 (411) / 0.846 (266) | 0.854 | 0.850 |
| v14 + v15 | ff679923 | mean of both probabilities | 0.846 (0.813, 0.896) | 0.850 (405) / 0.845 (264) | 0.855 | |
| v10, seed 1 | ff679923 | seed check | 0.835 (0.803, 0.883) | 0.846 (393) / 0.824 (260) | 0.849 | 0.844 |
| v16 | v15's output | noisy student: v15 recipe on v15's labels (net > 0.5 through the rules) | 0.842 (0.810, 0.890) | 0.848 (409) / 0.838 (263) | 0.856 | 0.845 |

Splitting plants with the second output instead of the rule necks was always worse:
- Cut head at 0.5: v9 0.803, v10 0.826 (the neck split gives 0.825 and 0.832). Cut probability never passes 0.3.
- Centre-ness cores at 0.3: v11 0.732, with 525 / 338 canopies. It over-splits.
The table's threshold 0.5 for "net" is within 0.008 of 0.4 and 0.6 in every run. Dropping the rule's closing for
the net mask costs 0.008–0.020 (v10).

## Why the results are what they are

1. **Grass is where self-training breaks.**
   - The failure: in grassed plots the row detector can put axes on the inter-rows. The teacher then labels grass as
     canopy and the vines beside it as background. On the grassiest training tiles, 42–44% of the kept axes have a
     tube/flank contrast under 1.3; on the reference tiles 0–8% do (intermediate rules).
   - With grass as a negative (v4), the net rejects the grassed rows of r006: 37 m² of teacher canopy, 72% of it true
     reference canopy, at a median probability of 0.14. r006 scores 0.755 against 0.850 for the rules.
   - Dropping low-contrast rows from training (v6) does not help (0.793); the net still has no positive example of
     vines in grass.
   - Never using green outside the tube as a negative (v5/v9) recovers those rows: r006 area goes from 269 to 302 m²
     (reference 299). The net improves by +0.023 (v4 → v5), and r006 alone by +0.029.
   - Limiting the loss to 1.5 m of the axes (v1 → v2, old teacher) gave +0.019 for a related reason: otherwise rows
     the teacher missed count as background.
2. **Merges come from the network's smooth masks.** v9's masks bridge the 5–20 cm gaps between neighbouring plants:
   40 + 27 references are merged, against 22 + 7 for the rules. The separation weight (v10) cuts that to 37 + 9 and
   brings counts to 397 / 265 (+0.007). The cut head itself stays weak (0.01–0.07% positive pixels). It is the
   weighting of the canopy channel that separates plants.
3. **Brightness is a feature here, not a nuisance.** The DN index (2g − r − b in pixel values) beats normalised ExG
   because it drops dark, shadowed leaves, and the rules trace "the leaves, not their shadow". ±30% brightness jitter
   teaches the net to ignore brightness, so it keeps shadow. v10's extra pixels on r021 have brightness 59 against 83
   for canopy and DN 23 (just under 25), and only 18% of them are reference canopy. Jitter 0.1 (v13) fixes r021's area
   (250 → 230 m²; reference 237) and lifts IoU to 0.805, but over-splits r021 (417 canopies), so the score stays level.
4. **Capacity helps.** Five levels and twice the steps (v14) give +0.011 over v10/v13. The theoretical receptive field
   grows from 2.4 m to 5 m, so the net sees the neighbouring rows. Counts are 402 / 256, with 34 + 8 merges. The
   remaining gap to the rules is r021 pixel precision (0.848 against 0.890; 258 m² against 237). r021 also has 23
   predictions covering no reference canopy, against the rules' 13.
   - v15 (jitter 0.1) fixes most of that (0.882; 244 m²) and scores 0.847. It over-splits r021, though (411; 9
     splits against 2).
5. **The net cannot find the teacher's mistakes.**
   - Its mean probability over each rule polygon does not separate the rule's false canopies from its matched ones
     (AUC 0.53 / 0.54, v4). It learned the same notion of canopy.
   - As a filter ("and"), v14 removes 0.16% / 3.4% of the colour pixels in the tube. It is mostly right to: only
     11–13% of the removed pixels are reference canopy. But the pixels are too few and too scattered to move a
     polygon, so the filter ties the rules (−0.0005).
   - For v4/v6, "and" is worse (−0.03), because it also removes the grassed rows.
6. **A noisy-student round adds nothing.** v16, trained on v15's own output, scores 0.842 alone, below its teacher
   (0.847). It inherits v15's over-splitting (409 on r021) and gets no new information. Building the crops takes 235 s,
   because every tile is run through the teacher first.
7. **Centre-ness from isolated teacher plants** (v11) finds several peaks per plant: 525 canopies against 399. On
   r006 the reference has whole-row strips in the grassed block, and the rules measured that annotators do not split
   continuous canopy at planting distance. Peak-splitting fights the reference there.

## Cross-tile diagnostic with hand labels (DIAGNOSTIC ONLY, never shipped)

The net is trained on one organizer tile's hand-drawn canopies and scored on the other
(`canopy_net_train.py --diagnostic`: 1,024 crops, `DIAGNOSTIC_*.pt`). CLAUDE.md forbids shipping this; it only prices
labels. Scores are on the held-out tile, at the net's best threshold 0.1:

| Trained on → scored on | From scratch (2,000 steps) | v9 fine-tuned (1,000 steps, lr 1e-3) | Self-trained v14 | Rules |
|---|---|---|---|---|
| r021 → r006 | 0.692 (net at 0.5: 0.598) | 0.687 (0.567) | 0.842 | 0.850 |
| r006 → r021 | 0.649 (0.533) | 0.770 (0.615) | 0.845 | 0.862 |

- On their own training tile these nets reach only 0.80–0.83, below the rules.
- They are conservative: at threshold 0.5, pixel precision is 0.87–0.96 and recall 0.55–0.79.
- Their raw masks are better pixel classifiers near the rows than colour. Within 0.6 m of the axes, IoU is 0.73 against
  0.68 on r021 and 0.60 against 0.54 on held-out r006. The rule post-processing, tuned for the colour mask, doesn't turn
  that into score.
- The two tiles look different (older vines on pale soil against young vines on dark soil with a grassed block). One
  tile's 251–399 canopies therefore buy nothing over 118 tiles of rule labels. A labelled model would need labels from
  many tiles, and CLAUDE.md forbids training on hand-drawn Sireț3 geometry.

## Seed and runtime

- Seed check: the v10 recipe with seed 1 scores net 0.835 (seed 0: 0.832) and "and" 0.849 (0.851). The spread is
  about 0.003.
- Full run on the 131 vineyard tiles (frozen rows, `canopy_net_infer.py`, `combine="and"`) takes 174 s, 1.33 s per
  tile. The network is 104 s of that (4 flips); the rest is reading and the unchanged rule post-processing. The rules
  alone take 62 s.
  - Counts: 12,783 canopies against the rules' 12,709 (+0.6%), and 12,729 m² against 12,751 m².
  - Per-tile count ratio against the rules: median 1.000, p10 0.978, p90 1.056.
  - The network alone moves further from the rules across the site: 12,442 canopies (−2.1%) and 13,414 m² (+5.2%),
    per-tile ratio median 0.988, p10 0.800, p90 1.104. With no reference away from the two tiles, nothing says which
    is right there.
  - Without flips the network takes 0.2 s per tile instead of 0.8 s. "and" still scores 0.855 (0.8548); the network
    alone drops to 0.840.
  - Weights: 7.9 MB. Training takes 834 s, plus 61 s to build the crops and 95 s for the label snapshot.

## How to wire it in (not done; predict.py is not mine)

In `marcaj/predict.py`, replace the canopy line with:

```python
from marcaj import canopy_net
found += [feature for tile in tiles for feature in canopy_net.tile_canopies(tile, plot_rows, combine="and", threshold=0.2, flips=False)]
```

- `combine="and", threshold=0.2, flips=False` is the tie with the rules (0.855), for about 26 s of network time on
  top of the rules.
- `combine="net"` is the network alone (0.843, −0.012).
- It needs `--group sam` (torch), and the weights at `data/generated/work/canopy_net/canopy_net.pt` (7.9 MB fp32; the
  `v14_deep` state_dict with its config). `data/generated` is gitignored, and the brief wants published weights, so
  copying them to a tracked or released location is a decision for the user.
- Reproduce:
  1. `uv run --frozen --group sam python ../research/probes/canopy_net_labels.py`
  2. `... canopy_net_train.py --name v14_deep --labels ff679923 --no-grass --gap-weight 5 --cut-reach 9 --jitter 0.2 --doubt 0.1 --depth 5 --steps 6000`
     This writes `runs/v14_deep.pt` and scores it; copy it to `canopy_net.pt`.
  3. `... canopy_net_train.py --evaluate <weights>` for the full variant grid on the two tiles, and
     `... canopy_net_infer.py <weights> --combine and --threshold 0.2` for the full-run timing.
