import { useEffect, useMemo, useState } from 'react';
import type { RunRow } from '../lib/types';
import { runHref } from '../lib/data';
import { compareRuns } from '../lib/compare';
import {
  formatExact,
  formatNumber,
  formatPercent,
  formatScore,
  methodLabel,
  shortModel,
  titleCase,
} from '../lib/format';

interface Props {
  runs: RunRow[];
  families: string[];
  providers: string[];
  totals: { runs: number; playerGames: number; messages: number; calls: number; tokens: number };
}

type SortKey =
  | 'players'
  | 'rounds'
  | 'calls'
  | 'totalTokens'
  | 'duplication'
  | 'reciprocity'
  | 'benchmarkScore'
  | 'id';

const outcomeLabel = (run: RunRow) => {
  if (run.winningTeam === 'traitor') return 'Traitor';
  if (run.winningTeam === 'faithful') return 'Faithful';
  return 'Unknown';
};

function Bar({ value, max = 1, tone = 'gold' }: { value: number | null | undefined; max?: number; tone?: 'gold' | 'green' | 'red' }) {
  if (value == null) return <span className="text-muted">—</span>;
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const color =
    tone === 'green'
      ? 'linear-gradient(90deg, #3b6b57, #6e9b86)'
      : tone === 'red'
        ? 'linear-gradient(90deg, #7a1f2b, #b4485a)'
        : 'linear-gradient(90deg, #3b6b57, #c8a24a)';
  return (
    <span className="flex items-center gap-2">
      <span className="bar-track w-14">
        <span className="bar-fill block" style={{ width: `${pct}%`, background: color }} />
      </span>
      <span className="mono text-[10px] text-parchment/75">{value <= 1 ? formatPercent(value) : formatNumber(value)}</span>
    </span>
  );
}

function Distribution({
  label,
  values,
  format = (value) => formatPercent(value),
}: {
  label: string;
  values: (number | null | undefined)[];
  format?: (value: number) => string;
}) {
  const valid = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  const min = Math.min(...valid, 0);
  const max = Math.max(...valid, 1);
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="stat-label">{label}</p>
        <p className="mono text-[10px] text-muted">{valid.length} runs</p>
      </div>
      <div className="relative mt-4 h-9">
        <div className="absolute inset-x-0 top-1/2 h-px bg-parchment/10" />
        {values.map((value, index) => {
          if (typeof value !== 'number' || !Number.isFinite(value)) return null;
          const pct = max === min ? 50 : ((value - min) / (max - min)) * 100;
          return (
            <span
              key={index}
              className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-gold/80"
              style={{ left: `${pct}%`, opacity: 0.55 + (index % 3) * 0.15 }}
              title={format(value)}
            />
          );
        })}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[9px] text-muted">
        <span>{format(min)}</span>
        <span>{format(max)}</span>
      </div>
    </div>
  );
}

export default function RunsLab({ runs, families, providers, totals }: Props) {
  const [query, setQuery] = useState('');
  const [family, setFamily] = useState('all');
  const [provider, setProvider] = useState('all');
  const [outcome, setOutcome] = useState('all');
  const [finale, setFinale] = useState('all');
  const [flagshipOnly, setFlagshipOnly] = useState(false);
  const [zeroHallucination, setZeroHallucination] = useState(false);
  const [hasBenchmark, setHasBenchmark] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>('players');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [drawer, setDrawer] = useState<RunRow | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [baselineId, setBaselineId] = useState(runs.find((run) => run.flagship)?.id ?? runs[0]?.id ?? '');
  const [candidateId, setCandidateId] = useState(
    runs.filter((run) => run.flagship)[1]?.id ?? runs[1]?.id ?? '',
  );

  useEffect(() => {
    if (!drawer) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDrawer(null);
    };
    window.addEventListener('keydown', onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [drawer]);

  const filtered = useMemo(() => {
    const search = query.trim().toLowerCase();
    return runs
      .filter((run) => {
        if (family !== 'all' && run.family !== family) return false;
        if (provider !== 'all' && run.provider !== provider) return false;
        if (outcome !== 'all' && run.winningTeam !== outcome) return false;
        if (finale === 'yes' && !run.finale) return false;
        if (finale === 'no' && run.finale) return false;
        if (flagshipOnly && !run.flagship) return false;
        if (zeroHallucination && (run.hallucination ?? 0) > 0) return false;
        if (hasBenchmark && !run.hasBenchmark) return false;
        if (!search) return true;
        return [run.id, run.game, run.family, run.model, run.label]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(search));
      })
      .sort((a, b) => {
        if (a.flagship !== b.flagship) return a.flagship ? -1 : 1;
        const av = a[sortKey];
        const bv = b[sortKey];
        if (typeof av === 'number' && typeof bv === 'number') {
          return sortDir === 'asc' ? av - bv : bv - av;
        }
        return sortDir === 'asc'
          ? String(av).localeCompare(String(bv))
          : String(bv).localeCompare(String(av));
      });
  }, [
    runs,
    query,
    family,
    provider,
    outcome,
    finale,
    flagshipOnly,
    zeroHallucination,
    hasBenchmark,
    sortKey,
    sortDir,
  ]);

  const baseline = runs.find((run) => run.id === baselineId);
  const candidate = runs.find((run) => run.id === candidateId);
  const gates = baseline && candidate ? compareRuns(baseline, candidate) : [];
  const failing = gates.filter((gate) => !gate.passed && !gate.skipped).length;

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((direction) => (direction === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  return (
    <div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {[
          ['runs', totals.runs],
          ['player-games', totals.playerGames],
          ['messages', totals.messages],
          ['model calls', totals.calls],
          ['tokens', totals.tokens],
        ].map(([label, value]) => (
          <div key={String(label)} className="card p-4">
            <p className="stat-value">{formatNumber(Number(value))}</p>
            <p className="stat-label">{label}</p>
          </div>
        ))}
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Distribution label="duplication" values={runs.map((run) => run.duplication)} />
        <Distribution label="hallucination" values={runs.map((run) => run.hallucination)} />
        <Distribution label="private-chat reciprocity" values={runs.map((run) => run.reciprocity)} />
        <Distribution
          label="season benchmark"
          values={runs.map((run) => run.benchmarkScore)}
          format={(value) => formatScore(value)}
        />
      </div>

      <div className="card mt-6 p-4">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label className="block">
            <span className="stat-label">Search</span>
            <input
              className="input mt-1"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="run id, game, model…"
            />
          </label>
          <label className="block">
            <span className="stat-label">Family</span>
            <select className="select mt-1" value={family} onChange={(event) => setFamily(event.target.value)}>
              <option value="all">All families</option>
              {families.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="stat-label">Provider</span>
            <select className="select mt-1" value={provider} onChange={(event) => setProvider(event.target.value)}>
              <option value="all">All providers</option>
              {providers.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="stat-label">Outcome</span>
            <select className="select mt-1" value={outcome} onChange={(event) => setOutcome(event.target.value)}>
              <option value="all">All outcomes</option>
              <option value="faithful">Faithful win</option>
              <option value="traitor">Traitor win</option>
            </select>
          </label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-muted">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={flagshipOnly} onChange={(event) => setFlagshipOnly(event.target.checked)} />
            Flagships only
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={zeroHallucination} onChange={(event) => setZeroHallucination(event.target.checked)} />
            Zero hallucination
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={hasBenchmark} onChange={(event) => setHasBenchmark(event.target.checked)} />
            Has benchmark
          </label>
          <label className="flex items-center gap-2">
            <span>Finale</span>
            <select className="select !min-h-7 !w-auto !px-2 !py-1 !text-[11px]" value={finale} onChange={(event) => setFinale(event.target.value)}>
              <option value="all">any</option>
              <option value="yes">yes</option>
              <option value="no">no</option>
            </select>
          </label>
          <span className="mono ml-auto text-[10px] uppercase tracking-[0.12em]">{filtered.length} shown</span>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div className="pill-row">
          <button
            className={compareMode ? 'btn btn-primary !min-h-9 !px-3 !py-1.5 !text-[11px]' : 'btn btn-ghost !min-h-9 !px-3 !py-1.5 !text-[11px]'}
            onClick={() => setCompareMode((value) => !value)}
            type="button"
          >
            {compareMode ? 'Close compare' : 'Compare two runs'}
          </button>
          {compareMode && (
            <span className={`badge ${failing ? 'badge-traitor' : 'badge-faithful'}`}>
              {failing ? `${failing} gate${failing === 1 ? '' : 's'} failed` : 'All publishable gates pass'}
            </span>
          )}
        </div>
        <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted">
          latency omitted: host-specific measurement
        </p>
      </div>

      {compareMode && baseline && candidate && (
        <div className="card mt-3 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block">
              <span className="stat-label">Baseline</span>
              <select className="select mt-1" value={baselineId} onChange={(event) => setBaselineId(event.target.value)}>
                {runs.map((run) => <option key={run.id} value={run.id}>{run.label ?? run.id}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="stat-label">Candidate</span>
              <select className="select mt-1" value={candidateId} onChange={(event) => setCandidateId(event.target.value)}>
                {runs.map((run) => <option key={run.id} value={run.id}>{run.label ?? run.id}</option>)}
              </select>
            </label>
          </div>
          <div className="mt-4 overflow-x-auto">
            <table className="table">
              <thead>
                <tr><th>Gate</th><th>Baseline</th><th>Candidate</th><th>Delta</th><th>Rule</th><th>Status</th></tr>
              </thead>
              <tbody>
                {gates.map((gate) => (
                  <tr key={gate.metric}>
                    <td className="font-mono text-[10px]">{gate.metric}</td>
                    <td className="mono">{gate.baseline == null ? '—' : formatExact(gate.baseline)}</td>
                    <td className="mono">{gate.candidate == null ? '—' : formatExact(gate.candidate)}</td>
                    <td className="mono">{gate.delta == null ? '—' : `${gate.delta > 0 ? '+' : ''}${formatExact(gate.delta)}`}</td>
                    <td className="text-[11px] text-muted">{gate.rule}</td>
                    <td>
                      <span className={`badge ${gate.skipped ? '' : gate.passed ? 'badge-faithful' : 'badge-traitor'}`}>
                        {gate.skipped ? 'skipped' : gate.passed ? 'pass' : 'fail'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(baseline.holdout || candidate.holdout) && (
            <p className="mt-3 text-[11px] text-gold/90">
              One side uses a holdout seed (seed % 10 &gt;= 7); promotion decisions should not run on holdout data.
            </p>
          )}
        </div>
      )}

      <div className="card mt-3 overflow-x-auto">
        <table className="table min-w-[980px]">
          <thead>
            <tr>
              <th>Run</th>
              <th className="cursor-pointer" onClick={() => toggleSort('players')}>Cast</th>
              <th className="cursor-pointer" onClick={() => toggleSort('rounds')}>Rounds</th>
              <th>Result</th>
              <th className="cursor-pointer" onClick={() => toggleSort('calls')}>Calls</th>
              <th className="cursor-pointer" onClick={() => toggleSort('totalTokens')}>Tokens</th>
              <th>Halluc.</th>
              <th className="cursor-pointer" onClick={() => toggleSort('duplication')}>Dup.</th>
              <th className="cursor-pointer" onClick={() => toggleSort('reciprocity')}>Recip.</th>
              <th className="cursor-pointer" onClick={() => toggleSort('benchmarkScore')}>Bench.</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {filtered.map((run) => (
              <tr
                key={run.id}
                className="cursor-pointer"
                onClick={() => setDrawer(run)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') setDrawer(run);
                }}
                tabIndex={0}
              >
                <td>
                  <div className="flex items-center gap-2">
                    {run.flagship && <span className="h-1.5 w-1.5 rounded-full bg-gold" aria-label="flagship" />}
                    <div>
                      <p className="font-medium">{run.label ?? run.id}</p>
                      <p className="font-mono text-[9px] uppercase tracking-[0.1em] text-muted">
                        {run.id} · {shortModel(run.model)}
                      </p>
                    </div>
                  </div>
                </td>
                <td className="mono">{run.players}</td>
                <td className="mono">{run.rounds}</td>
                <td>
                  <span className={`badge ${run.winningTeam === 'traitor' ? 'badge-traitor' : 'badge-faithful'}`}>
                    {outcomeLabel(run)}{run.finale ? ' · finale' : ''}{run.solo ? ' · solo' : ''}
                  </span>
                </td>
                <td className="mono">{formatNumber(run.calls)}</td>
                <td className="mono">{formatNumber(run.totalTokens)}</td>
                <td><Bar value={run.hallucination} tone={(run.hallucination ?? 0) > 0 ? 'red' : 'green'} /></td>
                <td><Bar value={run.duplication} tone="red" /></td>
                <td><Bar value={run.reciprocity} tone="green" /></td>
                <td className="mono">{formatScore(run.benchmarkScore)}</td>
                <td className="text-right">
                  <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-gold">Inspect →</span>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={11} className="py-10 text-center text-sm text-muted">No runs match these filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {drawer && (
        <div className="fixed inset-0 z-[80] flex items-end justify-center bg-black/70 p-0 backdrop-blur-sm md:items-center md:p-6" role="dialog" aria-modal="true" aria-label={`Run ${drawer.id}`} onClick={() => setDrawer(null)}>
          <div className="card max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-b-none p-5 md:rounded-2xl" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-gold">{drawer.family}</p>
                <h2 className="mt-1 font-display text-2xl">{drawer.label ?? drawer.id}</h2>
                <p className="mt-1 text-[13px] text-muted">{drawer.game} · seed {drawer.seed ?? '—'} · {titleCase(drawer.provider)} / {shortModel(drawer.model)}</p>
              </div>
              <button className="btn btn-ghost !min-h-8 !px-3 !py-1 !text-[11px]" onClick={() => setDrawer(null)} type="button">Close</button>
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              <span className={`badge ${drawer.winningTeam === 'traitor' ? 'badge-traitor' : 'badge-faithful'}`}>
                {outcomeLabel(drawer)} win
              </span>
              <span className="badge">{drawer.players} players</span>
              <span className="badge">{drawer.rounds} rounds</span>
              <span className="badge">{formatNumber(drawer.messages)} messages</span>
              {drawer.holdout && <span className="badge badge-gold">holdout seed</span>}
            </div>
            <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ['calls', formatNumber(drawer.calls)],
                ['tokens', formatNumber(drawer.totalTokens)],
                ['hallucination', formatScore(drawer.hallucination)],
                ['duplication', formatScore(drawer.duplication)],
                ['reciprocity', formatScore(drawer.reciprocity)],
                ['secrecy flags', formatExact(drawer.secrecyFlags)],
                ['rejected actions', formatExact(drawer.rejectedActions)],
                ['benchmark', formatScore(drawer.benchmarkScore)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-[var(--border)] bg-black/15 p-3">
                  <dt className="stat-label">{label}</dt>
                  <dd className="mono mt-1 text-sm">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-5">
              <p className="stat-label">Elimination order</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {drawer.eliminations.map((elimination) => (
                  <span key={elimination.id} className="badge">
                    r{elimination.round} · {elimination.id} · {methodLabel(elimination.method)}
                  </span>
                ))}
              </div>
            </div>
            <div className="mt-6 flex flex-wrap gap-2">
              <a className="btn btn-primary" href={runHref(drawer)}>Open full run</a>
              <a className="btn btn-ghost" href={`/runs#lab`}>Back to lab</a>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
