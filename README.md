# LLM Social Simulation Framework

Configuration-driven framework for running multi-agent LLM social
simulations. First environment: a text-only game inspired by *The
Traitors*.

**Spec:** `02-framework-requirements-and-scaffolding-spec.md`
**Progress tracker:** `docs/progress.md`

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

### Resuming a crashed run

A long replay can die on a transport timeout and take hours of model
calls with it. `--resume` continues the run from its own event log
instead of starting over:

```
uv run python -m simulation run configs/traitors/season_uk_s01.r2.yaml \
  --game-id uk-s01-r2 --resume
```

The log is truncated back to its last completed phase and the engine is
rebuilt from what remains: roles, board, items, recruitment count and
the seeded ambitions all come from the log rather than being redealt. A
phase that never finished is redone from its start, so a crash during an
elimination cannot banish the same player twice.

Resume refuses to touch a log written in the last five minutes, since
that usually means the original run is still going.

Game rules are config-driven as well: `game.players`, `game.traitors`,
`game.max_rounds`, the `phases:` ordering, seasonal cadence
(`quiet_murder_rounds` and `quiet_banishment_rounds` skip a night or a
round table, the show never murders on night one), the endgame
(`finale_total` starts the finale at that many living players whatever
the split, `finale_traitors`/`finale_faithful` still work as a pair,
`endgame_vote` adds the end-or-banish-again vote and
`blind_finale_banishments` hides finale-time banishments until the game
ends), recruitment
(`game.recruit_choice` makes the traitors choose recruit or murder,
with the offer answered by the target and a lone traitor able to force
it as an ultimatum; `game.recruit_on_banish` is the older automatic
conversion; `game.max_recruits` caps how many times it can happen). Traitors carry
a seeded solo or team ambition: they can win alone as the last traitor
standing or together as a team. Before each night kill the living
traitors get one message on a channel no faithful can read, to argue
who dies and whose name takes the blame, and at the round table they
are told to build the case against a specific innocent.

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
traitors to match the real final five, and recruitment off because the
season never refilled the tower by conversion. The ground truth for it
lives in `configs/seasons/the-traitors-uk-s01.yaml`, and
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
