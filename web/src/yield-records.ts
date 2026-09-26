export type YieldRecord = {
  id: string;
  fieldId: string;
  kind: 'reported' | 'estimate';
  date: string;
  quantityKg: number;
  crop: string;
  pricePerKg: number | null;
  notes: string;
};
const STORAGE_KEY = 'agrocontrol.yield-records.v1';
function isRecord(value: unknown): value is YieldRecord {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Partial<YieldRecord>;
  return typeof record.id === 'string' && typeof record.fieldId === 'string' &&
    (record.kind === 'reported' || record.kind === 'estimate') &&
    typeof record.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(record.date) &&
    typeof record.quantityKg === 'number' && Number.isFinite(record.quantityKg) && record.quantityKg > 0 &&
    typeof record.crop === 'string' && typeof record.notes === 'string' &&
    (record.pricePerKg === null || (typeof record.pricePerKg === 'number' && Number.isFinite(record.pricePerKg) && record.pricePerKg >= 0));
}
export function loadYieldRecords(): YieldRecord[] {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (!stored) return [];
  const data: unknown = JSON.parse(stored);
  if (!Array.isArray(data) || !data.every(isRecord)) throw new Error('Saved records could not be read. Existing browser data has been preserved.');
  return data;
}
export function saveYieldRecords(records: YieldRecord[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
}
export function yieldCsv(records: YieldRecord[]): string {
  const cell = (value: string | number | null): string => {
    const raw = value === null ? '' : String(value);
    const safe = /^[=+@\-\t\r]/.test(raw) ? `'${raw}` : raw;
    return `"${safe.replaceAll('"', '""')}"`;
  };
  return [
    ['Field', 'Type', 'Date', 'Quantity (kg)', 'Crop / variety', 'Price (MDL/kg)', 'Notes'],
    ...records.map((record) => [record.fieldId, record.kind, record.date, record.quantityKg, record.crop, record.pricePerKg, record.notes]),
  ].map((row) => row.map(cell).join(',')).join('\r\n');
}
