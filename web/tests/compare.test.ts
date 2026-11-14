import { describe, expect, it } from 'vitest';
import type { RunRow } from '../src/lib/types';
import { compareRuns } from '../src/lib/compare';

function run(overrides: Partial<RunRow> = {}): RunRow {
  return {
    id: 'baseline',
    slug: null,
    label: null,
    blurb: null,
    game: 'game',
    season: 'season',
    family: 'uk-s01',
    flagship: false,
    holdout: false,
    seed: 1,
    players: 22,
    rounds: 12,
    traitorCount: 3,
    traitors: [],
    survivors: [],
    eliminations: [],
    sampleQuote: null,
    sampleSpeaker: null,
    winner: 'traitor',
    winningTeam: 'traitor',
    reason: 'endgame',
    finale: true,
    solo: true,
    provider: 'ollama',
    model: 'local',
    calls: 100,
    totalTokens: 1_000_000,
    messages: 100,
    traitorMessages: 4,
    hallucination: 0,
    duplication: 0.2,
    secrecyFlags: 0,
    rejectedActions: 0,
    reciprocity: 0.6,
    similarityContent: 0.7,
    similarityPhrasing: 0.9,
    benchmarkScore: 0.3,
    hasBenchmark: true,
    hasDiagnosis: false,
    hasMessages: true,
    ...overrides,
  };
}

describe('compareRuns', () => {
  it('passes a candidate inside every absolute and token gate', () => {
    const gates = compareRuns(run(), run({ id: 'candidate', duplication: 0.21, totalTokens: 1_100_000 }));
    expect(gates.every((gate) => gate.passed)).toBe(true);
  });

  it('fails a higher duplication score', () => {
    const gates = compareRuns(run(), run({ id: 'candidate', duplication: 0.3 }));
    const gate = gates.find((item) => item.metric === 'duplication_score');
    expect(gate?.passed).toBe(false);
    expect(gate?.delta).toBeCloseTo(0.1);
  });

  it('fails a token regression above 1.25x', () => {
    const gates = compareRuns(run(), run({ id: 'candidate', totalTokens: 1_300_000 }));
    const gate = gates.find((item) => item.metric === 'llm.total_tokens');
    expect(gate?.passed).toBe(false);
  });

  it('skips the token gate when one side has no token data', () => {
    const gates = compareRuns(run(), run({ id: 'candidate', totalTokens: 0 }));
    const gate = gates.find((item) => item.metric === 'llm.total_tokens');
    expect(gate?.skipped).toBe(true);
  });

  it('skips a gate when a value is missing', () => {
    const gates = compareRuns(run({ hallucination: null }), run({ id: 'candidate' }));
    const gate = gates.find((item) => item.metric === 'hallucination_score');
    expect(gate?.skipped).toBe(true);
  });
});
