import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { Message, Player, RoundDigest, RunSummary } from '../../lib/types';
import { loadMessages } from '../../lib/load-run';
import { canSeeMessage, roleAt } from '../../lib/visibility';
import {
  channelLabel,
  formatNumber,
  formatPercent,
  formatScore,
  methodLabel,
  shortModel,
  titleCase,
} from '../../lib/format';
import Sigil from '../Sigil';

type Tab = 'overview' | 'timeline' | 'replay' | 'votes' | 'players' | 'metrics' | 'benchmark' | 'diagnosis';
type Mode = 'audience' | 'play';

const traitorGroundTruthCaveat = new Set(['the-traitors-us-s03', 'the-traitors-india-s01']);

function percent(value: number | null | undefined, digits = 0) {
  return formatPercent(value ?? null, digits);
}

function Bar({
  value,
  max = 1,
  tone = 'gold',
  height = 6,
}: {
  value: number | null | undefined;
  max?: number;
  tone?: 'gold' | 'green' | 'red';
  height?: number;
}) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
  const background =
    tone === 'green'
      ? 'linear-gradient(90deg,#3b6b57,#6e9b86)'
      : tone === 'red'
        ? 'linear-gradient(90deg,#7a1f2b,#b4485a)'
        : 'linear-gradient(90deg,#3b6b57,#c8a24a)';
  return (
    <span className="flex items-center gap-2">
      <span className="bar-track" style={{ height, flex: 1 }}>
        <span className="bar-fill block" style={{ width: `${pct}%`, background }} />
      </span>
      <span className="mono text-[10px] text-parchment/70">
        {value == null ? '—' : value <= 1 ? percent(value) : formatNumber(value)}
      </span>
    </span>
  );
}

function RolePill({ role, hidden = false }: { role: 'faithful' | 'traitor'; hidden?: boolean }) {
  if (hidden) return <span className="badge">role hidden</span>;
  return (
    <span className={`badge ${role === 'traitor' ? 'badge-traitor' : 'badge-faithful'}`}>
      {role === 'traitor' ? '◆ traitor' : '◇ faithful'}
    </span>
  );
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-black/15 p-3">
      <p className="stat-label">{label}</p>
      <p className="mono mt-1 text-sm text-parchment">{value}</p>
      {hint && <p className="mt-1 text-[10px] leading-4 text-muted">{hint}</p>}
    </div>
  );
}

function SectionHeading({ eyebrow, title, detail }: { eyebrow: string; title: string; detail?: string }) {
  return (
    <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h3 className="display mt-2 text-2xl md:text-3xl">{title}</h3>
      </div>
      {detail && <p className="max-w-md text-[12px] leading-5 text-muted">{detail}</p>}
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-[var(--border)] p-6 text-sm text-muted">{children}</div>;
}

export default function RunExperience({ summary }: { summary: RunSummary }) {
  const [tab, setTab] = useState<Tab>('overview');
  const [mode, setMode] = useState<Mode>('audience');
  const [round, setRound] = useState(1);
  const [selectedPlayer, setSelectedPlayer] = useState<string | null>(summary.players[0]?.id ?? null);
  const [messages, setMessages] = useState<Message[] | null>(summary.hasMessages ? null : []);
  const [messageError, setMessageError] = useState<string | null>(null);

  useEffect(() => {
    const hash = window.location.hash.replace('#', '') as Tab;
    if (hash === 'replay' && !summary.hasMessages) return;
    if (hash === 'benchmark' && !summary.benchmark) return;
    if (hash === 'diagnosis' && !summary.diagnosis) return;
    if (['overview', 'timeline', 'replay', 'votes', 'players', 'metrics', 'benchmark', 'diagnosis'].includes(hash)) {
      setTab(hash);
    }
  }, [summary.hasMessages, summary.benchmark, summary.diagnosis]);

  useEffect(() => {
    if (window.location.hash !== `#${tab}`) {
      window.history.replaceState(null, '', `#${tab}`);
    }
  }, [tab]);

  useEffect(() => {
    if (mode === 'play' && (tab === 'benchmark' || tab === 'diagnosis')) {
      setTab('overview');
    }
  }, [mode, tab]);

  useEffect(() => {
    let active = true;
    if (!summary.hasMessages) {
      setMessages([]);
      return;
    }
    loadMessages(summary.id)
      .then((data) => {
        if (active) setMessages(data ?? []);
      })
      .catch(() => {
        if (active) {
          setMessages([]);
          setMessageError('The transcript could not be loaded.');
        }
      });
    return () => {
      active = false;
    };
  }, [summary.id, summary.hasMessages]);

  const byId = useMemo(() => new Map(summary.players.map((player) => [player.id, player])), [summary.players]);
  const name = (id: string | null | undefined) => (id ? byId.get(id)?.name ?? id : '—');
  const showRoles = mode === 'audience';
  const selected = selectedPlayer ? byId.get(selectedPlayer) ?? null : null;

  const firstMurder = [...summary.players]
    .filter((player) => player.exit?.method === 'night')
    .sort((a, b) => (a.exit?.round ?? 0) - (b.exit?.round ?? 0))[0];
  const firstBanishment = [...summary.players]
    .filter((player) => player.exit?.method === 'vote')
    .sort((a, b) => (a.exit?.round ?? 0) - (b.exit?.round ?? 0))[0];

  const tabs: { id: Tab; label: string; show: boolean }[] = [
    { id: 'overview', label: 'Overview', show: true },
    { id: 'timeline', label: 'Timeline', show: true },
    { id: 'replay', label: 'Replay', show: summary.hasMessages },
    { id: 'votes', label: 'Votes', show: true },
    { id: 'players', label: 'Players', show: true },
    { id: 'metrics', label: 'Metrics', show: true },
    { id: 'benchmark', label: 'Benchmark', show: Boolean(summary.benchmark) && mode === 'audience' },
    { id: 'diagnosis', label: 'Diagnosis', show: Boolean(summary.diagnosis) && mode === 'audience' },
  ];

  const survivors = summary.players.filter((player) => !player.exit);
  const traitors = summary.players.filter((player) => player.role === 'traitor');

  return (
    <div className="page-shell py-8 md:py-12">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <a className="btn btn-ghost !min-h-8 !px-3 !py-1 !text-[11px]" href="/runs">← Metrics lab</a>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-full border border-[var(--border)] p-0.5" role="group" aria-label="Spoiler mode">
            <button
              type="button"
              className={`rounded-full px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] ${mode === 'audience' ? 'bg-gold text-ink' : 'text-muted'}`}
              aria-pressed={mode === 'audience'}
              onClick={() => setMode('audience')}
            >
              Audience
            </button>
            <button
              type="button"
              className={`rounded-full px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] ${mode === 'play' ? 'bg-gold text-ink' : 'text-muted'}`}
              aria-pressed={mode === 'play'}
              onClick={() => setMode('play')}
            >
              Play along
            </button>
          </div>
          <a className="btn btn-ghost !min-h-8 !px-3 !py-1 !text-[11px]" href="/method">Method</a>
        </div>
      </div>

      <header className="mt-8 grid gap-6 border-b border-[var(--border)] pb-8 lg:grid-cols-[1.25fr_0.75fr] lg:items-end">
        <div>
          <p className="eyebrow">{summary.family} · {summary.game}</p>
          <h1 className="display mt-3 text-[clamp(2.4rem,5vw,4.4rem)]">{summary.label ?? summary.game}</h1>
          <p className="lede mt-4 max-w-3xl">{summary.blurb ?? 'A completed simulation run with full artifacts, cast, timeline, and metrics.'}</p>
          <div className="mt-5 flex flex-wrap gap-2">
            <span className={`badge ${summary.outcome.winningTeam === 'traitor' ? 'badge-traitor' : 'badge-faithful'}`}>
              {summary.outcome.winningTeam === 'traitor' ? '◆' : '◇'} {summary.outcome.winningTeam ?? 'unknown'} win
            </span>
            <span className="badge">{summary.outcome.reason || 'completed'}</span>
            {summary.outcome.finale && <span className="badge badge-gold">finale</span>}
            {summary.outcome.solo && <span className="badge badge-gold">solo traitor</span>}
            <span className="badge">{summary.config.provider} / {shortModel(summary.config.model)}</span>
            <span className="badge">seed {summary.seed ?? '—'}</span>
            {summary.holdout && <span className="badge badge-gold">holdout seed</span>}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-2">
          <Stat label="players" value={summary.players.length} />
          <Stat label="rounds" value={summary.rounds} />
          <Stat label="messages" value={formatNumber(summary.counts.messages)} />
          <Stat label="model calls" value={formatNumber(summary.counts.calls)} />
        </div>
      </header>

      <div className="tabs mt-6" role="tablist" aria-label="Run sections">
        {tabs.filter((item) => item.show).map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className="tab"
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="mt-6" role="tabpanel">
        {tab === 'overview' && (
          <Overview
            summary={summary}
            showRoles={showRoles}
            name={name}
            survivors={survivors}
            traitors={traitors}
            firstMurder={firstMurder}
            firstBanishment={firstBanishment}
            onOpenPlayer={(id) => {
              setSelectedPlayer(id);
              setTab('players');
            }}
            round={round}
          />
        )}
        {tab === 'timeline' && (
          <Timeline
            summary={summary}
            round={round}
            setRound={setRound}
            showRoles={showRoles}
            name={name}
          />
        )}
        {tab === 'replay' && (
          <Replay
            summary={summary}
            messages={messages}
            error={messageError}
            mode={mode}
            name={name}
            round={round}
            setRound={setRound}
          />
        )}
        {tab === 'votes' && <Votes summary={summary} round={round} setRound={setRound} name={name} />}
        {tab === 'players' && (
          <Players
            summary={summary}
            selected={selected}
            setSelected={setSelectedPlayer}
            showRoles={showRoles}
            messages={messages}
            name={name}
          />
        )}
        {tab === 'metrics' && <MetricsPanel summary={summary} />}
        {tab === 'benchmark' && <BenchmarkPanel summary={summary} name={name} />}
        {tab === 'diagnosis' && <DiagnosisPanel summary={summary} />}
      </div>
    </div>
  );
}

function Overview({
  summary,
  showRoles,
  name,
  survivors,
  traitors,
  firstMurder,
  firstBanishment,
  onOpenPlayer,
  round,
}: {
  summary: RunSummary;
  showRoles: boolean;
  name: (id: string | null | undefined) => string;
  survivors: Player[];
  traitors: Player[];
  firstMurder?: Player;
  firstBanishment?: Player;
  onOpenPlayer: (id: string) => void;
  round: number;
}) {
  const plot = [
    firstBanishment && `${name(firstBanishment.id)} is the first banishment in round ${firstBanishment.exit?.round}.`,
    firstMurder && `${name(firstMurder.id)} is the first night victim.`,
    summary.outcome.finale && 'The finale opens with an end-or-banish vote.',
    summary.outcome.solo && 'A single traitor takes the prize.',
    !summary.outcome.finale && summary.outcome.winningTeam === 'faithful' && 'The faithful close it out by elimination.',
  ].filter(Boolean) as string[];

  return (
    <div className="space-y-10">
      <section className="grid gap-3 md:grid-cols-3">
        <div className="card p-5 md:col-span-2">
          <p className="stat-label">What happened</p>
          <ul className="mt-3 space-y-2 text-[13px] leading-6 text-parchment/80">
            {plot.map((line) => (
              <li key={line} className="flex gap-2">
                <span className="mt-2 h-1 w-1 flex-none rounded-full bg-gold" aria-hidden="true" />
                {line}
              </li>
            ))}
          </ul>
          <div className="mt-5 grid gap-2 sm:grid-cols-3">
            <Stat label="traitors" value={traitors.length} hint={showRoles ? traitors.map((player) => name(player.id)).join(', ') : 'hidden in play-along mode'} />
            <Stat label="survivors" value={survivors.length} hint={survivors.slice(0, 5).map((player) => name(player.id)).join(', ')} />
            <Stat label="winner" value={summary.outcome.winningTeam ?? '—'} hint={summary.outcome.reason} />
          </div>
        </div>
        <div className="card p-5">
          <p className="stat-label">Run identity</p>
          <dl className="mt-3 space-y-2 text-[12px]">
            {[
              ['config', summary.config.name],
              ['language', summary.config.language],
              ['provider', summary.config.provider],
              ['model', summary.config.model],
              ['players', `${summary.config.players} (${summary.config.traitors} traitors)`],
              ['max rounds', String(summary.config.maxRounds)],
              ['tokens', formatNumber(summary.counts.totalTokens)],
              ['rejected actions', String(summary.counts.rejectedActions)],
            ].map(([label, value]) => (
              <div key={label} className="flex justify-between gap-3 border-b border-[var(--border)] pb-1.5 last:border-0">
                <dt className="text-muted">{label}</dt>
                <dd className="mono text-right text-[10px]">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section>
        <SectionHeading
          eyebrow="Cast"
          title="The table"
          detail={showRoles ? 'Roles are revealed for the audience. Switch to Play along to hide them.' : 'Roles are hidden. Switch to Audience to reveal the cast.'}
        />
        <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {summary.players.map((player) => (
            <button
              key={player.id}
              type="button"
              onClick={() => onOpenPlayer(player.id)}
              className="card card-hover flex items-start gap-3 p-3 text-left"
            >
              <Sigil id={player.id} name={player.name} size={38} role={showRoles ? player.role : undefined} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-[13px] font-medium">{player.name}</p>
                  {player.exit ? (
                    <span className="badge !px-1.5 !py-0.5 !text-[8px]">{methodLabel(player.exit.method)} r{player.exit.round}</span>
                  ) : (
                    <span className="badge badge-gold !px-1.5 !py-0.5 !text-[8px]">alive</span>
                  )}
                </div>
                <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-muted">
                  {player.persona?.description || (showRoles ? `${titleCase(player.role)} · ${player.outcome}` : 'Simulation character')}
                </p>
                <p className={`mt-1 font-mono text-[8px] uppercase tracking-[0.12em] ${showRoles ? (player.role === 'traitor' ? 'text-traitor' : 'text-faithful') : 'text-muted'}`}>
                  {showRoles ? player.role : 'role hidden'}
                </p>
              </div>
            </button>
          ))}
        </div>
      </section>

      <section>
        <SectionHeading eyebrow="Mechanics" title="What was switched on" detail="A curated subset of the run's config. Every key is read from the run's own YAML." />
        <div className="mt-4 flex flex-wrap gap-2">
          {Object.entries(summary.config.flags).map(([key, value]) => {
            const on = value === true || (typeof value === 'number' && value !== 0) || (Array.isArray(value) && value.length > 0) || (typeof value === 'string' && value !== 'english' && value !== '');
            return (
              <span key={key} className={`badge ${on ? 'badge-gold' : ''}`} title={`${key}: ${String(value)}`}>
                {key.replaceAll('_', ' ')}: {Array.isArray(value) ? (value.length ? value.join(', ') : 'none') : String(value)}
              </span>
            );
          })}
        </div>
      </section>

      {(summary.benchmark || summary.benchmarkScore != null) && (
        <section>
          <SectionHeading eyebrow="Season benchmark" title="How close is the simulation?" detail="Alignment against recorded ground truth; skipped components are excluded and their weight is redistributed." />
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <div className="card p-5">
              <p className="stat-value text-gold">{formatScore(summary.benchmarkScore)}</p>
              <p className="stat-label">overall alignment</p>
            </div>
            <div className="card p-5">
              <p className="text-sm text-parchment/85">{summary.benchmark?.season.name ?? 'Recorded benchmark'}</p>
              <p className="stat-label mt-2">ground truth</p>
            </div>
            <div className="card p-5">
              <p className="text-sm text-parchment/85">
                {summary.benchmark
                  ? `${summary.benchmark.result.overall.components_scored.length} / ${Object.keys(summary.benchmark.result.overall.weights).length} components scored`
                  : 'summary only'}
              </p>
              <p className="stat-label mt-2">coverage</p>
            </div>
          </div>
          <div className="mt-3">
            <p className="text-[11px] text-muted">
              {summary.benchmark
                ? traitorGroundTruthCaveat.has(summary.benchmark.season.id)
                  ? 'This season file is marked unverified in the repository. Treat the score as a comparison, not an accuracy claim.'
                  : 'Open the Benchmark tab for the weighted components and elimination alignment.'
                : 'This run has a benchmark score, but its full component table was not exported. Open the run on GitHub for the original artifact.'}
            </p>
          </div>
        </section>
      )}

      <section>
        <SectionHeading eyebrow="Try it" title="The same run, from another seat" detail="The replay tab can filter to one player's structurally visible messages." />
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="btn btn-primary" type="button" onClick={() => onOpenPlayer(summary.players[0]?.id ?? '')}>
            Open a dossier
          </button>
          <span className="badge">round {round} selected in Timeline</span>
        </div>
      </section>
    </div>
  );
}

function Timeline({
  summary,
  round,
  setRound,
  showRoles,
  name,
}: {
  summary: RunSummary;
  round: number;
  setRound: (round: number) => void;
  showRoles: boolean;
  name: (id: string | null | undefined) => string;
}) {
  const digest = summary.roundDigests.find((item) => item.r === round);
  const rounds = Array.from({ length: summary.rounds }, (_, index) => index + 1);

  return (
    <div className="space-y-6">
      <div
        className="flex flex-wrap gap-1"
        role="group"
        aria-label="Select a round"
        onKeyDown={(event) => {
          if (event.key === 'ArrowRight') setRound(Math.min(summary.rounds, round + 1));
          if (event.key === 'ArrowLeft') setRound(Math.max(1, round - 1));
        }}
      >
        {rounds.map((value) => (
          <button
            key={value}
            type="button"
            className={`min-w-9 rounded-lg border px-2 py-1.5 font-mono text-[10px] ${value === round ? 'border-gold bg-gold/15 text-gold' : 'border-[var(--border)] text-muted hover:text-parchment'}`}
            aria-pressed={value === round}
            onClick={() => setRound(value)}
          >
            {value}
          </button>
        ))}
      </div>

      <div className="card p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="stat-label">Season strip</p>
          <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted">rounds →</p>
        </div>
        <div className="mt-3 overflow-x-auto">
          <div className="min-w-[620px]">
            <div className="grid items-center gap-1" style={{ gridTemplateColumns: `110px repeat(${summary.rounds}, minmax(14px, 1fr))` }}>
              <span />
              {rounds.map((value) => (
                <span key={value} className={`text-center font-mono text-[8px] ${value === round ? 'text-gold' : 'text-muted'}`}>{value}</span>
              ))}
              {summary.players.map((player) => (
                <div key={player.id} className="contents">
                  <span className="truncate pr-2 text-[10px] text-parchment/70">{player.name}</span>
                  {rounds.map((value) => {
                    const role = roleAt(player, value);
                    const exit = player.exit?.round === value;
                    const alive = player.exit ? player.exit.round > value : true;
                    const classes = exit
                      ? 'timeline-cell exit'
                      : !alive
                        ? 'timeline-cell dead'
                        : showRoles && role === 'traitor'
                          ? 'timeline-cell traitor'
                          : 'timeline-cell alive';
                    return (
                      <span
                        key={value}
                        className={`${classes} ${value === round ? 'ring-1 ring-gold/60' : ''}`}
                        title={`${player.name}, round ${value}: ${exit ? methodLabel(player.exit?.method ?? '') : alive ? (showRoles ? role : 'alive') : 'out'}`}
                      />
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[9px] uppercase tracking-[0.1em] text-muted">
          <span><span className="mr-1 inline-block h-2 w-2 rounded-[2px] bg-faithful/50" />alive</span>
          {showRoles && <span><span className="mr-1 inline-block h-2 w-2 rounded-[2px] bg-traitor/60" />traitor</span>}
          <span><span className="mr-1 inline-block h-2 w-2 rounded-[2px] bg-[var(--color-oxblood)]" />eliminated</span>
        </div>
      </div>

      <div className="card p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="eyebrow">Round {round}</p>
            <h3 className="display mt-1 text-2xl">{digest?.finaleStarted ? 'Finale' : 'Round detail'}</h3>
          </div>
          <div className="flex gap-2">
            <button className="btn btn-ghost !min-h-8 !px-3 !py-1 !text-[11px]" type="button" onClick={() => setRound(Math.max(1, round - 1))} disabled={round === 1}>← Previous</button>
            <button className="btn btn-ghost !min-h-8 !px-3 !py-1 !text-[11px]" type="button" onClick={() => setRound(Math.min(summary.rounds, round + 1))} disabled={round === summary.rounds}>Next →</button>
          </div>
        </div>
        <RoundDetail digest={digest} name={name} showRoles={showRoles} />
      </div>
    </div>
  );
}

function RoundDetail({ digest, name, showRoles }: { digest?: RoundDigest; name: (id: string | null | undefined) => string; showRoles: boolean }) {
  if (!digest) return <Empty>No events for this round.</Empty>;
  const tally = new Map<string, number>();
  for (const vote of digest.votes) tally.set(vote.to, (tally.get(vote.to) ?? 0) + vote.weight);
  const tallyRows = [...tally.entries()].sort((a, b) => b[1] - a[1]);
  const voteTotal = tallyRows.reduce((sum, [, value]) => sum + value, 0);
  const maxVote = Math.max(...tallyRows.map(([, value]) => value), 1);

  return (
    <div className="mt-5 grid gap-5 lg:grid-cols-2">
      <div className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-3">
          <Stat label="missions" value={digest.missions} />
          <Stat label="host warnings" value={digest.hostWarnings} />
          <Stat label="items" value={digest.items.length} />
        </div>

        {digest.items.length > 0 && (
          <div>
            <p className="stat-label">Items</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {digest.items.map((item, index) => (
                <span key={`${item.item}-${index}`} className="badge badge-gold">
                  {item.item}{showRoles ? ` → ${name(item.holder)}` : ' awarded'}
                </span>
              ))}
            </div>
          </div>
        )}

        {digest.nominations && (
          <div>
            <p className="stat-label">Nominations</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {digest.nominations.nominees.map((id) => (
                <span key={id} className="badge">{name(id)} · {digest.nominations?.counts[id] ?? 0}</span>
              ))}
            </div>
            <details className="mt-2">
              <summary className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">accusations</summary>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {Object.entries(digest.nominations.accusations).map(([from, to]) => (
                  <span key={`${from}-${to}`} className="badge !text-[9px]">{name(from)} → {name(to)}</span>
                ))}
              </div>
            </details>
          </div>
        )}

        {digest.revote?.called && (
          <div>
            <p className="stat-label">Revote</p>
            <p className="mt-1 text-[12px] text-muted">
              A tied ballot opened a restricted revote.
              {digest.revote.resolved && ` Resolved: ${Object.entries(digest.revote.resolved).map(([id, count]) => `${name(id)} ${count}`).join(', ')}.`}
            </p>
          </div>
        )}

        {showRoles && digest.council && (
          <div>
            <p className="stat-label">Traitor council</p>
            <div className="mt-2 space-y-2">
              {digest.council.proposals.map((proposal, index) => (
                <div key={index} className="rounded-lg border border-[var(--border)] bg-black/15 p-3">
                  <p className="font-mono text-[9px] uppercase tracking-[0.1em] text-gold">
                    {name(proposal.by)} proposes {name(proposal.target)}
                  </p>
                  {proposal.reason && <p className="mt-1 line-clamp-4 text-[11px] leading-5 text-parchment/70">{proposal.reason}</p>}
                </div>
              ))}
              {digest.council.verdict && (
                <p className="text-[11px] text-muted">
                  Final: {Object.entries(digest.council.verdict.final ?? {}).map(([id, target]) => `${name(id)} → ${name(target)}`).join(' · ')}
                  {digest.council.verdict.unanimous === false && ' · divided'}
                </p>
              )}
            </div>
          </div>
        )}

        {showRoles && digest.recruit && (
          <div>
            <p className="stat-label">Night decision</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {digest.recruit.choice && <span className="badge">{digest.recruit.choice}</span>}
              {digest.recruit.target && <span className="badge badge-gold">offer → {name(digest.recruit.target)}</span>}
              {digest.recruit.accepted && <span className="badge badge-traitor">accepted</span>}
              {digest.recruit.declined && <span className="badge">declined</span>}
              {digest.recruit.ultimatum && <span className="badge badge-traitor">ultimatum → {name(digest.recruit.ultimatumTarget)}</span>}
            </div>
          </div>
        )}
      </div>

      <div className="space-y-4">
        <div>
          <p className="stat-label">Ballot</p>
          {tallyRows.length === 0 ? (
            <p className="mt-2 text-[12px] text-muted">No vote was cast in this round.</p>
          ) : (
            <div className="mt-2 space-y-2">
              {tallyRows.map(([id, value]) => (
                <div key={id}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[12px]">{name(id)}</span>
                    <span className="mono text-[10px] text-muted">{value} / {voteTotal}</span>
                  </div>
                  <div className="mt-1"><Bar value={value} max={maxVote} /></div>
                </div>
              ))}
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-1.5">
            {digest.votes.map((vote, index) => (
              <span key={index} className="badge !text-[9px]">
                {name(vote.from)} → {name(vote.to)}{vote.weight > 1 ? ` ×${vote.weight}` : ''}
              </span>
            ))}
          </div>
        </div>

        {digest.elimination && (
          <div className={`rounded-xl border p-4 ${digest.elimination.method === 'vote' ? 'border-[var(--color-oxblood)]/40 bg-[var(--color-oxblood)]/10' : 'border-traitor/30 bg-traitor/10'}`}>
            <p className="stat-label">{methodLabel(digest.elimination.method)}</p>
            <p className="mt-1 font-display text-xl">{name(digest.elimination.player)}</p>
            {Object.keys(digest.elimination.tally).length > 0 && (
              <p className="mt-1 text-[11px] text-muted">
                {Object.entries(digest.elimination.tally).map(([id, count]) => `${name(id)} ${count}`).join(' · ')}
              </p>
            )}
          </div>
        )}

        {digest.murder && (
          <div>
            <p className="stat-label">Night</p>
            <p className="mt-1 text-[12px] text-muted">
              {digest.murder.skipped
                ? 'No murder this round.'
                : showRoles
                  ? `Murdered: ${name(digest.murder.victim)}`
                  : 'A murder was recorded; the victim is hidden in play-along mode.'}
            </p>
          </div>
        )}

        {digest.banishmentSkipped && <p className="text-[12px] text-muted">The round table was quiet: nobody banished.</p>}

        {digest.endVotes.length > 0 && (
          <div>
            <p className="stat-label">End-or-banish vote</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {digest.endVotes.map((vote, index) => (
                <span key={index} className="badge !text-[9px]">{name(vote.player)}: {vote.choice}</span>
              ))}
            </div>
          </div>
        )}

        {!showRoles && (
          <p className="rounded-lg border border-[var(--border)] bg-black/15 p-3 text-[11px] leading-5 text-muted">
            Play-along mode hides roles and the traitor channel. Night choices are shown as public facts only.
          </p>
        )}
      </div>
    </div>
  );
}

function Replay({
  summary,
  messages,
  error,
  mode,
  name,
  round,
  setRound,
}: {
  summary: RunSummary;
  messages: Message[] | null;
  error: string | null;
  mode: Mode;
  name: (id: string | null | undefined) => string;
  round: number;
  setRound: (round: number) => void;
}) {
  const [channel, setChannel] = useState('all');
  const [player, setPlayer] = useState('all');
  const [focusPlayer, setFocusPlayer] = useState('all');
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(80);

  const focus = focusPlayer === 'all' ? null : summary.players.find((item) => item.id === focusPlayer) ?? null;
  const filtered = useMemo(() => {
    if (!messages) return [];
    const search = query.trim().toLowerCase();
    return messages.filter((message) => {
      if (round > 0 && message.r !== round) return false;
      if (mode === 'play' && message.channel === 'traitor') return false;
      if (channel !== 'all' && message.channel !== channel) return false;
      if (player !== 'all' && message.from !== player && !message.to.includes(player)) return false;
      if (focus && !canSeeMessage(message, focus, message.r, 'player')) return false;
      if (search && !message.text.toLowerCase().includes(search)) return false;
      return true;
    });
  }, [messages, round, mode, channel, player, focus, query]);

  if (error) return <Empty>{error}</Empty>;
  if (messages == null) return <Empty>Loading transcript…</Empty>;
  if (messages.length === 0) {
    return <Empty>This run's transcript was not exported. The summary, timeline, and metrics are still available.</Empty>;
  }

  const visible = filtered.slice(0, limit);
  const channelCounts = summary.counts;

  return (
    <div className="space-y-4">
      <div className="card p-4">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <label className="block">
            <span className="stat-label">Round</span>
            <select className="select mt-1" value={round} onChange={(event) => setRound(Number(event.target.value))}>
              <option value={0}>All rounds</option>
              {Array.from({ length: summary.rounds }, (_, index) => index + 1).map((value) => (
                <option key={value} value={value}>Round {value}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="stat-label">Channel</span>
            <select className="select mt-1" value={channel} onChange={(event) => setChannel(event.target.value)}>
              <option value="all">All channels</option>
              <option value="public">Public</option>
              <option value="private">Private</option>
              {mode === 'audience' && <option value="traitor">Traitor</option>}
              <option value="host">Host</option>
            </select>
          </label>
          <label className="block">
            <span className="stat-label">Player</span>
            <select className="select mt-1" value={player} onChange={(event) => setPlayer(event.target.value)}>
              <option value="all">Everyone</option>
              {summary.players.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="stat-label">View as player</span>
            <select className="select mt-1" value={focusPlayer} onChange={(event) => setFocusPlayer(event.target.value)}>
              <option value="all">No filter</option>
              {summary.players.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}{mode === 'audience' ? ` (${roleAt(item, round || 1)})` : ''}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="stat-label">Search</span>
            <input className="input mt-1" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="text…" />
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-muted">
          <span className="badge">{filtered.length} visible</span>
          <span className="badge">{channelCounts.public} public</span>
          <span className="badge">{channelCounts.private} private</span>
          {mode === 'audience' && <span className="badge">{channelCounts.traitor} traitor</span>}
          <span className="ml-auto font-mono uppercase tracking-[0.12em]">latency omitted</span>
        </div>
        {focus && (
          <p className="mt-3 rounded-lg border border-gold/25 bg-gold/[0.04] p-3 text-[11px] leading-5 text-muted">
            Viewing as <span className="text-parchment">{focus.name}</span> ({roleAt(focus, round || 1)}).
            Only messages structurally visible to this player are shown. The real prompt also carries
            role knowledge, memory, and a bounded transcript window.
          </p>
        )}
      </div>

      <div className="space-y-3">
        {visible.map((message, index) => (
          <article key={`${message.r}-${message.from}-${index}`} className="message-card" data-channel={message.channel}>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`badge ${message.channel === 'traitor' ? 'badge-traitor' : message.channel === 'host' ? 'badge-gold' : ''}`}>
                {channelLabel(message.channel)}
              </span>
              <span className="mono text-[10px] text-muted">round {message.r} · {message.phase.replaceAll('_', ' ')}</span>
              <span className="ml-auto text-[11px] text-parchment/80">{name(message.from)}</span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-[13px] leading-6 text-parchment/80">{message.text}</p>
            {message.to.length > 0 && message.channel === 'private' && (
              <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.1em] text-muted">to {message.to.map(name).join(', ')}</p>
            )}
          </article>
        ))}
        {visible.length === 0 && <Empty>No messages match these filters.</Empty>}
      </div>

      {filtered.length > limit && (
        <button className="btn btn-ghost" type="button" onClick={() => setLimit((value) => value + 100)}>
          Show more ({filtered.length - limit} remaining)
        </button>
      )}
    </div>
  );
}

function Votes({
  summary,
  round,
  setRound,
  name,
}: {
  summary: RunSummary;
  round: number;
  setRound: (round: number) => void;
  name: (id: string | null | undefined) => string;
}) {
  const digest = summary.roundDigests.find((item) => item.r === round);
  const tally = new Map<string, number>();
  for (const vote of digest?.votes ?? []) tally.set(vote.to, (tally.get(vote.to) ?? 0) + vote.weight);
  const rows = [...tally.entries()].sort((a, b) => b[1] - a[1]);
  const max = Math.max(...rows.map(([, value]) => value), 1);
  const total = rows.reduce((sum, [, value]) => sum + value, 0);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: summary.rounds }, (_, index) => index + 1).map((value) => (
          <button
            key={value}
            type="button"
            className={`min-w-9 rounded-lg border px-2 py-1.5 font-mono text-[10px] ${value === round ? 'border-gold bg-gold/15 text-gold' : 'border-[var(--border)] text-muted'}`}
            onClick={() => setRound(value)}
          >
            {value}
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <SectionHeading eyebrow={`Round ${round}`} title="The ballot" />
          {rows.length === 0 ? (
            <div className="mt-4"><Empty>No votes in this round.</Empty></div>
          ) : (
            <div className="mt-4 space-y-3">
              {rows.map(([id, value]) => (
                <div key={id}>
                  <div className="flex items-center justify-between">
                    <span className="text-[13px]">{name(id)}</span>
                    <span className="mono text-[10px] text-muted">{value} / {total}</span>
                  </div>
                  <div className="mt-1"><Bar value={value} max={max} /></div>
                </div>
              ))}
            </div>
          )}
          <div className="mt-4 flex flex-wrap gap-1.5">
            {(digest?.votes ?? []).map((vote, index) => (
              <span key={index} className="badge !text-[9px]">{name(vote.from)} → {name(vote.to)}{vote.weight > 1 ? ` ×${vote.weight}` : ''}</span>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          {digest?.nominations && (
            <div className="card p-5">
              <p className="stat-label">Nominations</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {digest.nominations.nominees.map((id) => (
                  <span key={id} className="badge">{name(id)} · {digest.nominations?.counts[id] ?? 0}</span>
                ))}
              </div>
              <div className="mt-3 grid gap-1 sm:grid-cols-2">
                {Object.entries(digest.nominations.accusations).map(([from, to]) => (
                  <span key={`${from}-${to}`} className="text-[11px] text-muted">{name(from)} → <span className="text-parchment/85">{name(to)}</span></span>
                ))}
              </div>
            </div>
          )}
          {digest?.elimination && (
            <div className="card border-[var(--color-oxblood)]/30 p-5">
              <p className="stat-label">{methodLabel(digest.elimination.method)}</p>
              <p className="mt-1 font-display text-2xl">{name(digest.elimination.player)}</p>
              <p className="mt-1 text-[11px] text-muted">
                {Object.entries(digest.elimination.tally).map(([id, count]) => `${name(id)} ${count}`).join(' · ')}
              </p>
            </div>
          )}
          {digest?.revote?.called && (
            <div className="card p-5">
              <p className="stat-label">Revote</p>
              <p className="mt-2 text-[12px] text-muted">The first ballot tied; a restricted revote followed.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Players({
  summary,
  selected,
  setSelected,
  showRoles,
  messages,
  name,
}: {
  summary: RunSummary;
  selected: Player | null;
  setSelected: (id: string) => void;
  showRoles: boolean;
  messages: Message[] | null;
  name: (id: string | null | undefined) => string;
}) {
  const [query, setQuery] = useState('');
  const [role, setRole] = useState('all');
  const filtered = summary.players.filter((player) => {
    if (role !== 'all' && player.role !== role) return false;
    const search = query.trim().toLowerCase();
    if (!search) return true;
    return player.name.toLowerCase().includes(search) || player.id.includes(search);
  });
  const playerMessages = selected && messages
    ? messages
        .filter((message) => message.from === selected.id && (showRoles || message.channel !== 'traitor'))
        .slice(0, 5)
    : [];

  return (
    <div className="grid gap-5 lg:grid-cols-[0.9fr_1.1fr]">
      <div>
        <div className="grid gap-2 sm:grid-cols-2">
          <input className="input" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search cast…" />
          <select className="select" value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="all">All roles</option>
            <option value="faithful">Faithful</option>
            <option value="traitor">Traitor</option>
          </select>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {filtered.map((player) => (
            <button
              key={player.id}
              type="button"
              onClick={() => setSelected(player.id)}
              className={`card card-hover flex items-center gap-3 p-3 text-left ${selected?.id === player.id ? 'border-gold/60' : ''}`}
            >
              <Sigil id={player.id} name={player.name} size={36} role={showRoles ? player.role : undefined} />
              <div className="min-w-0">
                <p className="truncate text-[12px]">{player.name}</p>
                <p className="mono text-[9px] uppercase tracking-[0.1em] text-muted">
                  {showRoles ? player.role : 'role hidden'} · {player.exit ? methodLabel(player.exit.method) : 'alive'}
                </p>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="card p-5">
        {!selected ? (
          <Empty>Select a player to open their dossier.</Empty>
        ) : (
          <div>
            <div className="flex items-start gap-4">
              <Sigil id={selected.id} name={selected.name} size={64} role={showRoles ? selected.role : undefined} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="display text-3xl">{selected.name}</h3>
                  <RolePill role={selected.role} hidden={!showRoles} />
                  {selected.ambition && showRoles && <span className="badge">ambition: {selected.ambition}</span>}
                </div>
                <p className="mt-2 text-[12px] leading-5 text-muted">
                  {selected.persona?.description || 'Simulation character. Persona description is a simplified behavioural sketch, not a claim about a real person.'}
                </p>
                <p className="mt-2 font-mono text-[9px] uppercase tracking-[0.12em] text-muted">
                  {selected.exit ? `${methodLabel(selected.exit.method)} · round ${selected.exit.round}` : 'survived'}
                </p>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="public" value={selected.stats.public ?? 0} />
              <Stat label="private" value={selected.stats.private ?? 0} />
              <Stat label="votes cast" value={selected.stats.votesCast ?? 0} />
              <Stat label="accused" value={selected.stats.accusationsReceived ?? 0} />
            </div>

            {selected.persona && Object.keys(selected.persona.traits).length > 0 && (
              <div className="mt-5">
                <p className="stat-label">Persona traits</p>
                <div className="mt-2 space-y-2">
                  {Object.entries(selected.persona.traits).map(([trait, value]) => (
                    <div key={trait}>
                      <div className="flex justify-between text-[11px]">
                        <span className="text-muted">{titleCase(trait)}</span>
                        <span className="mono">{value.toFixed(2)}</span>
                      </div>
                      <div className="mt-1"><Bar value={value} max={1} /></div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {selected.stats.conversations.length > 0 && (
              <div className="mt-5">
                <p className="stat-label">Most private messages with</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {selected.stats.conversations.map((conversation) => (
                    <span key={conversation.id} className="badge">{name(conversation.id)} · {conversation.count}</span>
                  ))}
                </div>
              </div>
            )}

            {playerMessages.length > 0 && (
              <div className="mt-5">
                <p className="stat-label">Sample messages</p>
                <div className="mt-2 space-y-2">
                  {playerMessages.map((message, index) => (
                    <div key={index} className="rounded-lg border border-[var(--border)] bg-black/15 p-3">
                      <p className="font-mono text-[9px] uppercase tracking-[0.1em] text-muted">
                        round {message.r} · {channelLabel(message.channel)}
                      </p>
                      <p className="mt-1 line-clamp-4 text-[11px] leading-5 text-parchment/75">{message.text}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function MetricsPanel({ summary }: { summary: RunSummary }) {
  const quality = summary.metrics.quality;
  const similarity = quality.speech_similarity?.per_player ?? {};
  const similarityRows = Object.entries(similarity)
    .filter(([, value]) => value)
    .sort((a, b) => (b[1].content_words ?? 0) - (a[1].content_words ?? 0));
  const actionTypes = Object.entries(summary.metrics.byActionType).sort((a, b) => b[1].calls - a[1].calls);
  const maxAction = Math.max(...actionTypes.map(([, value]) => value.calls), 1);
  const eventTypes = Object.entries(summary.metrics.events.byType).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const maxEvent = Math.max(...eventTypes.map(([, value]) => value), 1);
  const chatLengths = Object.entries(quality.private_chats?.pairs_by_length ?? {}).sort((a, b) => Number(a[0]) - Number(b[0]));
  const maxChat = Math.max(...chatLengths.map(([, value]) => value), 1);

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="card p-4">
          <p className="stat-value">{formatScore(quality.hallucination_score)}</p>
          <p className="stat-label">hallucination</p>
          <p className="mt-1 text-[10px] text-muted">{quality.messages_checked ?? 0} messages checked</p>
        </div>
        <div className="card p-4">
          <p className="stat-value">{formatScore(quality.diversity?.duplication_score)}</p>
          <p className="stat-label">duplication</p>
          <p className="mt-1 text-[10px] text-muted">{quality.diversity?.distinct_texts ?? 0} distinct texts</p>
        </div>
        <div className="card p-4">
          <p className="stat-value">{quality.secrecy?.flags_total ?? 0}</p>
          <p className="stat-label">secrecy flags</p>
          <p className="mt-1 text-[10px] text-muted">checked against the role held at the time</p>
        </div>
        <div className="card p-4">
          <p className="stat-value">{summary.counts.rejectedActions}</p>
          <p className="stat-label">rejected actions</p>
          <p className="mt-1 text-[10px] text-muted">rule violations, not model errors</p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <SectionHeading eyebrow="Cost" title="Tokens and calls" detail="Absolute runtime latency and host concurrency are intentionally omitted." />
          <div className="mt-4 grid grid-cols-2 gap-2">
            <Stat label="calls" value={formatNumber(summary.metrics.calls)} />
            <Stat label="failed calls" value={formatNumber(summary.metrics.failedCalls)} />
            <Stat label="input tokens" value={formatNumber(summary.metrics.inputTokens)} />
            <Stat label="output tokens" value={formatNumber(summary.metrics.outputTokens)} />
            <Stat label="provider calls" value={formatNumber(summary.metrics.providerCalls)} />
            <Stat label="parse retries" value={formatNumber(summary.metrics.parseRetryCalls)} />
          </div>
          <div className="mt-5 space-y-2">
            {actionTypes.map(([action, value]) => (
              <div key={action}>
                <div className="flex justify-between text-[11px]">
                  <span className="text-muted">{action.replaceAll('_', ' ')}</span>
                  <span className="mono">{value.calls}</span>
                </div>
                <div className="mt-1"><Bar value={value.calls} max={maxAction} /></div>
              </div>
            ))}
          </div>
        </div>

        <div className="card p-5">
          <SectionHeading eyebrow="Conversation" title="Private chats" detail="Reciprocity separates a conversation from a broadcast." />
          <div className="mt-4 grid grid-cols-2 gap-2">
            <Stat label="reciprocity" value={formatPercent(quality.private_chats?.reciprocity_rate ?? null, 0)} />
            <Stat label="pairs" value={quality.private_chats?.pairs ?? 0} />
            <Stat label="two-way pairs" value={quality.private_chats?.two_way_pairs ?? 0} />
            <Stat label="longest thread" value={quality.private_chats?.longest_thread ?? 0} />
          </div>
          <div className="mt-5 space-y-2">
            {chatLengths.map(([length, count]) => (
              <div key={length}>
                <div className="flex justify-between text-[11px]">
                  <span className="text-muted">{length} message{length === '1' ? '' : 's'}</span>
                  <span className="mono">{count}</span>
                </div>
                <div className="mt-1"><Bar value={count} max={maxChat} tone="green" /></div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <SectionHeading eyebrow="Similarity" title="Do players sound alike?" detail="Per-player average cosine similarity to every other player. Higher means less distinct." />
          <div className="mt-4 space-y-2">
            {similarityRows.map(([id, value]) => (
              <div key={id}>
                <div className="flex justify-between text-[11px]">
                  <span className="text-muted">{id}</span>
                  <span className="mono">{formatScore(value.content_words)} / {formatScore(value.phrasing)}</span>
                </div>
                <div className="mt-1 flex gap-1">
                  <span className="flex-1"><Bar value={value.content_words} max={1} tone="red" /></span>
                  <span className="flex-1"><Bar value={value.phrasing} max={1} tone="gold" /></span>
                </div>
              </div>
            ))}
          </div>
          {(quality.speech_similarity?.content_words?.max_pair || quality.speech_similarity?.phrasing?.max_pair) && (
            <p className="mt-3 text-[11px] text-muted">
              Most similar pair: <span className="text-parchment">{quality.speech_similarity?.content_words?.max_pair?.join(' ~ ')}</span>
              {quality.speech_similarity?.content_words?.max != null && ` · content ${formatScore(quality.speech_similarity.content_words.max)}`}
              {quality.speech_similarity?.phrasing?.max != null && ` · phrasing ${formatScore(quality.speech_similarity.phrasing.max)}`}
            </p>
          )}
        </div>

        <div className="card p-5">
          <SectionHeading eyebrow="Activity" title="What the run produced" />
          <div className="mt-4 space-y-2">
            {eventTypes.map(([type, count]) => (
              <div key={type}>
                <div className="flex justify-between text-[11px]">
                  <span className="text-muted">{type.replaceAll('_', ' ').toLowerCase()}</span>
                  <span className="mono">{count}</span>
                </div>
                <div className="mt-1"><Bar value={count} max={maxEvent} tone="green" /></div>
              </div>
            ))}
          </div>
          <div className="mt-5 grid grid-cols-2 gap-2">
            <Stat label="public messages" value={summary.metrics.messages.public ?? 0} />
            <Stat label="private messages" value={summary.metrics.messages.private ?? 0} />
            <Stat label="transport retries" value={summary.metrics.transportRetries} />
            <Stat label="rejected actions" value={summary.metrics.rejectedActions} />
          </div>
        </div>
      </div>
    </div>
  );
}

function BenchmarkPanel({ summary, name }: { summary: RunSummary; name: (id: string | null | undefined) => string }) {
  const benchmark = summary.benchmark;
  if (!benchmark) return <Empty>No season benchmark was recorded for this run.</Empty>;
  const result = benchmark.result;
  const overall = result.overall;
  const componentKeys = Object.keys(overall.weights);
  const cast = new Map(summary.players.map((player) => [player.id, player]));
  const banishmentPairs = (result.banishment_alignment as { pairs?: { run_round: number | null; season_episode: number; run: string | null; season: string; matched: boolean | null }[] })?.pairs ?? [];
  const murderPairs = (result.murder_alignment as { pairs?: { run_round: number | null; season_episode: number; run: string | null; season: string; matched: boolean | null }[] })?.pairs ?? [];

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="card p-5">
          <p className="stat-value text-gold">{formatScore(overall.score)}</p>
          <p className="stat-label">overall alignment</p>
        </div>
        <div className="card p-5">
          <p className="text-sm">{benchmark.season.name}</p>
          <p className="stat-label mt-2">ground truth</p>
        </div>
        <div className="card p-5">
          <p className="text-sm">{overall.components_scored.length} / {componentKeys.length} components scored</p>
          <p className="stat-label mt-2">coverage</p>
        </div>
      </div>

      {traitorGroundTruthCaveat.has(benchmark.season.id) && (
        <div className="rounded-xl border border-gold/30 bg-gold/[0.05] p-4 text-[12px] leading-5 text-muted">
          The ground-truth file for this season is marked unverified in the repository. This score is a comparison against recorded data, not an accuracy claim.
        </div>
      )}

      <div className="card overflow-x-auto p-5">
        <SectionHeading eyebrow="Components" title="Weighted alignment" detail="Skipped components are excluded and their weight is redistributed." />
        <table className="table mt-4 min-w-[720px]">
          <thead><tr><th>Component</th><th>Weight</th><th>Score</th><th>Run</th><th>Season</th></tr></thead>
          <tbody>
            {componentKeys.map((key) => {
              const component = result[key] as { score?: number | null; run?: unknown; season?: unknown } | undefined;
              const skipped = overall.components_skipped.includes(key);
              return (
                <tr key={key}>
                  <td className="font-mono text-[10px]">{key}</td>
                  <td className="mono">{overall.weights[key].toFixed(2)}</td>
                  <td className={`mono ${skipped ? 'text-muted' : ''}`}>{skipped ? 'skipped' : formatScore(component?.score)}</td>
                  <td className="max-w-[220px] truncate text-[11px] text-muted">{formatValue(component?.run)}</td>
                  <td className="max-w-[220px] truncate text-[11px] text-muted">{formatValue(component?.season)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <SectionHeading eyebrow="Banishments" title="Elimination alignment" />
          <div className="mt-3 space-y-1.5">
            {banishmentPairs.map((pair, index) => (
              <div key={index} className="flex items-center justify-between gap-3 border-b border-[var(--border)] pb-1.5 text-[11px] last:border-0">
                <span className="text-muted">run r{pair.run_round ?? '—'} · {pair.run ? name(pair.run) : '—'}</span>
                <span className={pair.matched ? 'text-faithful' : 'text-muted'}>{pair.matched ? 'match' : 'diff'} · ep {pair.season_episode} · {pair.season ? name(pair.season) : '—'}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="card p-5">
          <SectionHeading eyebrow="Murders" title="Night alignment" />
          <div className="mt-3 space-y-1.5">
            {murderPairs.map((pair, index) => (
              <div key={index} className="flex items-center justify-between gap-3 border-b border-[var(--border)] pb-1.5 text-[11px] last:border-0">
                <span className="text-muted">run r{pair.run_round ?? '—'} · {pair.run ? name(pair.run) : '—'}</span>
                <span className={pair.matched ? 'text-faithful' : 'text-muted'}>{pair.matched ? 'match' : 'diff'} · ep {pair.season_episode} · {pair.season ? name(pair.season) : '—'}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <SectionHeading eyebrow="Overlap" title="Who the run and season removed" />
          <div className="mt-4 space-y-3">
            {[
              ['Banishments', result.banishment_overlap],
              ['Murders', result.murder_overlap],
            ].map(([label, value]) => {
              const overlap = value as { shared?: string[]; run_only?: string[]; season_only?: string[] } | undefined;
              return (
                <div key={String(label)}>
                  <p className="stat-label">{String(label)}</p>
                  <p className="mt-1 text-[11px] text-muted">shared: {overlap?.shared?.map(name).join(', ') || '—'}</p>
                  <p className="text-[11px] text-muted">run only: {overlap?.run_only?.map(name).join(', ') || '—'}</p>
                  <p className="text-[11px] text-muted">season only: {overlap?.season_only?.map(name).join(', ') || '—'}</p>
                </div>
              );
            })}
          </div>
        </div>
        <div className="card p-5">
          <SectionHeading eyebrow="Cast" title="Season vs run" />
          <div className="mt-3 max-h-72 overflow-y-auto">
            <table className="table">
              <thead><tr><th>Player</th><th>Season</th><th>Run</th></tr></thead>
              <tbody>
                {benchmark.season.cast.map((member) => {
                  const player = cast.get(member.id);
                  return (
                    <tr key={member.id}>
                      <td>{name(member.id)}</td>
                      <td className="text-[10px] text-muted">{member.role} · ep {member.exit_episode ?? '—'}</td>
                      <td className="text-[10px] text-muted">{player ? `${player.role} · ${player.exit ? `r${player.exit.round}` : 'alive'}` : 'not in run'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="stat-label">Sources</p>
          <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted">winner faction (season): {benchmark.season.winner_faction}</p>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {benchmark.season.sources.length === 0 && <span className="text-[11px] text-muted">No sources recorded in the season file.</span>}
          {benchmark.season.sources.map((source) => (
            <a key={source} className="badge hover:border-gold/50 hover:text-parchment" href={source} target="_blank" rel="noreferrer">
              {new URL(source).hostname}
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

function DiagnosisPanel({ summary }: { summary: RunSummary }) {
  if (!summary.diagnosis) return <Empty>No diagnosis file was generated for this run.</Empty>;
  return (
    <div className="space-y-4">
      {summary.diagnosis.map((section) => (
        <div key={section.title} className="card p-5">
          <h3 className="font-display text-xl">{section.title}</h3>
          <div className="mt-3 space-y-2">
            {section.lines.map((line, index) => {
              if (line.startsWith('- ')) {
                return (
                  <p key={index} className="flex gap-2 text-[12px] leading-5 text-parchment/75">
                    <span className="mt-2 h-1 w-1 flex-none rounded-full bg-gold" aria-hidden="true" />
                    {line.slice(2)}
                  </p>
                );
              }
              if (line.trimStart().startsWith('> ')) {
                return (
                  <blockquote key={index} className="border-l border-gold/40 pl-3 text-[12px] leading-5 text-muted">
                    {line.trimStart().slice(2)}
                  </blockquote>
                );
              }
              return <p key={index} className="text-[12px] leading-5 text-parchment/75">{line}</p>;
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function formatValue(value: unknown): string {
  if (value == null) return '—';
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
  if (typeof value === 'object') return JSON.stringify(value).slice(0, 120);
  return String(value);
}
