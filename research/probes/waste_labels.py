"""My visual classification of the ranked waste candidates (sheets r2_field_*.jpg, r2_town_*.jpg, zooms), written to
data/generated/work/waste/labels.json as {tile|x0,y0,x1,y1: {"label": likely|unsure|not, "why": ...}}.
Unlisted sheet numbers are `unsure` (small compact white blobs: stone, blossom or litter cannot be told apart at 2.5 cm)."""

import json

from marcaj.tiles import REPO_ROOT

W = REPO_ROOT / "data" / "generated" / "work" / "waste"
FIELD = {
    "likely": {2: "irregular white bag in tall grass", 9: "white plastic bag/sheet in grass", 11: "crumpled white bag",
               34: "crumpled blue plastic in grass", 39: "white sheet with straight edges in grass", 45: "crumpled white bag at vineyard edge",
               93: "white packaging with print", 129: "white bottle/cup and lid in grass", 136: "heap of white film by a yard"},
    "not": {3: "blue sliver in tree canopy", 5: "wall top", 7: "round concrete well lid", 8: "tile-border strip", 13: "wall",
            18: "round concrete well lid", 20: "round concrete well lid", 24: "glint on black mulch film", 29: "wall/post edge",
            30: "vine tube/stake on row", 32: "pale grass", 37: "flowering shrub", 38: "red machinery in compound", 41: "wall edge",
            49: "concrete well ring", 51: "round concrete well lid", 63: "mulch glint", 66: "mulch glint", 70: "limestone rocks",
            72: "roof/shadow edge", 78: "vine tube", 79: "vine tube", 86: "wall", 89: "mulch glint", 91: "pale edge of a track",
            94: "mulch glint", 95: "mulch glint", 96: "mulch glint", 103: "mulch glint", 104: "vehicle/implement", 108: "mulch glint",
            111: "mulch glint", 114: "mulch glint", 117: "mulch end", 120: "building", 121: "blue planter in a yard", 127: "mulch glint",
            128: "mulch glint", 130: "car edge", 140: "chalky soil on a track", 145: "pale soil by brush pile", 146: "mulch glint",
            150: "white stones", 151: "car", 152: "mulch glint", 158: "mulch glint", 159: "mulch glint", 160: "mulch glint"},
}
TOWN = {
    "likely": {16: "blue patterned plastic sheet at a yard fence"},
    "not": {1: "wall edge", 2: "shed", 3: "pool", 6: "roof", 7: "roof sheet", 8: "wall edge", 10: "round well lid", 11: "wall",
            12: "round well lid", 17: "pool", 19: "roof", 23: "pool", 24: "wall", 25: "tile-border strip", 26: "skylight", 31: "concrete slab",
            32: "roof", 34: "red machine in compound", 35: "tile-border strip", 38: "car", 39: "stones/concrete"},
}


def _key(candidate: dict) -> str:
    return f"{candidate['tile']}|{','.join(map(str, candidate['px']))}"


if __name__ == "__main__":
    labels = {}
    for sheet, table in (("r2_field.json", FIELD), ("r2_town.json", TOWN)):
        for number, candidate in enumerate(json.loads((W / sheet).read_text()), start=1):
            label = next((name for name, rows in table.items() if number in rows), "unsure")
            labels[_key(candidate)] = {"label": label, "why": table.get(label, {}).get(number, "small white/colour blob, ambiguous"),
                                       "sheet": f"{sheet[:-5]}#{number}"}
    control = json.loads((W / "control_examples.json").read_text())[0]
    labels[_key(control)] = {"label": "not", "why": "pale object at r006_c004 headland; organizer example tile has no waste", "sheet": "control#1"}
    (W / "labels.json").write_text(json.dumps(labels, indent=1))
    print({name: sum(v["label"] == name for v in labels.values()) for name in ("likely", "unsure", "not")})
