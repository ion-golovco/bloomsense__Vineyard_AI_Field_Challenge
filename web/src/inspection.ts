import type { MapFeature } from './scene-types';
import './inspection.css';

// Roles, point statuses and farmer scores (GET /api/points, POST /api/points/{id}/status). One field is one farmer.
// No authentication: the role is picked with the site-wide switch (?role=farmer, remembered in this browser), a
// single-site demo.
export type Role = 'inspector' | 'farmer';
export type PointStatus = 'open' | 'in_progress' | 'fixed' | 'false_positive';
export type PointRecord = { status: PointStatus; by: Role; at: string; note: string; approved: boolean };
export type FieldScore = {
  score: number; model_score: number; waste_open: number; waste: number; missing_open: number; missing: number;
  missing_open_m: number; missing_m: number; to_review: number;
};
type Summary = { records: Record<string, PointRecord>; fields: Record<string, FieldScore> };
type Sources = { changed: () => void };

const CLOSED: PointStatus[] = ['fixed', 'false_positive'];
const ROLE_KEY = 'agrocontrol-role';
const element = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing inspection element: ${id}`);
  return found as T;
};
const plural = (count: number, word: string): string => `${count} ${word}${count === 1 ? '' : 's'}`;
export const isWaste = (item: MapFeature): boolean => item.properties.label === 'waste';
export const isMissing = (item: MapFeature): boolean => item.properties.reason === 'gap' || item.properties.reason === 'planting';

/** The stop title: what the point is, in the farmer's words. */
export function pointTitle(item: MapFeature): string {
  if (isWaste(item)) return 'Waste to remove';
  if (item.properties.reason === 'gap') return `Missing canopy${item.properties.gap_m ? ` · ${Math.round(item.properties.gap_m)} m gap` : ''}`;
  if (item.properties.reason === 'planting') return `Missing planting at row end${item.properties.gap_m ? ` · ${Math.round(item.properties.gap_m)} m` : ''}`;
  return item.properties.reason?.startsWith('sentinel') ? 'Satellite signal to check' : 'Inspect this location';
}

function chipText(record: PointRecord | undefined): string {
  if (!record || (record.status === 'open' && !record.approved)) return 'Open';
  if (record.status === 'open') return record.by === 'inspector' ? 'Confirmed by inspector' : 'Open';
  if (record.status === 'in_progress') return 'Farmer working on it';
  const claim = record.status === 'fixed' ? 'Fixed' : 'False positive';
  return `${claim} · ${record.approved ? 'approved' : 'awaiting review'}`;
}

export function createInspection(sources: Sources) {
  let remembered: string | null = null;
  try { remembered = localStorage.getItem(ROLE_KEY); } catch { /* storage blocked: the URL still carries the role */ }
  let role: Role = (new URLSearchParams(location.search).get('role') ?? remembered) === 'farmer' ? 'farmer' : 'inspector';
  let summary: Summary = { records: {}, fields: {} };
  let error = '';
  let busy = false;
  let refocus: string | null = null;  // the re-rendered list drops the clicked button; focus that point's actions again

  async function load(): Promise<void> {
    try {
      const response = await fetch('/api/points');
      if (!response.ok) throw new Error(`The status service returned ${response.status}`);
      summary = await response.json() as Summary;
      error = '';
    } catch (failure) {
      error = `Statuses unavailable: ${failure instanceof Error ? failure.message : 'unknown error'}`;
    }
    sources.changed();
  }

  async function setStatus(item: MapFeature, status: PointStatus): Promise<void> {
    const id = item.properties.id;
    if (!id || busy) return;
    busy = true;
    refocus = id;
    try {
      const response = await fetch(`/api/points/${encodeURIComponent(id)}/status`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, status }),
      });
      if (!response.ok) throw new Error(`The status service returned ${response.status}`);
      const saved = await response.json() as { record: PointRecord; field: string; score: FieldScore | null };
      summary.records[id] = saved.record;
      if (saved.score) summary.fields[saved.field] = saved.score;
      error = '';
    } catch (failure) {
      error = `Could not save the status: ${failure instanceof Error ? failure.message : 'unknown error'}`;
    } finally {
      busy = false;
      sources.changed();
    }
  }

  function applyRole(): void {
    document.body.dataset.role = role;
    for (const button of document.querySelectorAll<HTMLButtonElement>('[data-role]')) button.setAttribute('aria-pressed', String(button.dataset.role === role));
    const url = new URL(location.href);
    url.searchParams.set('role', role);
    history.replaceState(null, '', url);
    try { localStorage.setItem(ROLE_KEY, role); } catch { /* a convenience only */ }
    element('missing-toggle').hidden = role !== 'farmer';
    // one field is one farmer: a farmer plans only their own field's walk
    if (role === 'farmer' && location.hash === '#all-fields') location.hash = 'per-field';
  }

  function actions(record: PointRecord | undefined): [string, PointStatus][] {
    const status = record?.status ?? 'open';
    if (role === 'farmer') {
      return ([['Working on it', 'in_progress'], ['Fixed', 'fixed'], ['Not a problem', 'false_positive'], ['Reopen', 'open']] as [string, PointStatus][])
        .filter(([, next]) => next !== status);
    }
    // an inspector's event is a review: the current status approves it, `open` rejects a claim
    if (record?.approved) return status === 'open' ? [['False positive', 'false_positive']] : [['Reopen', 'open']];
    if (CLOSED.includes(status)) return [['Approve', status], ['Reject', 'open']];
    return [['Confirm issue', status], ['False positive', 'false_positive']];
  }

  /** Status chip and the role's actions for one stop; `null` for a point without a stable id. */
  function statusControls(item: MapFeature, index: number): HTMLElement | null {
    const id = item.properties.id;
    if (!id) return null;
    const record = summary.records[id];
    const box = document.createElement('div');
    box.className = 'point-status';
    const chip = document.createElement('span');
    chip.className = `status-chip ${record?.status ?? 'open'}${record?.approved ? ' approved' : ''}`;
    chip.textContent = chipText(record);
    if (record) chip.title = `${record.by === 'farmer' ? 'Farmer' : 'Inspector'} · ${new Date(record.at).toLocaleString()}${record.note ? ` · ${record.note}` : ''}`;
    const group = document.createElement('div');
    group.className = 'status-actions';
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', `Status of visit point ${index + 1}`);
    for (const [label, next] of actions(record)) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.addEventListener('click', () => { void setStatus(item, next); });
      group.append(button);
    }
    box.append(chip, group);
    if (refocus === id && !busy) {
      refocus = null;
      requestAnimationFrame(() => (group.querySelector('button') ?? chip).focus());
    }
    return box;
  }

  function renderScore(field: string | null): void {
    const card = element('field-score');
    const score = field ? summary.fields[field] : undefined;
    element('score-error').textContent = error;
    card.hidden = !score && !error;
    if (!score) { element('score-now').textContent = '—'; element('score-model').textContent = ''; element('score-detail').textContent = ''; return; }
    card.dataset.band = score.score >= 80 ? 'good' : score.score >= 50 ? 'fair' : 'poor';
    element('score-now').textContent = String(score.score);
    const untouched = score.waste_open === score.waste && score.missing_open === score.missing;
    element('score-model').textContent = untouched ? 'as detected' : `detected ${score.model_score}`;
    element('score-detail').textContent = [
      `${score.waste_open} of ${plural(score.waste, 'waste item')} open`,
      `${score.missing_open} of ${plural(score.missing, 'missing-canopy point')} open (${Math.round(score.missing_open_m)} m)`,
      score.to_review ? `${plural(score.to_review, 'farmer claim')} to review` : '',
    ].filter(Boolean).join(' · ');
  }

  for (const button of document.querySelectorAll<HTMLButtonElement>('[data-role]')) {
    button.addEventListener('click', () => {
      role = button.dataset.role === 'farmer' ? 'farmer' : 'inspector';
      applyRole();
      void load();
    });
  }
  element('farmer-missing').addEventListener('change', () => sources.changed());
  // the other role may have changed statuses meanwhile: refresh when the page comes back into view
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void load(); });
  applyRole();

  return {
    role: (): Role => role,
    load,
    statusControls,
    renderScore,
    score: (field: string): FieldScore | undefined => summary.fields[field],
    /** A point's latest status record; none means open. */
    record: (id: string | undefined): PointRecord | undefined => id ? summary.records[id] : undefined,
    /** The inspector sees every point; a farmer sees waste, and missing canopy when they ask for it. */
    visible: (item: MapFeature): boolean => role === 'inspector' || isWaste(item) || (element<HTMLInputElement>('farmer-missing').checked && isMissing(item)),
    /** Extra POST /api/route fields: a farmer's walk covers only the open points they see. */
    routeFilter: (): { kinds?: string[]; open_only?: boolean } => role === 'inspector' ? {}
      : { kinds: element<HTMLInputElement>('farmer-missing').checked ? ['waste', 'canopy'] : ['waste'], open_only: true },
  };
}
