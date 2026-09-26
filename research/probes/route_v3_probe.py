"""Route v3: the outside weight trial and per-block coverage on the client's world (`route.planning_features` of the
scene at MARCAJ_SCENE_PATH, POIs from MARCAJ_POI or work/poi/poi.geojson). Site routes as marcaj-export picks them
(hops at HOP_PENALTY_M and none, keep the better: fits the budget, more visited, shorter). Writes data/generated/work/route_v3/.
Run from backend/:
uv run --frozen python -u ../research/probes/route_v3_probe.py weights 20,40,100"""

import json
import os
import sys
import time
from pathlib import Path

from marcaj import route as solver
from marcaj.scene import OVERLAYS, load_projected_scene, scene_path
from marcaj.tiles import REPO_ROOT

OUT = REPO_ROOT / "data" / "generated" / "work" / "route_v3"
OUT.mkdir(parents=True, exist_ok=True)
POI = Path(os.environ.get("MARCAJ_POI", OVERLAYS[0]))
if "ROUTE_BLOCK_VALUE" in os.environ:
    solver.BLOCK_VALUE = float(os.environ["ROUTE_BLOCK_VALUE"])
KEYS = ("min_confidence", "length_m", "targets", "visited", "over_budget", "unreachable", "outside_share", "robust_outside_share",
        "end_gap_m", "hops", "legal", "closed")


def world() -> tuple[list[dict], list[dict]]:
    features, name = solver.planning_features(load_projected_scene())
    targets = solver.route_target_features(features) + solver.poi_targets(POI)
    print(f"world {name} of {scene_path()}, POIs {POI}: {len(features)} features, {len(targets)} targets", flush=True)
    return features, targets


def rank(item: tuple) -> tuple:
    p = item[0]["properties"]
    return (p["legal"] and p["closed"] and p["robust_outside_share"] <= solver.OUTSIDE_BUDGET + 1e-9, p["visited"], -p["length_m"])


def site_routes(features: list[dict], targets: list[dict]) -> list[tuple]:
    """The three site routes (all, >= 0.5, >= 0.7) as marcaj-export picks them."""
    solved = []
    for hops in (solver.HOP_PENALTY_M, None):
        prepared = solver.plan(features, targets, hops)
        solved.append(solver.routes_by_confidence(features, targets, (None, 0.5, 0.7), fields=False, hop_penalty=hops, prepared=prepared))
        del prepared
    return [max(pair, key=rank) for pair in zip(*solved)]


def weights() -> None:
    features, targets = world()
    block = {t["id"]: t.get("vineyard_id") or "" for t in targets}
    result = {}
    for weight in [float(item) for item in sys.argv[2].split(",")]:
        solver.OUTSIDE_WEIGHT = solver.HOP_OUTSIDE_WEIGHT = weight
        started = time.perf_counter()
        result[weight] = []
        for feature, line, rows, report in site_routes(features, targets):
            p = feature["properties"]
            missed = [item | {"vineyard_id": block[item["id"]]} for item in p["target_status"] if item["status"] != "visited"]
            result[weight].append({key: p[key] for key in KEYS} | {"missed": missed})
            print(f"weight {weight:g} block value {solver.BLOCK_VALUE:g}, POI >= {p['min_confidence']}: {p['length_m']:.0f} m, {p['visited']}/{p['targets']} visited, "
                  f"{p['robust_outside_share']:.2%} robust / {p['outside_share']:.2%} plain outside, over budget {p['over_budget']}, "
                  f"unreachable {p['unreachable']}, end gap {p['end_gap_m']:.2f} m, {p['hops']} hops, legal {p['legal']}, closed {p['closed']}", flush=True)
        print(f"weight {weight:g}: {time.perf_counter() - started:.0f} s", flush=True)
    (OUT / f"weights_{'_'.join(f'{w:g}' for w in result)}_block{solver.BLOCK_VALUE:g}.json").write_text(json.dumps(result, indent=1))


def blocks() -> None:
    """Per block (vineyard_id) of the site all-POI route in a routes file (argv[2]): targets, visited, and each
    unvisited target's status, outside metres its insertion needs, and distance off the scene's and the robust passable space."""
    from shapely.geometry import shape
    from marcaj.routing import check_spaces
    features, targets = world()
    passable, robust = check_spaces(features)["passable"], solver.robust_space(features)
    by_id = {t["id"]: t for t in targets}
    site = next(f for f in json.loads(Path(sys.argv[2]).read_text())["features"]
                if f["properties"]["scope"] == "site" and f["properties"]["min_confidence"] is None)
    line = shape(site["geometry"])
    rows = {}
    for item in site["properties"]["target_status"]:
        target = by_id[item["id"]]
        rows.setdefault(target.get("vineyard_id") or "(none)", []).append(item | {
            "off_passable_m": round(passable.distance(target["point"]), 1), "off_robust_m": round(robust.distance(target["point"]), 1),
            "route_m": round(line.distance(target["point"]), 1)})
    lines = []
    for block, items in sorted(rows.items()):
        visited = sum(item["status"] == "visited" for item in items)
        lines.append(f"{block}: {visited}/{len(items)} visited{'   <-- none' if not visited else ''}")
        lines += [f"    {i['id']:34} {i['status']:12} needs {i['needs_outside_m'] or '-':>5} m outside, {i['off_passable_m']:5.1f} m off passable "
                  f"({i['off_robust_m']:.1f} robust), {i['route_m']:6.1f} m from route; {i['reason']}" for i in items if i["status"] != "visited"]
    print("\n".join(lines))
    (OUT / f"blocks_{Path(sys.argv[2]).stem}.txt").write_text("\n".join(lines) + "\n")


def request() -> None:
    """The on-request path as the API runs it (`request_route` on the scene's cached plan, API token, row hops): closed
    from START, open start -> end, one field, waste only; each must be closed at its endpoints, legal and within the
    robust budget. Then the controls that must fail: the site route plus an outside spur to 3% scores 0, and
    marcaj-export (`export._write_route`) refuses to write it."""
    import numpy as np
    from shapely.geometry import LineString, Point
    from marcaj import export
    from marcaj.routing import _geometries, check_route, check_spaces
    features, targets = world()
    path = scene_path()
    token = f"{path}:{path.stat().st_size}:{path.stat().st_mtime_ns}:{solver.planning_features(load_projected_scene())[1]}"  # api._world's
    start = _geometries(features, "start")[0]
    lane = solver.robust_space(features)
    far = max((t["point"] for t in targets if lane.distance(t["point"]) < 0.1), key=lambda p: p.distance(start))
    field = max({t["vineyard_id"] for t in targets} - {""}, key=lambda v: sum(t["vineyard_id"] == v for t in targets))
    cases = {"site closed": (None, None, None), "site open start -> far lane": (None, far, None),
             f"field {field}": (None, None, {i for i, t in enumerate(targets) if t["vineyard_id"] == field}),
             "waste only, from far lane": (far, None, {i for i, t in enumerate(targets) if t["label"] == "waste"})}
    site_line = None
    for name, (a, b, include) in cases.items():
        started = time.perf_counter()
        line, rows, report = solver.request_route(features, targets, token, a, b, include)
        ok = report["legal"] and report["closed"] and report["robust_outside_share"] <= solver.OUTSIDE_BUDGET + 1e-9
        print(f"{name}: {line.length:.0f} m, {report['visited']}/{len(rows)} visited, {report['robust_outside_share']:.2%} robust / "
              f"{report['outside_share']:.2%} plain, start/end gap {report['start_gap_m']:.2f}/{report['end_gap_m']:.2f} m, {report['hops']} hops, "
              f"legal {report['legal']}, closed {report['closed']}, {time.perf_counter() - started:.1f} s -> {'PASS' if ok else 'FAIL'}", flush=True)
        assert ok, name
        site_line = site_line or line
    spaces = check_spaces(features)
    reach = 0.015 * site_line.length / 0.97
    for angle in np.linspace(0, 2 * np.pi, 72, endpoint=False):
        tip = Point(start.x + reach * np.cos(angle), start.y + reach * np.sin(angle))
        if LineString([start, tip]).difference(spaces["passable"]).length > 0.99 * reach - 1:
            break
    coords = list(site_line.coords)
    spur = LineString(coords[:1] + [tip.coords[0], coords[0]] + coords[1:])
    report = check_route(spur, features, [t["point"] for t in targets], spaces)
    print(f"control, site route + {2 * reach:.0f} m outside spur: {report['outside_share']:.2%} outside, scores {report['scores']}, legal {report['legal']} (must be False)")
    assert report["outside_share"] > 0.02 and not report["scores"] and not report["legal"]
    fake = solver._route_feature(None, "", spur, [], report | {"robust_outside_share": 0.0, "robust_outside_m": 0.0, "budget": solver.OUTSIDE_BUDGET,
                                                                "hops": 0, "hop_m": 0.0, "open": False})
    real, export.routes_by_confidence = export.routes_by_confidence, lambda *args, **kwargs: [(fake, spur, [], report)]
    try:
        export._write_route({"features": features}, OUT / "refusal", None, None, solver.OUTSIDE_BUDGET, OUT / "refusal" / "routes.geojson", None)
        raise AssertionError("marcaj-export wrote a route that scores 0")
    except ValueError as error:
        print(f"control, marcaj-export on that route: refused ({str(error)[:60]}...) (must refuse)")
    finally:
        export.routes_by_confidence = real
    assert not (OUT / "refusal" / "route.geojson").exists()


def verify() -> None:
    """Every route in a routes file (argv[2]) re-checked on the world: closed within 5 m, legal (check_route), robust
    outside within the budget, and its counts and target_status equal to what the line actually visits."""
    from shapely.geometry import shape
    from marcaj.routing import check_route, check_spaces
    features, targets = world()
    spaces, robust = check_spaces(features), solver.robust_space(features)
    points = {t["id"]: t for t in targets}
    failures, collection = [], json.loads(Path(sys.argv[2]).read_text())
    for feature in collection["features"]:
        p, line = feature["properties"], shape(feature["geometry"])
        ids = [t["id"] for t in targets if (not p["vineyard_id"] or t.get("vineyard_id") == p["vineyard_id"])
               and (p["min_confidence"] is None or t.get("confidence") is None or t["confidence"] >= p["min_confidence"])]
        report = check_route(line, features, [points[i]["point"] for i in ids], spaces)
        share = 1 - solver._length_in(line, robust) / line.length
        status = {item["id"]: item["status"] for item in p["target_status"]}
        truly = {i for i in ids if line.distance(points[i]["point"]) <= solver.VISIT_RADIUS_M}
        problems = [name for name, bad in (
            ("not closed", not report["closed"]), ("not legal", not report["legal"]), ("robust over budget", share > solver.OUTSIDE_BUDGET + 1e-6),
            ("targets", p["targets"] != len(ids) or set(status) != set(ids)), ("visited", not p["visited"] == report["visited"] == len(truly)),
            ("status visited", {i for i, s in status.items() if s == "visited"} != truly),
            ("unreachable count", p["unreachable"] != list(status.values()).count("unreachable")),
            ("over_budget count", p["over_budget"] != list(status.values()).count("over_budget"))) if bad]
        if problems:
            failures.append((p["scope"], p["vineyard_id"], p["min_confidence"], problems))
        if p["scope"] == "site":
            print(f"site {p['min_confidence']}: {line.length:.0f} m, {report['visited']}/{len(ids)}, {share:.2%} robust / {report['outside_share']:.2%} plain, "
                  f"gaps {report['start_gap_m']:.2f}/{report['end_gap_m']:.2f} m, canopy {report['canopy_m']:.2f} m, forbidden {report['forbidden_m']:.2f} m, "
                  f"over budget {p['over_budget']}, unreachable {p['unreachable']}, hops {p['hops']}")
    routes = collection["features"]
    print(f"{len(routes)} routes: {len(routes) - len(failures)} pass (closed, legal, robust <= {solver.OUTSIDE_BUDGET:.1%}, counts and target_status "
          f"match the line); visited {sum(f['properties']['visited'] for f in routes)}, length {sum(f['properties']['length_m'] for f in routes):,.0f} m")
    for failure in failures:
        print("FAIL", failure)
    assert not failures


if __name__ == "__main__":
    {"weights": weights, "blocks": blocks, "request": request, "verify": verify}[sys.argv[1]]()
