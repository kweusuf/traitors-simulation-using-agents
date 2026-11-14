import { describe, expect, it } from 'vitest';
import type { Message, Player } from '../src/lib/types';
import { canSeeMessage, roleAt, visibleMessages, wasAliveAtStart } from '../src/lib/visibility';

function player(overrides: Partial<Player> = {}): Player {
  return {
    id: 'aaron',
    name: 'Aaron',
    role: 'faithful',
    outcome: 'lost',
    items: [],
    roleByRound: Object.fromEntries(
      Array.from({ length: 5 }, (_, index) => [String(index + 1), 'faithful']),
    ),
    aliveByRound: Object.fromEntries(
      Array.from({ length: 5 }, (_, index) => [String(index + 1), true]),
    ),
    stats: {
      public: 0,
      private: 0,
      traitor: 0,
      host: 0,
      accusationsMade: 0,
      accusationsReceived: 0,
      votesCast: 0,
      votesReceived: 0,
      conversations: [],
    },
    ...overrides,
  };
}

const messages: Message[] = [
  { r: 1, phase: 'round_table', channel: 'public', from: 'aaron', to: [], text: 'public' },
  { r: 1, phase: 'private_chat', channel: 'private', from: 'aaron', to: ['alyssa'], text: 'mine' },
  { r: 1, phase: 'private_chat', channel: 'private', from: 'alyssa', to: ['imran'], text: 'not mine' },
  { r: 1, phase: 'traitor_night', channel: 'traitor', from: 'imran', to: ['meryl'], text: 'secret' },
  { r: 1, phase: 'round_table', channel: 'host', from: 'host', to: [], text: 'host' },
];

describe('visibility', () => {
  it('shows public and host messages to everyone', () => {
    const aaron = player();
    expect(canSeeMessage(messages[0], aaron, 1, 'player')).toBe(true);
    expect(canSeeMessage(messages[4], aaron, 1, 'player')).toBe(true);
  });

  it('shows private messages only to sender and recipients', () => {
    const aaron = player();
    expect(canSeeMessage(messages[1], aaron, 1, 'player')).toBe(true);
    expect(canSeeMessage(messages[2], aaron, 1, 'player')).toBe(false);
  });

  it('hides the traitor channel from faithful players', () => {
    const aaron = player();
    const imran = player({
      id: 'imran',
      role: 'traitor',
      roleByRound: { '1': 'traitor', '2': 'traitor', '3': 'traitor', '4': 'traitor', '5': 'traitor' },
    });
    expect(canSeeMessage(messages[3], aaron, 1, 'player')).toBe(false);
    expect(canSeeMessage(messages[3], imran, 1, 'player')).toBe(true);
  });

  it('switches role at the recruitment round', () => {
    const recruited = player({
      id: 'theo',
      role: 'traitor',
      roleByRound: { '1': 'faithful', '2': 'faithful', '3': 'traitor', '4': 'traitor', '5': 'traitor' },
    });
    expect(roleAt(recruited, 2)).toBe('faithful');
    expect(roleAt(recruited, 3)).toBe('traitor');
  });

  it('gives a dead player no view after their exit round', () => {
    const dead = player({ exit: { round: 2, method: 'vote' } });
    expect(wasAliveAtStart(dead, 2)).toBe(true);
    expect(wasAliveAtStart(dead, 3)).toBe(false);
    expect(canSeeMessage(messages[0], dead, 3, 'player')).toBe(false);
  });

  it('play-along mode hides the traitor channel but keeps public traffic', () => {
    const aaron = player();
    expect(visibleMessages(messages, aaron, 1, 'play').map((message) => message.channel)).toEqual([
      'public',
      'private',
      'private',
      'host',
    ]);
  });

  it('audience mode exposes every channel', () => {
    const aaron = player();
    expect(visibleMessages(messages, aaron, 1, 'audience')).toHaveLength(messages.length);
  });
});
