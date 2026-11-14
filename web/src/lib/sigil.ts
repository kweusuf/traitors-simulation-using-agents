export interface SigilSpec {
  shape: number;
  palette: number;
  rotation: number;
  tick: number;
  initials: string;
  colors: [string, string];
}

const PALETTES: [string, string][] = [
  ['#C8A24A', '#3B6B57'],
  ['#6E9B86', '#7A1F2B'],
  ['#B4485A', '#C8A24A'],
  ['#8C9A92', '#3B6B57'],
  ['#D8C79A', '#5B3A46'],
  ['#7FA7A0', '#C8A24A'],
];

export function sigilFor(id: string, name?: string): SigilSpec {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const positive = hash >>> 0;
  const source = (name || id).replace(/[^a-zA-Z ]/g, ' ').trim();
  const parts = source.split(/\s+/).filter(Boolean);
  const initials = (parts.length > 1
    ? `${parts[0][0]}${parts[parts.length - 1][0]}`
    : (parts[0] ?? id).slice(0, 2)
  ).toUpperCase();

  return {
    shape: positive % 4,
    palette: (positive >>> 4) % PALETTES.length,
    rotation: (positive >>> 8) % 360,
    tick: (positive >>> 12) % 5,
    initials,
    colors: PALETTES[(positive >>> 4) % PALETTES.length],
  };
}
