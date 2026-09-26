"""Do straight multi-row crossings (single row hops chained across aligned gaps, `route._hop_edges` rows_max) reach
the site route's unvisited targets with less walking outside the robust passable space? For each grid (no hops,
hops across 1 row as the official route uses, and across up to 3 and 6 aligned rows, HOP_PENALTY_M per row
crossed): per unvisited target of the current site route (data/generated/routes.geojson), the least outside metres
on any path from that route to it (one way), and the outside metres and rows crossed on its cheapest-cost path.
`weights`: the site routes (all POIs, >= 0.5, >= 0.7; no hops) chosen as marcaj-export chooses them, with outside
metres weighted OUTSIDE_WEIGHT 6 (the default) and 20, since the cheapest-cost path to a target is not its
least-outside path. Report only; it changes no route. Run from backend/:
uv run --frozen python -u ../research/probes/route_crossings_probe.py [crossings | weights [W1,W2 [HOP_PENALTY]]]"""

import json
import sys
import time

import numpy as np
from scipy.sparse import coo_matrix, triu
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import shape

from marcaj import route as solver
from marcaj.scene import OVERLAYS, load_projected_scene
from marcaj.tiles import REPO_ROOT

OUT = REPO_ROOT / "data" / "generated" / "work" / "route_v2" / "crossings.json"


def main() -> None:
    features, name = solver.planning_features(load_projected_scene())
    site = next(f for f in json.loads((REPO_ROOT / "data" / "generated" / "routes.geojson").read_text())["features"]
                if f["properties"]["scope"] == "site" and f["properties"]["min_confidence"] is None)
    line = shape(site["geometry"])
    todo = [item for item in site["properties"]["target_status"] if item["status"] != "visited"]
    points = {t["id"]: t["point"] for t in solver.route_target_features(features) + solver.poi_targets(OVERLAYS[0])}
    print(f"world {name}; site route {line.length:.0f} m, {len(todo)} unvisited targets", flush=True)
    samples = [line.interpolate(d) for d in np.arange(0, line.length, 0.5)]
    result = {}
    for rows_max in (0, 1, 3, 6):
        started = time.perf_counter()
        grid = solver.build_grid(features, None, None if rows_max == 0 else solver.HOP_PENALTY_M, None, max(rows_max, 1))
        upper = triu(grid.graph).tocoo()
        a, b = upper.row, upper.col
        length = np.hypot(grid.rows[a] - grid.rows[b], grid.cols[a] - grid.cols[b]) * solver.GRID_M
        share = np.concatenate([grid.outside(a[i:i + solver.CHUNK], b[i:i + solver.CHUNK]) for i in range(0, a.size, solver.CHUNK)])
        weight = length * share + 1e-3 * length  # outside metres, ties broken by length
        n = grid.rows.size
        outside_graph = coo_matrix((np.r_[weight, weight], (np.r_[a, b], np.r_[b, a])), shape=(n, n)).tocsr()
        sources = sorted({node for p in samples if (hit := solver.snap(grid, p, 1.0)) for node in [hit[0]]})
        least = dijkstra(outside_graph, indices=sources, min_only=True)
        cost, pred, _ = dijkstra(grid.graph, indices=sources, min_only=True, return_predecessors=True)
        rows = {}
        for item in todo:
            stops = solver._attach(grid, points[item["id"]])
            reachable = [s for s in stops if np.isfinite(cost[s])]
            if not reachable:
                rows[item["id"]] = {"reachable": False}
                continue
            stop = min(reachable, key=lambda s: cost[s])
            path = [stop]
            while pred[path[-1]] >= 0:
                path.append(int(pred[path[-1]]))
            path = np.array(path[::-1])
            steps = np.hypot(np.diff(grid.rows[path]), np.diff(grid.cols[path])) * solver.GRID_M
            path_out = float((steps * grid.outside(path[:-1], path[1:])).sum()) if path.size > 1 else 0.0
            pairs = [(min(p, q), max(p, q)) for p, q in zip(path[:-1].tolist(), path[1:].tolist())]
            hops = [pair for pair in pairs if pair in grid.hop_tier]
            rows[item["id"]] = {
                "least_outside_m": round(float(min(least[s] for s in reachable)), 1), "cheapest_outside_m": round(path_out, 1),
                "cheapest_m": round(float(steps.sum()), 1), "hops": len(hops), "rows_crossed": sum(grid.hop_rows.get(p, 1) for p in hops),
            }
        result[rows_max] = rows
        print(f"hops across up to {rows_max} rows: {n} nodes, {len(grid.hop_tier)} crossings ({len(grid.hop_rows)} multi-row), "
              f"{time.perf_counter() - started:.0f} s", flush=True)
        del grid, upper, outside_graph, least, cost, pred
    OUT.write_text(json.dumps(result, indent=1))
    print(f"{'target':34} " + " ".join(f"{f'k={k} least/cheap (x)':>24}" for k in result))
    for item in todo:
        cells = []
        for k in result:
            r = result[k][item["id"]]
            cells.append(f"{r['least_outside_m']:6.1f} / {r['cheapest_outside_m']:6.1f} ({r['rows_crossed']:2d})" if r.get("reachable", True) else f"{'unreachable':>24}")
        print(f"{item['id']:34} " + " ".join(f"{c:>24}" for c in cells))


def weights() -> None:
    features, name = solver.planning_features(load_projected_scene())
    targets = solver.route_target_features(features) + solver.poi_targets(OVERLAYS[0])
    result = {}
    hops = float(sys.argv[3]) if len(sys.argv) > 3 else None  # e.g. `weights 6,20 50`: row hops at 50 m
    for weight in [float(item) for item in (sys.argv[2] if len(sys.argv) > 2 else "6,20").split(",")]:
        solver.OUTSIDE_WEIGHT = solver.HOP_OUTSIDE_WEIGHT = weight
        started = time.perf_counter()
        prepared = solver.plan(features, targets, hops)
        solved = solver.routes_by_confidence(features, targets, (None, 0.5, 0.7), fields=False, hop_penalty=hops, prepared=prepared)
        result[weight] = []
        for feature, line, rows, report in solved:
            p = feature["properties"]
            result[weight].append({key: p[key] for key in ("min_confidence", "length_m", "targets", "visited", "over_budget", "unreachable",
                                                            "outside_share", "robust_outside_share", "legal", "closed")}
                                  | {"visited_ids": sorted(item["id"] for item in rows if item["status"] == "visited")})
            print(f"weight {weight:g}, POI >= {p['min_confidence']}: {p['length_m']:.0f} m, {p['visited']}/{p['targets']} visited, "
                  f"{p['outside_share']:.2%} outside ({p['robust_outside_share']:.2%} robust), legal {p['legal']}, closed {p['closed']}", flush=True)
        print(f"weight {weight:g}: {time.perf_counter() - started:.0f} s", flush=True)
        del prepared, solved
    (OUT.parent / f"weights_{'_'.join(f'{w:g}' for w in result)}{'' if hops is None else f'_hops{hops:g}'}.json").write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    {"crossings": main, "weights": weights}[sys.argv[1] if len(sys.argv) > 1 else "crossings"]()
