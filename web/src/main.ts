import L from 'leaflet';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import './style.css';

type Props = { label: string; source?: string; vineyard_id?: string; row_id?: string; target_id?: string; reason?: string; area_m2?: number; length_m?: number };
type MapFeature = Feature<Geometry, Props>;
type Scene = { crs: string; features: FeatureCollection<Geometry, Props>; metrics?: { route_length_m?: number } };
type Overlay = 'field' | 'route' | 'points' | 'row' | 'vineyard' | 'interrow_area' | 'access';
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const currency = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const el = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing element: ${id}`);
  return found as T;
};
const text = (id: string, value: string): void => { el(id).textContent = value; };
const input = (id: string): number | null => {
  const raw = el<HTMLInputElement>(id).value.trim();
  if (!raw) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
};
const map = L.map('map', { preferCanvas: true, zoomControl: false, zoomSnap: 0.25, maxZoom: 19 });
const street = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
}).addTo(map);
const drone = L.tileLayer('/api/imagery/{z}/{x}/{y}.png', {
  minZoom: 13, maxNativeZoom: 22, maxZoom: 22,
  attribution: 'Sireț3 imagery CC BY 4.0, 3DATA COLLECT / OpenAerialMap',
});
L.control.zoom({ position: 'topleft' }).addTo(map);
map.setView([47.12, 28.71], 14);
const overlays: Record<Overlay, L.LayerGroup> = {
  field: L.layerGroup(), route: L.layerGroup(), points: L.layerGroup(),
  row: L.layerGroup(), vineyard: L.layerGroup(), interrow_area: L.layerGroup(), access: L.layerGroup(),
};
const visible = new Set<Overlay>(['field', 'route', 'points']);
const usualByField = new Map<string, string>();
const pointLayers = new Map<MapFeature, L.Layer>();
let scene: Scene | null = null;
let fields: MapFeature[] = [];
let field: MapFeature | null = null;
let boundary: L.GeoJSON | null = null;
let targets: MapFeature[] = [];
let routes: MapFeature[] = [];
let routeLength: number | null = null;
const fieldId = (item: MapFeature): string => item.properties.vineyard_id ?? '';
const belongsToField = (item: MapFeature): boolean => item.properties.vineyard_id === fieldId(field!) ||
  (fields.length === 1 && !item.properties.vineyard_id);

function popupFor(item: MapFeature): HTMLDivElement {
  const popup = document.createElement('div');
  popup.className = 'map-popup';
  const title = document.createElement('strong');
  title.textContent = item.properties.reason || `${item.properties.source === 'prediction' ? 'Candidate ' : ''}${item.properties.label.replaceAll('_', ' ')}`;
  popup.append(title);
  for (const [key, label] of [['vineyard_id', 'Field'], ['row_id', 'Row'], ['target_id', 'Point']] as const) {
    const value = item.properties[key];
    if (!value) continue;
    const line = document.createElement('div');
    line.textContent = `${label}: ${value}`;
    popup.append(line);
  }
  return popup;
}
function addFeature(item: MapFeature, group: Overlay, style: L.PathOptions): L.GeoJSON {
  const layer = L.geoJSON(item, {
    style,
    pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
      radius: 9, color: '#fff', weight: 2.5, fillColor: '#ed7625', fillOpacity: 1,
    }),
    onEachFeature: (_feature, mapped) => {
      mapped.bindPopup(popupFor(item));
      if (group === 'points') pointLayers.set(item, mapped);
    },
  });
  layer.addTo(overlays[group]);
  return layer;
}
function applyVisibility(): void {
  for (const [name, group] of Object.entries(overlays) as [Overlay, L.LayerGroup][]) {
    if (visible.has(name) && !map.hasLayer(group)) group.addTo(map);
    if (!visible.has(name) && map.hasLayer(group)) map.removeLayer(group);
  }
  for (const checkbox of document.querySelectorAll<HTMLInputElement>('[data-layer]')) {
    checkbox.checked = visible.has(checkbox.dataset.layer as Overlay);
  }
  const toggle = el<HTMLButtonElement>('route-toggle');
  toggle.setAttribute('aria-pressed', String(routes.length > 0 && visible.has('route')));
  toggle.firstChild!.textContent = routes.length === 0 ? 'Route unavailable ' : visible.has('route') ? 'Hide route ' : 'Show route ';
}
function fitField(): void {
  if (!boundary) return;
  const width = window.innerWidth;
  const paddingTopLeft: L.PointTuple = width <= 700 ? [12, 300] : width <= 1020 ? [390, 320] : [420, 145];
  const paddingBottomRight: L.PointTuple = width <= 700 ? [12, 265] : width <= 1020 ? [20, 145] : [340, 130];
  map.fitBounds(boundary.getBounds().pad(0.18), { paddingTopLeft, paddingBottomRight, maxZoom: 18 });
}
function focusPoint(item: MapFeature): void {
  const layer = pointLayers.get(item);
  if (!layer || item.geometry.type !== 'Point') return;
  const [lng, lat] = item.geometry.coordinates;
  map.setView([lat, lng], Math.max(map.getZoom(), 18));
  if (!visible.has('points')) { visible.add('points'); applyVisibility(); }
  layer.openPopup();
}
function renderStops(): void {
  const list = el('stop-list');
  list.replaceChildren();
  text('target-count', String(targets.length));
  if (!targets.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    const icon = document.createElement('span');
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = '◎';
    const title = document.createElement('strong');
    title.textContent = 'No visit points yet';
    const detail = document.createElement('p');
    detail.textContent = 'Inspection targets will appear here when the field analysis supplies them.';
    empty.append(icon, title, detail);
    list.append(empty);
  } else {
    targets.forEach((item, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      const badge = document.createElement('i');
      badge.textContent = String(index + 1);
      const copy = document.createElement('span');
      const title = document.createElement('strong');
      title.textContent = item.properties.reason || (item.properties.label === 'waste' ? 'Inspect visible waste' : 'Inspect this location');
      const meta = document.createElement('small');
      meta.textContent = [item.properties.source === 'prediction' ? 'Candidate point' : 'Mapped point', item.properties.row_id].filter(Boolean).join(' · ');
      copy.append(title, meta);
      button.append(badge, copy);
      button.addEventListener('click', () => focusPoint(item));
      list.append(button);
    });
  }
  text('plan-summary', targets.length
    ? `${targets.length} visit point${targets.length === 1 ? '' : 's'} in this field · ${routes.length ? 'route available' : 'route pending'}`
    : routes.length ? 'A field route is available, but no visit points were supplied.' : 'No route or visit points have been supplied for this field.');
  text('route-status', routes.length ? 'Field route available' : 'Route pending');
  el<HTMLButtonElement>('route-toggle').disabled = routes.length === 0;
}
function measuredRouteLength(): number | null {
  if (!scene || !routes.length) return null;
  const lengths = routes.map((item) => item.properties.length_m);
  if (lengths.every((length) => typeof length === 'number' && Number.isFinite(length) && length > 0)) {
    return lengths.reduce<number>((total, length) => total + (length ?? 0), 0);
  }
  const allRoutes = scene.features.features.filter((item) => item.properties.label === 'route');
  const total = scene.metrics?.route_length_m;
  return allRoutes.length === routes.length && typeof total === 'number' && Number.isFinite(total) && total > 0 ? total : null;
}
function updateSavings(): void {
  const badge = el('impact-state');
  const pending = (detail: string): void => {
    text('money-saved', '—'); text('time-saved', '—'); text('impact-detail', detail);
    badge.textContent = 'PENDING'; badge.classList.remove('calculated');
  };
  text('route-distance', routeLength === null ? '—' : routeLength < 1000 ? `${number.format(routeLength)} m` : `${number.format(routeLength / 1000)} km`);
  if (!routes.length) return pending('Waiting for a route assigned to this field.');
  if (routeLength === null) return pending('This field route has no measured length yet.');
  const usual = input('usual-minutes');
  const hourlyCost = input('hourly-cost');
  const speed = input('walking-speed');
  const stopMinutes = input('stop-minutes');
  if (speed === null || speed <= 0 || stopMinutes === null) return pending('Enter a valid walking speed and stop time.');
  const planned = routeLength / 1000 / speed * 60 + targets.length * stopMinutes;
  if (usual === null) return pending(`Planned walk: ${number.format(planned)} min. Add your usual time for this field.`);
  const saved = usual - planned;
  text('time-saved', `${saved < 0 ? '−' : ''}${number.format(Math.abs(saved))} min`);
  text('impact-detail', `${number.format(usual)} min usual − ${number.format(planned)} min planned · estimate only`);
  text('money-saved', hourlyCost === null ? '—' : `${saved * hourlyCost < 0 ? '−' : ''}${currency.format(Math.abs(saved / 60 * hourlyCost))} MDL`);
  badge.textContent = hourlyCost === null ? 'ADD COST' : 'ESTIMATE';
  badge.classList.add('calculated');
}
function renderField(): void {
  if (!scene || !field) return;
  pointLayers.clear();
  for (const group of Object.values(overlays)) group.clearLayers();
  addFeature(field, 'field', { color: '#fff', weight: 10, fillOpacity: 0, opacity: 0.95, interactive: false });
  boundary = addFeature(field, 'field', {
    color: '#7e22ce', weight: 5, fillColor: '#a132ea', fillOpacity: 0.19,
    dashArray: field.properties.source === 'prediction' ? '12 7' : undefined,
  });
  boundary.bindTooltip(`Field ${fieldId(field)}${field.properties.source === 'prediction' ? ' · candidate boundary' : ''}`, { sticky: true });
  targets = []; routes = [];
  for (const item of scene.features.features) {
    const { label } = item.properties;
    if (item === field) continue;
    if (label === 'passage' || label === 'forbidden') {
      addFeature(item, 'access', label === 'forbidden'
        ? { color: '#a83e38', weight: 2, fillColor: '#db7770', fillOpacity: 0.15 }
        : { color: '#ba7e30', weight: 2, fillColor: '#e6ba70', fillOpacity: 0.18 });
      continue;
    }
    if (!belongsToField(item)) continue;
    if (label === 'route') {
      routes.push(item);
      addFeature(item, 'route', { color: '#fff', weight: 11, opacity: 0.95, interactive: false });
      addFeature(item, 'route', { color: '#ed7625', weight: 6, opacity: 1, dashArray: '12 8' });
    } else if (label === 'inspection' || label === 'waste') {
      targets.push(item); addFeature(item, 'points', {});
    } else if (label === 'row') {
      addFeature(item, 'row', { color: '#7448ad', weight: 2, opacity: 0.8 });
    } else if (label === 'vineyard') {
      addFeature(item, 'vineyard', { color: '#a7742b', weight: 1.5, fillColor: '#d09b4e', fillOpacity: 0.28 });
    } else if (label === 'interrow_area') {
      addFeature(item, 'interrow_area', { color: '#397d95', weight: 1.2, fillColor: '#5a9cad', fillOpacity: 0.18 });
    }
  }
  routeLength = measuredRouteLength();
  const area = field.properties.area_m2;
  text('field-meta', `${typeof area === 'number' && Number.isFinite(area) ? `${number.format(area / 10_000)} ha · ` : ''}${field.properties.source === 'prediction' ? 'Candidate boundary from the model' : 'Mapped field boundary'}`);
  text('status', `${fields.length} fields in scene · ${routes.length ? 'field route available' : 'field route pending'}`);
  renderStops(); updateSavings(); applyVisibility();
  requestAnimationFrame(fitField);
}
function renderScene(data: Scene): void {
  if (data.crs !== 'EPSG:4326' || data.features?.type !== 'FeatureCollection' || !Array.isArray(data.features.features)) {
    throw new Error('The field scene has an unsupported map format');
  }
  scene = data;
  fields = data.features.features.filter((item) => item.properties?.label === 'block' && item.properties.vineyard_id)
    .sort((a, b) => fieldId(a).localeCompare(fieldId(b), undefined, { numeric: true }));
  const select = el<HTMLSelectElement>('field-select');
  select.replaceChildren();
  for (const item of fields) {
    const option = document.createElement('option');
    option.value = fieldId(item); option.textContent = `Field ${fieldId(item)}`;
    select.append(option);
  }
  select.disabled = fields.length === 0;
  el<HTMLButtonElement>('fit-field').disabled = fields.length === 0;
  if (!fields.length) {
    const option = document.createElement('option');
    option.textContent = 'No fields in scene';
    select.append(option);
    text('field-heading', 'No fields available'); text('field-meta', 'The current scene contains no field boundaries.');
    text('status', 'No fields in the current scene'); text('plan-summary', 'Field boundaries are needed before inspection points can be shown.');
    text('target-count', '0'); return;
  }
  field = fields[0];
  text('field-heading', 'Choose a field');
  renderField();
}

el<HTMLSelectElement>('field-select').addEventListener('change', (event) => {
  const select = event.currentTarget as HTMLSelectElement;
  if (field) usualByField.set(fieldId(field), el<HTMLInputElement>('usual-minutes').value);
  field = fields.find((item) => fieldId(item) === select.value) ?? null;
  el<HTMLInputElement>('usual-minutes').value = field ? usualByField.get(fieldId(field)) ?? '' : '';
  renderField();
});
el('fit-field').addEventListener('click', fitField);
for (const checkbox of document.querySelectorAll<HTMLInputElement>('[data-layer]')) {
  checkbox.addEventListener('change', () => {
    const name = checkbox.dataset.layer as Overlay;
    if (checkbox.checked) visible.add(name); else visible.delete(name);
    applyVisibility();
  });
}
el('route-toggle').addEventListener('click', () => {
  if (visible.has('route')) visible.delete('route'); else visible.add('route');
  applyVisibility();
});
for (const id of ['usual-minutes', 'hourly-cost', 'walking-speed', 'stop-minutes']) el(id).addEventListener('input', updateSavings);
for (const button of document.querySelectorAll<HTMLButtonElement>('[data-panel]')) {
  button.addEventListener('click', () => {
    const evidence = button.dataset.panel === 'evidence';
    el('stops-panel').hidden = evidence; el('evidence-panel').hidden = !evidence;
    for (const item of document.querySelectorAll<HTMLButtonElement>('[data-panel]')) {
      item.classList.toggle('selected', item === button);
      item.setAttribute('aria-pressed', String(item === button));
    }
  });
}
for (const button of document.querySelectorAll<HTMLButtonElement>('[data-base]')) {
  button.addEventListener('click', () => {
    const useDrone = button.dataset.base === 'drone';
    map.setMaxZoom(useDrone ? 22 : 19);
    if (useDrone) { map.removeLayer(street); drone.addTo(map); }
    else { map.removeLayer(drone); street.addTo(map); }
    for (const item of document.querySelectorAll<HTMLButtonElement>('[data-base]')) item.setAttribute('aria-pressed', String(item === button));
    const credit = el<HTMLAnchorElement>('map-credit');
    credit.textContent = useDrone ? 'Sireț3 imagery · 3DATA COLLECT / OpenAerialMap' : '© OpenStreetMap contributors';
    credit.href = useDrone ? 'https://openaerialmap.org/' : 'https://www.openstreetmap.org/copyright';
  });
}
for (const button of document.querySelectorAll<HTMLButtonElement>('[data-nav]')) {
  button.addEventListener('click', () => {
    for (const item of document.querySelectorAll<HTMLButtonElement>('[data-nav]')) {
      if (item === button) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
    }
    switch (button.dataset.nav) {
      case 'map': fitField(); break;
      case 'fields': el<HTMLSelectElement>('field-select').focus(); break;
      case 'visits': el<HTMLButtonElement>('visits-tab').click(); el('plan-panel').focus(); break;
      case 'impact': el<HTMLDetailsElement>('assumptions').open = true; el<HTMLInputElement>('usual-minutes').focus(); break;
    }
  });
}
window.addEventListener('resize', () => { map.invalidateSize(); fitField(); });
async function loadScene(): Promise<void> {
  try {
    const response = await fetch('/api/scene');
    if (!response.ok) throw new Error(`Field service returned ${response.status}`);
    renderScene(await response.json() as Scene);
  } catch (error) {
    const select = el<HTMLSelectElement>('field-select');
    const option = document.createElement('option');
    option.textContent = 'Fields unavailable';
    select.replaceChildren(option);
    select.disabled = true;
    el<HTMLButtonElement>('fit-field').disabled = true;
    text('status', `Could not load fields: ${error instanceof Error ? error.message : 'unknown error'}`);
    text('field-meta', 'Start the field service to load current boundaries.');
    text('plan-summary', 'Visit points and routes are unavailable.'); text('target-count', '—');
  }
}
void loadScene();
