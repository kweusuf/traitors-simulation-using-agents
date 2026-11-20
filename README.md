# Traitors simulation monorepo

Two independent projects share this repository:

| Path | What it is |
|---|---|
| [`agent/`](agent/) | The Python multi-agent LLM simulation framework: game engine, configs, personas, runs, metrics, and CLI. |
| [`web/`](web/) | A static Astro site that turns completed runs into an interactive showcase. Deployed on Vercel. |

## Agent

All Python commands run from `agent/`, because `runs/` and `configs/` are
resolved relative to the working directory.

```bash
cd agent
uv sync --all-extras --dev
uv run --no-sync python -m pytest -q
uv run --no-sync python -m simulation run configs/traitors/basic.yaml --provider fake
```

See [`agent/README.md`](agent/README.md) for the full documentation. The
game mechanics and how they map to the television show are documented in
[`agent/docs/2026-11-19-traitors-mechanics-and-drift.md`](agent/docs/2026-11-19-traitors-mechanics-and-drift.md);
the shipped `season_*.opencode_go.yaml` configs enable the show-faithful
rules (final-five endgame, missions/prize pot, recruitment, items).

## Web

The site reads sanitized JSON generated from `agent/runs/`. The exporter lives
in `web/tools/` and runs in the agent environment:

```bash
cd agent
uv run --no-sync python ../web/tools/export_site_data.py \
  --runs-dir runs --configs-dir configs --out ../web/src/data --check
```

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web dev
pnpm --dir web build
```

See [`web/README.md`](web/README.md) for deployment and data details.

## Rules

- `agent/runs/` contains local artifacts and is never committed.
- `web/src/data/` contains only sanitized, public data.
- No LAN addresses, local paths, model digests, API keys, or host-specific
  performance numbers may be published.
- `AGENTS.md` is local and untracked; keep machine-specific notes there.
