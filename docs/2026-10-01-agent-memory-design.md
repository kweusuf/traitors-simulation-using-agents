# Agent memory: decaying, relationship-weighted, per-observer

**Status:** implemented (`agent_memory`, default off). `TRAITOR_KILL` is
traitor-only and a faithful player's motive is reconstructed from the
public record, as agreed. The animosity effect went in as option (a): a
room-wide attention shift toward a living player.
**Motivation:** the illegal-target failure (`target 'alex' is not legal`,
naming an already-eliminated player) is not a missing-information problem.
Every prompt already carries `Alive players:`, `Eliminated:` and
`Legal targets:`. The model names a dead player because they are the most
recently-discussed name, and writing a plausible continuation beats
consulting a constraint list. The fix is to make the memory that drives
those continuations forget the trivia and keep the weight.

## What exists and is dead

`ShortTermMemory`, `Relationships` and `Beliefs` are all implemented and
none of them is ever written to or read outside tests. `Agent.remember()`
has no callers in the game. The prompt's "Your recent memories:" block has
never rendered in a single run. `retrieve()` also takes an empty query
from the runtime, so its keyword path could never run.

This is a wiring job with the data structures already in place, not a
green-field build.

## Information scoping (the part that must not be wrong)

What an agent may remember is a function of what it could have observed.

| Event | Traitors remember | Faithful remember |
|---|---|---|
| `TRAITOR_KILL` | the victim **and who chose them** (`proposals`/`final`) | nothing: this payload names the traitors and must never reach them |
| `PLAYER_ELIMINATED` (`night`) | victim, plus the council that chose them from `TRAITOR_KILL` | victim, and *that the traitors did it*, plus any motive reconstructable from public nominations |
| `PLAYER_ELIMINATED` (`vote`) | full `votes` map | full `votes` map |
| `NOMINATION_TALLY` | `accusations` map and `counts` | same |
| `VOTE_CAST` | voter to target | same |
| `ACCUSE` (public message) | accuser to nominee, with the reason text | same |
| `ULTIMATUM_ISSUED`, `RECRUIT_*` | participants only | nothing |
| `PRIVATE_MESSAGE` | sender and recipients only | sender and recipients only |

The leak risk is concrete: `TRAITOR_KILL.payload` keys `proposals` and
`final` by traitor name. Ingestion must write from a whitelist per event
type, never from the raw payload.

The night row deserves a note, because it is the row that is easy to get
wrong in the other direction. A faithful player does learn that somebody
was murdered, and that the traitors did it — that is public the moment
the body is announced. What they do not get is *who argued for it* or
*why*. The "why" a faithful player holds is reconstructed by
`_public_motive` from nominations already in the public log, so it reads
as group consensus ("they had been put up at the round table three
times") because it genuinely is, without naming a traitor or a council.
An earlier draft of this table said faithful players remember nothing at
all from a night kill; that was wrong and would have made the murder
unmotivated in their heads.

## Salience

Deterministic, from the event type and cheap text signals. No LLM calls:
at 22 players over 12 rounds an LLM judge per message is not affordable
and would not be reproducible.

| Kind | Base | Text lift |
|---|---|---|
| ultimatum, recruitment offer/accept | 5.0 | "if I'm eliminated", "remember", "don't trust" |
| murder, elimination | 4.0 | |
| accusation, rebuttal | 3.0 | accusation language |
| nomination tally, vote, tie, revote | 2.0 | |
| public message, private message | 1.0 | pointed/hostile wording |

## Decay, and why it is computed on read

```
strength = salience × decay ** (now_round - event_round) × relationship_factor
```

`decay` defaults to 0.6. Trivia at 1.0 is below the floor within two
rounds; a named accusation at 3.0 survives eight or more. Nothing is
deleted, so the full history stays inspectable in the event log and decay
can be retuned without re-running anything.

## Per-observer weighting: the part that is actually new

The same event means different things to different agents, so the
weight cannot live on the event.

`relationship_factor(observer, subject)` comes from the observer's
`Relationships` state, which is itself derived from observable behaviour
rather than assumed:

- `trust` rises when two players talk privately, defend each other, or
  vote the same way.
- `suspicion` rises on accusation and on voting against.
- Animosity is high suspicion combined with low trust.

A close relationship means the subject's own words carry weight — this is
the "if I'm eliminated, remember this" case. Animosity means they carry
none, which is the "I didn't care for them" case. An observer who is
indifferent is the default, and their memories of that player fade
quickest.

## Animosity becomes attention (the fourth case)

When a player is eliminated, anyone who was publicly at odds with them
becomes the centre of discussion: the feud is a motive, so the room starts
looking at the survivor. This is a fact about how the room sees a living
player, so it is written to **every** observer, not only to those who held
the grudge.

The one piece whose shape was put to the user before building, since it
is a social mechanic rather than a memory. As agreed, it went in as the
room-wide version: on elimination of `P`, every living agent records that
any player with documented animosity toward `P` is now under scrutiny,
weighted by how public that animosity was. `public_rivals` derives the
animosity from nominations and ballots, so every observer agrees.

It is also the one part that should *help* the illegal-target problem:
it moves suspicion toward someone who is still in the game.

## Wiring

Ingestion is driven from the event stream in the runner, writing only to
agents allowed to see each event, and runs before the next phase's
prompts are built. The existing router `visible_to` filter is the
authority on visibility for messages; the event-type whitelist above is
the authority for derived facts.

`tests/security/test_information_isolation.py` must stay green: the
traitor-night scoping is a new path for hidden information to escape and
that test is the guard.
