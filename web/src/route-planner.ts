import L from 'leaflet';
import type { MapFeature, PlannedRoute } from './scene-types';

type Point = 'start' | 'end';
type PlannerSources = {
  /** `site` on All fields, the selected field's id per field, `null` without a field. */
  scope: () => string | null;
  /** Extra request fields for the role (a farmer's walk: target kinds, open points only). */
  filter: () => { kinds?: string[]; open_only?: boolean };
  organizerStart: () => L.LatLng | null;
  /** Fit the map to a planned whole-farm route, clear of the panels. */
  fit: (bounds: L.LatLngBounds) => void;
  changed: () => void;
};
const element = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing route planner element: ${id}`);
  return found as T;
};
const distance = (metres: number): string => metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(2)} km`;

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

// Start / end picking on the map and on-request routes (POST /api/route, every point: no confidence cutoff, row
// hops on); main.ts draws the planned route and moves this block between the per-field and All fields panels.
export function createRoutePlanner(map: L.Map, sources: PlannerSources) {
  const layer = L.layerGroup();
  const points: Record<Point, L.LatLng | null> = { start: null, end: null };
  const markers: Record<Point, L.Marker | null> = { start: null, end: null };
  let picking: Point | null = null;
  const planned = new Map<string, { route: PlannedRoute; body: object }>();  // one per scope and role filter
  let statusKey = '';  // the scope the status line describes
  let busy = false;
  const status = element('planner-status');
  const say = (message: string, error = false): void => {
    status.textContent = message;
    status.classList.toggle('error', error);
    status.setAttribute('role', error ? 'alert' : 'status');
  };
  const key = (): string => `${sources.scope()}|${JSON.stringify(sources.filter())}`;
  const icon = (name: Point): L.DivIcon => L.divIcon({
    className: `planner-marker ${name}`, html: name === 'start' ? 'S' : 'E', iconSize: [28, 28], iconAnchor: [14, 14],
  });
  const summary = (route: PlannedRoute): string => {
    const left = route.targets - route.visited;
    const snapped = [route.start.snapped_m > 1 ? `start moved ${Math.round(route.start.snapped_m)} m onto the nearest inter-row` : '',
      points.end && route.end.snapped_m > 1 ? `end moved ${Math.round(route.end.snapped_m)} m` : ''].filter(Boolean).join(', ');
    return `${distance(route.length_m)} · ${route.visited} of ${route.targets} points${left ? ` (${left} left off, grey on the map)` : ''}`
      + ` · ${(route.outside_share * 100).toFixed(1)}% outside the lanes (limit 2%)` + (snapped ? `. The ${snapped}.` : '.')
      + (route.legal ? '' : ' Warning: this route breaks the inspection rules; move the start or end.');
  };

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
    const target = sources.scope();
    element('planner-title').textContent = target === 'site' ? 'Route through every point on the farm' : 'Route through this field\'s points';
    element('start-label').textContent = picking === 'start' ? 'Tap the map' : points.start ? 'Set on the map' : 'Official START';
    element('end-label').textContent = picking === 'end' ? 'Tap the map' : points.end ? 'Set on the map' : 'Back to start';
    for (const name of ['start', 'end'] as const) element(`pick-${name}`).setAttribute('aria-pressed', String(picking === name));
    element('reset-points').hidden = !points.start && !points.end;
    const planButton = element<HTMLButtonElement>('plan-route');
    planButton.disabled = !target;  // not while busy: disabling the focused button would drop keyboard focus
    planButton.setAttribute('aria-busy', String(busy));
    element('plan-text').textContent = busy ? 'Planning…' : 'Plan route';
    element('plan-label').textContent = busy ? 'Up to 2 min' : planned.has(key()) ? 'Planned' : 'Not planned';
    element('download-route').hidden = !planned.has(key());
    map.getContainer().classList.toggle('picking', picking !== null);
  }
  function moved(): void {
    const had = planned.size > 0;
    planned.clear();
    labels();
    if (had) { say('Start or end changed. Plan the route again.'); sources.changed(); }
  }
  function set(name: Point, at: L.LatLng): void {
    points[name] = at;
    picking = null;
    place(name, at);
    say(name === 'start' ? `Start set. ${points.end ? 'Plan the route.' : 'Set an end, or plan the route back to the start.'}` : 'End set. Plan the route.');
    moved();
  }
  function pick(name: Point): void {
    picking = picking === name ? null : name;
    labels();
    if (!picking) { say(''); return; }
    say(`Tap the map to set the ${name}. With a keyboard: move the map with the arrow keys, Enter sets it at the centre, Esc cancels.`);
    map.getContainer().focus();
  }
  async function plan(): Promise<void> {
    const target = sources.scope();
    if (!target || busy) return;
    const requested = key();
    const body = {
      start: points.start ? [points.start.lng, points.start.lat] : null,
      end: points.end ? [points.end.lng, points.end.lat] : null,
      scope: target, ...sources.filter(),
    };
    busy = true; picking = null; labels();
    statusKey = requested;
    say(target === 'site' ? 'Planning the route through every point on the farm…' : 'Planning the route…');
    try {
      const route = await (await requestRoute('/api/route', body)).json() as PlannedRoute;
      planned.set(requested, { route, body });
      if (key() === requested) say(summary(route), !route.legal);
      sources.changed();
      if (target === 'site' && key() === requested) {
        const bounds = L.geoJSON(route.route).getBounds();
        if (bounds.isValid()) sources.fit(bounds);
      }
    } catch (error) {
      say(`Could not plan the route: ${error instanceof Error ? error.message : 'unknown error'}`, true);
    } finally {
      busy = false; labels();
      status.scrollIntoView({ block: 'nearest' });  // the panels scroll on phones
    }
  }
  async function download(): Promise<void> {
    const current = planned.get(key());
    if (!current) return;
    try {
      const blob = await (await requestRoute('/api/route/geojson', current.body)).blob();
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
    say('Start and end reset: the route starts and ends at the official START.');
    moved();
    element('pick-start').focus();  // the reset button hides itself
  });
  element('plan-route').addEventListener('click', () => { void plan(); });
  element('download-route').addEventListener('click', () => { void download(); });
  labels();

  return {
    /** The planned route for the current scope and role, if any. */
    current(): { feature: MapFeature; route: PlannedRoute } | null {
      const found = planned.get(key());
      return found ? { feature: found.route.route, route: found.route } : null;
    },
    /** Redraw the start marker once the scene (and the organizer START) is known; follow a scope change in the labels and status. */
    refresh(): void {
      if (!markers.start) place('start', points.start);
      if (!busy && key() !== statusKey) {
        statusKey = key();
        const found = planned.get(statusKey);
        say(found ? summary(found.route) : points.start || points.end ? 'Start or end changed: plan the route to walk from them.' : '',
          found ? !found.route.legal : false);
      }
      labels();
    },
    show(visible: boolean): void {
      if (visible && !map.hasLayer(layer)) layer.addTo(map);
      if (!visible && map.hasLayer(layer)) { map.removeLayer(layer); picking = null; labels(); }
    },
  };
}
