import L from 'leaflet';
import type { AppView, MapFeature } from './scene-types';

type LayerView = 'ndvi' | 'ndmi' | 'cadastre';
type ViewSources = {
  fields: () => MapFeature[];
  selected: () => MapFeature | null;
  features: () => MapFeature[];
  select: (id: string) => void;
};
const element = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing map view element: ${id}`);
  return found as T;
};
const setText = (id: string, value: string): void => { element(id).textContent = value; };
const fieldId = (feature: MapFeature): string => feature.properties.vineyard_id ?? '';
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
function tooltip(text: string): HTMLElement {
  const label = document.createElement('span'); label.textContent = text; return label;
}
export function createMapViews(map: L.Map, sources: ViewSources) {
  const overview = L.layerGroup();
  const analysis = L.layerGroup();
  let bounds = L.latLngBounds([]);
  let selectedLayer: LayerView = 'ndvi';
  function renderList(): void {
    const query = element<HTMLInputElement>('field-search').value.trim().toLowerCase();
    const list = element('field-list'); list.replaceChildren();
    const matches = sources.fields().filter((field) => `field ${fieldId(field)}`.toLowerCase().includes(query));
    if (!matches.length) {
      const empty = document.createElement('p'); empty.className = 'fields-empty';
      empty.textContent = query ? 'No fields match your search.' : 'No field boundaries are available.';
      list.append(empty);
    }
    for (const field of matches) {
      const button = document.createElement('button'); button.type = 'button';
      const title = document.createElement('strong'); title.textContent = `Field ${fieldId(field)}`;
      const detail = document.createElement('span');
      const area = field.properties.area_m2;
      detail.textContent = [typeof area === 'number' && Number.isFinite(area) ? `${number.format(area / 10000)} ha` : '', field.properties.source === 'prediction' ? 'Candidate' : 'Mapped'].filter(Boolean).join(' · ');
      button.append(title, detail); button.addEventListener('click', () => sources.select(fieldId(field))); list.append(button);
    }
  }
  function refreshOverview(): void {
    overview.clearLayers(); bounds = L.latLngBounds([]);
    for (const field of sources.fields()) {
      const polygon = L.geoJSON(field, { pane: 'field-boundaries', style: {
        color: '#7e22ce', weight: 3, fillColor: '#a132ea', fillOpacity: 0.13,
        dashArray: field.properties.source === 'prediction' ? '8 5' : undefined,
      } }).bindTooltip(tooltip(`Field ${fieldId(field)}${field.properties.source === 'prediction' ? ' · candidate' : ''}`));
      polygon.on('click', () => sources.select(fieldId(field)));
      polygon.addTo(overview); bounds.extend(polygon.getBounds());
    }
    setText('overview-count', String(sources.fields().length));
    setText('overview-points', String(sources.features().filter((feature) => ['inspection', 'waste'].includes(feature.properties.label)).length));
    renderList();
  }
  function refreshAnalysis(): void {
    analysis.clearLayers();
    const selected = sources.selected();
    const id = selected ? fieldId(selected) : null;
    const label = selectedLayer === 'cadastre' ? 'Cadastru' : selectedLayer.toUpperCase();
    const items = sources.features().filter((feature) => selectedLayer === 'cadastre'
      ? ['cadastre', 'parcel'].includes(feature.properties.label) && (!feature.properties.vineyard_id || feature.properties.vineyard_id === id)
      : feature.properties.label === 'sentinel_zone' && feature.properties.index === selectedLayer && feature.properties.vineyard_id === id);
    const color = selectedLayer === 'ndvi' ? '#c68412' : selectedLayer === 'ndmi' ? '#0284c7' : '#db2777';
    for (const item of items) {
      L.geoJSON(item, { style: { color, weight: 2.5, fillColor: color, fillOpacity: 0.28 } })
        .bindTooltip(tooltip(`${label} ${selectedLayer === 'cadastre' ? 'parcel outline' : 'candidate zone'}`)).addTo(analysis);
    }
    const dates = [...new Set(items.flatMap((item) => Array.isArray(item.properties.dates) ? item.properties.dates.filter((date) => typeof date === 'string') : []))].sort();
    setText('analysis-field', id ? `Field ${id}` : 'No field selected');
    setText('analysis-dates', dates.length ? dates.join(' · ') : 'Not supplied');
    setText('analysis-count', items.length ? `${items.length} ${selectedLayer === 'cadastre' ? 'parcel outlines' : 'candidate zones'}` : 'Not available');
    setText('analysis-badge', items.length ? 'AVAILABLE IN SCENE' : 'NOT AVAILABLE');
    setText('analysis-state-title', items.length ? `${label} overlay` : selectedLayer === 'cadastre' ? 'No cadastral overlay' : `No ${label} observations`);
    setText('analysis-detail', items.length
      ? selectedLayer === 'cadastre' ? 'Cadastral outlines supplied by the current scene. They do not establish ownership.' : 'Candidate zones supplied by the satellite analysis. Review the dates and field observations before deciding to visit.'
      : selectedLayer === 'cadastre' ? 'The current scene does not supply cadastral parcel outlines. The model field boundary remains visible for reference.' : `The current scene has no ${label} zones for this field. No visit or treatment decision can be inferred from this empty layer.`);
    setText('analysis-footnote', selectedLayer === 'cadastre' ? 'Violet: model field boundary. Pink: cadastral outlines, when supplied.' : `Violet: field boundary. ${selectedLayer === 'ndvi' ? 'Amber' : 'Blue'}: satellite candidate zones, when supplied.`);
  }
  function show(view: AppView, showBoundaries = true): void {
    if (map.hasLayer(overview)) map.removeLayer(overview);
    if (map.hasLayer(analysis)) map.removeLayer(analysis);
    if (view === 'all-fields' && showBoundaries) overview.addTo(map);
    if (view === 'analysis') analysis.addTo(map);
  }
  function fitOverview(): void {
    if (!bounds.isValid()) return;
    const width = window.innerWidth;
    map.fitBounds(bounds.pad(0.08), {
      paddingTopLeft: width <= 700 ? [12, 310] : width <= 1020 ? [390, 90] : [555, 90],
      paddingBottomRight: width <= 700 ? [12, 220] : [30, 100], maxZoom: 17,
    });
  }
  element('field-search').addEventListener('input', renderList);
  for (const button of document.querySelectorAll<HTMLButtonElement>('[data-analysis]')) {
    button.addEventListener('click', () => {
      selectedLayer = button.dataset.analysis as LayerView;
      for (const item of document.querySelectorAll<HTMLButtonElement>('[data-analysis]')) item.setAttribute('aria-pressed', String(item === button));
      refreshAnalysis();
    });
  }
  return { refreshOverview, refreshAnalysis, show, fitOverview };
}
