#!/usr/bin/env python3
"""Run the traitor-bias A/B arms with one fixed model.

Sequential, resumable screening harness for the arms in
``configs/traitors/season_uk_s01.bias_*.yaml`` (plus the control). It runs
one game at a time, a fixed seed set shared across arms (paired design), and
never switches models: a rate limit is backoff, not a rotation, because a
mixed-model comparison would confound the lever with the model.

Usage (from ``agent/``)::

    uv run --no-sync python tools/run_bias_experiments.py --dry-run
    uv run --no-sync python tools/run_bias_experiments.py \
        --model iceman-contributor-free --effort none --games 1 \
        --arms control,ledger,evidence,rand

Then report with::

    uv run --no-sync python tools/win_rate_report.py --include bias-
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

AGENT = Path(__file__).resolve().parents[1]
CONFIGS = AGENT / "configs" / "traitors"
RUNS = AGENT / "runs"
STATE_PATH = RUNS / "bias_experiments_state.json"
LOG_PATH = RUNS / "bias_experiments.log"

sys.path.insert(0, str(AGENT / "src"))
from simulation.experiments.win_rate import format_report, load_outcomes  # noqa: E402

# One lever per arm; the control is the unmodified season config.
ARMS: tuple[tuple[str, str], ...] = (
    ("control", "season_uk_s01.opencode_go.yaml"),
    ("ledger", "season_uk_s01.bias_ledger.yaml"),
    ("evidence", "season_uk_s01.bias_evidence.yaml"),
    ("audit", "season_uk_s01.bias_audit.yaml"),
    ("votesignal", "season_uk_s01.bias_votesignal.yaml"),
    ("rand", "season_uk_s01.bias_rand.yaml"),
    ("nomandate", "season_uk_s01.bias_nomandate.yaml"),
)

NETWORK_MARKERS = (
    "connection to ",
    "errno 8",
    "nodename nor servname",
    "temporary failure in name resolution",
    "name resolution",
    "connection reset",
    "connection aborted",
    "connection refused",
    "network is unreachable",
    "eof occurred",
)
RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "quota",
    "usage limit",
    "free usage",
)
UNAVAILABLE_MARKERS = (
    "model_not_found",
    "model not found",
    "no such model",
    "does not exist",
    "trains on request data",
)

_child: subprocess.Popen | None = None


def log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run_dir(game_id: str) -> Path:
    return RUNS / game_id


def is_complete(game_id: str) -> bool:
    path = run_dir(game_id) / "metrics.json"
    if not path.exists():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("status") == "completed"
    except (OSError, json.JSONDecodeError):
        return False


def has_events(game_id: str) -> bool:
    path = run_dir(game_id) / "events.jsonl"
    return path.exists() and path.stat().st_size > 0


def load_state() -> dict[str, Any]:
    empty: dict[str, Any] = {"runs": {}, "attempts": {}}
    if not STATE_PATH.exists():
        return empty
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    data.setdefault("runs", {})
    data.setdefault("attempts", {})
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def classify(path: Path, offset: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            text = handle.read().lower()
    except OSError:
        return "other"
    if any(marker in text for marker in NETWORK_MARKERS):
        return "network"
    if any(marker in text for marker in RATE_LIMIT_MARKERS):
        return "rate_limit"
    if any(marker in text for marker in UNAVAILABLE_MARKERS):
        return "unavailable"
    return "other"


def run_one(
    arm: str,
    config: str,
    rep: int,
    *,
    model: str,
    effort: str,
    concurrency: int,
    seed: int,
    tag: str,
    max_attempts: int,
    backoff: int,
    poll: int,
) -> str:
    global _child
    game_id = f"{tag}-{arm}-r{rep}"
    attempt = 0
    network_retries = 0
    while attempt < max_attempts:
        attempt += 1
        if is_complete(game_id):
            return "completed"
        command = [
            "uv", "run", "--no-sync", "python", "-m", "simulation", "run",
            str(CONFIGS / config),
            "--game-id", game_id,
            "--concurrency", str(concurrency),
            "--model", model,
            "--reasoning-effort", effort,
            "--seed", str(seed),
        ]
        if has_events(game_id):
            command += ["--resume", "--idle-seconds", "5"]
        run_dir(game_id).mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir(game_id) / "queue_stdout.log"
        start = stdout_path.stat().st_size if stdout_path.exists() else 0
        log(f"{arm} r{rep}: attempt {attempt}/{max_attempts} on {model}")
        with stdout_path.open("a", encoding="utf-8") as stdout:
            _child = subprocess.Popen(
                command, cwd=AGENT, stdout=stdout, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"}, text=True,
            )
            while _child.poll() is None:
                time.sleep(poll)
            code = _child.returncode
        _child = None
        if code == 0 and is_complete(game_id):
            log(f"{arm} r{rep}: completed")
            return "completed"
        kind = classify(stdout_path, start)
        if kind == "unavailable":
            log(f"{arm} r{rep}: model unavailable ({model}); aborting this arm")
            return "aborted"
        if kind == "network":
            network_retries += 1
            if network_retries > 120:
                log(f"{arm} r{rep}: no network after {network_retries} retries; giving up")
                return "failed"
            delay = min(backoff * (2 ** min(network_retries - 1, 5)), 300)
            log(f"{arm} r{rep}: network down; retrying in {delay}s (not an attempt)")
            time.sleep(delay)
            attempt -= 1
            continue
        delay = min(backoff * (2 ** min(attempt - 1, 5)), 300)
        log(f"{arm} r{rep}: exited {code} ({kind}) on {model}; resuming in {delay}s")
        time.sleep(delay)
    return "failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=False, default="", help="fixed model for every run")
    parser.add_argument("--effort", default="none", help="reasoning effort for that model")
    parser.add_argument("--games", type=int, default=1, help="games per arm")
    parser.add_argument("--arms", default="", help="comma-separated arm names (default: all)")
    parser.add_argument("--concurrency", type=int, default=1, help="calls in flight")
    parser.add_argument("--seed-base", type=int, default=1, help="seed for rep 1; reps increment")
    parser.add_argument("--tag", default="biasp", help="game-id prefix")
    parser.add_argument("--max-attempts", type=int, default=6)
    parser.add_argument("--backoff", type=int, default=30)
    parser.add_argument("--poll", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    selected = ARMS
    if args.arms:
        wanted = {name.strip() for name in args.arms.split(",") if name.strip()}
        selected = tuple((name, config) for name, config in ARMS if name in wanted)
        if not selected:
            parser.error(f"no known arms in {args.arms!r}")
    if not args.dry_run and not args.model:
        parser.error("--model is required unless --dry-run")

    RUNS.mkdir(parents=True, exist_ok=True)
    plan = [
        (arm, config, rep, args.seed_base + rep - 1)
        for arm, config in selected
        for rep in range(1, args.games + 1)
    ]
    if args.dry_run:
        for arm, config, rep, seed in plan:
            status = "completed" if is_complete(f"{args.tag}-{arm}-r{rep}") else "pending"
            print(f"{arm:12} r{rep} seed={seed:<4} {status:9} {config}")
        return 0

    try:
        import signal
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    except (ImportError, ValueError):
        pass

    LOG_PATH.touch(exist_ok=True)
    state = load_state()
    log(f"bias experiments: model={args.model} effort={args.effort} games/arm={args.games}")
    try:
        for arm, config, rep, seed in plan:
            game_id = f"{args.tag}-{arm}-r{rep}"
            if is_complete(game_id):
                log(f"{arm} r{rep}: already completed; skipping")
                state["runs"][game_id] = "completed"
                save_state(state)
                continue
            state["attempts"][game_id] = state["attempts"].get(game_id, 0) + 1
            save_state(state)
            result = run_one(
                arm, config, rep,
                model=args.model, effort=args.effort, concurrency=args.concurrency,
                seed=seed, tag=args.tag, max_attempts=args.max_attempts,
                backoff=args.backoff, poll=args.poll,
            )
            state["runs"][game_id] = result
            save_state(state)
            if result == "aborted":
                break
    except KeyboardInterrupt:
        log("interrupted; stopping experiments")

    report = format_report(load_outcomes(RUNS, include=[args.tag]), by=["model", "pinned"])
    log("report:\n" + report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
