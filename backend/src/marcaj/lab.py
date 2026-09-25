"""Lab notebook: review model output over the imagery, record verdicts, re-run, re-judge.
Needs the `lab` dependency group: `uv run --group lab jupyter lab`."""

import json
import socket
import threading
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import ipywidgets as widgets
import uvicorn
from ipyleaflet import DrawControl, GeoJSON, LayersControl, Map, TileLayer, WidgetControl
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

from marcaj import review
from marcaj.cvat import build_scene
from marcaj.judge import judge, print_report
from marcaj.scene import DEFAULT_SCENE
from marcaj.tiles import DATA_DIR, PIXEL_M, TILE_PX, load_tiles
from marcaj.layers import load_layers
from marcaj.plots import PlotParams, load_excess
from marcaj.predict import PREDICTIONS_PATH, predict, write

EXAMPLES = DATA_DIR / "05_examples" / "siret3_examples_cvat.zip"
GRID_LEFT, GRID_TOP = 628992.0, 5221222.4
_TO_DISPLAY = Transformer.from_crs("EPSG:32635", "EPSG:4326", always_xy=True)
_TO_WORLD = Transformer.from_crs("EPSG:4326", "EPSG:32635", always_xy=True)
_WRONG_REASONS = ["not a vineyard", "wrong outline", "two plots merged", "one plot split", "wrong attribute", "wrong block id", "other"]
_DRAW_MARKS = [("plot: vineyard, bare soil", "plot:vineyard"), ("plot: vineyard, overgrown", "plot:overgrown"),
               ("plot: orchard", "plot:orchard"), ("plot: other (field, garden)", "plot:other")] + [
    (f"missed: {label}", f"missed:{label}") for label in ["block", "vineyard", "row", "interrow_area", "waste"]]
_VERDICT_COLOURS = {"right": "#16A34A", "vineyard": "#16A34A", "wrong": "#DC2626", "no_vineyard": "#DC2626", "missed": "#F59E0B",
                    "overgrown": "#06B6D4", "orchard": "#8B5CF6", "other": "#94A3B8"}
_LABEL_COLOURS = {"vineyard": "#7CFC00", "row": "#FF3B30", "interrow_area": "#22D3EE", "waste": "#E8772E", "passage": "#A7906A", "forbidden": "#CC1F1F", "study_area": "#F5C400", "start": "#CC1F1F"}


def _display(geometry: dict[str, Any]) -> dict[str, Any]:
    return mapping(transform(_TO_DISPLAY.transform, shape(geometry)))


def _collection(features: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": _display(f["geometry"]), "properties": {**f["properties"], **extra, "lab_index": i}} for i, f in enumerate(features)
    ]}


def _imagery_url() -> str:
    """Reuses the client app's imagery on :8000 if it is up, otherwise serves it from this kernel."""
    for port in (8000, 8765):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return f"http://127.0.0.1:{port}/api/imagery/{{z}}/{{x}}/{{y}}.png"
    server = uvicorn.Server(uvicorn.Config("marcaj.api:app", host="127.0.0.1", port=8765, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return "http://127.0.0.1:8765/api/imagery/{z}/{x}/{y}.png"


class Lab:
    def __init__(self, reviewer: str = "") -> None:
        self.tiles = load_tiles()
        self.tile_names = {tile.name for tile in self.tiles}
        base = build_scene([EXAMPLES], [], tiles=self.tiles)
        self.base_features = base["features"]
        self.reference = [f for f in self.base_features if f["properties"].get("source") == "reference"]
        self.organizer = [f for f in self.base_features if f["properties"].get("source") == "organizer" and f["properties"]["label"] != "tile"]
        self.layers, self.excess = load_layers(), load_excess()
        self.params = PlotParams()
        self.predictions: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []
        self.selected: dict[str, Any] | None = None
        self.selected_mark = ""
        self.clicked_tile = ""
        self.last_ids: list[str] = []
        self._build_map(reviewer)
        self.run(self.params)

    def _build_map(self, reviewer: str) -> None:
        self.map = Map(center=(47.1215, 28.709), zoom=16, max_zoom=24, scroll_wheel_zoom=True, layout=widgets.Layout(height="720px"))
        self.map.add(TileLayer(url=_imagery_url(), max_zoom=24, max_native_zoom=22, name="Sireț3",
                               attribution="Sireț3 CC BY 4.0, 3DATA COLLECT / OpenAerialMap"))
        colour = lambda feature: _LABEL_COLOURS.get(feature["properties"]["label"], "#FFFFFF")
        self.organizer_layer = GeoJSON(data=_collection(self.organizer), name="organizer",
                                       style_callback=lambda f: {"color": colour(f), "weight": 1.5, "fillOpacity": 0.1 if f["properties"]["label"] != "study_area" else 0})
        self.reference_layer = GeoJSON(data=_collection(self.reference), name="reference",
                                       style_callback=lambda f: {"color": colour(f), "weight": 1.2, "fillOpacity": 0.15})
        self.interrow_layer = GeoJSON(data=_collection([]), name="predicted inter-rows",
                                      style_callback=lambda f: {"color": "#22D3EE" if f["properties"].get("interrow_cover") == "bare_soil" else "#F59E0B",
                                                                "weight": 0.5, "fillOpacity": 0.12})
        self.row_layer = GeoJSON(data=_collection([]), name="predicted rows",
                                 style_callback=lambda f: {"color": "#FF3B30" if f["properties"].get("row_structure") == "regular" else "#E11DFF", "weight": 1.2})
        self.prediction_layer = GeoJSON(data=_collection([]), name="predicted plots",
                                        style={"color": "#E11DFF", "weight": 2.5, "dashArray": "8 5", "fillOpacity": 0.0},
                                        hover_style={"fillOpacity": 0.15})
        self.verdict_layer = GeoJSON(data=_collection([]), name="your outlines and verdicts",
                                     style_callback=lambda f: {"color": _VERDICT_COLOURS.get(f["properties"]["verdict"], "#FFFFFF"), "weight": 3, "fillOpacity": 0.2},
                                     point_style={"radius": 6, "fillOpacity": 1, "weight": 2})
        for layer in (self.organizer_layer, self.reference_layer, self.interrow_layer, self.row_layer, self.prediction_layer, self.verdict_layer):
            self.map.add(layer)
        self.prediction_layer.on_click(self._select_prediction)
        self.verdict_layer.on_click(self._select_mark)
        self.map.add(LayersControl(position="topleft"))
        self.map.on_interaction(self._on_map)
        self.draw = DrawControl(polygon={"shapeOptions": {"color": "#F59E0B"}}, polyline={"shapeOptions": {"color": "#F59E0B"}},
                                circlemarker={"pathOptions": {"color": "#F59E0B"}}, rectangle={}, circle={}, marker={})
        self.draw.on_draw(self._on_draw)
        self.map.add(self.draw)

        self.reviewer = widgets.Text(value=reviewer, description="Reviewer")
        self.info = widgets.HTML("Draw a polygon to outline a plot; pick its class first. Click a drawn mark to select it.")
        self.reason = widgets.Dropdown(options=_WRONG_REASONS, description="Reason")
        self.draw_mark = widgets.Dropdown(options=_DRAW_MARKS, value="plot:vineyard", description="Draw marks")
        self.counts = widgets.HTML()
        self.status = widgets.HTML()
        buttons = {
            "Right": lambda _: self._judge_selected("right"), "Wrong": lambda _: self._judge_selected("wrong"),
            "Vineyard tile": lambda _: self._judge_tile("vineyard"), "No vineyard": lambda _: self._judge_tile("no_vineyard"),
            "Undo last": lambda _: self._undo(), "Delete selected mark": lambda _: self._delete_mark(),
        }
        row = []
        for text, action in buttons.items():
            button = widgets.Button(description=text, layout=widgets.Layout(width="auto"),
                                    button_style={"Right": "success", "Wrong": "danger", "Vineyard tile": "success", "No vineyard": "danger"}.get(text, ""))
            button.on_click(action)
            row.append(button)
        panel = widgets.VBox([self.reviewer, self.info, self.draw_mark, widgets.HBox(row[4:6]), self.counts, widgets.HTML("<b>Model output</b>"),
                              widgets.HBox(row[:2]), self.reason, widgets.HBox(row[2:4]), self.status],
                             layout=widgets.Layout(width="330px", padding="6px"))
        self.map.add(WidgetControl(widget=panel, position="topright"))

    def show(self) -> Map:
        return self.map

    def run(self, params: PlotParams | None = None, **changes: Any) -> None:
        """Re-runs the prediction (plots, rows, inter-rows, attributes; about 35 s), e.g. lab.run(ratio_min=1.5)."""
        self.params = PlotParams(**{**asdict(params or self.params), **changes})
        self.predictions = predict(self.params, tiles=self.tiles, layers=self.layers, excess=self.excess)
        by_label = lambda label: [f for f in self.predictions if f["properties"]["label"] == label]
        self.blocks = by_label("block")
        self.prediction_layer.data = _collection(self.blocks)
        self.row_layer.data = _collection(by_label("row"))
        self.interrow_layer.data = _collection(by_label("interrow_area"))
        self._refresh_verdicts()
        labels = Counter(f["properties"]["label"] for f in self.predictions)
        total = sum(f["properties"]["area_m2"] for f in self.predictions if f["properties"]["label"] == "block")
        self._say(f"{labels['block']} plots ({total / 1e4:.2f} ha), {len({f['properties']['row_id'] for f in self.predictions if 'row_id' in f['properties']})} rows, "
                  f"{labels['interrow_area']} inter-row pieces with {self.params}")

    def scene(self) -> dict[str, Any]:
        tagged = [{**f, "properties": {**f["properties"], "source": "prediction"}} for f in self.predictions]
        return {"type": "FeatureCollection", "crs": "EPSG:32635", "source": "lab", "features": self.base_features + tagged}

    def report(self) -> dict[str, Any]:
        result = judge(self.scene())
        print_report(result)
        return result

    def save(self) -> Path:
        """Writes the predictions and rebuilds the client scene so the Vite app shows this run."""
        write(self.predictions)
        DEFAULT_SCENE.write_text(json.dumps({**build_scene([EXAMPLES], self.predictions, tiles=self.tiles), "source": f"examples + {len(self.predictions)} predicted objects"}), encoding="utf-8")
        self._say(f"saved {PREDICTIONS_PATH.name} and the client scene")
        return PREDICTIONS_PATH

    def _say(self, message: str) -> None:
        self.status.value = f"<small>{message}</small>"

    def _tile_at(self, lat: float, lon: float) -> str:
        x, y = _TO_WORLD.transform(lon, lat)
        size = TILE_PX * PIXEL_M
        name = f"siret3_r{int((GRID_TOP - y) // size):03d}_c{int((x - GRID_LEFT) // size):03d}.tif"
        return name if name in self.tile_names else ""

    def _on_map(self, **event: Any) -> None:
        if event.get("type") == "click":
            self.clicked_tile = self._tile_at(*event["coordinates"])
            self.info.value = f"tile <b>{self.clicked_tile or 'outside the tiles'}</b>" + (f" · plot <b>{self.selected['properties']['vineyard_id']}</b>" if self.selected else "")

    def _select_prediction(self, feature: dict[str, Any], **_: Any) -> None:
        self.selected = self.blocks[feature["properties"]["lab_index"]]
        properties = self.selected["properties"]
        details = " · ".join(f"{key} {properties[key]}" for key in ("row_id", "area_m2", "row_angle", "row_spacing_m", "rows", "row_structure", "interrow_cover", "tile") if key in properties)
        self.info.value = f"{properties['label']} <b>{properties.get('vineyard_id', '')}</b> · {details}"

    def _record(self, action) -> None:
        try:
            record = action()
        except ValueError as error:
            self._say(f"not saved: {error}")
            return
        self.last_ids.append(record["id"])
        self._refresh_verdicts()
        self._say(f"saved: {record['verdict']} {record.get('label') or record.get('tile', '')}")

    def _judge_selected(self, verdict: str) -> None:
        if not self.selected:
            self._say("click a plot first")
            return
        feature = {**self.selected, "properties": {**self.selected["properties"], "source": "prediction", "tile": self.clicked_tile}}
        self._record(lambda: review.object_verdict(feature, verdict, self.reason.value if verdict == "wrong" else "", self.reviewer.value))

    def _judge_tile(self, verdict: str) -> None:
        if not self.clicked_tile:
            self._say("click inside a tile first")
            return
        self._record(lambda: review.tile_verdict(self.clicked_tile, verdict, self.reviewer.value))

    def _on_draw(self, target: Any, action: str, geo_json: dict[str, Any]) -> None:
        if action != "created":
            return
        world = mapping(transform(_TO_WORLD.transform, shape(geo_json["geometry"])))
        centre = shape(geo_json["geometry"]).centroid
        kind, label = self.draw_mark.value.split(":")
        if kind == "plot":
            self._record(lambda: review.plot_outline(label, world, reviewer=self.reviewer.value))
        else:
            self._record(lambda: review.missed(label, world, self._tile_at(centre.y, centre.x), reviewer=self.reviewer.value))
        self.draw.clear()

    def _select_mark(self, feature: dict[str, Any], **_: Any) -> None:
        properties = feature["properties"]
        self.selected_mark = properties["id"]
        fit = ""
        if properties["kind"] == "plot":
            outline = shape(next(v for v in review.load_verdicts() if v["id"] == properties["id"])["geometry"])
            ious = [outline.intersection(g).area / outline.union(g).area for g in (shape(b["geometry"]) for b in self.blocks) if g.intersects(outline)]
            fit = f" · best IoU with a predicted plot {max(ious, default=0):.2f}"
        self.info.value = f"selected <b>{properties['kind']} {properties['verdict']}</b> ({properties['id']}){fit}"

    def _delete_mark(self) -> None:
        if not self.selected_mark:
            self._say("click a drawn mark first")
            return
        review.delete_verdict(self.selected_mark)
        self.selected_mark = ""
        self._refresh_verdicts()
        self._say("deleted the selected mark")

    def _undo(self) -> None:
        if not self.last_ids:
            self._say("nothing to undo in this session")
            return
        review.delete_verdict(self.last_ids.pop())
        self._refresh_verdicts()
        self._say("removed the last verdict")

    def _refresh_verdicts(self) -> None:
        tiles = {tile.name: mapping(tile.bounds) for tile in self.tiles}
        shown = []
        verdicts = review.load_verdicts()
        for verdict in verdicts:
            geometry = verdict.get("geometry") or tiles.get(verdict.get("tile", ""))
            if geometry:
                shown.append({"type": "Feature", "geometry": geometry,
                              "properties": {"id": verdict["id"], "verdict": verdict["verdict"], "kind": verdict["kind"], "label": verdict.get("label", "")}})
        self.verdict_layer.data = _collection(shown)
        plots = Counter(verdict["label"] for verdict in verdicts if verdict["kind"] == "plot")
        self.counts.value = "<small>plots drawn: " + (" · ".join(f"{label} {count}" for label, count in sorted(plots.items())) or "none") + "</small>"
