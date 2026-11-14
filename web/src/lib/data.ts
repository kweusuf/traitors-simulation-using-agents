import manifestJson from '../data/manifest.json';
import type { Manifest, RunRow } from './types';

export const manifest = manifestJson as Manifest;
export const runs = manifest.runs;
export const flagshipRows = runs.filter((run) => run.flagship);

export function runHref(run: Pick<RunRow, 'id' | 'slug'>): string {
  return `/runs/${run.slug ?? run.id}`;
}

export function findRun(idOrSlug: string): RunRow | undefined {
  return runs.find((run) => run.id === idOrSlug || run.slug === idOrSlug);
}

export const families = [...new Set(runs.map((run) => run.family))].sort();
export const providers = [...new Set(runs.map((run) => run.provider))].sort();
