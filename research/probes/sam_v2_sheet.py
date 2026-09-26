"""Contact sheets for the SAM round-two probes: base canopies magenta, SAM output cyan. Evaluation only.
Run from backend/: uv run --frozen python ../research/probes/sam_v2_sheet.py recall NAME  |  split NAME
(`recall`: crops centred on the added canopies of work/sam_v2/recall_NAME.geojson, largest first, on the central and
organizer tiles; `split`: long labelled pieces with their SAM parts from work/sam_v2/split_NAME.geojson)."""

import json
import sys
from pathlib import Path

from shapely import STRtree
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent))
from canopy_v2_sheet import CYAN, MAGENTA, YELLOW, sheet  # noqa: E402
from sam_v2_lib import WORK, base_features, labels, tile_of  # noqa: E402

if __name__ == "__main__":
    kind, name = sys.argv[1], sys.argv[2]
    base = [shape(f["geometry"]) for f in base_features() if f["properties"]["label"] == "vineyard"]
    base_tree = STRtree(base)
    rows = [shape(f["geometry"]) for f in base_features() if f["properties"]["label"] == "row"]
    row_tree = STRtree(rows)
    items = []
    if kind == "recall":
        added = [shape(f["geometry"]) for f in json.loads((WORK / f"recall_{name}.geojson").read_text())["features"]]
        added_tree = STRtree(added)
        step = max(1, len(added) // 36)
        for g in sorted(added, key=lambda g: -g.area)[::step][:36]:
            window = g.centroid.buffer(4)
            overlays = [(base[i], MAGENTA) for i in base_tree.query(window)] + [(rows[i], YELLOW) for i in row_tree.query(window)] + \
                       [(added[i], CYAN) for i in added_tree.query(window)]
            items.append((g, f"{tile_of(g)[7:16]} {g.area:.2f} m2", overlays))
    else:
        parts = [shape(f["geometry"]) for f in json.loads((WORK / f"split_{name}.geojson").read_text())["features"]]
        parts_tree = STRtree(parts)
        for lab in sorted(labels(), key=lambda v: (v["properties"]["review_answer"], -v["properties"]["length_m"]))[:48]:
            g = shape(lab["geometry"])
            window = g.buffer(1.5)
            overlays = [(parts[i], CYAN) for i in parts_tree.query(window)]
            items.append((g, f"{lab['properties']['review_answer']} {lab['properties']['length_m']:.1f} m", overlays))
    print(sheet(items, WORK / f"sheet_{kind}_{name}.jpg"))
