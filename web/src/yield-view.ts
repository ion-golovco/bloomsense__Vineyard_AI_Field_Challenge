import { loadYieldRecords, saveYieldRecords, yieldCsv, type YieldRecord } from './yield-records';

const element = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing yield element: ${id}`);
  return found as T;
};
const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const form = element<HTMLFormElement>('yield-form');
let records: YieldRecord[] = [];
let editingId: string | null = null;
let removed: YieldRecord | null = null;
let storageReadable = true;
function message(text: string, error = false): void {
  const status = element('yield-message');
  status.textContent = text;
  status.classList.toggle('error', error);
}
function today(): string {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
function resetForm(): void {
  form.reset();
  element<HTMLInputElement>('yield-date').value = today();
  editingId = null;
  element('yield-form-title').textContent = 'Add a record';
  element('yield-save').textContent = 'Save record';
  element('yield-cancel').hidden = true;
}
function persist(next: YieldRecord[]): boolean {
  if (!storageReadable) { message('Saved browser data could not be read. Adding entries is paused to preserve that data.', true); return false; }
  try { saveYieldRecords(next); records = next; return true; }
  catch { message('Could not save in this browser. Your form is still here; check available browser storage.', true); return false; }
}
function edit(record: YieldRecord): void {
  const select = element<HTMLSelectElement>('yield-field');
  if (![...select.options].some((option) => option.value === record.fieldId)) {
    select.add(new Option(`Field ${record.fieldId} · saved record`, record.fieldId));
  }
  select.disabled = false;
  editingId = record.id;
  select.value = record.fieldId;
  element<HTMLSelectElement>('yield-kind').value = record.kind;
  element<HTMLInputElement>('yield-date').value = record.date;
  element<HTMLInputElement>('yield-quantity').value = String(record.quantityKg);
  element<HTMLInputElement>('yield-crop').value = record.crop;
  element<HTMLInputElement>('yield-price').value = record.pricePerKg === null ? '' : String(record.pricePerKg);
  element<HTMLTextAreaElement>('yield-notes').value = record.notes;
  element('yield-form-title').textContent = 'Edit record';
  element('yield-save').textContent = 'Save changes';
  element<HTMLButtonElement>('yield-save').disabled = !storageReadable;
  element('yield-cancel').hidden = false;
  message('Editing an existing entry.');
  element<HTMLInputElement>('yield-quantity').focus();
}
function renderRecords(): void {
  const total = (kind: YieldRecord['kind']): number => records.filter((record) => record.kind === kind).reduce((sum, record) => sum + record.quantityKg, 0);
  element('harvest-total').textContent = `${number.format(total('reported'))} kg`;
  element('estimate-total').textContent = `${number.format(total('estimate'))} kg`;
  element('yield-record-count').textContent = String(records.length);
  element<HTMLButtonElement>('yield-export').disabled = records.length === 0;
  const list = element('yield-records');
  list.replaceChildren();
  if (!records.length) {
    const empty = document.createElement('p');
    empty.className = 'records-empty';
    empty.textContent = storageReadable ? 'No records yet. Add your first picking or estimate.' : 'Saved records are unavailable. Existing browser data has been preserved.';
    list.append(empty);
  }
  for (const record of [...records].sort((a, b) => b.date.localeCompare(a.date))) {
    const article = document.createElement('article');
    article.className = 'yield-record';
    const top = document.createElement('div');
    top.className = 'record-top';
    const heading = document.createElement('strong');
    heading.textContent = `Field ${record.fieldId} · ${number.format(record.quantityKg)} kg`;
    const badge = document.createElement('span');
    badge.className = `record-badge ${record.kind}`;
    badge.textContent = record.kind === 'reported' ? 'Reported harvest' : 'Grower estimate';
    top.append(heading, badge);
    const details = document.createElement('p');
    details.textContent = [record.date, record.crop, record.pricePerKg === null ? 'Price unknown' : `${number.format(record.pricePerKg)} MDL/kg`].filter(Boolean).join(' · ');
    article.append(top, details);
    if (record.notes) {
      const notes = document.createElement('p');
      notes.className = 'record-notes'; notes.textContent = record.notes;
      article.append(notes);
    }
    const actions = document.createElement('div');
    actions.className = 'record-actions';
    const editButton = document.createElement('button');
    editButton.type = 'button'; editButton.textContent = 'Edit';
    editButton.setAttribute('aria-label', `Edit ${record.kind} for field ${record.fieldId} on ${record.date}`);
    editButton.addEventListener('click', () => edit(record));
    const removeButton = document.createElement('button');
    removeButton.type = 'button'; removeButton.textContent = 'Remove';
    removeButton.setAttribute('aria-label', `Remove ${record.kind} for field ${record.fieldId} on ${record.date}`);
    removeButton.addEventListener('click', () => {
      if (!persist(records.filter((item) => item.id !== record.id))) return;
      removed = record; element('yield-undo').hidden = false;
      if (editingId === record.id) resetForm();
      message('Record removed. You can undo this removal.'); renderRecords();
    });
    actions.append(editButton, removeButton); article.append(actions); list.append(article);
  }
}
export function updateYieldFields(ids: string[], selectedId: string | null): void {
  const select = element<HTMLSelectElement>('yield-field');
  const previous = select.value;
  const available = [...new Set([...ids, ...records.map((record) => record.fieldId)])];
  select.replaceChildren();
  for (const id of available) select.add(new Option(`Field ${id}${ids.includes(id) ? '' : ' · saved record'}`, id));
  if (available.includes(previous)) select.value = previous;
  else if (selectedId && available.includes(selectedId)) select.value = selectedId;
  if (!available.length) select.add(new Option('Fields unavailable', ''));
  select.disabled = available.length === 0;
  element<HTMLButtonElement>('yield-save').disabled = !storageReadable || available.length === 0;
}
export function initializeYieldView(): void {
  try { records = loadYieldRecords(); }
  catch (error) { storageReadable = false; message(error instanceof Error ? error.message : 'Browser storage is unavailable.', true); }
  resetForm(); renderRecords(); updateYieldFields([], null);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const kind = element<HTMLSelectElement>('yield-kind').value;
    const quantity = Number(element<HTMLInputElement>('yield-quantity').value);
    const priceRaw = element<HTMLInputElement>('yield-price').value.trim();
    const price = priceRaw === '' ? null : Number(priceRaw);
    const fieldId = element<HTMLSelectElement>('yield-field').value;
    if (!fieldId || (kind !== 'reported' && kind !== 'estimate') || !Number.isFinite(quantity) || quantity <= 0 || (price !== null && (!Number.isFinite(price) || price < 0))) {
      message('Choose a field and enter a positive quantity and a valid price, or leave price blank.', true); return;
    }
    const record: YieldRecord = {
      id: editingId ?? crypto.randomUUID(), fieldId, kind,
      date: element<HTMLInputElement>('yield-date').value, quantityKg: quantity,
      crop: element<HTMLInputElement>('yield-crop').value.trim(), pricePerKg: price,
      notes: element<HTMLTextAreaElement>('yield-notes').value.trim(),
    };
    const next = editingId ? records.map((item) => item.id === editingId ? record : item) : [...records, record];
    if (!persist(next)) return;
    const updated = editingId !== null;
    resetForm(); element<HTMLSelectElement>('yield-field').value = fieldId;
    message(updated ? 'Changes saved on this browser.' : 'Record saved on this browser.'); renderRecords();
  });
  element('yield-cancel').addEventListener('click', () => { resetForm(); message('Edit cancelled.'); });
  element('yield-undo').addEventListener('click', () => {
    if (!removed || !persist([...records, removed])) return;
    removed = null; element('yield-undo').hidden = true; message('Record restored.'); renderRecords();
  });
  element('yield-export').addEventListener('click', () => {
    const url = URL.createObjectURL(new Blob(['\uFEFF', yieldCsv(records)], { type: 'text/csv;charset=utf-8;' }));
    const link = document.createElement('a');
    link.href = url; link.download = `agrocontrol-yield-${today()}.csv`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message('CSV exported. Browser records are unchanged.');
  });
}
