import L from 'leaflet';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import './style.css';

type MapProperties = {
  label: string;
  source?: string;
  vineyard_id?: string;
  row_id?: string;
  row_structure?: string;
  interrow_cover?: string;
  target_id?: string;
  reason?: string;
  area_m2?: number;
};
type SceneFeature = Feature<Geometry, MapProperties>;
type Scene = {
  source: string;
  crs: string;
  features: FeatureCollection<Geometry, MapProperties>;
  metrics: {
    block_count: number;
    row_count: number;
    route_length_m: number;
    target_count: number;
  };
};

const DRAW_ORDER = ['study_area', 'passage', 'forbidden', 'block', 'interrow_area', 'vineyard', 'row', 'waste', 'inspection', 'route', 'start'];
const TOGGLE_GROUP: Record<string, string> = {
  passage: 'organizer', forbidden: 'organizer', study_area: 'organizer',
  start: 'route', inspection: 'route', waste: 'route',
};
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const money = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const map = L.map('map', { preferCanvas: true, scrollWheelZoom: true, zoomControl: true, zoomSnap: 0.25, maxZoom: 24 });
L.tileLayer('/api/imagery/{z}/{x}/{y}.png', {
  minZoom: 13, maxNativeZoom: 22, maxZoom: 24,
  attribution: 'Sireț3 imagery CC BY 4.0, 3DATA COLLECT / OpenAerialMap · route data © OpenStreetMap contributors, ODbL',
}).addTo(map);

const layers: Array<{ layer: L.GeoJSON; group: string; label: string }> = [];
const hidden = new Set(['row', 'vineyard', 'interrow_area', 'organizer']);
let fieldBounds: L.LatLngBounds | null = null;
let currentScene: Scene | null = null;
let routeAvailable = false;

function element(id: string): HTMLElement {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing interface element: ${id}`);
  return found;
}
function setText(id: string, value: string): void { element(id).textContent = value; }
function inputValue(id: string): number | null {
  const input = element(id) as HTMLInputElement;
  if (input.value.trim() === '') return null;
  const parsed = Number(input.value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}
function featureStyle(label: string, source: string | undefined): L.PathOptions {
  const predicted = source === 'prediction';
  switch (label) {
    case 'study_area': return { color: '#d3a02f', weight: 2, dashArray: '6 5', fill: false };
    case 'block': return { color: predicted ? '#8aa929' : '#4d751f', weight: 2.5, dashArray: predicted ? '7 5' : undefined, fillColor: '#91b92d', fillOpacity: 0.12 };
    case 'passage': return { color: '#846a48', weight: 1.5, fillColor: '#e4d9c3', fillOpacity: 0.35 };
    case 'forbidden': return { color: '#b84e42', weight: 2, fillColor: '#f6d2ca', fillOpacity: 0.4 };
    case 'interrow_area': return { color: '#5dafa7', weight: 1, fillColor: '#5dafa7', fillOpacity: 0.16 };
    case 'vineyard': return { color: predicted ? '#d08a44' : '#75a82f', weight: 1, fillColor: '#85b62d', fillOpacity: 0.35 };
    case 'row': return { color: predicted ? '#bd7947' : '#805a40', weight: 2 };
    case 'waste': return { color: '#c16c39', weight: 2, fillColor: '#e6a265', fillOpacity: 0.5 };
    case 'route': return { color: '#205c49', weight: 5, opacity: 0.95 };
    default: return { color: '#61756a', weight: 2 };
  }
}
function bindDetails(feature: SceneFeature, layer: L.Layer): void {
  const details = document.createElement('div');
  details.className = 'map-popup';
  const title = document.createElement('strong');
  const label = feature.properties.label.replaceAll('_', ' ');
  title.textContent = feature.properties.source === 'prediction' ? `Candidate ${label}` : label;
  details.append(title);
  const detailLabels: Record<string, string> = {
    vineyard_id: 'Block', row_id: 'Row', target_id: 'Point', reason: 'Reason',
    row_structure: 'Row condition', interrow_cover: 'Ground cover', area_m2: 'Area',
  };
  for (const key of ['vineyard_id', 'row_id', 'target_id', 'reason', 'row_structure', 'interrow_cover', 'area_m2'] as const) {
    const value = feature.properties[key];
    if (value === undefined || value === '') continue;
    const line = document.createElement('div');
    line.textContent = `${detailLabels[key]}: ${key === 'area_m2' ? `${number.format(Number(value))} m²` : value}`;
    details.append(line);
  }
  layer.bindPopup(details);
}
function applyVisibility(): void {
  for (const { layer, group } of layers) {
    const visible = !hidden.has(group);
    if (visible && !map.hasLayer(layer)) layer.addTo(map);
    if (!visible && map.hasLayer(layer)) map.removeLayer(layer);
  }
}
function renderMap(features: Scene['features']): void {
  for (const { layer } of layers) map.removeLayer(layer);
  layers.length = 0;
  const groups = new Map<string, SceneFeature[]>();
  for (const feature of features.features) {
    const key = `${feature.properties.source ?? ''}|${feature.properties.label}`;
    const group = groups.get(key) ?? [];
    group.push(feature);
    groups.set(key, group);
  }
  const keys = [...groups.keys()].sort((a, b) => DRAW_ORDER.indexOf(a.split('|')[1]) - DRAW_ORDER.indexOf(b.split('|')[1]));
  for (const key of keys) {
    const [source, label] = key.split('|');
    const collection: FeatureCollection<Geometry, MapProperties> = { type: 'FeatureCollection', features: groups.get(key) ?? [] };
    const layer = L.geoJSON(collection, {
      style: () => featureStyle(label, source),
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
        radius: label === 'start' ? 6 : 8, color: '#fff', weight: 2,
        fillColor: label === 'start' ? '#315c2a' : '#d88642', fillOpacity: 1,
      }),
      onEachFeature: (feature, object) => bindDetails(feature as SceneFeature, object),
    });
    layers.push({ layer, group: TOGGLE_GROUP[label] ?? label, label });
  }
  applyVisibility();
  const study = layers.find((entry) => entry.label === 'study_area')?.layer.getBounds();
  const blocks = layers.filter((entry) => entry.label === 'block');
  const bounds = L.latLngBounds([]);
  for (const block of blocks) bounds.extend(block.layer.getBounds());
  fieldBounds = study?.isValid() ? study : bounds.isValid() ? bounds : null;
  fitField();
}
function fitField(): void {
  if (fieldBounds?.isValid()) map.fitBounds(fieldBounds.pad(0.05));
  else map.setView([47.12, 28.71], 15);
}
function focusFeature(feature: SceneFeature): void {
  const layer = L.geoJSON(feature);
  const bounds = layer.getBounds();
  if (!bounds.isValid()) return;
  if (feature.geometry.type === 'Point') map.setView(bounds.getCenter(), Math.max(map.getZoom(), 19));
  else map.fitBounds(bounds.pad(0.6), { maxZoom: 19 });
  for (const entry of layers) {
    entry.layer.eachLayer((item) => {
      const mapped = item as L.Layer & { feature?: SceneFeature };
      if (mapped.feature === feature) item.openPopup();
    });
  }
}
function renderPois(features: SceneFeature[], hasRoute: boolean): void {
  const targets = features.filter((feature) => ['inspection', 'waste'].includes(feature.properties.label));
  setText('target-count', String(targets.length));
  const list = element('poi-list');
  list.replaceChildren();
  if (targets.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.innerHTML = '<span aria-hidden="true">◎</span><strong>No visit points yet</strong><p>Gap and waste targets will appear here when the field analysis provides them.</p>';
    list.append(empty);
    setText('plan-summary', hasRoute ? 'A route is available, but this scene has no listed visit points.' : 'The model has not supplied visit points or a route yet.');
    return;
  }
  setText('plan-summary', `${targets.length} point${targets.length === 1 ? '' : 's'} identified · ${hasRoute ? 'route shown on map' : 'route pending'}`);
  for (const feature of targets) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'poi-item';
    const title = document.createElement('strong');
    title.textContent = feature.properties.reason || (feature.properties.label === 'waste' ? 'Check visible waste' : 'Inspect this location');
    const meta = document.createElement('span');
    const location = [feature.properties.vineyard_id, feature.properties.row_id].filter(Boolean).join(' · ');
    meta.textContent = `${feature.properties.source === 'prediction' ? 'Candidate' : 'Mapped point'}${location ? ` · ${location}` : ''}`;
    const badge = document.createElement('em');
    badge.textContent = feature.properties.label === 'waste' ? '!' : '•';
    button.append(badge, title, meta);
    button.addEventListener('click', () => focusFeature(feature));
    list.append(button);
  }
}
function updateSavings(): void {
  if (!currentScene || !routeAvailable) {
    setText('money-saved', '—');
    setText('time-saved', '—');
    setText('money-detail', 'Available after a planned route and your labour cost are supplied.');
    setText('time-detail', 'Available after a planned route and your usual inspection time are supplied.');
    return;
  }
  const usualMinutes = inputValue('usual-minutes');
  const hourlyCost = inputValue('hourly-cost');
  const walkingSpeed = inputValue('walking-speed');
  const stopMinutes = inputValue('stop-minutes');
  if (walkingSpeed === null || walkingSpeed <= 0 || stopMinutes === null) {
    setText('time-saved', '—');
    setText('money-saved', '—');
    setText('time-detail', 'Enter a valid walking speed and stop time.');
    setText('money-detail', 'Enter a valid walking speed and stop time.');
    return;
  }
  const plannedMinutes = currentScene.metrics.route_length_m / 1000 / walkingSpeed * 60 + currentScene.metrics.target_count * stopMinutes;
  if (usualMinutes === null) {
    setText('time-saved', '—');
    setText('money-saved', '—');
    setText('time-detail', `Planned walk: about ${number.format(plannedMinutes)} min. Add your usual inspection time.`);
    setText('money-detail', 'Add your usual inspection time and labour cost.');
    return;
  }
  const savedMinutes = usualMinutes - plannedMinutes;
  setText('time-saved', `${savedMinutes < 0 ? '−' : ''}${number.format(Math.abs(savedMinutes))} min`);
  setText('time-detail', `${number.format(usualMinutes)} min usual − ${number.format(plannedMinutes)} min planned`);
  if (hourlyCost === null) {
    setText('money-saved', '—');
    setText('money-detail', 'Add your labour cost to calculate the value of this time.');
    return;
  }
  const savedMoney = savedMinutes / 60 * hourlyCost;
  setText('money-saved', `${savedMoney < 0 ? '−' : ''}${money.format(Math.abs(savedMoney))} MDL`);
  setText('money-detail', `${number.format(savedMinutes / 60)} hours × ${number.format(hourlyCost)} MDL/hour`);
}
function renderScene(scene: Scene): void {
  if (scene.crs !== 'EPSG:4326') throw new Error(`Unexpected display CRS: ${scene.crs}`);
  currentScene = scene;
  const features = scene.features.features;
  routeAvailable = features.some((feature) => feature.properties.label === 'route') && scene.metrics.route_length_m > 0;
  const candidateBlocks = features.filter((feature) => feature.properties.label === 'block' && feature.properties.source === 'prediction').length;
  setText('source-badge', scene.source.toLowerCase().includes('example') ? 'Example data + model candidates' : scene.source);
  setText('status', `${candidateBlocks ? `${candidateBlocks} candidate blocks mapped · ` : ''}${routeAvailable ? 'inspection route available' : 'inspection route pending'}`);
  setText('route-distance', routeAvailable ? `${number.format(scene.metrics.route_length_m / 1000)} km` : 'Not planned');
  setText('route-detail', routeAvailable ? `${scene.metrics.target_count} visit targets in this scene · tap the map to explore` : 'The current scene has no inspection route. Savings will appear when one is supplied.');
  renderPois(features, routeAvailable);
  renderMap(scene.features);
  updateSavings();
}
for (const button of document.querySelectorAll<HTMLButtonElement>('[data-layer]')) {
  button.addEventListener('click', () => {
    const group = button.dataset.layer ?? '';
    if (hidden.has(group)) hidden.delete(group);
    else hidden.add(group);
    const visible = !hidden.has(group);
    button.setAttribute('aria-pressed', String(visible));
    button.classList.toggle('is-active', visible);
    applyVisibility();
  });
}
for (const id of ['usual-minutes', 'hourly-cost', 'walking-speed', 'stop-minutes']) {
  element(id).addEventListener('input', updateSavings);
}
element('fit-map').addEventListener('click', fitField);
async function loadScene(): Promise<void> {
  try {
    const response = await fetch('/api/scene');
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    renderScene(await response.json() as Scene);
  } catch (error) {
    setText('status', `Could not load the field scene: ${error instanceof Error ? error.message : 'unknown error'}`);
    setText('source-badge', 'Service unavailable');
    setText('plan-summary', 'Start the field service to load visit points and routes.');
    setText('target-count', '—');
    setText('route-distance', '—');
    map.setView([47.12, 28.71], 15);
  }
}
void loadScene();
