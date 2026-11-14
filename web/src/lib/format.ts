const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const full = new Intl.NumberFormat('en');

export function formatNumber(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return Math.abs(value) >= 10000 ? compact.format(value) : full.format(value);
}

export function formatExact(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return full.format(value);
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatScore(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toFixed(3);
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function shortModel(model: string): string {
  if (!model) return 'unknown';
  const base = model.split('/').pop() ?? model;
  return base.length > 28 ? `${base.slice(0, 26)}…` : base;
}

export function roleLabel(role: string): string {
  return role === 'traitor' ? 'Traitor' : 'Faithful';
}

export function channelLabel(channel: string): string {
  if (channel === 'traitor') return 'Traitor channel';
  if (channel === 'private') return 'Private chat';
  if (channel === 'host') return 'Host';
  return 'Public';
}

export function methodLabel(method: string): string {
  if (method === 'vote') return 'Banished';
  if (method === 'night') return 'Murdered';
  if (method === 'endgame') return 'Endgame';
  return titleCase(method);
}

export function relativeTime(round: number): string {
  return round === 0 ? 'Setup' : `Round ${round}`;
}
