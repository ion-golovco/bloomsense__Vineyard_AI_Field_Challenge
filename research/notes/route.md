# Walking route solver (26 September)

`marcaj.route` solves the challenge route. `marcaj-export` calls it and writes `route.geojson`, `route_targets.csv` and `routes.geojson`. `routing.check_route` stays the validator. Probe: `research/probes/route_probe.py`. Outputs and renders are in `data/generated/work/route/`.

## Design

**Planning space.** The route plans on passages plus the scene's inter-rows eroded by 0.3 m, minus forbidden zones (`robust_space`). The organizers' inter-rows may be tighter than ours. Planning on our own inter-rows put 0.97% of the site route outside them, but 1.89% outside the eroded ones, because every lane entry and exit adds its 0.3 m.

**Grid.** 0.5 m cells, 1.32 M nodes on the site, built in 4–5 s.
- A raster 3× finer gives each cell's passable cover. It also gives each step's outside share, sampled at 8 points along the step.
  - The cell-average cover used before missed steps that clip an eroded lane's corner: it put a tour's outside metres at 48 m against 87 m exact.
  - Sampled along the steps, the estimate is 86 m. A planner that optimises against the samples still finds their gaps (63 m sampled against 78 m exact), which is why every leg the tour uses is also measured exactly (see "Choosing the targets").
- Walkable cells: passable space, plus a 12 m outside corridor around it.
- The corridor excludes forbidden zones (all touched), row strips (axis ± 0.3 m, the trellis), canopies, and the imagery's no-data margin. The margin mask uses the 0.2 m mosaic, R + G + B > 30 with holes filled. It lets the route reach blocks behind a bare headland.
- A step costs 1 per metre at a lane centre and up to 2 within 0.75 m of the passable edge, so the route keeps to lane centres. Outside passable space it costs 6 per metre. A canopy that overlaps passable space also costs 6 but stays walkable.
- Steps go in 16 directions (length error at most 2.7%). No step cuts past a blocked cell, so the route cannot slip through a 1-cell row strip.

**Lanes are implicit.** No centreline graph is built. A shortest path enters a lane from its cheaper end, visits several targets in one lane, U-turns around a row end across a headland when that is cheaper than walking back, and crosses into the small passage component. All of this falls out of the costs.

**Targets.**
- Each target attaches to the cheapest walkable cell within 1.75 m of it, one per local component, up to 3. A row gap usually gets one cell in each neighbouring lane.
- With the 0.2 m simplification the line stays within 2 m. The status in `route_targets.csv` is always measured on the final line.
- Waste boxes are targeted at their centroid.
- A gap POI's `gap_start` and `gap_end` get an out-and-back walk along the lane when the route does not already pass them, since the organizers' point may sit anywhere in the gap. A walk is only added if it stays entirely inside the planning space; otherwise it is skipped. The earlier walks added 23 m of outside metres that nothing budgeted. Both ends are now within 2 m on 120 of 125 visited gaps.

**Matrices.** One scipy Dijkstra per candidate stop, 0.3 s each on 1.3 M nodes (it holds the GIL, so threads don't help). Each run gives cost, metres and outside metres to every stop: the path tree is traced for all stops at once, and only its used part is kept, so legs are rebuilt without rerunning Dijkstra.

**Tour.** A tour for a fixed set of targets:
1. Nearest neighbour, then 2-opt and Or-opt (runs of 1–3 stops, both orientations) on the group-minimum costs.
2. A Viterbi pass re-picks each target's candidate for the current order.
3. 2-opt and Or-opt again on the chosen stops, repeated until the cost stops falling.

**Choosing the targets (`route`), under `OUTSIDE_BUDGET` = 1.2% outside the eroded space.**
- **Build up from START (`build=True`).** `_add` inserts targets at their cheapest place while the budget holds.
  - Targets that add no outside metres go first.
  - Then the one with the most targets per outside metre goes next. The count includes the pool targets its insertion makes free: the other gaps of a lane behind a headland share that lane's access.
- **Drop step.** It is used when a starting tour is over budget. It drops the stop, or run of stops behind one outside access, that saves the most outside metres per target. If no removal saves anything, it rebuilds from START.
- **Exact legs.** An unmeasured leg's raster outside metres are scaled by 1.3. After each round, every leg of the tour is measured exactly against the eroded space and kept on the plan. A tour that turns out over budget is trimmed on the exact numbers, for at most 8 rounds.

**Across cutoffs (`_best_routes`).** Each POI confidence cutoff is built on its own. Then it is warm-started from every other cutoff's tour, and it is also offered that cutoff's line unchanged. The best route is kept, ranked by fitting the budget, then most targets visited, then shortest. A route over a subset of targets is a legal route for the full set, so the all-POI route now visits at least as many targets as any cutoff's route.

**Per-field routes.** One closed route START → that field's targets → START for each field (`vineyard_id`) and cutoff. They reuse the same plan and the same choice procedure, which takes about 70 s for all 23 fields.

**Validator fix (`routing.py`).** `check_route` used `route.difference(space).length`. An overlay dissolves a line that retraces itself, so an out-and-back spur outside counted only once. That undercounts exactly the spurs this solver makes. It now sums per segment (`_length_in`), and it also reports `forbidden_m` and `canopy_m`. It takes an explicit `targets` list, because the POIs are not scene features, and optional `spaces` (`check_spaces`), so checking 71 routes computes the unions only once.

## Controls (`route_probe.py controls`; all fail as they must)

1. A straight line START → an old waste candidate 28.7 m inside P25 → START: 78.0% outside, `legal=False`.
2. A retraced 40 m spur: 40.4 m outside per segment, against 20.2 m from the old whole-line overlay.
3. A route that ends 8 m from START: `closed=False`.

## Results

**Site prediction** (final prediction, 26 September ~12:00). `predictions.geojson` is relabelled `source: dev` for development only. Targets: 177 challenge POIs plus the 2 in-block waste boxes, 179 in total. `route_probe.py site` takes 195 s: planning 120 s (374 stops) plus 3 site routes and 68 field routes. Peak memory 3.4 GB.

| POI confidence | before: length, visited | after: length, visited / targets | outside, scene | outside, eroded 0.3 m | if inter-rows stopped 1 m short |
|---|---|---|---|---|---|
| all (official) | 8,153 m, 97 | **9,533 m, 127 / 179** | 0.62% | 1.15% | 1.93% |
| ≥ 0.5 | 8,603 m, 111 | **9,430 m, 122 / 172** | 0.60% | 1.14% | 1.92% |
| ≥ 0.7 | 7,901 m, 87 | **9,430 m, 93 / 132** | 0.60% | 1.14% | 1.92% |

- **Before:** the committed solver on the same targets. Its all-POI route visited fewer targets than the ≥ 0.5 route.
- **After:** the all-POI route visits the most. The ≥ 0.7 cutoff took the ≥ 0.5 line as it is, because it visits more of its targets than the ≥ 0.7 route built on its own.
- All routes are closed (gap 0 m) and legal, with 0 m forbidden and 0 m through canopies. Both waste boxes are visited.
- **Unreachable:** 5 targets have "no walkable connection to START". Four are in P06 at the imagery edge, reachable before only across no-data, and one is in P32, enclosed. The other 47 unvisited targets are over the budget.
- **Lower bound:** Held-Karp on plain grid metres gives 7,238 m, so the route is at most 31.7% above it. The ordering gap on the solver's own costs is at most 18.9%. Both bounds are loose.

**Field routes:** 68 routes (3 cutoffs, fewer where a cutoff leaves a field without targets) over 23 fields. All 68 are legal, closed and within the robust budget, and 28 of them visit every target of their field. Examples:
- P01: 2,476 m, 20 of 20
- P02: 1,082 m, 13 of 13
- P03: 812 m, 10 of 10

**Organizer examples scene** (synthetic targets: 12 on reference row axes, 1 in a passage, 1 inside the forbidden village): the route is 1,980 m, 0.80% outside, closed, and visits 6 of 14 in 9 s. The forbidden box is unreachable ("77.0 m from passable space"). Ordering gap at most 0.5%.

**`marcaj-export` on the current `scene.json`** works end to end in 22 s. It wrote `route.geojson`, `route_targets.csv`, `measurements.csv` and 71 routes (3 site, 68 field) to `work/route/export_test/`. That scene's scored inter-rows are only the 2 reference tiles, so the all-POI route visits only 18 of 177, at 1.10% outside. It also reports 0.91 m through reference canopies: a 0.5 m step can clip a canopy corner. The brief rules this out, but the score does not count it.

## Sunday

Run from `backend/`:

```sh
uv run --frozen marcaj-scene --cvat <marcaj_export.zip>                 # -> data/generated/scene.json
uv run --frozen python -m marcaj.poi --scene ../data/generated/scene.json  # POIs from the corrected canopies (POI agent)
uv run --frozen marcaj-export --output-dir .. --poi ../data/generated/work/poi/poi.geojson
```

- `route.geojson` is the all-POI route; export refuses it if it would score 0.
- `route_targets.csv` lists every target as visited, over_budget, unreachable or missed, with its distance, the distances of its gap ends and the reason.
- `data/generated/routes.geojson` holds the 3 site routes (`scope` "site", `vineyard_id` "") and the per-field routes (`scope` "field"), for each cutoff (all, ≥ 0.5, ≥ 0.7).
  - Each route carries `label` and `source` "route", `scope`, `vineyard_id`, `min_confidence`, `length_m`, `targets`, `visited`, `unreachable` (= targets − visited), `over_budget`, `outside_share`, `robust_outside_share`, `start_gap_m`, `end_gap_m`, `legal` and `closed`.
  - A route that would score 0 is left out and reported.
- Options: `--poi-confidence-over X`, `--outside-budget B` (default 0.012; the zero-score limit is 0.02) and `--routes PATH`.
- Waste targets are the scene's scored `waste` boxes from the Marcaj export. Budget about 3.5 min for the whole export on a full corrected scene.

## What the client needs (web/, api.py and scene.py are not mine)

- Show the routes from `data/generated/routes.geojson`. It is in EPSG:32635, so transform it for display the way `scene._TO_DISPLAY` does. The slider picks `min_confidence`, and a field's route is `scope` "field" with its `vineyard_id`. Show `length_m` and `visited` / `targets`.
- Show target status from `route_targets.csv`, or by joining POI ids: visited, over budget and unreachable in different colours, with the reason on hover. The farmer should see why a gap is off the route, and the map should not draw a shortcut.
- The official route is the `scope: "site"`, `min_confidence: null` feature. It matches `route.geojson`.

## Open items and decisions

- **Budget (the user's call).** 1.2% of the eroded space is the default. The pessimistic stress case is at 1.93%, just under 2%. An earlier sweep found about 20 more targets at 1.6%, but that stress case then passed 2%.
- **Gap ends.** 5 of the 125 visited gaps have an end more than 2 m from the route, because the walk to it would leave the planning space.
- **Whether headland access counts.** Blocks behind a headland (P24, P12, P06 and others) cost outside metres for any team, the organizers' own route included. The rule does not say whether the hidden target list excludes targets that cannot be legally reached. Ask in Slack.
- **Tour quality.** The ordering gap is at most 19% on the site, which is not proven tight. An Or-opt move that also re-picks candidates, or a 3-opt, could gain a few percent of length (efficiency is 10 points, and needs 90% coverage first).
- **For rows.py.** 48% of inter-row strips have no passage at either end. Every lane that dead-ends against a headland costs outside metres. If the organizers' inter-rows run to the headland edge where ours stop short, carrying inter-row ends across a headland narrower than about 2 m would cut outside metres. That needs evidence from the reference; none of the reference tiles shows a real row end.
