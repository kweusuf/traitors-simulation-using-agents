#!/usr/bin/env python3
"""Run every configured season one at a time, resuming after failures.

The OpenCode route rate-limits concurrent games, so this queue keeps exactly
one simulation process alive. A failed or rate-limited run is resumed from its
event log after a backoff; a completed run is recorded and the next season
starts immediately.

When a model hits its quota (HTTP 429 / usage limit) the queue advances to the
next model in ``MODELS`` and retries the same season there, rather than idling
on an exhausted model. The order is the free models first, then the paid
``deepseek-v4.1-flash``. The chosen model is persisted so a restarted queue
continues past the models already exhausted.

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

# Season run order. Completed seasons are skipped by the completion check,
# so a re-run of the set simply starts the first not-yet-completed one.
SEASONS: tuple[tuple[str, str, str], ...] = (
    ("India S01", "season_india_s01.opencode_go.yaml", "india-s01-opencode-go"),
    ("UK S01", "season_uk_s01.opencode_go.yaml", "uk-s01-opencode-go"),
    ("Australia S01", "season_australia_s01.opencode_go.yaml", "australia-s01-opencode-go"),
    ("Australia S02", "season_australia_s02.opencode_go.yaml", "australia-s02-opencode-go"),
    ("NZ S01", "season_nz_s01.opencode_go.yaml", "nz-s01-opencode-go"),
    ("NZ S02", "season_nz_s02.opencode_go.yaml", "nz-s02-opencode-go"),
    ("US S01", "season_us_s01.opencode_go.yaml", "us-s01-opencode-go"),
    ("US S02", "season_us_s02.opencode_go.yaml", "us-s02-opencode-go"),
    ("US S03", "season_us_s03.opencode_go.yaml", "us-s03-opencode-go"),
    ("US S04", "season_us_s04.opencode_go.yaml", "us-s04-opencode-go"),
)

# Free models first, each with its own daily quota, then the paid model.
# Only these ids are served by the Go endpoint; the longer public catalog
# is not all present here. The effort is chosen per model so the request
# never sends a level the model rejects ("none" omits the field).
MODELS: tuple[tuple[str, str], ...] = (
    ("beam-free", "low"),
    ("hy4-flash-free", "high"),
    ("iceman-contributor-free", "none"),
    ("longcat-2.5-free", "none"),
    ("deepseek-v4.1-flash", "low"),
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
)
# A dropped network or a failed DNS lookup is not the season's fault, so it
# must not consume the attempt budget: a ten-minute outage once burned
# twenty attempts on every in-flight season and marked them failed.
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


def classify_failure(path: Path, offset: int) -> str:
    """Classify one attempt's failure from the output it wrote this attempt."""
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


def load_state() -> dict[str, Any]:
    empty = {"completed": [], "failed": [], "attempts": {}, "model_index": 0}
    if not STATE_PATH.exists():
        return empty
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    data.setdefault("model_index", 0)
    return data


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def command_for(
    config: str,
    game_id: str,
    concurrency: int,
    model: str,
    effort: str,
    faithful: bool = False,
) -> list[str]:
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
        "--model",
        model,
        "--reasoning-effort",
        effort,
    ]
    if faithful:
        command += ["--voting-record", "--faithful-mandate"]
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
    state: dict[str, Any],
    concurrency: int,
    max_attempts: int,
    backoff: int,
    poll_seconds: int,
    faithful: bool = False,
    free_only: bool = False,
) -> str:
    global _child
    attempt = 0
    network_retries = 0
    while attempt < max_attempts:
        attempt += 1
        if is_complete(game_id):
            return "completed"

        model, effort = MODELS[state["model_index"]]
        command = command_for(
            config, game_id, concurrency, model, effort, faithful
        )
        run_dir(game_id).mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir(game_id) / "queue_stdout.log"
        start_offset = stdout_path.stat().st_size if stdout_path.exists() else 0
        log(
            f"{label}: attempt {attempt}/{max_attempts} on {model}: "
            f"{' '.join(command)}"
        )
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
            log(f"{label}: completed on {model}")
            return "completed"

        # An exhausted quota is a reason to change model, not to wait.
        kind = classify_failure(stdout_path, start_offset)
        model_limit = len(MODELS) - 1 if free_only else len(MODELS)
        if (
            kind in {"rate_limit", "unavailable"}
            and state["model_index"] + 1 < model_limit
        ):
            previous = model
            state["model_index"] += 1
            save_state(state)
            upcoming = MODELS[state["model_index"]][0]
            log(
                f"{label}: {kind} on {previous}; switching to {upcoming} "
                "and retrying"
            )
            time.sleep(5)
            continue

        if kind == "network":
            network_retries += 1
            if network_retries > 120:
                log(
                    f"{label}: still no network after {network_retries} "
                    f"retries; giving up on {game_id}"
                )
                break
            delay = min(backoff * (2 ** min(network_retries - 1, 5)), 300)
            log(
                f"{label}: network unavailable on {model}; retrying in "
                f"{delay}s (not consuming the attempt budget)"
            )
            time.sleep(delay)
            attempt -= 1  # a dropped network is not the season's fault
            continue

        delay = min(backoff * (2 ** min(attempt - 1, 5)), 300)
        log(
            f"{label}: exited with code {code} on {model} "
            f"({'no metrics.json yet' if code == 0 else 'failed'}); "
            f"resuming in {delay}s"
        )
        time.sleep(delay)

    return "failed"


def _wait_for_tag(tag: str, expected: int, poll_seconds: int) -> None:
    """Block until another tagged queue has finished its season set."""
    path = RUNS / f"season_queue_state.{tag}.json"
    log(f"waiting for tag '{tag}' to finish before starting ({path.name})")
    deadline = time.time() + 48 * 3600
    while time.time() < deadline:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        done = len(set(data.get("completed", [])) | set(data.get("failed", [])))
        if done >= expected:
            log(f"tag '{tag}' finished ({done}/{expected}); starting")
            return
        time.sleep(poll_seconds)
    log(f"tag '{tag}' did not finish within 48h; starting anyway")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    parser.add_argument("--concurrency", type=int, default=4, help="calls in flight per game")
    parser.add_argument("--max-attempts", type=int, default=20, help="attempts per season")
    parser.add_argument("--backoff", type=int, default=30, help="base retry delay in seconds")
    parser.add_argument("--poll", type=int, default=10, help="process poll interval in seconds")
    parser.add_argument(
        "--tag",
        default="",
        help="namespace suffix for run ids and separate state/log (parallel marathons)",
    )
    parser.add_argument(
        "--start-model-index",
        type=int,
        default=0,
        help="model index a fresh tagged queue starts at (0 = the first model)",
    )
    parser.add_argument(
        "--random-traitors",
        action="store_true",
        help="use the season_*.rand.opencode_go.yaml configs (no pinned traitors)",
    )
    parser.add_argument(
        "--wait-for-tag",
        default="",
        help="wait until this tagged queue's state is finished before starting",
    )
    parser.add_argument(
        "--faithful",
        action="store_true",
        help="enable the faithful package (voting record + faithful mandate)",
    )
    parser.add_argument(
        "--free-only",
        action="store_true",
        help="never advance to the paid model; back off when free models are limited",
    )
    args = parser.parse_args()
    if args.start_model_index < 0 or args.start_model_index >= len(MODELS):
        parser.error(f"--start-model-index must be between 0 and {len(MODELS) - 1}")

    global STATE_PATH, LOG_PATH
    suffix = f"-{args.tag}" if args.tag else ""
    if args.tag:
        STATE_PATH = RUNS / f"season_queue_state.{args.tag}.json"
        LOG_PATH = RUNS / f"season_queue.{args.tag}.log"
    state_existed = STATE_PATH.exists()

    RUNS.mkdir(parents=True, exist_ok=True)
    LOG_PATH.touch(exist_ok=True)
    state = load_state()
    if not state_existed and args.start_model_index:
        state["model_index"] = args.start_model_index
        save_state(state)

    seasons = [
        (label, config, f"{game_id}{suffix}")
        for label, config, game_id in SEASONS
    ]
    if args.random_traitors:
        seasons = [
            (label, config.replace(".opencode_go.yaml", ".rand.opencode_go.yaml"), game_id)
            for label, config, game_id in seasons
        ]
        missing = [config for _, config, _ in seasons if not (CONFIGS / config).exists()]
        if missing:
            parser.error("missing random configs: " + ", ".join(missing))

    if args.dry_run:
        for label, config, game_id in seasons:
            status = "completed" if is_complete(game_id) else (
                "resume" if has_events(game_id) else "fresh"
            )
            print(f"{label:15} {status:9} {game_id}")
        return 0

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log("season queue starting")
    log(f"tag: {args.tag or '(default)'} | run ids suffix: {suffix or '(none)'}")
    log(
        "starting model: "
        + MODELS[state["model_index"]][0]
        + " | plan: "
        + " -> ".join(model for model, _ in MODELS)
    )
    if args.wait_for_tag:
        _wait_for_tag(args.wait_for_tag, len(SEASONS), 60)
    for label, config, game_id in seasons:
        if is_complete(game_id):
            log(f"{label}: already completed; skipping")
            if game_id not in state["completed"]:
                state["completed"].append(game_id)
            if game_id in state["failed"]:
                state["failed"].remove(game_id)
            save_state(state)
            continue

        state["attempts"][game_id] = state["attempts"].get(game_id, 0) + 1
        save_state(state)
        result = run_one(
            label,
            config,
            game_id,
            state=state,
            concurrency=args.concurrency,
            max_attempts=args.max_attempts,
            backoff=args.backoff,
            poll_seconds=args.poll,
            faithful=args.faithful,
            free_only=args.free_only,
        )
        if result == "completed":
            if game_id not in state["completed"]:
                state["completed"].append(game_id)
            if game_id in state["failed"]:
                state["failed"].remove(game_id)
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
