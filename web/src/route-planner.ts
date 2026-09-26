import L from 'leaflet';
import type { MapFeature, PlannedRoute } from './scene-types';

type Point = 'start' | 'end';
type PlannerSources = {
  fieldId: () => string | null;
  minConfidence: () => number | null;
  /** Extra request fields for the role (a farmer's walk: target kinds, open points only). */
  filter: () => { kinds?: string[]; open_only?: boolean };
  organizerStart: () => L.LatLng | null;
  changed: () => void;
};
const element = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing route planner element: ${id}`);
  return found as T;
};
const distance = (metres: number): string => metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(2)} km`;
const coordinates = (point: L.LatLng): string => `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;

async function requestRoute(path: string, body: object): Promise<Response> {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (response.ok) return response;
  let detail = `The route service returned ${response.status}`;
  try {
    const payload = await response.json() as { detail?: unknown };
    if (typeof payload.detail === 'string') detail = payload.detail;
    else if (Array.isArray(payload.detail)) detail = payload.detail.map((item: { msg?: string }) => item.msg ?? '').filter(Boolean).join('; ') || detail;
  } catch { /* the status line is the message */ }
  throw new Error(detail);
}

// Start / end picking on the map and on-request routes (POST /api/route); main.ts draws the planned route.
export function createRoutePlanner(map: L.Map, sources: PlannerSources) {
  const layer = L.layerGroup();
  const points: Record<Point, L.LatLng | null> = { start: null, end: null };
  const markers: Record<Point, L.Marker | null> = { start: null, end: null };
  let picking: Point | null = null;
  let planned: { key: string; route: PlannedRoute; body: object } | null = null;
  let busy = false;
  const status = element('planner-status');
  const say = (message: string, error = false): void => {
    status.textContent = message;
    status.classList.toggle('error', error);
    status.setAttribute('role', error ? 'alert' : 'status');
  };
  const scope = (): string | null => element<HTMLSelectElement>('route-scope').value === 'site' ? 'site' : sources.fieldId();
  const key = (): string => `${scope()}|${sources.minConfidence()}|${JSON.stringify(sources.filter())}`;
  const icon = (name: Point): L.DivIcon => L.divIcon({
    className: `planner-marker ${name}`, html: name === 'start' ? 'S' : 'E', iconSize: [28, 28], iconAnchor: [14, 14],
  });

  function place(name: Point, at: L.LatLng | null): void {
    const shown = name === 'start' ? at ?? sources.organizerStart() : at;
    markers[name]?.remove();
    markers[name] = null;
    if (shown) {
      const marker = L.marker(shown, { icon: icon(name), draggable: true, keyboard: false, pane: 'visit-points', title: name === 'start' ? 'Walk start' : 'Walk end' });
      marker.on('dragend', () => { points[name] = marker.getLatLng(); moved(); });
      markers[name] = marker.addTo(layer);
    }
  }
  function labels(): void {
    element('start-label').textContent = points.start ? coordinates(points.start) : 'Organizer START';
    element('end-label').textContent = points.end ? coordinates(points.end) : 'Back to the start';
    for (const name of ['start', 'end'] as const) {
      const button = element<HTMLButtonElement>(`pick-${name}`);
      button.setAttribute('aria-pressed', String(picking === name));
      button.textContent = picking === name ? 'Cancel' : 'Set on map';
    }
    element<HTMLButtonElement>('reset-points').disabled = !points.start && !points.end;
    const planButton = element<HTMLButtonElement>('plan-route');
    planButton.disabled = !scope();  // not while busy: disabling the focused button would drop keyboard focus
    planButton.setAttribute('aria-busy', String(busy));
    planButton.textContent = busy ? 'Planning…' : 'Plan route';
    element<HTMLButtonElement>('download-route').hidden = !planned || planned.key !== key();
    map.getContainer().classList.toggle('picking', picking !== null);
  }
  function moved(): void {
    const had = planned !== null;
    planned = null;
    labels();
    if (had) { say('Start or end changed. Plan the route again.'); sources.changed(); }
  }
  function set(name: Point, at: L.LatLng): void {
    points[name] = at;
    picking = null;
    place(name, at);
    say(`${name === 'start' ? 'Start' : 'End'} set at ${coordinates(at)}.`);
    moved();
  }
  function pick(name: Point): void {
    picking = picking === name ? null : name;
    labels();
    if (!picking) { say(''); return; }
    say(`Click or tap the map to place the ${name}. With a keyboard: move the map with the arrow keys, Enter places it at the centre, Esc cancels.`);
    map.getContainer().focus();
  }
  async function plan(): Promise<void> {
    const target = scope();
    if (!target || busy) return;
    const requested = key();
    const body = {
      start: points.start ? [points.start.lng, points.start.lat] : null,
      end: points.end ? [points.end.lng, points.end.lat] : null,
      scope: target, min_confidence: sources.minConfidence(), hops: element<HTMLInputElement>('route-hops').checked, ...sources.filter(),
    };
    busy = true; labels();
    say(target === 'site' ? 'Planning the route over the whole farm…' : 'Planning the route…');
    try {
      const route = await (await requestRoute('/api/route', body)).json() as PlannedRoute;
      planned = { key: requested, route, body };
      const snapped = [route.start.snapped_m > 1 ? `start moved ${Math.round(route.start.snapped_m)} m onto the nearest inter-row` : '',
        points.end && route.end.snapped_m > 1 ? `end moved ${Math.round(route.end.snapped_m)} m` : ''].filter(Boolean).join(', ');
      say([
        `${distance(route.length_m)} · ${route.visited} of ${route.targets} visit points${route.targets > route.visited ? ` (${route.targets - route.visited} left off, grey on the map)` : ''}`,
        route.hops ? `${route.hops} row step${route.hops === 1 ? '' : 's'}` : 'no row steps',
        `${(route.outside_share * 100).toFixed(1)}% off inter-rows and passages`,
        `${route.compute_s.toFixed(1)} s`,
      ].join(' · ') + (snapped ? `. The ${snapped}.` : '.') + (route.legal ? '' : ' Warning: this route breaks the inspection rules; move the start or end.'), !route.legal);
      sources.changed();
      if (target === 'site') {
        const bounds = L.geoJSON(route.route).getBounds();
        if (bounds.isValid()) map.fitBounds(bounds.pad(0.05));
      }
    } catch (error) {
      say(`Could not plan the route: ${error instanceof Error ? error.message : 'unknown error'}`, true);
    } finally {
      busy = false; labels();
      status.scrollIntoView({ block: 'nearest' });  // the plan panel scrolls on phones
    }
  }
  async function download(): Promise<void> {
    if (!planned) return;
    try {
      const blob = await (await requestRoute('/api/route/geojson', planned.body)).blob();
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob); link.download = 'route.geojson';
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    } catch (error) {
      say(`Could not download the route: ${error instanceof Error ? error.message : 'unknown error'}`, true);
    }
  }

  map.on('click', (event: L.LeafletMouseEvent) => {
    if (!picking) return;
    map.closePopup();
    set(picking, event.latlng);
  });
  map.getContainer().addEventListener('keydown', (event) => {
    if (!picking) return;
    if (event.key === 'Enter') { event.preventDefault(); const name = picking; set(name, map.getCenter()); element(`pick-${name}`).focus(); }
    if (event.key === 'Escape') { const name = picking; picking = null; labels(); say(''); element(`pick-${name}`).focus(); }
  });
  element('pick-start').addEventListener('click', () => pick('start'));
  element('pick-end').addEventListener('click', () => pick('end'));
  element('reset-points').addEventListener('click', () => {
    points.start = points.end = null; picking = null;
    place('start', null); place('end', null);
    say('Start and end reset to the organizer START.');
    moved();
  });
  element('plan-route').addEventListener('click', () => { void plan(); });
  element('download-route').addEventListener('click', () => { void download(); });
  element('route-scope').addEventListener('change', () => { labels(); sources.changed(); });
  element('route-hops').addEventListener('change', moved);
  labels();

  return {
    /** The planned route for the current scope and confidence, if any. */
    current(): { feature: MapFeature; route: PlannedRoute } | null {
      return planned && planned.key === key() ? { feature: planned.route.route, route: planned.route } : null;
    },
    /** Redraw the start marker once the scene (and the organizer START) is known; refresh labels. */
    refresh(): void { if (!markers.start) place('start', points.start); labels(); },
    show(visible: boolean): void {
      if (visible && !map.hasLayer(layer)) layer.addTo(map);
      if (!visible && map.hasLayer(layer)) { map.removeLayer(layer); picking = null; labels(); }
    },
  };
}
