(() => {
  const map = L.map('map', { zoomControl: false, scrollWheelZoom: true, preferCanvas: true, maxZoom: 19 });
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    minZoom: 3,
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
  }).addTo(map);
  L.control.zoom({ position: 'topleft' }).addTo(map);
  map.setView([47.12, 28.71], 14);

  const fieldGroup = L.layerGroup().addTo(map);
  const routeGroup = L.layerGroup().addTo(map);
  const fieldSelect = document.getElementById('field-select');
  const fieldMeta = document.getElementById('field-meta');
  const fieldsButton = document.getElementById('toggle-fields');
  const routeButtons = [document.getElementById('toggle-route'), document.getElementById('route-toggle')];
  const stopButtons = [...document.querySelectorAll('[data-stop]')];
  let fields = [];
  let selectedBoundary = null;
  let stopMarkers = [];
  let routeShown = true;
  let usingSnapshot = false;

  function fitSelected() {
    if (!selectedBoundary) return;
    const width = window.innerWidth;
    const studio = document.body.dataset.view === 'studio' && width > 1020;
    const paddingTopLeft = width <= 700 ? [12, 345] : width <= 1020 ? [390, 320] : studio ? [575, 220] : [420, 155];
    const paddingBottomRight = width <= 700 ? [12, 295] : width <= 1020 ? [20, 140] : studio ? [350, 240] : [340, 135];
    map.fitBounds(selectedBoundary.getBounds().pad(.18), { paddingTopLeft, paddingBottomRight, maxZoom: 18 });
  }

  function setView(view) {
    document.body.dataset.view = view;
    for (const button of document.querySelectorAll('[data-view-button]')) {
      button.setAttribute('aria-pressed', String(button.dataset.viewButton === view));
    }
    const url = new URL(window.location.href);
    url.searchParams.set('view', view);
    history.replaceState(null, '', url);
    requestAnimationFrame(() => { map.invalidateSize(); fitSelected(); });
  }

  function samplePositions(bounds) {
    const center = bounds.getCenter();
    const lat = bounds.getNorth() - bounds.getSouth();
    const lng = bounds.getEast() - bounds.getWest();
    return [
      L.latLng(center.lat - lat * 0.12, center.lng - lng * 0.12),
      L.latLng(center.lat + lat * 0.12, center.lng - lng * 0.03),
      L.latLng(center.lat + lat * 0.04, center.lng + lng * 0.13),
    ];
  }

  function renderField() {
    const feature = fields.find((item) => item.properties.vineyard_id === fieldSelect.value);
    if (!feature) return;
    fieldGroup.clearLayers();
    routeGroup.clearLayers();
    stopMarkers = [];
    const border = L.geoJSON(feature, {
      style: { color: '#fff', weight: 10, fillOpacity: 0, opacity: 0.96, interactive: false },
    }).addTo(fieldGroup);
    selectedBoundary = L.geoJSON(feature, {
      style: { color: '#7e22ce', weight: 5, fillColor: '#a132ea', fillOpacity: 0.18 },
    }).addTo(fieldGroup);
    const tooltip = document.createElement('strong');
    tooltip.textContent = `Field ${feature.properties.vineyard_id} · model candidate`;
    selectedBoundary.bindTooltip(tooltip, { sticky: true });
    const bounds = border.getBounds();
    const positions = samplePositions(bounds);
    const path = [positions[0], positions[1], positions[2], positions[0]];
    L.polyline(path, { color: '#fff', weight: 10, opacity: .94, interactive: false }).addTo(routeGroup);
    L.polyline(path, { color: '#ed7625', weight: 6, dashArray: '12 8', opacity: 1, interactive: false }).addTo(routeGroup);
    const reasons = ['Check canopy gap', 'Review weak growth', 'Inspect visible object'];
    positions.forEach((position, index) => {
      const icon = L.divIcon({ className: '', html: `<span class="sample-marker">${index + 1}</span>`, iconSize: [30, 30], iconAnchor: [15, 15] });
      const popup = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = reasons[index];
      const note = document.createElement('p');
      note.textContent = 'Illustrative stop for layout review; no actual visit point is available.';
      note.style.margin = '5px 0 0';
      popup.append(title, note);
      stopMarkers.push(L.marker(position, { icon }).bindPopup(popup).addTo(routeGroup));
    });
    if (!routeShown) map.removeLayer(routeGroup);
    const area = Number(feature.properties.area_m2);
    fieldMeta.textContent = `${Number.isFinite(area) ? (area / 10000).toFixed(2) + ' ha · ' : ''}Candidate boundary from ${usingSnapshot ? 'a saved model snapshot' : 'the current model'}`;
    fitSelected();
  }

  async function loadFields() {
    let features;
    try {
      const response = await fetch('/api/scene');
      if (!response.ok) throw new Error('Scene unavailable');
      const scene = await response.json();
      features = scene.features.features;
    } catch {
      usingSnapshot = true;
      const fallback = await fetch('/mockups/fields.json');
      if (!fallback.ok) {
        fieldSelect.innerHTML = '<option>Fields unavailable</option>';
        fieldMeta.textContent = 'Start the field service to load candidate boundaries.';
        return;
      }
      features = (await fallback.json()).features;
    }
    fields = features.filter((feature) => feature.properties?.label === 'block' && feature.properties?.vineyard_id)
      .sort((a, b) => a.properties.vineyard_id.localeCompare(b.properties.vineyard_id, undefined, { numeric: true }));
    fieldSelect.replaceChildren();
    for (const field of fields) {
      const option = document.createElement('option');
      option.value = field.properties.vineyard_id;
      option.textContent = `Field ${option.value}`;
      fieldSelect.append(option);
    }
    if (fields.length) renderField();
    else fieldMeta.textContent = 'No candidate field boundaries in this scene.';
  }

  document.getElementById('fit-field').addEventListener('click', () => {
    fitSelected();
  });
  fieldSelect.addEventListener('change', renderField);
  fieldsButton.addEventListener('click', () => {
    const visible = fieldsButton.getAttribute('aria-pressed') !== 'true';
    fieldsButton.setAttribute('aria-pressed', String(visible));
    if (visible) fieldGroup.addTo(map);
    else map.removeLayer(fieldGroup);
  });
  function toggleRoute() {
    routeShown = !routeShown;
    for (const button of routeButtons) button.setAttribute('aria-pressed', String(routeShown));
    document.getElementById('route-toggle').firstChild.textContent = routeShown ? 'Shown ' : 'Hidden ';
    if (routeShown) routeGroup.addTo(map);
    else map.removeLayer(routeGroup);
  }
  routeButtons.forEach((button) => button.addEventListener('click', toggleRoute));
  stopButtons.forEach((button, index) => button.addEventListener('click', () => {
    if (!routeShown) toggleRoute();
    stopButtons.forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    const marker = stopMarkers[index];
    if (marker) {
      map.setView(marker.getLatLng(), Math.max(map.getZoom(), 17));
      marker.openPopup();
    }
  }));
  for (const button of document.querySelectorAll('[data-panel]')) {
    button.addEventListener('click', () => {
      const evidence = button.dataset.panel === 'evidence';
      document.getElementById('stops-panel').hidden = evidence;
      document.getElementById('evidence-panel').hidden = !evidence;
      document.querySelectorAll('[data-panel]').forEach((item) => item.classList.toggle('selected', item === button));
    });
  }
  for (const button of document.querySelectorAll('[data-view-button]')) {
    button.addEventListener('click', () => setView(button.dataset.viewButton));
  }
  setView(new URLSearchParams(window.location.search).get('view') === 'studio' ? 'studio' : 'focus');
  void loadFields();
})();
