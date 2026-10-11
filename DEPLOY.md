# Deployment guide: Docker/Podman + native Ollama (remote)

This guide covers bundling the simulation into a container, deploying to a remote machine running Podman with native Ollama, triggering remotely from local, monitoring progress, and pulling run artifacts back.

## Prerequisites
- Podman (or Docker) installed locally.
- Podman installed on remote machine (Linux recommended). Native Ollama running on remote.
- SSH access to remote (key-based auth recommended). rsync available locally and on remote.
- Required models pulled in native Ollama on remote (`ollama pull <model>`).

## Quick start (Option 1: registry + SSH)
1. Build and push locally:
```bash
podman build -t traitors-sim:latest .
podman tag traitors-sim:latest registry.example/traitors-sim:latest
podman push registry.example/traitors-sim:latest
```

2. Run remotely (host network, native Ollama at localhost:11434):
```bash
ssh USER@REMOTE_HOST 'podman run --rm --network host \
  -v /path/to/runs:/app/runs \
  -v /path/to/configs:/app/configs:ro \
  registry.example/traitors-sim:latest run configs/traitors/basic.yaml \
    --provider ollama --base-url http://localhost:11434 --model <model>'
```

3. Monitor. The run above is attached, so progress streams to the terminal. To follow a detached run instead, use its logs, or tail its event log (the game id is printed at start, and the JSONL log flushes per event):
```bash
ssh USER@REMOTE_HOST 'podman logs -f <container>'
# or tail events
ssh USER@REMOTE_HOST 'tail -f /path/to/runs/<game_id>/events.jsonl'
```

4. Pull artifacts back after completion:
```bash
rsync -avz --exclude="*.tmp" USER@REMOTE_HOST:/path/to/runs/ ./runs/
```

## Quick start (Option 2: save/load over SSH)
1. Build and save locally:
```bash
podman build -t traitors-sim:latest .
podman save -o traitors-sim.tar traitors-sim:latest
```

2. Transfer and load on remote:
```bash
scp traitors-sim.tar USER@REMOTE_HOST:/tmp/traitors-sim.tar
ssh USER@REMOTE_HOST 'podman load -i /tmp/traitors-sim.tar && rm /tmp/traitors-sim.tar'
```

3. Run remotely as in Option 1 (using local image name/tag loaded on remote).

## Monitoring and artifacts
- CLI streams progress to stdout (game start/rounds/phases/eliminations/win). When run attached over SSH, you see live progress; the image sets `PYTHONUNBUFFERED=1`, so a detached run's `podman logs -f` streams too.
- Artifacts are written under mounted `runs/` on remote: per run folder `events.jsonl`, `metrics.json`, `llm_calls.jsonl`, `game.db` (folder named by `game_id`), plus `ledger.jsonl` at the runs root.
- After completion, pull back with rsync to local `./runs/` so local tools (replay/inspect/compare) can read them.

## Smoke test (deterministic, no Ollama)
```bash
podman run --rm --network host \
  -v $(pwd)/runs:/app/runs \
  -v $(pwd)/configs:/app/configs:ro \
  traitors-sim:latest run configs/traitors/basic.yaml \
    --provider fake --quiet
```

## Notes
- Use placeholders (registry.example, USER, REMOTE_HOST, paths, <model>). Never commit machine-specific details.
- Host networking: on Linux use `--network host` so container reaches native Ollama at `localhost:11434`. For macOS/Windows Docker Desktop, host networking is limited.
- Rootless Podman may require SELinux volume labels (`:Z/:z`) depending on filesystem.
- The mounted `runs/` directory must be writable by the container user (uid 1000). A fresh bind mount is created root-owned, and the run fails at its first write; `chown 1000:1000 <runs_dir>` on the host, or run with `--userns=keep-id`.
- The `configs/` mount is required, not optional: the season YAML is read from it, and each config resolves its personas against the sibling `configs/personas/` directory. Without the mount a run either fails to find its config or silently drops its personas.
- Size `--concurrency` to the host. The default is 2 in-flight model calls; set above what the host serves, native Ollama queues the extra calls into the request timeout instead of erroring. The `--provider fake` smoke test checks wiring, not capacity.
- Keep `base_url` out of tracked configs; pass via `--base-url` or use `.local.yaml` (gitignored).
- Example orchestration script: see `deploy_remote.example.sh` (copy and customize locally; do not commit with real values).

## Trigger from local, monitor, pull back (end-to-end)
1. **Build** locally: `podman build -t traitors-sim:latest .`
2. **Transfer** to remote: choose registry push/pull (Option 1) or save/load over SSH (Option 2).
3. **Run remotely** (trigger): `ssh USER@REMOTE_HOST 'podman run -d --name traitors-run --network host -v /path/to/runs:/app/runs -v /path/to/configs:/app/configs:ro traitors-sim:latest run configs/traitors/basic.yaml --provider ollama --base-url http://localhost:11434 --model <model>'`
4. **Monitor** from local: `ssh USER@REMOTE_HOST 'podman logs -f traitors-run'` or `ssh USER@REMOTE_HOST 'tail -f /path/to/runs/<game_id>/events.jsonl'` (game_id is printed at start). CLI streams progress lines.
5. **Wait for completion**: container exits when simulation finishes (`podman wait traitors-run` or stop following logs).
6. **Pull artifacts back**: `rsync -avz --exclude="*.tmp" USER@REMOTE_HOST:/path/to/runs/ ./runs/`
7. **Cleanup (optional)**: `ssh USER@REMOTE_HOST 'podman rm traitors-run'` (auto if `--rm` not used; with `-d` consider cleanup).

Use `deploy_remote.example.sh` as a starting point (copy locally, replace placeholders, never commit).
