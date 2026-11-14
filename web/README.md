# Round Table showcase

Static Astro site for the completed simulation runs. It is built from sanitized
JSON in `src/data`, not from `agent/runs/` directly.

## Develop

```bash
pnpm install --frozen-lockfile
pnpm dev
```

## Regenerate data

The exporter lives here but runs in the agent environment:

```bash
pnpm data:export
# equivalent:
cd ../agent
uv run --no-sync python ../web/tools/export_site_data.py \
  --runs-dir runs --configs-dir configs --out ../web/src/data --check
```

## Check and build

```bash
pnpm check        # astro check + tsc
pnpm check:data   # schema + sensitive-string scan
pnpm build
```

## Deploy

Vercel project root: `web/`. Framework preset: Astro. Install:
`pnpm install --frozen-lockfile`. Build: `pnpm build`. Output: `dist`.

The site is fully static and does not run Python at build time. Regenerate and
commit `src/data` before deploying.
