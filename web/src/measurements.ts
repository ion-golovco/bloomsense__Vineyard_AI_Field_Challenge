import L from 'leaflet';
import type { MapFeature, MeasuredBlock, MeasuredRow, MeasuredTotals, Measurements } from './scene-types';
import './measurements.css';

// Measurement tables from /api/scene (EPSG:32635 metres, the numbers of measurements.csv): the site totals card, the
// field card (per-field plan tab and the Measurements view) and the sortable row table that highlights a row on the map.
type Sources = {
  features: () => MapFeature[];
  /** Open the Measurements view (from the per-field card). */
  open: () => void;
  /** Map padding [top-left, bottom-right] that keeps a fitted row clear of the panels. */
  padding: () => [L.PointTuple, L.PointTuple];
  /** Detected missing-canopy points (gaps, missing planting) and waste spots in a field. */
  spots: (field: string) => { gaps: number; waste: number };
};
type SortKey = 'row_id' | 'length_m' | 'row_structure';
const element = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing measurement element: ${id}`);
  return found as T;
};
const metres = new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const hectares = new Intl.NumberFormat('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const count = new Intl.NumberFormat('en-US');
const ESTIMATE_NOTE = 'Model estimate, before correction in Marcaj';

function stat(label: string, value: string, detail = '', wide = false): HTMLDivElement {
  const item = document.createElement('div');
  if (wide) item.className = 'wide';
  const term = document.createElement('dt'); term.textContent = label;
  const data = document.createElement('dd'); data.textContent = value;
  if (detail) { const small = document.createElement('small'); small.textContent = detail; data.append(small); }
  item.append(term, data);
  return item;
}
function badge(estimate: boolean, text = estimate ? 'MODEL ESTIMATE' : 'ANNOTATED'): HTMLSpanElement {
  const span = document.createElement('span');
  span.className = `basis-badge${estimate ? ' estimate' : ''}`;
  span.textContent = text;
  return span;
}
function areaStats(item: MeasuredBlock | Measurements['total']): HTMLDivElement[] {
  return [
    stat('Canopy area', `${metres.format(item.canopy_area_m2)} m²`, `${hectares.format(item.canopy_area_ha)} ha`),
    stat('Inter-row area', `${metres.format(item.interrow_area_m2)} m²`, `${hectares.format(item.interrow_area_ha)} ha`),
  ];
}

export function createMeasurements(map: L.Map, sources: Sources) {
  const highlight = L.layerGroup();
  let data: Measurements | null = null;
  let field: string | null = null;
  let selected: string | null = null;
  let sort: { key: SortKey; ascending: boolean } = { key: 'row_id', ascending: true };
  const block = (): MeasuredBlock | undefined => data?.blocks.find((item) => item.vineyard_id === field);

  function renderSite(): void {
    const grid = element('measure-site-grid');
    const basis = element('measure-site-basis');
    if (!data) {
      grid.replaceChildren(); basis.replaceChildren();
      element('measure-site-note').textContent = 'The current scene has no measurements.';
      return;
    }
    const { total, estimate_blocks: estimated } = data;
    // the official site route (route.geojson: scope site, every visit point), as marcaj.scene picks it
    const official = sources.features().filter((item) => item.properties.label === 'route'
      && (item.properties.scope ?? 'site') === 'site' && item.properties.min_confidence == null && typeof item.properties.length_m === 'number');
    const route = official.length ? [stat('Inspection route', `${metres.format(official.reduce((sum, item) => sum + (item.properties.length_m ?? 0), 0))} m`, 'site route')] : [];
    grid.replaceChildren(
      stat('Fields', count.format(total.block_count)), stat('Rows', count.format(total.row_count)),
      ...areaStats(total), stat('Total row length', `${metres.format(total.row_length_m)} m`, '', !route.length), ...route,
    );
    const all = estimated > 0 && estimated === total.block_count;
    basis.replaceChildren(badge(estimated > 0, all ? 'MODEL ESTIMATE' : estimated ? 'PART ESTIMATE' : 'ANNOTATED'));
    element('measure-site-note').textContent = all ? `${ESTIMATE_NOTE}.`
      : estimated ? `${ESTIMATE_NOTE} for ${estimated} of ${total.block_count} fields; the other ${total.block_count - estimated} are measured from annotations.`
      : 'Measured from the annotated objects.';
  }
  function fieldCard(withLink: boolean): HTMLElement {
    const card = document.createElement('div');
    card.className = 'measure-card';
    const item = block();
    if (!item) {
      const empty = document.createElement('p');
      empty.className = 'measure-empty';
      empty.textContent = field ? `No rows, canopy or inter-rows are measured in field ${field}.` : 'Choose a field to see its measurements.';
      card.append(empty);
      return card;
    }
    const basis = document.createElement('div');
    basis.className = 'measure-basis';
    const note = document.createElement('small');
    note.textContent = item.estimate ? 'before correction in Marcaj' : 'from the annotated objects';
    basis.append(badge(item.estimate), note);
    const grid = document.createElement('dl');
    grid.className = 'measure-grid';
    const { gaps, waste } = sources.spots(item.vineyard_id);
    // the field plan's card also counts what to visit; the Measurements view keeps its room for the row table
    grid.append(stat('Rows', count.format(item.row_count)), stat('Total row length', `${metres.format(item.row_length_m)} m`), ...areaStats(item),
      ...(withLink ? [stat('Gaps', count.format(gaps), 'missing canopy'), stat('Waste spots', count.format(waste))] : []));
    card.append(basis, grid);
    if (withLink) {
      const link = document.createElement('button');
      link.type = 'button'; link.className = 'secondary-button measure-open';
      link.textContent = 'Row lengths and site totals';
      link.addEventListener('click', sources.open);
      card.append(link);
    }
    return card;
  }
  function rows(): MeasuredRow[] {
    const direction = sort.ascending ? 1 : -1;
    return (data?.rows ?? []).filter((item) => item.vineyard_id === field).sort((a, b) => direction * (sort.key === 'length_m'
      ? a.length_m - b.length_m
      : String(a[sort.key]).localeCompare(String(b[sort.key]), undefined, { numeric: true }) || a.row_id.localeCompare(b.row_id, undefined, { numeric: true })));
  }
  function renderTable(): void {
    const body = element('row-table-body');
    const list = rows();
    body.replaceChildren(...list.map((item) => {
      const tr = document.createElement('tr');
      tr.dataset.row = item.row_id;
      if (item.row_id === selected) tr.className = 'selected';
      const name = document.createElement('td');
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = item.row_id;
      button.setAttribute('aria-pressed', String(item.row_id === selected));
      button.setAttribute('aria-label', `Show row ${item.row_id} on the map`);
      button.addEventListener('click', () => select(item.row_id));
      name.append(button);
      const length = document.createElement('td'); length.textContent = `${metres.format(item.length_m)} m`;
      const structure = document.createElement('td'); structure.textContent = item.row_structure || '—';
      tr.append(name, length, structure);
      return tr;
    }));
    element('row-table').hidden = !list.length;
    element('row-table-empty').hidden = list.length > 0;
    element('row-table-empty').textContent = !data ? 'No measurements in the current scene.' : field ? `No rows are measured in field ${field}.` : 'Choose a field to see its rows.';
    element('row-table-count').textContent = list.length ? `${count.format(list.length)} row${list.length === 1 ? '' : 's'} · select one to show it on the map` : '';
    for (const header of document.querySelectorAll<HTMLTableCellElement>('#row-table th[data-sort]')) {
      header.setAttribute('aria-sort', header.dataset.sort === sort.key ? (sort.ascending ? 'ascending' : 'descending') : 'none');
    }
  }
  function renderField(): void {
    element('field-measure-card').replaceChildren(fieldCard(true));
    element('measure-field-card').replaceChildren(fieldCard(false));
    element('measure-title').textContent = field ? `Field ${field}` : 'Measurements';
    renderTable();
  }
  /** The field's row pieces as measured: the model's for an estimated field, the annotated ones otherwise. */
  function measuredPieces(): MapFeature[] {
    const estimate = block()?.estimate ?? false;
    return sources.features().filter((item) => item.properties.label === 'row' && item.properties.vineyard_id === field
      && (item.properties.source === 'prediction') === estimate);
  }
  /** Highlight and label a row; a table pick (`fit`) zooms to it, a map pick (`at`, the click) labels it where clicked. */
  function select(rowId: string, fit = true, at?: L.LatLng): void {
    selected = rowId;
    highlight.clearLayers();
    const pieces = measuredPieces().filter((item) => item.properties.row_id === rowId);
    for (const item of pieces) {
      L.geoJSON(item, { pane: 'inspection-routes', interactive: false, style: { color: '#fff', weight: 10, opacity: 0.95 } }).addTo(highlight);
      L.geoJSON(item, { pane: 'inspection-routes', interactive: false, style: { color: '#f59e0b', weight: 5, opacity: 1 } }).addTo(highlight);
    }
    const bounds = L.geoJSON(pieces).getBounds();
    if (bounds.isValid()) {
      // the row's ID and length on the map, as a label (text content: IDs come from the scene)
      const row = data?.rows.find((item) => item.row_id === rowId && item.vineyard_id === field);
      const label = document.createElement('span');
      label.textContent = row ? `${rowId} · ${metres.format(row.length_m)} m` : rowId;
      // a table pick is centred by the fit below; a map pick's label opens towards the middle of the map the panels leave
      const [[left], [right]] = sources.padding();
      const middle = (left + map.getSize().x - right) / 2;
      const direction = !at ? 'top' : map.latLngToContainerPoint(at).x < middle ? 'right' : 'left';
      L.tooltip({ permanent: true, direction }).setLatLng(at ?? bounds.getCenter()).setContent(label).addTo(highlight);
    }
    if (fit && bounds.isValid()) {
      const [paddingTopLeft, paddingBottomRight] = sources.padding();
      map.fitBounds(bounds.pad(0.25), { paddingTopLeft, paddingBottomRight, maxZoom: 20 });
    }
    // restyle in place: re-rendering the table would drop the keyboard focus on the clicked row
    for (const tr of element('row-table-body').querySelectorAll<HTMLTableRowElement>('tr')) {
      tr.classList.toggle('selected', tr.dataset.row === rowId);
      tr.querySelector('button')?.setAttribute('aria-pressed', String(tr.dataset.row === rowId));
      if (!fit && tr.dataset.row === rowId) tr.scrollIntoView({ block: 'nearest' });
    }
  }
  // a click on the map selects the nearest row of the field within a finger's width (rows are not interactive in this
  // view, main.ts, so every click lands here)
  map.on('click', (event: L.LeafletMouseEvent) => {
    if (!map.hasLayer(highlight) || !field) return;
    const at = map.latLngToLayerPoint(event.latlng);
    let nearest: { item: MapFeature; distance: number } | null = null;
    for (const item of measuredPieces()) {
      const lines = item.geometry.type === 'LineString' ? [item.geometry.coordinates] : item.geometry.type === 'MultiLineString' ? item.geometry.coordinates : [];
      for (const line of lines) {
        const points = line.map(([lon, lat]) => map.latLngToLayerPoint([lat, lon]));
        for (let k = 1; k < points.length; k += 1) {
          const distance = L.LineUtil.pointToSegmentDistance(at, points[k - 1], points[k]);
          if (!nearest || distance < nearest.distance) nearest = { item, distance };
        }
      }
    }
    const rowId = nearest && nearest.distance <= 14 ? nearest.item.properties.row_id : undefined;
    if (rowId) select(rowId, false, event.latlng);
  });
  // phones start with the site totals folded, so the map stays visible under the field sheet
  if (matchMedia('(max-width: 700px)').matches) element<HTMLDetailsElement>('measure-site-details').open = false;
  for (const header of document.querySelectorAll<HTMLTableCellElement>('#row-table th[data-sort]')) {
    header.querySelector('button')?.addEventListener('click', () => {
      const key = header.dataset.sort as SortKey;
      sort = { key, ascending: sort.key === key ? !sort.ascending : true };
      renderTable();
    });
  }

  return {
    setData(next: Measurements | null | undefined): void { data = next ?? null; renderSite(); renderField(); },
    /** Show one field's card and rows; clears the highlighted row. */
    showField(id: string | null): void {
      if (id !== field) { selected = null; highlight.clearLayers(); }
      field = id; renderField();
    },
    show(visible: boolean): void {
      if (visible && !map.hasLayer(highlight)) highlight.addTo(map);
      if (!visible && map.hasLayer(highlight)) map.removeLayer(highlight);
    },
    /** A field's measured totals, or the site's for `null`. */
    totals: (vineyardId: string | null): MeasuredTotals | undefined =>
      vineyardId === null ? data?.total : data?.blocks.find((item) => item.vineyard_id === vineyardId),
    /** The measured length of a row, for its map popup. */
    rowLength: (rowId: string, vineyardId?: string): number | undefined =>
      data?.rows.find((item) => item.row_id === rowId && (!vineyardId || item.vineyard_id === vineyardId))?.length_m,
  };
}
