import L from 'leaflet';
import type { AppView, CadastreInfo, MapFeature, Satellite } from './scene-types';

type LayerView = 'ndvi' | 'ndmi' | 'cadastre';
type ViewSources = {
  fields: () => MapFeature[];
  selected: () => MapFeature | null;
  features: () => MapFeature[];
  satellite: () => Satellite | null;
  cadastre: () => CadastreInfo | null;
  select: (id: string) => void;
  /** The field's current farmer score (0-100), once statuses are loaded. */
  score: (id: string) => number | undefined;
};
const element = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing map view element: ${id}`);
  return found as T;
};
const setText = (id: string, value: string): void => { element(id).textContent = value; };
const fieldId = (feature: MapFeature): string => feature.properties.vineyard_id ?? '';
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const decimals = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const index = { format: (value: number): string => decimals.format(value).replace('-', '−') };
const day = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const dayLabel = (iso: string): string => day.format(new Date(`${iso}T00:00:00Z`));
const plural = (count: number, word: string): string => `${count} ${word}${count === 1 ? '' : 's'}`;
const SCALE_WORDS = { ndvi: ['less green', 'greener'], ndmi: ['drier', 'wetter'] } as const;
const SHORT_WORDS = { ndvi: ['low', 'high'], ndmi: ['dry', 'wet'] } as const;
function tooltip(text: string): HTMLElement {
  const label = document.createElement('span'); label.textContent = text; return label;
}
export function createMapViews(map: L.Map, sources: ViewSources) {
  const overview = L.layerGroup();
  const analysis = L.layerGroup();
  let bounds = L.latLngBounds([]);
  let selectedLayer: LayerView = 'ndvi';
  let selectedDate = 'median';
  let currentView: AppView = 'per-field';
  let credits = { sentinel: false, cadastre: false };
  // the map key carries credits on phones, where Leaflet's attribution control is hidden; main.ts sets them for the map views
  function updateCredits(): void {
    if (currentView !== 'analysis') return;
    element('sentinel-credit').hidden = !credits.sentinel;
    element('cadastre-credit').hidden = !credits.cadastre;
  }
  function renderList(): void {
    const query = element<HTMLInputElement>('field-search').value.trim().toLowerCase();
    const list = element('field-list'); list.replaceChildren();
    // lowest farmer score first: where the inspector should look first (a stable sort keeps field order on ties)
    const matches = sources.fields().filter((field) => `field ${fieldId(field)}`.toLowerCase().includes(query))
      .sort((a, b) => (sources.score(fieldId(a)) ?? 101) - (sources.score(fieldId(b)) ?? 101));
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
      const score = sources.score(fieldId(field));
      detail.textContent = [score === undefined ? '' : `Score ${score}`, typeof area === 'number' && Number.isFinite(area) ? `${number.format(area / 10000)} ha` : '', field.properties.source === 'prediction' ? 'Candidate' : 'Mapped'].filter(Boolean).join(' · ');
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
  function renderDates(satellite: Satellite | null): void {
    const select = element<HTMLSelectElement>('analysis-date');
    const options = satellite ? ['median', ...satellite.dates] : [];
    if (select.options.length !== options.length) {
      select.replaceChildren(...options.map((value) => new Option(value === 'median' ? `Median · ${satellite!.dates.length} dates` : dayLabel(value), value)));
    }
    select.value = options.includes(selectedDate) ? selectedDate : 'median';
  }
  function refreshAnalysis(): void {
    analysis.clearLayers();
    const selected = sources.selected();
    const id = selected ? fieldId(selected) : null;
    const satellite = selectedLayer === 'cadastre' ? null : sources.satellite();
    const cadastre = sources.cadastre();
    const label = selectedLayer === 'cadastre' ? 'Cadastru' : selectedLayer.toUpperCase();
    const items = sources.features().filter((feature) => selectedLayer === 'cadastre'
      ? ['cadastre', 'parcel'].includes(feature.properties.label) && feature.properties.vineyard_id === id
      : feature.properties.label === 'sentinel_zone' && feature.properties.index === selectedLayer && feature.properties.vineyard_id === id);
    const color = selectedLayer === 'ndvi' ? '#c68412' : selectedLayer === 'ndmi' ? '#0284c7' : '#db2777';
    const image = satellite?.images.find((item) => item.index === selectedLayer && item.date === selectedDate)
      ?? satellite?.images.find((item) => item.index === selectedLayer && item.date === 'median');
    if (satellite && image) {
      L.imageOverlay(image.url, satellite.bounds, {
        pane: 'satellite-raster', opacity: 0.82, className: 'sentinel-raster', attribution: satellite.credit,
        alt: `${label} ${image.date === 'median' ? 'median' : dayLabel(image.date)}`,
      }).addTo(analysis);
    }
    for (const item of items) {
      const parcel = selectedLayer === 'cadastre';
      const pane = parcel ? 'cadastral-parcels' : `${selectedLayer}-zones`;
      const attribution = parcel ? cadastre?.credit : satellite?.credit;
      if (!parcel) L.geoJSON(item, { pane, interactive: false, style: { color: '#fff', weight: 6, opacity: 0.9, fill: false } }).addTo(analysis);
      const area = item.properties.area_m2;
      L.geoJSON(item, { pane, attribution, style: { color, weight: 2.5, fillColor: color, fillOpacity: parcel ? 0.12 : 0.08 } })
        .bindTooltip(tooltip(parcel
          ? `Cadastral parcel ${item.properties.parcel_id ?? ''}${typeof area === 'number' ? ` · ${number.format(area / 10000)} ha` : ''}`
          : `${label} low zone${typeof area === 'number' ? ` · ${number.format(area)} m²` : ''}`)).addTo(analysis);
    }
    renderDates(satellite);
    element('analysis-raster').hidden = !image;
    if (satellite && image && selectedLayer !== 'cadastre') {
      const legend = satellite.legend[selectedLayer];
      const [low, high] = SCALE_WORDS[selectedLayer];
      const [shortLow, shortHigh] = SHORT_WORDS[selectedLayer];
      element('analysis-ramp').style.background = `linear-gradient(90deg, ${legend.colors.join(', ')})`;
      setText('analysis-ramp-min', `${index.format(legend.min)} ${shortLow}`);
      setText('analysis-ramp-max', `${shortHigh} ${index.format(legend.max)}`);
      element('analysis-legend').setAttribute('aria-label', `${label} colour scale: light is ${index.format(legend.min)} or lower (${low}), dark is ${index.format(legend.max)} or higher (${high})`);
    }
    const credit = element('analysis-credit');
    credit.textContent = selectedLayer === 'cadastre' ? cadastre?.credit ?? '' : satellite?.credit ?? '';
    credit.hidden = !credit.textContent;
    credits = { sentinel: selectedLayer !== 'cadastre' && Boolean(image), cadastre: selectedLayer === 'cadastre' && Boolean(cadastre) };
    updateCredits();
    setText('analysis-field', id ? `Field ${id}` : 'No field selected');
    if (selectedLayer === 'cadastre') {
      setText('analysis-dates-label', 'Registry snapshot');
      setText('analysis-dates', cadastre ? dayLabel(cadastre.snapshot) : 'Not supplied');
      setText('analysis-count', items.length ? plural(items.length, 'parcel outline') : cadastre ? 'None over this field' : 'Not available');
      setText('analysis-badge', cadastre ? `${cadastre.count} PARCELS ON THE SITE` : 'NOT AVAILABLE');
      setText('analysis-state-title', items.length ? `${plural(items.length, 'cadastral parcel')} in this field` : cadastre ? 'No parcel overlaps this field' : 'No cadastral overlay');
      setText('analysis-detail', items.length
        ? 'Parcel outlines from the public cadastral map that overlap the model field. They do not establish ownership.'
        : cadastre ? 'No cadastral parcel outline overlaps this model field. Outlines do not establish ownership.'
        : 'The current scene does not supply cadastral parcel outlines. The model field boundary remains visible for reference.');
      setText('analysis-footnote', 'Violet: model field boundary. Pink: cadastral parcel outlines.');
      return;
    }
    const zones = plural(items.length, 'low zone');
    setText('analysis-dates-label', 'Observation dates');
    setText('analysis-dates', satellite ? satellite.dates.map(dayLabel).join(' · ') : 'Not supplied');
    setText('analysis-count', image ? `${satellite!.legend[selectedLayer].pixel_m} m raster · ${zones}` : items.length ? zones : 'Not available');
    setText('analysis-badge', image ? `SENTINEL-2 · ${satellite!.legend[selectedLayer].pixel_m} M PIXELS` : items.length ? 'AVAILABLE IN SCENE' : 'NOT AVAILABLE');
    setText('analysis-state-title', image ? `${label} · ${image.date === 'median' ? `median of ${satellite!.dates.length} dates` : dayLabel(image.date)}` : items.length ? `${label} zones` : `No ${label} observations`);
    setText('analysis-detail', image
      ? `${selectedLayer === 'ndvi' ? 'Greenness' : 'Moisture'} before the ${dayLabel(satellite!.flight)} flight. One pixel spans several vine rows: a pale patch is a place to check, not a finding. ${items.length ? `${zones} in this field stay${items.length === 1 ? 's' : ''} low on every date.` : 'No part of this field stays low on every date.'}`
      : items.length ? 'Candidate zones supplied by the satellite analysis. Review the dates and field observations before deciding to visit.'
      : `The current scene has no ${label} data for this field. No visit or treatment decision can be inferred from this empty layer.`);
    setText('analysis-footnote', `Violet: field boundary. ${selectedLayer === 'ndvi' ? 'Amber' : 'Blue'} outline: part of the field lower than the rest of it on every date.`);
  }
  function show(view: AppView, showBoundaries = true): void {
    currentView = view;
    updateCredits();
    if (map.hasLayer(overview)) map.removeLayer(overview);
    if (map.hasLayer(analysis)) map.removeLayer(analysis);
    if (view === 'all-fields' && showBoundaries) overview.addTo(map);
    if (view === 'analysis') analysis.addTo(map);
  }
  /** Fit all fields (or `target`, a planned route) clear of the overview and savings panels. */
  function fitOverview(target = bounds): void {
    if (!target.isValid()) return;
    const width = window.innerWidth;
    map.fitBounds(target.pad(0.08), {
      paddingTopLeft: width <= 700 ? [12, 310] : width <= 1020 ? [390, 90] : [555, 90],
      paddingBottomRight: width <= 700 ? [12, 220] : width <= 1020 ? [300, 100] : [345, 100], maxZoom: 17,  // clear of the savings card
    });
  }
  element('field-search').addEventListener('input', renderList);
  element<HTMLSelectElement>('analysis-date').addEventListener('change', (event) => {
    selectedDate = (event.currentTarget as HTMLSelectElement).value;
    refreshAnalysis();
  });
  for (const button of document.querySelectorAll<HTMLButtonElement>('[data-analysis]')) {
    button.addEventListener('click', () => {
      selectedLayer = button.dataset.analysis as LayerView;
      for (const item of document.querySelectorAll<HTMLButtonElement>('[data-analysis]')) item.setAttribute('aria-pressed', String(item === button));
      refreshAnalysis();
    });
  }
  return { refreshOverview, refreshAnalysis, show, fitOverview };
}
