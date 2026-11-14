import type { Message, Player } from './types';

export type ViewMode = 'audience' | 'play' | 'player';

export function roleAt(player: Player, round: number): 'faithful' | 'traitor' {
  return player.roleByRound[String(round)] ?? player.role;
}

export function wasAliveAtStart(player: Player, round: number): boolean {
  return !player.exit || player.exit.round >= round;
}

export function canSeeMessage(
  message: Message,
  player: Player,
  round: number,
  mode: ViewMode,
): boolean {
  if (mode === 'audience') return true;
  if (mode === 'play') return message.channel !== 'traitor';
  if (!wasAliveAtStart(player, round)) return false;
  if (message.channel === 'public' || message.channel === 'host') return true;
  if (message.channel === 'private') {
    return message.from === player.id || message.to.includes(player.id);
  }
  if (message.channel === 'traitor') {
    return roleAt(player, round) === 'traitor';
  }
  return false;
}

export function visibleMessages(
  messages: Message[],
  player: Player,
  round: number,
  mode: ViewMode,
): Message[] {
  return messages.filter(
    (message) => message.r === round && canSeeMessage(message, player, round, mode),
  );
}

export function playersSpeaking(
  messages: Message[],
  round: number,
): { id: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const message of messages) {
    if (message.r !== round) continue;
    counts.set(message.from, (counts.get(message.from) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([id, count]) => ({ id, count }))
    .sort((a, b) => b.count - a.count);
}
