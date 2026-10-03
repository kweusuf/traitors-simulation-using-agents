# LLM Social Simulation Framework

Configuration-driven framework for running multi-agent LLM social
simulations. First environment: a text-only game inspired by *The
Traitors*.

**Spec:** [`docs/02-framework-requirements-and-scaffolding-spec.md`](docs/02-framework-requirements-and-scaffolding-spec.md)
**Progress tracker:** [`docs/progress.md`](docs/progress.md)

**New here?** Read [Rules of the game](#rules-of-the-game) for how a game
is set up, played and won, and
[Design decisions worth knowing](#design-decisions-worth-knowing) for the
choices behind those rules and why they were made.

## Status

Milestone 1 (deterministic fake-backend game) and Milestone 2 (CLI,
run artifacts, replay tooling) are complete; see `docs/progress.md`.
One git commit per phase.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

Model selection is config-driven: the `llm:` block in the YAML config
holds the provider, model id, and model parameters.

```bash
# one game from a config (live model, progress streams to the terminal)
python -m simulation run configs/traitors/basic.yaml

# same game flow with no model at all (deterministic prompt-driven fake)
python -m simulation run configs/traitors/basic.yaml --provider fake

# N games, one deterministic seed per game
python -m simulation batch configs/traitors/basic.yaml --games 10 --seed 100 --provider fake
```

Useful flags on `run` and `batch`: `--seed`, `--game-id`, `--provider
{ollama,fake}`, `--runs-dir`, `--db`, `--quiet`.

## Rules of the game

Everything below is config-driven from the `game:` block. Defaults are
chosen so a bare config plays the plain game; every optional mechanic is
off unless a config turns it on.

### Setup

`players` and `traitors` decide the roster; `faithful` is always
`players - traitors` and is validated rather than configured. Roles are
drawn from the run seed, **or** pinned by name with `traitor_names` when
replaying a real season where the traitors are known. Every player is
also seeded a `solo` or `team` ambition; traitors keep their ambition as
a goal, so one may play to be the last traitor standing rather than to
win as a group. `personas` assigns behaviour files round-robin.

### The round

A round runs the `phases:` list in order. The default order, which the
season config extends with a third mission, is:

| Phase | What happens |
| --- | --- |
| `mission` | a team task; how many run per round is just how many times the phase is listed |
| `public_discussion` | everyone speaks in the open |
| `private_chat` | side conversations, plus a traitors-only channel |
| `round_table` | open nomination of suspects |
| `voting` | everyone votes; the most-voted player is banished |
| `elimination` | the banishment resolves and the role is revealed |
| `traitor_night` | the traitors argue on their private channel, pick a victim, and the night plays out |

An incomplete phase is always replayed from its start, so a crash during
an elimination cannot banish the same player twice.

### Winning

- **No traitors left** → the faithful win.
- **No faithful left** → the traitors win.
- **Parity** → traitors win when living traitors are at least as many as
  living faithful. This is the default and it is deliberately blunt.
- **A configured endgame replaces parity.** With `finale_traitors` and
  `finale_faithful` set, the finale opens at exactly those counts; with
  `finale_total` it opens at that many living players whatever the
  split. Once a finale is configured, parity no longer ends the game,
  which is what lets the real final five actually be reached.
- **`endgame_vote`** ends each finale round with every living player
  answering `end` (finish) or `banish` (vote again). At the final two the
  traitors win automatically.
- **`blind_finale_banishments`** keeps a finale banishment's role hidden
  until the game ends, as the show plays its last round table.
- **`max_rounds`** is a backstop: on exhaustion `round_limit_winner`
  decides, so a stalemate cannot hang.
- **`finale_max_votes`** bounds the finale itself: after that many
  consecutive rapid-fire rounds with nobody banished, `round_limit_winner`
  is declared instead. The show's final five can deadlock on a tie, and a
  run must still terminate.

### Recruitment

Recruitment is the mechanic with the most moving parts, and the part of
the rules most easily misread, so it is worth stating plainly.

- **`recruit_choice`** turns on the choice mechanic. When it is on, the
  traitors vote *recruit* or *murder* each time the night offers them
  the choice. A tie falls to murder. It fully overrides
  `recruit_on_banish`, so the two never both fire.
- **`recruit_window`** decides when that choice is on offer.
  - `true` (the default): only on the night after a traitor is banished
    by vote. The window is spent that night either way. This is the
    show's rule and the one the UK S01 configs use.
  - `false`: on every traitor night. A vacancy can be held open as long
    as the team likes and filled whenever they choose.
- **`traitor_capacity`** is a hard invariant in both modes: the living
  traitor count may never exceed the number the game dealt. A recruit can
  only ever fill a slot a banishment opened, never grow the team past
  where it started.
- **`max_recruits`** caps how many times it can happen all game (`0` is
  no cap). Without a cap the faithful can never empty the traitor team by
  voting alone.
- The offered player answers `accept` or `decline`. With a single
  traitor left the offer is an **ultimatum**: declining is fatal.
- **`recruit_on_banish`** is the older, simpler rule: a traitor banished
  at the table converts one living faithful before leaving. It is off by
  default and, on, would refill the tower on every traitor banishment,
  which makes a faithful win arithmetically impossible.

### Optional mechanics

All off by default, all independent:

- `shield` — one-shot item that blocks the next murder on its holder.
- `dagger` — one-shot item whose holder's vote counts twice.
- `seer` — one-shot item that checks one player's true role in private.
- `on_trial` — traitors nominate a murder shortlist before the kill.
- `nomination_enabled` — open nomination at the round table, keeping the
  `nomination_keep` most-nominated suspects to answer the room.
- `revote_enabled` — a restricted revote when the banishment ballot ties.
- `council_deliberation` — the night's kill is decided in two rounds:
  sequential proposals, then a simultaneous hold-or-switch where the
  majority of final picks wins.
- `discussion_budget` / `warning_turns` — a hosted discussion clock;
  `0` disables it and keeps one-turn-each phases.

### Seasonal cadence

`quiet_murder_rounds` lists rounds where the traitors do not murder at
all, and `quiet_banishment_rounds` lists rounds where the round table
votes on nobody. Both exist so a replay can match a real season's
rhythm, including episodes that were quiet for structural reasons rather
than dramatic ones.

### Pacing and prompt context

The `communication:` block caps how much each player says
(`public_messages_per_agent`, `private_messages_per_agent`) and how much
of the transcript an agent sees (`transcript_messages_per_prompt`; `0`
keeps the whole thing). These matter for model cost as much as for play,
since a 22-player game with an unbounded transcript is the single
biggest driver of token spend.

## Design decisions worth knowing

Choices that are not obvious from the code, including one claim that was
made and then withdrawn.

**The banishment window is not an artefact.** The obvious reading of the
show is that the traitors may recruit on any night they like, and that is
how later seasons play. But The Traitors UK Series 1 — the season this
project replays — made exactly two recruitment attempts, and both came
the night after one of the traitors was banished: episode 7 after Alyssa
was voted out, episode 11 after Amanda was. The `recruit_window: true`
default reproduces that. The free-choice rule is available behind
`recruit_window: false`, but it departs from the season being replayed,
so the S01 configs pin the window explicitly and say why.

**Recruitment is a faithful rule, not a balance knob.** The earlier
`recruit_on_banish` conversion would refill the tower on every traitor
banishment, making a faithful win arithmetically impossible and any
comparison with a real season meaningless. It is off in every season
config for that reason.

**A correction worth recording.** During this work it was claimed that
Series 1 had no recruitment mechanic at all, on the strength of a single
secondary article about Series 2's format changes. That was wrong:
Kieran was a recruited traitor and Series 1 made two attempts, exactly as
the ground truth in `configs/seasons/the-traitors-uk-s01.yaml` records.
The rule survived that scrutiny only because the two real events happen
to match the banishment window — a property of the season, not evidence
that the rule was right. Single-sourced claims about show format did not
get checked properly the first time.

**Traitor count is capped at the starting number, not by a round
count.** `traitor_capacity` derives from what the game dealt, so a
conversion can only refill a vacancy. Without it, a run with recruitment
on could grow the traitor team and the faithful could never catch up.

**The end vote was being decided by a hard-coded prompt default.** For a
closed-choice action the model answers with whatever value the prompt's
worked example holds, because it copies the example. The end vote's
example held `"content": "end"`, and every season run ended its finale
the same way: all five players — four faithful and the traitor alike —
answered `end`. A unanimous `end` stops the game, so a surviving traitor
takes the prize. That is the whole mechanism behind the run of traitor
wins. Measured on the real r1 final five with everything else held
constant: example `"end"` gave `end` 6 of 6, example `"banish"` gave
`banish` 4 of 4. The two recruitment answers carried the same default in
the same direction (`recruit`, `accept`).

The fix is two halves and both are needed. The response schema pins
`content` to an enum of the accepted answers (`action_schema`,
`CONTENT_CHOICES`), and the worked example shows `?`, plainly not an
answer. The enum is the half that matters: with a free `content` field the
model replies in prose, which the engine rejects and retries, and the
correction retry re-shows the example. Constrained to a token, the roles
answer for their own reasons — the traitor, which wins by stopping,
answered `end` 10 times of 10.

**The honest limit: the lean is only partly corrected.** Across three
faithful players, ten samples each, a faithful still answered `end` 22
times of 30 even though keeping a traitor alive is what loses it the
game. The hard-coded bias is gone — a final five is no longer settled by
a string in a prompt, and the game can now actually play on into
banishments instead of stopping at the first end vote — but the model's
disposition still favours ending, and a faithful majority that stops with
a traitor alive still hands it the win. An earlier six-sample probe
suggested the enum moved faithful players decisively towards `banish`;
ten samples each did not bear that out, and the smaller result was noise.
Closing that gap is prompt and behaviour work, not a mechanical bias: the
answer is no longer forced, and it is now genuinely the agents' own.

Two tests had the old behaviour baked in — they asserted the hint contains
`"content": "end"`, `"content": "recruit"` and `"content": "accept"` —
which is why it survived review. They now assert that the example holds no
answer at all, and that the schema pins the accepted tokens.

**Resume replays rather than restores.** `--resume` rebuilds the engine
from the event log instead of re-dealing, so roles, ambitions and the
board are the ones players were actually told about. Anything the log
does not record cannot be invented: an ambition missing from a legacy log
is refused rather than guessed, unless the traitors were pinned by name,
in which case the seeded draw replays exactly.

**Agent memory decays, and it is per-observer.** On by default, because an
agent that cannot remember the round table plays a different and much
weaker game. `memory_decay` and `memory_floor` give every player a memory
of the other participants that fades as the rounds pass: strength is
`salience * memory_decay ** age`, scaled by how close that observer is to
the subject. Trivia is gone in two rounds; a named accusation, a private
bequest, or a murder survives far longer. The same event therefore weighs
differently in two different heads, which is the point - a friend
remembers what you said, someone you are at odds with discounts it. Set
`agent_memory: false` for a memoryless control.

A resumed run rebuilds its memory from the event log rather than starting
amnesiac, which matters because `continue_after` clears the in-memory
event list. `EventSink.prior_events` is the accessor for the whole log.

Two rules make it safe to put in a prompt. First, nothing is deleted, so
decay is retunable without replaying a game. Second, what a player may
remember is scoped by the show, not by the log: the night murder is
public as a fact, the council that chose the victim is traitor-only, and
the motive a faithful player holds is reconstructed from public
nominations rather than from the deliberation. `TRAITOR_KILL.payload`
keys its `proposals` by traitor name, so ingesting a raw payload would
hand the whole tower to every faithful player; ingestion writes from a
per-event whitelist instead.

When a player is eliminated, anyone the room has seen at odds with them
comes under scrutiny - the feud is a motive, and it was public. That is
the one write that points suspicion at somebody still playing, which is
why it is the most likely of these to help the illegal-target problem
rather than worsen it. Design in
`docs/2026-10-01-agent-memory-design.md`.

**Most optional mechanics default to off.** A feature that changes the
game is only enabled deliberately by a config, so an existing config
keeps playing the same game when a new flag is added. Agent memory is the
one deliberate exception: it is on by default, because an agent that
cannot remember the round table plays a different and weaker game, and a
memoryless run is only wanted as an explicit control.

## Resuming a crashed run

A long replay can die on a transport timeout and take hours of model
calls with it. `--resume` continues the run from its own event log
instead of starting over:

```
uv run python -m simulation run configs/traitors/season_uk_s01.r2.yaml \
  --game-id uk-s01-r2 --resume
```

The log is truncated back to its last completed phase and the engine is
rebuilt from what remains: roles, board, items, the recruitment count and
window, and the seeded ambitions all come from the log rather than being
redealt. A phase that never finished is redone from its start, so a crash
during an elimination cannot banish the same player twice.

Resume refuses to touch a log written in the last five minutes, since
that usually means the original run is still going. Pass
`--idle-seconds N` to shorten that wait when you have just stopped the
run yourself and are resuming deliberately:

```bash
uv run python -m simulation run configs/traitors/season_uk_s01.r2.yaml \
  --game-id uk-s01-r2 --resume --idle-seconds 5
```

Before each night kill the living traitors get one message on a channel no
faithful can read, to argue who dies and whose name takes the blame, and at
the round table they are told to build the case against a specific
innocent.

Each game writes `runs/<game_id>/`:

```text
config.yaml       the config actually used (seed resolved)
events.jsonl      append-only event log, the replay source of truth
game.json         final game state
transcript.json   structured messages and eliminations
transcript.txt    human-readable narrative
metrics.json      outcome, experiment identity, LLM ops, quality signals
llm_calls.jsonl   one line per model call: tokens in/out, latency, retries
```

`metrics.json` gives each run a 360 degree view of the model
operations: input and output tokens, latency percentiles, transport
retries, per-action call breakdown, failed turns, and a quality block
computed from that run's own messages (hallucination score against the
game record, secrecy violations, a duplication score, cross-player
speech similarity on content and phrasing, parse failures).

## Inspect

```bash
python -m simulation list-games
python -m simulation replay game-001            # reconstruct from events
python -m simulation replay game-001 --json
python -m simulation inspect game-001 --agent alice   # one agent's view
python -m simulation snapshot game-001 --round 1      # stored snapshot
python -m simulation metrics game-001           # LLM ops + quality view
python -m simulation metrics game-001 --json    # raw metrics.json
python -m simulation metrics game-001 --recompute  # rebuild quality from events
python -m simulation compare game-001 game-002  # gate one run against another
python -m simulation diagnose game-001          # write runs/game-001/diagnosis.md
python -m simulation benchmark game-008 --season configs/seasons/the-traitors-uk-s01.yaml
```

`compare` scores a candidate against a baseline on paired seeds with per
metric gates (hallucination, duplication, speech similarity, secrecy,
parse failures, then latency and tokens as cost guards) and exits 1 on
any breach, so a prompt change can be promoted or rejected on evidence.
Every finished run also appends a line to `runs/ledger.jsonl`, and seeds
where `seed % 10 >= 7` are marked as holdout so they never drive a
promotion decision.

## Replay a real season

`configs/traitors/season_uk_s01.yaml` recreates The Traitors (UK) Series 1:
22 players with personas written from the cast's on-screen behaviour
(`configs/personas/uk_s01/`), the three original traitors pinned by name
(`game.traitor_names`), the finale counted at three faithful against two
traitors to match the real final five, and the season's quiet rounds
reproduced. Recruitment follows what the season actually did — two
attempts, one declined and one by ultimatum, each the night after a
traitor was banished — via `recruit_choice` with `recruit_window: true`
and `max_recruits: 2`, while the automatic `recruit_on_banish`
conversion stays off because it would refill the tower every time and
make a faithful win impossible. The ground truth for it lives in
`configs/seasons/the-traitors-uk-s01.yaml`, and
`simulation benchmark <game_id> --season <file>` scores a finished run
against it on twelve components, writing `benchmark.md` into the run
directory: outcome, traitor roster, banishment and murder alignment (the
run's k-th elimination against the season's k-th, so episodes with no
elimination are skipped rather than counted as misses), banishment and
murder set overlap with precision, recall and F1, traitor hit rate,
survival curve, final counts, finale, recruitments and exit order.
Shield-blocked attempts are listed but not counted as murders.

## Test

```bash
pytest
```
