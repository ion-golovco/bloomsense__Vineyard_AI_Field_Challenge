"""Ranks the scan's candidates for visual review and renders the sheets r2_field_*.jpg (>= 15 m from buildings,
top 160) and r2_town_*.jpg (top 40). The score is a review order, not the detector: size, solidity, isolation,
colour (white by clipped share, blue, vivid, dark), 0 on pale structures, x0.1 for small white blobs on a row axis.
Site-wide v1 scan: reads candidates_sitewide_v1.json. Run from backend/"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from waste_sheet import sheets  # noqa: E402

from marcaj.tiles import REPO_ROOT  # noqa: E402

W = REPO_ROOT / "data" / "generated" / "work" / "waste"


def score(c: dict) -> float:
    area = c["area_m2"]
    if c["structure"] > 0.3 or (c["kind"] in ("blue", "vivid") and area > 1.5):
        return 0.0
    size = min(area / 0.04, 1) ** 0.5 * (1 if area <= 3 else 0.3)
    isolated = math.exp(-max(c["density"] - area / 9, 0) / 0.03)
    colour = {"white": 0.4 + 0.6 * min(c["clipped"] / 0.3, 1), "blue": 1.0, "vivid": 0.9, "dark": 0.3}[c["kind"]]
    tube = 0.1 if c["kind"] == "white" and c["row_m"] < 0.35 and area < 0.1 else 1.0
    return round(size * min(c["fill"] / 0.5, 1) * isolated * colour * tube, 3)


if __name__ == "__main__":
    candidates = json.loads((W / "candidates_sitewide_v1.json").read_text())
    for c in candidates:
        c["score"] = score(c)
    for name, near, count in (("r2_field", False, 160), ("r2_town", True, 40)):
        ranked = sorted((c for c in candidates if (c["forbidden_m"] < 15) == near), key=lambda c: -c["score"])[:count]
        (W / f"{name}.json").write_text(json.dumps(ranked))
        print(name, len(ranked), sheets(ranked, W / name)[-1])
