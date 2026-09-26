# Walking route solver (26 September)

`marcaj.route` solves the challenge route. `marcaj-export` calls it and writes `route.geojson`, `route_targets.csv` and `routes.geojson`. `routing.check_route` stays the validator. Probe: `research/probes/route_probe.py`. Outputs and renders are in `data/generated/work/route/`.

## Design

**Planning space.** The route plans on passages plus the scene's inter-rows eroded by 0.3 m, minus forbidden zones (`robust_space`). The organizers' inter-rows may be tighter than ours. Planning on our own inter-rows put 0.97% of the site route outside them, but 1.89% outside the eroded ones, because every lane entry and exit adds its 0.3 m.

**Grid.** 0.5 m cells, 1.4 M nodes on the site, built in 2–5 s.
- Each cell's passable cover comes from a raster 3× finer, so a 0.3 m sliver at a lane end still counts as outside.
- Walkable cells: passable space, plus a 12 m outside corridor around it.
- The corridor excludes forbidden zones (all touched), row strips (axis ± 0.3 m, the trellis) and canopies. It lets the route reach blocks behind a bare headland.
- A step costs 1 per metre at a lane centre and up to 2 within 0.75 m of the passable edge, so the route keeps to lane centres. Outside passable space it costs 6 per metre. A canopy that overlaps passable space also costs 6 but stays walkable.
- Steps go in 16 directions (length error at most 2.7%). No step cuts past a blocked cell, so the route cannot slip through a 1-cell row strip.

**Lanes are implicit.** No centreline graph is built. A shortest path enters a lane from its cheaper end, visits several targets in one lane, U-turns around a row end across a headland when that is cheaper than walking back, and crosses into the small passage component. All of this falls out of the costs.

**Targets.**
- Each target attaches to the cheapest walkable cell within 1.75 m of it, one per local component, up to 3. A row gap usually gets one cell in each neighbouring lane.
- With the 0.2 m simplification the line stays within 2 m. The status in `route_targets.csv` is always measured on the final line.
- Waste boxes are targeted at their centroid.
- A gap POI's `gap_start` and `gap_end` get an out-and-back walk along the same lane when the route does not already pass them. The organizers' point may sit anywhere in the gap. On the site, both ends are within 2 m on 130 of 130 visited gaps (56 walks).

**Matrices.** One scipy Dijkstra per candidate stop, 0.3 s each on 1.4 M nodes (it holds the GIL, so threads don't help). Each run gives cost, metres and outside metres to every stop: the path tree is traced for all stops at once, and only its used part is kept, so legs are rebuilt without rerunning Dijkstra.

**Tour.**
1. Nearest neighbour, then 2-opt and Or-opt (runs of 1–3 stops, both orientations) on the group-minimum costs.
2. A Viterbi pass re-picks each target's candidate for the current order.
3. 2-opt and Or-opt again on the chosen stops, repeated until the cost stops falling.

**Budget.** While the tour is more than `OUTSIDE_BUDGET` = 1.2% outside the eroded space, the solver drops whatever saves the most outside metres per target. That is either one stop, or a whole run of consecutive stops behind the same outside access; dropping one stop of such a block saves nothing. Dropped targets that still fit are then put back, cheapest access first.

The raster undercounts exact outside metres (up to 2.7× on the site, at lane ends). So the simplified line is measured exactly against the eroded space. If it is over budget, the raster is rescaled by the measured ratio and the solver budgets again. `check_route` then reports against the scene's own passable space.

**Validator fix (`routing.py`).** `check_route` used `route.difference(space).length`. An overlay dissolves a line that retraces itself, so an out-and-back spur outside counted only once. That undercounts exactly the spurs this solver makes. It now sums per segment (`_length_in`), and it also reports `forbidden_m` and `canopy_m`. It takes an explicit `targets` list, because the POIs are not scene features.

## Controls (`route_probe.py controls`; all fail as they must)

1. A straight line START → an old waste candidate 28.7 m inside P25 → START: 79.4% outside, `legal=False`.
2. A retraced 40 m spur: 40.4 m outside per segment, against 20.2 m from the old whole-line overlay.
3. A route that ends 8 m from START: `closed=False`.

## Results

**Site prediction.** This is `predictions.geojson`, relabelled `source: dev` for development only. Targets are 191 challenge POIs plus the 2 in-block waste boxes from `work/waste/waste.geojson`, 193 in total. Planning takes 2.0–3.1 min: 400 stops, the time varies with machine load, and the peak is 2.9 GB. Each cutoff then takes 13–22 s.

| POI confidence | length | visited / targets | outside, scene | outside, eroded 0.3 m | if inter-rows stopped 1 m short of passages |
|---|---|---|---|---|---|
| all (official) | 9,119 m | 132 / 193 | 0.61% | 1.14% | 1.92% |
| ≥ 0.5 | 8,616 m | 118 / 180 | 0.63% | 1.14% | 1.87% |
| ≥ 0.7 | 8,435 m | 100 / 142 | 0.67% | 1.17% | 1.85% |

- All three routes are closed (start and end gaps of 0 m) and legal, with 0 m through forbidden zones and 0 m through canopies. Both waste boxes are visited, at 0.03 m and 0.08 m.
- The 61 targets not visited are all over the outside budget. They sit in blocks whose lanes end at a headland 5–8 m from any passage, or in dead-end lanes: P24 (11), P05, P12 (7 each), P06 (6), P10 and P22 (5 each). `route_targets.csv` gives each one's reason and the outside metres it would cost.
- **Budget sweep** on the same targets, before re-insertion and the 1.75 m attachment, using the robust budget:

  | budget | visited | length | outside, scene | if inter-rows stopped 1 m short |
  |---|---|---|---|---|
  | 0.8% | 97 | 7,968 m | 0.23% | 1.37% |
  | 1.0% | 108 | 8,181 m | 0.35% | 1.49% |
  | 1.2% | 121 | 8,611 m | 0.54% | 1.72% |
  | 1.6% | 141 | 9,952 m | 1.01% | 2.11% |

  1.2% is the largest budget that stays under 2% in the pessimistic case.
- **Lower bound.** A Held-Karp bound on plain grid metres (outside metres free, no gap walks) is 6,677 m, so the route is at most 36.6% above it. On the solver's own costs, the tour is 9,913 against a bound of 8,260: an ordering gap of at most 20%. Both bounds are loose. The first lets the route walk freely through headlands and skips the 56 gap walks. The second relaxes each target to its nearest candidate for every pair.
- **Component crossing.** The small passage component (2,137 m², SE) is 81 m from the big one, and no inter-row bridges them. The route reaches the lanes around it through the corridor. The two longest outside stretches of the official route are 13 m and 8 m (`site_zoom_outside0_13m.jpg` and `site_zoom_outside1_8m.jpg`). The 13 m stretch is a U-turn across the imagery's no-data edge at P05 (see the open items).

**Organizer examples scene** (`build_scene` of the examples ZIP). Targets: 12 synthetic points on reference row axes, 1 box in a passage, and 1 box 77 m inside the village's forbidden zone.

- The route is 2,001 m, 0.98% outside the scene's passable space, closed, and visits 7 of 14 targets. Solve time 6.9 s.
- The forbidden box is reported unreachable: "77.0 m from passable space, beyond the 12 m outside corridor".
- 6 row points are over budget. Only the 2 example tiles carry inter-rows, so the rest of each vineyard is outside passable space there.
- Held-Karp ordering gap 3.4%.

**`marcaj-export` on the current `scene.json`** works end to end, in 17 s for all three cutoffs.
- Its scored world is only the 2 reference tiles plus the organizer layers. The predicted inter-rows are excluded by `is_scored`, as they should be. So the all-POI route is 2,416 m, visits 8 of 191 targets, and is 1.12% outside passable space.
- It wrote `route.geojson` with the organizers' `crs` member, `route_targets.csv`, `measurements.csv` and `routes.geojson` to `work/route/export_test/`.
- It also reports 0.85 m through reference canopies. At 0.5 m cells, a step can clip a canopy corner. The brief rules this out, but the score does not count it.

**Fixed after this run:** when outside metres could not be reduced by dropping any target, the solver reported the previous iteration's line. It now keeps and reports the current tour.

## Sunday

Run from `backend/`:

```sh
uv run --frozen marcaj-scene --cvat <marcaj_export.zip>                 # -> data/generated/scene.json
uv run --frozen python -m marcaj.poi --scene ../data/generated/scene.json  # POIs from the corrected canopies (POI agent)
uv run --frozen marcaj-export --output-dir .. --poi ../data/generated/work/poi/poi.geojson
```

- `route.geojson` is the all-POI route; export refuses it if it would score 0.
- `route_targets.csv` lists every target as visited, over_budget, unreachable or missed, with its distance, the distances of its gap ends and the reason.
- `data/generated/routes.geojson` holds one route per cutoff (all, ≥ 0.5, ≥ 0.7). Each carries `label` and `source` "route", `min_confidence`, `length_m`, `targets`, `visited`, `unreachable`, `over_budget`, `outside_share`, `robust_outside_share`, `start_gap_m`, `end_gap_m`, `legal` and `closed`. A cutoff whose route would score 0 is left out and reported.
- Options: `--poi-confidence-over X`, `--outside-budget B` (default 0.012; the zero-score limit is 0.02) and `--routes PATH`.
- Waste targets are the scene's scored `waste` boxes from the Marcaj export. Budget about 3–5 min for the whole export.

## What the client needs (web/, api.py and scene.py are not mine)

- Show the routes from `data/generated/routes.geojson`. It is in EPSG:32635, so transform it for display the way `scene._TO_DISPLAY` does. The slider picks `min_confidence`. Show `length_m` and `visited` / `targets`.
- Show target status from `route_targets.csv`, or by joining POI ids: visited, over budget and unreachable in different colours, with the reason on hover. The farmer should see why a gap is off the route, and the map should not draw a shortcut.
- The official route is the `min_confidence: null` feature. It matches `route.geojson`.

## Open items and decisions

- **Budget (the user's call).** 1.2% of the eroded space is the default. At 1.6% the route visits about 20 more targets, but the pessimistic stress case passes 2%.
- **Whether headland access counts.** Blocks behind a headland (P24, P12, P06 and others) cost outside metres for any team, the organizers' own route included. The rule does not say whether the hidden target list excludes targets that cannot be legally reached. Ask in Slack.
- **No-data margins.** The corridor includes the black no-data margin inside the study area. Rows are clipped at the visible edge, so the route can U-turn across row ends we cannot see (the 13 m stretch at P05). The fix is to mask the corridor with the mosaic's valid pixels (`mosaic.load_mosaic`, RGB > 0).
- **Tour quality.** The ordering gap is at most 20% on the site, which is not proven tight. An Or-opt move that also re-picks candidates, or a 3-opt, could gain a few percent of length (efficiency is 10 points, and needs 90% coverage first).
- **For rows.py.** 48% of inter-row strips have no passage at either end. Every lane that dead-ends against a headland costs outside metres. If the organizers' inter-rows run to the headland edge where ours stop short, carrying inter-row ends across a headland narrower than about 2 m would cut outside metres. That needs evidence from the reference; none of the reference tiles shows a real row end.
