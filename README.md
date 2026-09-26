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

Game rules are config-driven as well: `game.players`, `game.traitors`,
`game.max_rounds`, the `phases:` ordering, recruitment
(`game.recruit_on_banish` converts a living faithful player when the
round table banishes a traitor; `game.max_recruits` caps it, 0 means
no cap), and the finale (`game.finale_traitors` / `game.finale_faithful`
stop normal play at that split and let rapid-fire voting decide;
`game.finale_max_votes` bounds a vote that keeps tying). Traitors carry
a seeded solo or team ambition: they can win alone as the last traitor
standing or together as a team.

Each game writes `runs/<game_id>/`:

```text
config.yaml       the config actually used (seed resolved)
events.jsonl      append-only event log, the replay source of truth
game.json         final game state
transcript.json   structured messages and eliminations
transcript.txt    human-readable narrative
metrics.json      outcome, activity counts, experiment identity
```

## Inspect

```bash
python -m simulation list-games
python -m simulation replay game-001            # reconstruct from events
python -m simulation replay game-001 --json
python -m simulation inspect game-001 --agent alice   # one agent's view
python -m simulation snapshot game-001 --round 1      # stored snapshot
```

## Test

```bash
pytest
```
