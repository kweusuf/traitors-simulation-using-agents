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
