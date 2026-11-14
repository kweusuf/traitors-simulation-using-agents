import { useEffect, useMemo, useState } from 'react';
import type { Message, RunSummary } from '../lib/types';
import { loadMessages } from '../lib/load-run';
import { canSeeMessage, roleAt } from '../lib/visibility';
import { channelLabel } from '../lib/format';
import Sigil from './Sigil';

export default function BoundaryExplorer({ summary }: { summary: RunSummary }) {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [round, setRound] = useState(1);
  const [playerId, setPlayerId] = useState(summary.players[0]?.id ?? '');
  const [limit, setLimit] = useState(12);

  useEffect(() => {
    let active = true;
    loadMessages(summary.id)
      .then((data) => {
        if (active) setMessages(data ?? []);
      })
      .catch(() => {
        if (active) setMessages([]);
      });
    return () => {
      active = false;
    };
  }, [summary.id]);

  const player = summary.players.find((item) => item.id === playerId) ?? summary.players[0];
  const role = player ? roleAt(player, round) : 'faithful';
  const visible = useMemo(() => {
    if (!messages || !player) return [];
    return messages.filter(
      (message) => message.r === round && canSeeMessage(message, player, round, 'player'),
    );
  }, [messages, player, round]);

  const counts = useMemo(() => {
    const map = new Map<string, number>();
    for (const message of visible) map.set(message.channel, (map.get(message.channel) ?? 0) + 1);
    return [...map.entries()];
  }, [visible]);

  return (
    <div className="card p-5">
      <div className="grid gap-3 md:grid-cols-3">
        <label className="block">
          <span className="stat-label">Player</span>
          <select className="select mt-1" value={playerId} onChange={(event) => setPlayerId(event.target.value)}>
            {summary.players.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="stat-label">Round</span>
          <select className="select mt-1" value={round} onChange={(event) => setRound(Number(event.target.value))}>
            {Array.from({ length: summary.rounds }, (_, index) => index + 1).map((value) => (
              <option key={value} value={value}>Round {value}</option>
            ))}
          </select>
        </label>
        <div className="flex items-end">
          <div className="rounded-lg border border-[var(--border)] bg-black/15 p-3">
            <p className="stat-label">This player knows</p>
            <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.1em]">
              {role === 'traitor' ? '◆ traitor' : '◇ faithful'}
            </p>
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="badge">{visible.length} visible messages</span>
        {counts.map(([channel, count]) => (
          <span key={channel} className={`badge ${channel === 'traitor' ? 'badge-traitor' : channel === 'host' ? 'badge-gold' : ''}`}>
            {channelLabel(channel)} · {count}
          </span>
        ))}
        <span className="ml-auto font-mono text-[9px] uppercase tracking-[0.12em] text-muted">
          structural view only
        </span>
      </div>

      {player && (
        <div className="mt-4 flex items-center gap-3 rounded-lg border border-[var(--border)] bg-black/15 p-3">
          <Sigil id={player.id} name={player.name} size={38} role={role} />
          <div>
            <p className="text-[13px]">{player.name}</p>
            <p className="text-[10px] text-muted">
              {player.exit ? `${player.exit.method} in round ${player.exit.round}` : 'alive'} · viewing round {round}
            </p>
          </div>
        </div>
      )}

      <div className="mt-4 space-y-2">
        {messages == null && <p className="text-sm text-muted">Loading transcript…</p>}
        {messages && visible.length === 0 && (
          <p className="rounded-lg border border-dashed border-[var(--border)] p-4 text-sm text-muted">
            This player sees no messages in this round (or was already out).
          </p>
        )}
        {visible.slice(0, limit).map((message, index) => (
          <article key={`${message.from}-${index}`} className="message-card" data-channel={message.channel}>
            <div className="flex items-center gap-2">
              <span className={`badge ${message.channel === 'traitor' ? 'badge-traitor' : message.channel === 'host' ? 'badge-gold' : ''}`}>
                {channelLabel(message.channel)}
              </span>
              <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-muted">{message.from}</span>
              <span className="ml-auto font-mono text-[9px] text-muted">r{message.r}</span>
            </div>
            <p className="mt-1 whitespace-pre-wrap text-[12px] leading-5 text-parchment/75">{message.text}</p>
          </article>
        ))}
        {visible.length > limit && (
          <button type="button" className="btn btn-ghost !min-h-8 !px-3 !py-1 !text-[11px]" onClick={() => setLimit((value) => value + 20)}>
            Show more
          </button>
        )}
      </div>

      <p className="mt-4 text-[11px] leading-5 text-muted">
        This models channel visibility: public and host messages reach everyone,
        private messages reach sender and recipients, and the traitor channel
        reaches only players who are traitors at that moment. The real prompt also
        carries role knowledge, memory, beliefs, and a bounded transcript window.
      </p>
    </div>
  );
}
