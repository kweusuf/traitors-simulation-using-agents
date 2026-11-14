import type { RunRow } from './types';

export interface GateResult {
  metric: string;
  baseline: number | null;
  candidate: number | null;
  delta: number | null;
  limit: number | null;
  rule: string;
  passed: boolean;
  skipped: boolean;
  note?: string;
}

const ABSOLUTE: { label: string; key: keyof RunRow; tolerance: number }[] = [
  { label: 'hallucination_score', key: 'hallucination', tolerance: 0.02 },
  { label: 'duplication_score', key: 'duplication', tolerance: 0.02 },
  { label: 'speech_similarity.content_words.mean', key: 'similarityContent', tolerance: 0.05 },
  { label: 'speech_similarity.phrasing.mean', key: 'similarityPhrasing', tolerance: 0.05 },
  { label: 'secrecy.flags_total', key: 'secrecyFlags', tolerance: 0 },
  { label: 'parsing.rejected_actions', key: 'rejectedActions', tolerance: 0 },
];

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function compareRuns(baseline: RunRow, candidate: RunRow): GateResult[] {
  const gates: GateResult[] = [];

  for (const { label, key, tolerance } of ABSOLUTE) {
    const base = number(baseline[key]);
    const cand = number(candidate[key]);
    if (base == null || cand == null) {
      gates.push({
        metric: label,
        baseline: base,
        candidate: cand,
        delta: null,
        limit: null,
        rule: `<= baseline + ${tolerance}`,
        passed: true,
        skipped: true,
        note: 'value missing from one side',
      });
      continue;
    }
    const limit = base + tolerance;
    gates.push({
      metric: label,
      baseline: base,
      candidate: cand,
      delta: cand - base,
      limit,
      rule: `<= baseline + ${tolerance}`,
      passed: cand <= limit + 1e-9,
      skipped: false,
    });
  }

  const baseTokens = number(baseline.totalTokens);
  const candTokens = number(candidate.totalTokens);
  if (baseTokens == null || candTokens == null || baseTokens === 0 || candTokens === 0) {
    gates.push({
      metric: 'llm.total_tokens',
      baseline: baseTokens,
      candidate: candTokens,
      delta: null,
      limit: null,
      rule: '<= baseline * 1.25',
      passed: true,
      skipped: true,
      note: 'token count unavailable or zero',
    });
  } else {
    const limit = baseTokens * 1.25;
    gates.push({
      metric: 'llm.total_tokens',
      baseline: baseTokens,
      candidate: candTokens,
      delta: candTokens - baseTokens,
      limit,
      rule: '<= baseline * 1.25',
      passed: candTokens <= limit + 1e-9,
      skipped: false,
    });
  }

  return gates;
}
