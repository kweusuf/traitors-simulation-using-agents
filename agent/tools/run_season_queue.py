#!/usr/bin/env python3
"""Run every configured season one at a time, resuming after failures.

The OpenCode route rate-limits concurrent games, so this queue keeps exactly
one simulation process alive. A failed or rate-limited run is resumed from its
event log after a backoff; a completed run is recorded and the next season
starts immediately.

Usage (from ``agent/``)::

    uv run --no-sync python tools/run_season_queue.py --dry-run
    uv run --no-sync python tools/run_season_queue.py
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

AGENT = Path(__file__).resolve().parents[1]
RUNS = AGENT / "runs"
CONFIGS = AGENT / "configs" / "traitors"
STATE_PATH = RUNS / "season_queue_state.json"
LOG_PATH = RUNS / "season_queue.log"

# Completed seasons are skipped by the completion check. In-progress and
# not-yet-run seasons come first; seasons that exhausted their retry budget
# on the old model are re-queued at the end for another pass.
SEASONS: tuple[tuple[str, str, str], ...] = (
    ("India S01", "season_india_s01.opencode_go.yaml", "india-s01-opencode-go"),
    ("UK S01", "season_uk_s01.opencode_go.yaml", "uk-s01-opencode-go"),
    ("Australia S02", "season_australia_s02.opencode_go.yaml", "australia-s02-opencode-go"),
    ("NZ S01", "season_nz_s01.opencode_go.yaml", "nz-s01-opencode-go"),
    ("US S02", "season_us_s02.opencode_go.yaml", "us-s02-opencode-go"),
    ("US S03", "season_us_s03.opencode_go.yaml", "us-s03-opencode-go"),
    ("US S04", "season_us_s04.opencode_go.yaml", "us-s04-opencode-go"),
    ("Australia S01", "season_australia_s01.opencode_go.yaml", "australia-s01-opencode-go"),
    ("NZ S02", "season_nz_s02.opencode_go.yaml", "nz-s02-opencode-go"),
    ("US S01", "season_us_s01.opencode_go.yaml", "us-s01-opencode-go"),
)

_child: subprocess.Popen[str] | None = None


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    line = f"[{stamp()}] {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run_dir(game_id: str) -> Path:
    return RUNS / game_id


def is_complete(game_id: str) -> bool:
    metrics = run_dir(game_id) / "metrics.json"
    if not metrics.exists():
        return False
    try:
        return json.loads(metrics.read_text(encoding="utf-8")).get("status") == "completed"
    except (OSError, json.JSONDecodeError):
        return False


def has_events(game_id: str) -> bool:
    events = run_dir(game_id) / "events.jsonl"
    return events.exists() and events.stat().st_size > 0


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"completed": [], "failed": [], "attempts": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"completed": [], "failed": [], "attempts": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def command_for(config: str, game_id: str, concurrency: int) -> list[str]:
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "simulation",
        "run",
        str(CONFIGS / config),
        "--game-id",
        game_id,
        "--concurrency",
        str(concurrency),
    ]
    if has_events(game_id):
        command += ["--resume", "--idle-seconds", "5"]
    return command


def stop_child() -> None:
    global _child
    if _child is not None and _child.poll() is None:
        _child.terminate()
        try:
            _child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            _child.kill()
            _child.wait()


def handle_signal(signum: int, _frame: object) -> None:
    log(f"received signal {signum}; stopping the queue")
    stop_child()
    raise SystemExit(128 + signum)


def run_one(
    label: str,
    config: str,
    game_id: str,
    *,
    concurrency: int,
    max_attempts: int,
    backoff: int,
    poll_seconds: int,
) -> str:
    global _child
    for attempt in range(1, max_attempts + 1):
        if is_complete(game_id):
            return "completed"

        command = command_for(config, game_id, concurrency)
        run_dir(game_id).mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir(game_id) / "queue_stdout.log"
        log(f"{label}: attempt {attempt}/{max_attempts}: {' '.join(command)}")
        with stdout_path.open("a", encoding="utf-8") as stdout:
            _child = subprocess.Popen(
                command,
                cwd=AGENT,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                text=True,
            )
            while _child.poll() is None:
                time.sleep(poll_seconds)
            code = _child.returncode
        _child = None

        if code == 0 and is_complete(game_id):
            log(f"{label}: completed")
            return "completed"

        delay = min(backoff * (2 ** min(attempt - 1, 5)), 300)
        log(
            f"{label}: exited with code {code} "
            f"({'no metrics.json yet' if code == 0 else 'failed'}); "
            f"resuming in {delay}s"
        )
        time.sleep(delay)

    return "failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    parser.add_argument("--concurrency", type=int, default=4, help="calls in flight per game")
    parser.add_argument("--max-attempts", type=int, default=20, help="attempts per season")
    parser.add_argument("--backoff", type=int, default=30, help="base retry delay in seconds")
    parser.add_argument("--poll", type=int, default=10, help="process poll interval in seconds")
    args = parser.parse_args()

    RUNS.mkdir(parents=True, exist_ok=True)
    LOG_PATH.touch(exist_ok=True)
    state = load_state()

    if args.dry_run:
        for label, config, game_id in SEASONS:
            status = "completed" if is_complete(game_id) else (
                "resume" if has_events(game_id) else "fresh"
            )
            print(f"{label:15} {status:9} {game_id}")
        return 0

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log("season queue starting")
    for label, config, game_id in SEASONS:
        if is_complete(game_id):
            log(f"{label}: already completed; skipping")
            if game_id not in state["completed"]:
                state["completed"].append(game_id)
            save_state(state)
            continue

        state["attempts"][game_id] = state["attempts"].get(game_id, 0) + 1
        save_state(state)
        result = run_one(
            label,
            config,
            game_id,
            concurrency=args.concurrency,
            max_attempts=args.max_attempts,
            backoff=args.backoff,
            poll_seconds=args.poll,
        )
        if result == "completed":
            if game_id not in state["completed"]:
                state["completed"].append(game_id)
        elif game_id not in state["failed"]:
            state["failed"].append(game_id)
        save_state(state)

    log(
        f"queue finished: {len(state['completed'])} completed, "
        f"{len(state['failed'])} failed"
    )
    return 0 if not state["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
