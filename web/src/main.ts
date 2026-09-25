import L from 'leaflet';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import './style.css';

type MapProperties = {
  label: string;
  vineyard_id?: string;
  row_id?: string;
  row_structure?: string;
  interrow_cover?: string;
  target_id?: string;
  reason?: string;
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

const map = L.map('map', { preferCanvas: true, scrollWheelZoom: false, zoomControl: true, zoomSnap: 0.1, maxZoom: 24 });
L.tileLayer('/api/imagery/{z}/{x}/{y}.png', {
  minZoom: 13, maxNativeZoom: 22, maxZoom: 24,
  attribution: 'Sireț3 imagery CC BY 4.0, 3DATA COLLECT / OpenAerialMap · route data © OpenStreetMap contributors, ODbL',
}).addTo(map);
const layers = new Map<string, L.GeoJSON>();
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });

function element(id: string): HTMLElement {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing interface element: ${id}`);
  return found;
}

function setText(id: string, value: string): void {
  element(id).textContent = value;
}

function featureStyle(label: string): L.PathOptions {
  switch (label) {
    case 'block': return { color: '#928F85', weight: 2, dashArray: '6 6', fillColor: '#FFFFFF', fillOpacity: 0.22 };
    case 'study_area': return { color: '#F5C400', weight: 2, dashArray: '6 4', fill: false };
    case 'passage': return { color: '#A7906A', weight: 1, fillColor: '#E9DEC8', fillOpacity: 0.3 };
    case 'forbidden': return { color: '#CC1F1F', weight: 2, fillColor: '#FEE2E2', fillOpacity: 0.35 };
    case 'interrow_area': return { color: '#B7CE91', weight: 1, fillColor: '#D6E6B8', fillOpacity: 0.3 };
    case 'vineyard': return { color: '#467410', weight: 1.5, fillColor: '#5C960C', fillOpacity: 0.45 };
    case 'row': return { color: '#625A4B', weight: 2, dashArray: '4 5', opacity: 0.9 };
    case 'waste': return { color: '#C43E00', weight: 2, fillColor: '#E8772E', fillOpacity: 0.8 };
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
  for (const key of ['vineyard_id', 'row_id', 'row_structure', 'interrow_cover', 'target_id', 'reason'] as const) {
    const value = feature.properties[key];
    if (!value) continue;
    const line = document.createElement('div');
    line.textContent = `${key.replaceAll('_', ' ')}: ${value}`;
    details.append(line);
  }
  layer.bindPopup(details);
}

function renderMap(features: Scene['features']): void {
  const order = ['study_area', 'block', 'passage', 'forbidden', 'interrow_area', 'vineyard', 'row', 'waste', 'inspection', 'route', 'start'];
  for (const label of order) {
    const matching = features.features.filter((feature) => feature.properties.label === label);
    if (matching.length === 0) continue;
    const collection: FeatureCollection<Geometry, MapProperties> = { type: 'FeatureCollection', features: matching };
    const layer = L.geoJSON(collection, {
      style: () => featureStyle(label),
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
        radius: 7, color: '#FFFFFF', weight: 2, fillColor: label === 'start' ? '#CC1F1F' : '#1F5AEE', fillOpacity: 1,
      }),
      onEachFeature: (feature, object) => bindDetails(feature as SceneFeature, object),
    });
    layers.set(label, layer);
    layer.addTo(map);
  }
  const bounds = (layers.get('study_area') ?? layers.get('block'))?.getBounds() ?? L.geoJSON(features).getBounds();
  if (bounds.isValid()) map.fitBounds(bounds.pad(0.08));
  else map.setView([47, 28], 12);
}

function renderRows(rows: Scene['metrics']['rows']): void {
  const container = element('rows');
  container.replaceChildren();
  for (const row of rows) {
    const wrapper = document.createElement('div');
    wrapper.className = 'flex items-center justify-between gap-3 py-3 text-sm';
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
  setText('canopy-area', `${number.format(scene.metrics.canopy_area_m2 / 10_000)} ha`);
  setText('interrow-area', `${number.format(scene.metrics.interrow_area_m2 / 10_000)} ha`);
  setText('route-length', number.format(scene.metrics.route_length_m));
  setText('target-count', String(scene.metrics.target_count));
  setText('total-row-length', `${number.format(scene.metrics.row_length_m)} m total`);
  renderRows(scene.metrics.rows);
  renderMap(scene.features);
}

for (const button of document.querySelectorAll<HTMLButtonElement>('[data-layer]')) {
  button.addEventListener('click', () => {
    const label = button.dataset.layer;
    const layer = label ? layers.get(label) : undefined;
    if (!layer) return;
    const visible = map.hasLayer(layer);
    if (visible) map.removeLayer(layer);
    else layer.addTo(map);
    button.setAttribute('aria-pressed', String(!visible));
    button.classList.toggle('is-active', !visible);
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
    map.setView([47, 28], 12);
  }
}

void loadScene();
