import L from 'leaflet';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import './style.css';

type MapProperties = {
  label: string;
  source?: string;
  tile?: string;
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
    row_length_m: number;
    canopy_area_m2: number;
    interrow_area_m2: number;
    route_length_m: number;
    target_count: number;
    rows: Array<{ vineyard_id: string; row_id: string; row_length_m: number }>;
  };
};

const DRAW_ORDER = ['study_area', 'passage', 'forbidden', 'block', 'interrow_area', 'vineyard', 'row', 'waste', 'inspection', 'route', 'start'];
const TOGGLE_GROUP: Record<string, string> = { passage: 'organizer', forbidden: 'organizer', study_area: 'organizer', start: 'route', inspection: 'route' };

const map = L.map('map', { preferCanvas: true, scrollWheelZoom: true, zoomControl: true, zoomSnap: 0.25, maxZoom: 24 });
L.tileLayer('/api/imagery/{z}/{x}/{y}.png', {
  minZoom: 13, maxNativeZoom: 22, maxZoom: 24,
  attribution: 'Sireț3 imagery CC BY 4.0, 3DATA COLLECT / OpenAerialMap · route data © OpenStreetMap contributors, ODbL',
}).addTo(map);
const layers: Array<{ layer: L.GeoJSON; group: string }> = [];
const hidden = new Set<string>();
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });

function element(id: string): HTMLElement {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing interface element: ${id}`);
  return found;
}

function setText(id: string, value: string): void {
  element(id).textContent = value;
}

function featureStyle(label: string, source: string | undefined): L.PathOptions {
  const predicted = source === 'prediction';
  switch (label) {
    case 'study_area': return { color: '#F5C400', weight: 2, dashArray: '6 4', fill: false };
    case 'block': return predicted
      ? { color: '#E11DFF', weight: 2.5, dashArray: '8 5', fillColor: '#E11DFF', fillOpacity: 0.06 }
      : { color: '#928F85', weight: 2, dashArray: '6 6', fillColor: '#FFFFFF', fillOpacity: 0.1 };
    case 'passage': return { color: '#A7906A', weight: 1, fillColor: '#E9DEC8', fillOpacity: 0.3 };
    case 'forbidden': return { color: '#CC1F1F', weight: 2, fillColor: '#FEE2E2', fillOpacity: 0.35 };
    case 'interrow_area': return { color: '#22D3EE', weight: 1, fillColor: '#22D3EE', fillOpacity: predicted ? 0.1 : 0.15 };
    case 'vineyard': return { color: predicted ? '#FF7A00' : '#7CFC00', weight: 1.2, fillColor: predicted ? '#FF7A00' : '#5C960C', fillOpacity: 0.3 };
    case 'row': return { color: predicted ? '#FF2E88' : '#FF3B30', weight: 2, opacity: 0.9 };
    case 'waste': return { color: '#C43E00', weight: 2, fillColor: '#E8772E', fillOpacity: 0.5 };
    case 'route': return { color: '#1F5AEE', weight: 4, opacity: 0.95 };
    default: return { color: '#5F5B55', weight: 2 };
  }
}

function bindDetails(feature: SceneFeature, layer: L.Layer): void {
  const details = document.createElement('div');
  details.className = 'min-w-36';
  const title = document.createElement('strong');
  title.textContent = feature.properties.label.replaceAll('_', ' ');
  details.append(title);
  for (const key of ['vineyard_id', 'row_id', 'row_structure', 'interrow_cover', 'target_id', 'reason', 'area_m2'] as const) {
    const value = feature.properties[key];
    if (value === undefined || value === '') continue;
    const line = document.createElement('div');
    line.textContent = `${key.replaceAll('_', ' ')}: ${key === 'area_m2' ? `${number.format(Number(value))} m²` : value}`;
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
  const groups = new Map<string, SceneFeature[]>();
  for (const feature of features.features) {
    const key = `${feature.properties.source ?? ''}|${feature.properties.label}`;
    groups.set(key, [...(groups.get(key) ?? []), feature]);
  }
  const keys = [...groups.keys()].sort((a, b) => DRAW_ORDER.indexOf(a.split('|')[1]) - DRAW_ORDER.indexOf(b.split('|')[1]));
  for (const key of keys) {
    const [source, label] = key.split('|');
    const collection: FeatureCollection<Geometry, MapProperties> = { type: 'FeatureCollection', features: groups.get(key) ?? [] };
    const layer = L.geoJSON(collection, {
      style: () => featureStyle(label, source),
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
        radius: 7, color: '#FFFFFF', weight: 2, fillColor: label === 'start' ? '#CC1F1F' : '#1F5AEE', fillOpacity: 1,
      }),
      onEachFeature: (feature, object) => bindDetails(feature as SceneFeature, object),
    });
    layers.push({ layer, group: TOGGLE_GROUP[label] ?? label });
  }
  applyVisibility();
  const study = layers.find(({ layer }) => layer.getLayers().some((item) => (item as L.Layer & { feature?: SceneFeature }).feature?.properties.label === 'study_area'))?.layer.getBounds();
  if (study?.isValid()) map.fitBounds(study.pad(0.05));
  else map.setView([47.12, 28.71], 15);
}

function renderRows(rows: Scene['metrics']['rows']): void {
  const container = element('rows');
  container.replaceChildren();
  for (const row of rows) {
    const wrapper = document.createElement('div');
    wrapper.className = 'flex items-center justify-between gap-3 py-2 text-sm';
    const id = document.createElement('span');
    id.className = 'font-semibold text-ink';
    id.textContent = row.row_id;
    const length = document.createElement('span');
    length.className = 'tabular-nums text-muted';
    length.textContent = `${number.format(row.row_length_m)} m`;
    wrapper.append(id, length);
    container.append(wrapper);
  }
  if (rows.length === 0) container.textContent = 'No rows in this scene.';
}

function renderScene(scene: Scene): void {
  setText('source-badge', scene.source);
  setText('status', scene.crs === 'EPSG:4326' ? 'Map ready' : `Unexpected display CRS: ${scene.crs}`);
  setText('block-count', String(scene.metrics.block_count));
  setText('row-count', String(scene.metrics.row_count));
  setText('canopy-area', `${number.format(scene.metrics.canopy_area_m2)} m²`);
  setText('interrow-area', `${number.format(scene.metrics.interrow_area_m2)} m²`);
  setText('route-length', number.format(scene.metrics.route_length_m));
  setText('target-count', String(scene.metrics.target_count));
  setText('total-row-length', `${number.format(scene.metrics.row_length_m)} m total`);
  renderRows(scene.metrics.rows);
  renderMap(scene.features);
}

for (const button of document.querySelectorAll<HTMLButtonElement>('[data-layer]')) {
  button.addEventListener('click', () => {
    const group = button.dataset.layer ?? '';
    const visible = hidden.has(group);
    if (visible) hidden.delete(group);
    else hidden.add(group);
    button.setAttribute('aria-pressed', String(visible));
    button.classList.toggle('is-active', visible);
    applyVisibility();
  });
}

async function loadScene(): Promise<void> {
  try {
    const response = await fetch('/api/scene');
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    renderScene(await response.json() as Scene);
  } catch (error) {
    setText('status', `Could not load scene: ${error instanceof Error ? error.message : 'unknown error'}`);
    setText('source-badge', 'Service unavailable');
    setText('rows', 'Start the Python service to load the map.');
    map.setView([47.12, 28.71], 15);
  }
}

void loadScene();
