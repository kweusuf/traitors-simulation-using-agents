# LLM Social Simulation Framework

Configuration-driven framework for running multi-agent LLM social
simulations. First environment: a text-only game inspired by *The
Traitors*.

**Spec:** `02-framework-requirements-and-scaffolding-spec.md`
**Progress tracker:** `docs/progress.md`

## Status

Early scaffold. Implementation proceeds in phases tracked in
`docs/progress.md`; one git commit per phase.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
python -m simulation run configs/traitors/basic.yaml
```

Not yet implemented: see Phase 11 in `docs/progress.md`.

## Test

```bash
pytest
```
