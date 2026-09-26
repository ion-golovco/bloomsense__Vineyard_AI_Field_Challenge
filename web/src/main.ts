import L from 'leaflet';
import type { AppView, MapFeature, Scene, TargetStatus } from './scene-types';
import { createMapViews } from './map-views';
import { createRoutePlanner } from './route-planner';
import { createInspection, pointTitle } from './inspection';
import { initializeYieldView, updateYieldFields } from './yield-view';
import { createMeasurements } from './measurements';
import './style.css';

type Overlay = 'field' | 'route' | 'points' | 'row' | 'vineyard' | 'interrow_area' | 'access' | 'ndvi' | 'ndmi' | 'cadastre' | 'start';
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
const map = L.map('map', { preferCanvas: true, zoomControl: false, zoomSnap: 0.25, maxZoom: 22 });
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxNativeZoom: 19, maxZoom: 22, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
}).addTo(map);
L.tileLayer('/api/imagery/{z}/{x}/{y}.png', {
  minZoom: 13, maxNativeZoom: 22, maxZoom: 22,
  attribution: 'Drone: 3DATA COLLECT / OpenAerialMap · <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>',
}).addTo(map);
L.control.zoom({ position: 'topleft' }).addTo(map);
map.setView([47.12, 28.71], 14);
const featurePanes: Record<Overlay, string> = {
  field: 'field-boundaries', route: 'inspection-routes', points: 'visit-points',
  row: 'vine-rows', vineyard: 'canopy-segmentation', interrow_area: 'interrow-segmentation', access: 'access-areas',
  ndvi: 'ndvi-zones', ndmi: 'ndmi-zones', cadastre: 'cadastral-parcels', start: 'visit-points',
};
for (const [name, zIndex] of [['satellite-raster', 402], ['access-areas', 405], ['interrow-segmentation', 410], ['canopy-segmentation', 415], ['vine-rows', 420], ['ndvi-zones', 421], ['ndmi-zones', 422], ['cadastral-parcels', 423], ['field-boundaries', 425], ['inspection-routes', 435], ['visit-points', 445]] as const) {
  map.createPane(name).style.zIndex = String(zIndex);
}
const overlays: Record<Overlay, L.LayerGroup> = {
  field: L.layerGroup(), route: L.layerGroup(), points: L.layerGroup(),
  row: L.layerGroup(), vineyard: L.layerGroup(), interrow_area: L.layerGroup(), access: L.layerGroup(),
  ndvi: L.layerGroup(), ndmi: L.layerGroup(), cadastre: L.layerGroup(), start: L.layerGroup(),
};
const fieldVisible = new Set<Overlay>(['field', 'route', 'points', 'vineyard']);
const allVisible = new Set<Overlay>(['field', 'route', 'points', 'row', 'vineyard', 'interrow_area', 'access', 'ndvi', 'ndmi', 'start']);
const measureVisible = new Set<Overlay>(['field', 'row', 'vineyard', 'interrow_area']);
let visible = fieldVisible;
let layerCounts: Record<Overlay, number> = { field: 0, route: 0, points: 0, row: 0, vineyard: 0, interrow_area: 0, access: 0, ndvi: 0, ndmi: 0, cadastre: 0, start: 0 };
const layerNames: Record<Overlay, string> = { field: 'Field boundaries', route: 'Routes', points: 'Visit points', row: 'Vine rows', vineyard: 'Canopy segmentation', interrow_area: 'Inter-rows', access: 'Access & constraints', ndvi: 'NDVI zones', ndmi: 'NDMI zones', cadastre: 'Cadastru', start: 'Inspection start' };
const usualByField = new Map<string, string>();
const pointLayers = new Map<MapFeature, L.Layer>();
let scene: Scene | null = null;
let fields: MapFeature[] = [];
let field: MapFeature | null = null;
let boundary: L.GeoJSON | null = null;
let targets: MapFeature[] = [];
let routes: MapFeature[] = [];
let routeLength: number | null = null;
let minConfidence: number | null = null;
let view: AppView = 'per-field';
const farmViews = createMapViews(map, {
  fields: () => fields, selected: () => field, features: () => scene?.features.features ?? [],
  satellite: () => scene?.sentinel ?? null, cadastre: () => scene?.cadastre ?? null,
  select: (id) => { selectField(id); navigateView('per-field'); },
  score: (id) => inspection.score(id)?.score,
});
initializeYieldView();
const inspection = createInspection({ changed: () => { farmViews.refreshOverview(); planner.refresh(); if (field) renderField(false); } });
const planner = createRoutePlanner(map, {
  fieldId: () => field ? fieldId(field) : null,
  minConfidence: () => minConfidence,
  filter: () => inspection.routeFilter(),
  organizerStart: () => {
    const start = scene?.features.features.find((item) => item.properties.label === 'start' && item.geometry.type === 'Point');
    return start && start.geometry.type === 'Point' ? L.latLng(start.geometry.coordinates[1], start.geometry.coordinates[0]) : null;
  },
  changed: () => { if (field) renderField(false); },
});
const measurements = createMeasurements(map, {
  features: () => scene?.features.features ?? [], open: () => navigateView('measurements'), padding: () => fitPadding(),
});
const fieldId =(item: MapFeature): string => item.properties.vineyard_id ?? '';
const belongsToField = (item: MapFeature): boolean => item.properties.vineyard_id === fieldId(field!) ||
  (fields.length === 1 && !item.properties.vineyard_id);

function popupOptions(): L.PopupOptions {
  const width = window.innerWidth;
  return {
    autoPanPaddingTopLeft: L.point(width <= 700 ? [108, 340] : width <= 1020 ? [390, 370] : [550, 170]),
    autoPanPaddingBottomRight: L.point(width <= 700 ? [12, 270] : width <= 1020 ? [20, 145] : [340, 145]),
  };
}
function popupFor(item: MapFeature): HTMLDivElement {
  const popup = document.createElement('div');
  popup.className = 'map-popup';
  const title = document.createElement('strong');
  const parcel = item.properties.label === 'cadastre';
  title.textContent = parcel ? 'Cadastral parcel outline' : item.properties.reason || `${item.properties.source === 'prediction' ? 'Candidate ' : ''}${item.properties.label.replaceAll('_', ' ')}`;
  popup.append(title);
  for (const [key, label] of [['parcel_id', 'Parcel'], ['vineyard_id', 'Field'], ['row_id', 'Row'], ['target_id', 'Point']] as const) {
    const value = item.properties[key];
    if (!value) continue;
    const line = document.createElement('div');
    line.textContent = `${label}: ${value}`;
    popup.append(line);
  }
  const rowLength = item.properties.label === 'row' && item.properties.row_id ? measurements.rowLength(item.properties.row_id, item.properties.vineyard_id) : undefined;
  if (rowLength !== undefined) {
    const line = document.createElement('div');
    line.textContent = `Row length: ${number.format(rowLength)} m`;
    popup.append(line);
  }
  if (parcel) {
    const note = document.createElement('small');
    note.textContent = `${typeof item.properties.area_m2 === 'number' ? `${number.format(item.properties.area_m2 / 10_000)} ha · ` : ''}does not establish ownership. ${scene?.cadastre?.credit ?? ''}`;
    popup.append(note);
  }
  return popup;
}
function addFeature(item: MapFeature, group: Overlay, style: L.PathOptions): L.GeoJSON {
  const layer = L.geoJSON(item, {
    pane: featurePanes[group], style,
    pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
      pane: featurePanes[group], radius: group === 'start' ? 7 : 9, color: '#fff', weight: 2.5, fillColor: group === 'start' ? '#24221f' : '#ed7625', fillOpacity: 1,
    }),
    onEachFeature: (_feature, mapped) => {
      mapped.bindPopup(() => popupFor(item), popupOptions());
      if (group === 'points') pointLayers.set(item, mapped);
    },
  });
  layer.addTo(overlays[group]);
  return layer;
}
function applyVisibility(): void {
  for (const [name, group] of Object.entries(overlays) as [Overlay, L.LayerGroup][]) {
    const shown = ((view === 'per-field' || view === 'measurements' || (view === 'all-fields' && name !== 'field')) && visible.has(name)) || (view === 'analysis' && name === 'field');
    if (shown && !map.hasLayer(group)) group.addTo(map);
    if (!shown && map.hasLayer(group)) map.removeLayer(group);
  }
  for (const checkbox of document.querySelectorAll<HTMLInputElement>('[data-layer]')) {
    const name = checkbox.dataset.layer as Overlay;
    checkbox.checked = visible.has(name) && layerCounts[name] > 0;
    checkbox.disabled = layerCounts[name] === 0;
    text(`${name}-layer-label`, `${layerNames[name]} (${layerCounts[name]})`);
  }
  el<HTMLButtonElement>('canopy-toggle').setAttribute('aria-pressed', String(visible.has('vineyard')));
  el<HTMLButtonElement>('canopy-toggle').disabled = layerCounts.vineyard === 0;
  for (const [id, name] of [['canopy-key', 'vineyard'], ['row-key', 'row'], ['route-key', 'route'], ['stop-key', 'points'], ['ndvi-key', 'ndvi'], ['ndmi-key', 'ndmi'], ['cadastre-key', 'cadastre'], ['access-key', 'access'], ['interrow-key', 'interrow_area'], ['start-key', 'start']] as const) {
    el(id).hidden = (view !== 'per-field' && view !== 'all-fields' && view !== 'measurements') || !visible.has(name) || layerCounts[name] === 0;
  }
  const mapView = view === 'per-field' || view === 'all-fields' || view === 'measurements';
  measurements.show(view === 'measurements');
  el('offroute-key').hidden = !mapView || !visible.has('points') || offRoute === 0;
  el('cadastre-credit').hidden = !mapView || !visible.has('cadastre') || layerCounts.cadastre === 0;
  el('sentinel-credit').hidden = !mapView || !((visible.has('ndvi') && layerCounts.ndvi > 0) || (visible.has('ndmi') && layerCounts.ndmi > 0));
  farmViews.show(view, visible.has('field'));
  planner.show(view === 'per-field');
  const toggle = el<HTMLButtonElement>('route-toggle');
  toggle.setAttribute('aria-pressed', String(routes.length > 0 && visible.has('route')));
  toggle.firstChild!.textContent = routes.length === 0 ? 'Route unavailable ' : visible.has('route') ? 'Hide route ' : 'Show route ';
}
/** Map padding that keeps a fitted shape clear of the Measurements view's panels. */
function fitPadding(): [L.PointTuple, L.PointTuple] {
  const width = window.innerWidth;
  return width <= 700 ? [[12, 160], [12, 330]] : width <= 1020 ? [[390, 440], [20, 100]] : [[550, 90], [350, 100]];
}
function fitField(): void {
  if (view === 'yield') return;
  if (view === 'all-fields') { farmViews.fitOverview(); return; }
  if (!boundary) return;
  if (view === 'measurements') {
    const [paddingTopLeft, paddingBottomRight] = fitPadding();
    map.fitBounds(boundary.getBounds().pad(0.18), { paddingTopLeft, paddingBottomRight, maxZoom: 18 });
    return;
  }
  const width = window.innerWidth;
  const paddingTopLeft: L.PointTuple = width <= 700 ? [12, view === 'analysis' ? 180 : 300] : width <= 1020 ? [390, 320] : [550, 145];
  const paddingBottomRight: L.PointTuple = width <= 700 ? [12, view === 'analysis' ? 350 : 265] : width <= 1020 || view === 'analysis' ? [20, 145] : [340, 130];
  map.fitBounds(boundary.getBounds().pad(0.18), { paddingTopLeft, paddingBottomRight, maxZoom: 18 });
}
function focusPoint(item: MapFeature): void {
  const layer = pointLayers.get(item);
  if (!layer) return;
  const bounds = L.geoJSON(item).getBounds();
  if (!bounds.isValid()) return;
  map.setView(bounds.getCenter(), Math.max(map.getZoom(), 18), { animate: false });
  if (!visible.has('points')) { visible.add('points'); applyVisibility(); }
  layer.bindPopup(() => popupFor(item), popupOptions()).openPopup();
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
      title.textContent = pointTitle(item);
      const meta = document.createElement('small');
      meta.textContent = [item.properties.source === 'prediction' ? 'Candidate point' : 'Mapped point', item.properties.row_id].filter(Boolean).join(' · ');
      copy.append(title, meta);
      button.append(badge, copy);
      button.addEventListener('click', () => focusPoint(item));
      const row = document.createElement('div');
      row.className = 'stop-item';
      row.append(button);
      const status = inspection.statusControls(item, index);
      if (status) row.append(status);
      list.append(row);
    });
  }
  text('plan-summary', targets.length
    ? `${targets.length} visit point${targets.length === 1 ? '' : 's'} in this field · ${routes.length ? 'route available' : 'route pending'}`
    : routes.length ? 'A field route is available, but no visit points were supplied.' : 'No route or visit points have been supplied for this field.');
  text('route-status', (planner.current() && view === 'per-field' ? 'Planned route' : routes.length ? 'Field route available' : 'Route pending')
    + (offRoute ? ` · ${offRoute} not on it` : ''));
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
// Visit points the shown route does not pass within 2 m of: grey and hollow, with why in a tooltip.
let offRoute = 0;
function offRouteText(entry: TargetStatus, left: number | null): string {
  if (entry.status === 'over_budget') {
    const needs = typeof entry.needs_outside_m === 'number' ? `~${Math.round(entry.needs_outside_m)} m` : 'more';
    return `Not on this route: reaching it needs ${needs} outside the allowed lanes${left !== null ? ` (the route has ${Math.max(Math.round(left), 0)} m of its allowance left)` : ''}. Going over 2% outside would score the route 0.`;
  }
  if (entry.status === 'unreachable') return `Not on this route: no legal path${entry.reason ? ` (${entry.reason})` : ''}.`;
  return 'Not on this route: the route passes more than 2 m away.';
}
function markOffRoute(shown: MapFeature[]): void {
  const status = new Map<string, { entry: TargetStatus; left: number | null }>();
  for (const route of shown) {
    const { outside_budget_m: budget, robust_outside_m: used } = route.properties;
    const left = typeof budget === 'number' && typeof used === 'number' ? budget - used : null;
    for (const entry of route.properties.target_status ?? []) status.set(entry.id, { entry, left });
  }
  offRoute = 0;
  for (const [item, layer] of pointLayers) {
    const found = status.get(String(item.properties.id ?? ''));
    if (!found || found.entry.status === 'visited' || !(layer instanceof L.CircleMarker)) continue;
    offRoute += 1;
    layer.setStyle({ color: '#6b7280', weight: 2.5, fillColor: '#fff', fillOpacity: 0.95, dashArray: '3 3' });
    const tip = document.createElement('span');
    tip.textContent = offRouteText(found.entry, found.left);
    layer.bindTooltip(tip, { direction: 'top', className: 'offroute-tip' });
  }
}
function renderField(fit = true): void {
  if (!scene || !field) return;
  // a route planned on request replaces the precomputed ones in the per-field view
  const planned = view === 'per-field' ? planner.current() : null;
  planner.refresh();  // its download button follows the field and cutoff too
  pointLayers.clear();
  for (const group of Object.values(overlays)) group.clearLayers();
  addFeature(field, 'field', { color: '#fff', weight: 10, fillOpacity: 0, opacity: 0.95, interactive: false });
  boundary = addFeature(field, 'field', {
    color: '#7e22ce', weight: 5, fillColor: '#a132ea', fillOpacity: view === 'analysis' ? 0 : 0.19,
    dashArray: field.properties.source === 'prediction' ? '12 7' : undefined,
  });
  const boundaryLabel = document.createElement('span');
  boundaryLabel.textContent = `Field ${fieldId(field)}${field.properties.source === 'prediction' ? ' · candidate boundary' : ''}`;
  boundary.bindTooltip(boundaryLabel, { sticky: true });
  targets = []; routes = [];
  const shownRoutes: MapFeature[] = planned ? [planned.feature] : [];
  layerCounts = { field: view === 'all-fields' ? fields.length : 1, route: 0, points: 0, row: 0, vineyard: 0, interrow_area: 0, access: 0, ndvi: 0, ndmi: 0, cadastre: 0, start: 0 };
  for (const item of scene.features.features) {
    const { label } = item.properties;
    if (item === field) continue;
    if (label === 'passage' || label === 'forbidden' || label === 'study_area') {
      layerCounts.access += 1;
      addFeature(item, 'access', label === 'forbidden'
        ? { color: '#a83e38', weight: 2, fillColor: '#db7770', fillOpacity: 0.15 }
        : label === 'study_area' ? { color: '#526252', weight: 2, dashArray: '6 5', fill: false }
        : { color: '#ba7e30', weight: 2, fillColor: '#e6ba70', fillOpacity: 0.18 });
      continue;
    }
    if (['cadastre', 'parcel'].includes(label)) {
      if (view === 'all-fields' || belongsToField(item)) {
        layerCounts.cadastre += 1;
        addFeature(item, 'cadastre', { color: '#db2777', weight: view === 'all-fields' ? 1.2 : 2.5, fillOpacity: view === 'all-fields' ? 0.03 : 0.08 });
      }
      continue;
    }
    // one route per point-confidence cutoff: the field's own in the per-field view, the site-wide route in all fields
    // a farmer's walk is always planned on request: the precomputed routes also visit missing canopy
    if (label === 'route' && (planned || inspection.role() === 'farmer' || (item.properties.min_confidence ?? null) !== minConfidence
      || (item.properties.scope === 'site') !== (view === 'all-fields'))) continue;
    if (label === 'inspection' && minConfidence !== null && (item.properties.confidence ?? 1) < minConfidence) continue;
    if ((label === 'inspection' || label === 'waste') && !inspection.visible(item)) continue;
    if (view !== 'all-fields' && !belongsToField(item)) continue;
    if (label === 'start') {
      layerCounts.start += 1;
      addFeature(item, 'start', {});
    } else if (label === 'route') {
      layerCounts.route += 1;
      if (belongsToField(item)) routes.push(item);
      shownRoutes.push(item);
      addFeature(item, 'route', { color: '#fff', weight: 11, opacity: 0.95, interactive: false });
      addFeature(item, 'route', { color: '#ed7625', weight: 6, opacity: 1, dashArray: '12 8' });
    } else if (label === 'inspection' || label === 'waste') {
      layerCounts.points += 1;
      if (belongsToField(item)) targets.push(item);
      if (item.geometry.type === 'Point') addFeature(item, 'points', {});
      else {
        const outline = addFeature(item, 'points', { color: '#ed7625', weight: 2, fillColor: '#ed7625', fillOpacity: 0.35 });
        const bounds = outline.getBounds();
        if (bounds.isValid()) {
          const marker = L.circleMarker(bounds.getCenter(), { pane: featurePanes.points, radius: 9, color: '#fff', weight: 2.5, fillColor: '#ed7625', fillOpacity: 1 })
            .bindPopup(() => popupFor(item), popupOptions()).addTo(overlays.points);
          pointLayers.set(item, marker);
        }
      }
    } else if (label === 'row') {
      layerCounts.row += 1;
      addFeature(item, 'row', { color: '#d52e36', weight: map.getZoom() >= 18 ? 2.5 : 1.25, opacity: 0.85 });
    } else if (label === 'vineyard') {
      layerCounts.vineyard += 1;
      addFeature(item, 'vineyard', { color: '#078ca0', weight: 1, fillColor: '#10bdd0', fillOpacity: 0.5 });
    } else if (label === 'interrow_area') {
      layerCounts.interrow_area += 1;
      addFeature(item, 'interrow_area', { color: '#397d95', weight: 1.2, fillColor: '#5a9cad', fillOpacity: 0.18 });
    } else if (label === 'sentinel_zone' && (item.properties.index === 'ndvi' || item.properties.index === 'ndmi')) {
      const name = item.properties.index;
      layerCounts[name] += 1;
      const color = name === 'ndvi' ? '#c68412' : '#0284c7';
      addFeature(item, name, { color, weight: 2.5, fillColor: color, fillOpacity: 0.28 });
    }
  }
  if (planned) {
    layerCounts.route += 1;
    routes.push(planned.feature);
    addFeature(planned.feature, 'route', { color: '#fff', weight: 11, opacity: 0.95, interactive: false });
    addFeature(planned.feature, 'route', { color: '#ed7625', weight: 6, opacity: 1, dashArray: '12 8' });
  }
  markOffRoute(planned ? [{ ...planned.feature, properties: { ...planned.feature.properties, target_status: planned.route.target_status } }] : shownRoutes);
  const satellite = scene.sentinel;
  text('evidence-satellite', satellite ? `${satellite.dates.length} dates before flight · ${layerCounts.ndvi + layerCounts.ndmi} low zones` : 'Not available');
  text('evidence-cadastre', scene.cadastre ? `${layerCounts.cadastre} parcel outline${layerCounts.cadastre === 1 ? '' : 's'}` : 'Not available');
  routeLength = measuredRouteLength();
  const area = field.properties.area_m2;
  text('field-meta', `${typeof area === 'number' && Number.isFinite(area) ? `${(area / 10_000).toFixed(2)} ha · ` : ''}${field.properties.source === 'prediction' ? 'Candidate boundary from the model' : 'Mapped field boundary'}`);
  updateViewStatus();
  renderStops(); updateSavings(); applyVisibility();
  inspection.renderScore(fieldId(field));
  farmViews.refreshAnalysis();
  measurements.showField(fieldId(field));
  if (fit) requestAnimationFrame(fitField);
}
function renderScene(data: Scene): void {
  if (data.crs !== 'EPSG:4326' || data.features?.type !== 'FeatureCollection' || !Array.isArray(data.features.features)) {
    throw new Error('The field scene has an unsupported map format');
  }
  scene = data;
  measurements.setData(data.measurements);
  overlays.cadastre.options.attribution = data.cadastre?.credit;
  overlays.ndvi.options.attribution = overlays.ndmi.options.attribution = data.sentinel?.credit;
  fields = data.features.features.filter((item) => item.properties?.label === 'block' && item.properties.vineyard_id)
    .sort((a, b) => fieldId(a).localeCompare(fieldId(b), undefined, { numeric: true }));
  const select = el<HTMLSelectElement>('field-select');
  select.replaceChildren();
  for (const item of fields) {
    const option = document.createElement('option');
    option.value = fieldId(item); option.textContent = `Field ${fieldId(item)}`;
    select.append(option);
  }
  farmViews.refreshOverview();
  updateYieldFields(fields.map(fieldId), fields.length ? fieldId(fields[0]) : null);
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
  planner.refresh();
  renderField();
}

function selectField(id: string): void {
  if (field) usualByField.set(fieldId(field), el<HTMLInputElement>('usual-minutes').value);
  field = fields.find((item) => fieldId(item) === id) ?? null;
  el<HTMLSelectElement>('field-select').value = id;
  el<HTMLInputElement>('usual-minutes').value = field ? usualByField.get(fieldId(field)) ?? '' : '';
  renderField();
}
el<HTMLSelectElement>('field-select').addEventListener('change', (event) => {
  selectField((event.currentTarget as HTMLSelectElement).value);
});
el('canopy-toggle').addEventListener('click', () => {
  if (visible.has('vineyard')) visible.delete('vineyard'); else visible.add('vineyard');
  applyVisibility();
});
el('fit-field').addEventListener('click', fitField);
el<HTMLSelectElement>('route-confidence').addEventListener('change', (event) => {
  const value = (event.currentTarget as HTMLSelectElement).value;
  minConfidence = value ? Number(value) : null;
  if (field) renderField();
});
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
    for (const name of ['stops', 'evidence', 'field-measure']) el(`${name}-panel`).hidden = button.dataset.panel !== name;
    for (const item of document.querySelectorAll<HTMLButtonElement>('[data-panel]')) {
      item.classList.toggle('selected', item === button);
      item.setAttribute('aria-pressed', String(item === button));
    }
  });
}
function viewFromHash(): AppView {
  const hash = location.hash.slice(1);
  return hash === 'all-fields' || hash === 'analysis' || hash === 'yield' || hash === 'measurements' ? hash : 'per-field';
}
function updateViewStatus(): void {
  if (!scene) return;
  const pointCount = scene.features.features.filter((item) => ['inspection', 'waste'].includes(item.properties.label)).length;
  text('status', view === 'all-fields' ? `${fields.length} fields · ${pointCount} visit points across the scene` : view === 'measurements' ? `Measurements · ${field ? `Field ${fieldId(field)}` : 'no field selected'}` : view === 'analysis' ? `Satellite & land records · ${field ? `Field ${fieldId(field)}` : 'no field selected'}` : `${fields.length} fields in scene · ${routes.length ? 'field route available' : 'field route pending'}`);
}
function switchView(next: AppView, focus = true): void {
  view = next;
  visible = view === 'all-fields' ? allVisible : view === 'measurements' ? measureVisible : fieldVisible;
  document.body.dataset.view = view;
  el('map').setAttribute('aria-label', view === 'all-fields' ? 'Map of all vineyard fields' : 'Map of the selected vineyard field');
  updateViewStatus();
  el('overview-panel').hidden = view !== 'all-fields';
  el('analysis-panel').hidden = view !== 'analysis';
  el('yield-page').hidden = view !== 'yield';
  el('measure-panel').hidden = el('measure-site').hidden = view !== 'measurements';
  text('field-key-label', view === 'all-fields' ? 'Field boundaries' : 'Selected field');
  el('route-key').hidden = view !== 'per-field';
  el('stop-key').hidden = view !== 'per-field';
  for (const item of document.querySelectorAll<HTMLButtonElement>('[data-nav]')) {
    if (item.dataset.nav === view) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  }
  if (scene && field) renderField();
  else applyVisibility();
  const titles = { 'per-field': 'Per field', 'all-fields': 'All fields', measurements: 'Measurements', analysis: 'NDVI / NDMI / Cadastru', yield: 'Yield & harvest' };
  document.title = `${titles[view]} · Agrocontrol by BloomSense`;
  if (focus) el(view === 'yield' ? 'yield-title' : view === 'analysis' ? 'analysis-title' : view === 'measurements' ? 'measure-title' : view === 'all-fields' ? 'overview-title' : 'field-select').focus();
  if (view !== 'yield') requestAnimationFrame(() => { map.invalidateSize(); fitField(); });
}
function navigateView(next: AppView): void {
  if (location.hash.slice(1) === next) switchView(next); else location.hash = next;
}
for (const button of document.querySelectorAll<HTMLButtonElement>('[data-nav]')) {
  button.addEventListener('click', () => navigateView(button.dataset.nav as AppView));
}
window.addEventListener('hashchange', () => switchView(viewFromHash()));
switchView(viewFromHash(), false);
map.on('zoomend', () => {
  for (const layer of overlays.row.getLayers()) {
    if (layer instanceof L.GeoJSON) layer.setStyle({ weight: map.getZoom() >= 18 ? 2.5 : 1.25 });
  }
});
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
    measurements.setData(null);
    text('field-meta', 'Start the field service to load current boundaries.');
    farmViews.refreshOverview(); updateYieldFields([], null);
    text('plan-summary', 'Visit points and routes are unavailable.'); text('target-count', '—');
  }
}
void loadScene();
void inspection.load();
