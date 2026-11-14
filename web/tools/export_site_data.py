#!/usr/bin/env python3
"""Export sanitized run artifacts for the static showcase site.

Usage (from ``agent/``)::

    uv run --no-sync python ../web/tools/export_site_data.py \
        --runs-dir runs --configs-dir configs --out ../web/src/data --check

The exporter is the only path from ``agent/runs/`` to ``web/src/data/``. It
deliberately drops host-specific measurements (absolute latency, wall-clock
duration, in-flight concurrency) and every local endpoint.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import yaml

from simulation.experiments.replay import load_events
from simulation.experiments.season import benchmark_run, load_season

# ----------------------------------------------------------------------
# Policy
# ----------------------------------------------------------------------

FORBIDDEN_KEYS = {
    "base_url",
    "api_key_env",
    "duration_seconds",
    "in_flight_peak",
    "latency_ms",
    "latency_ms_total",
    "failures",
    "model_parameters",  # rebuilt as a small allowlist below
}

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
LOCAL_PATHS = re.compile(r"(?:/Users/|/Volumes/|/home/|/private/|[A-Za-z]:\\\\|/tmp/)")
DIGEST = re.compile(r"\b(?:sha256:)?[0-9a-f]{64}\b", re.IGNORECASE)
LOCAL_HOST = re.compile(r"\b[a-z0-9][a-z0-9.-]*\.(?:local|lan|internal)\b", re.IGNORECASE)
API_KEY = re.compile(r"\b(?:sk-|ghp_|glpat-|xox[baprs]-)[A-Za-z0-9_\-]{12,}\b")
RUNTIME_METRIC = re.compile(
    r"\b(?:latency|duration|in[_-]?flight|throughput|tok/s)[\w\s-]*?[:=]\s*[^\s,;)]+",
    re.IGNORECASE,
)

SECRET_PATTERNS = (
    ("ipv4", IPV4),
    ("local-path", LOCAL_PATHS),
    ("digest", DIGEST),
    ("local-host", LOCAL_HOST),
    ("api-key", API_KEY),
    ("base-url", re.compile(r"base_url", re.IGNORECASE)),
    ("api-key-name", re.compile(r"api_key", re.IGNORECASE)),
    ("sha256", re.compile(r"sha256:", re.IGNORECASE)),
    ("runtime-metric", re.compile(
        r"\b(?:latency|duration|in[_-]?flight|throughput|tok/s)[\w\s-]*?[:=]",
        re.IGNORECASE,
    )),
)

DISPLAY_EVENT_TYPES = {
    "ITEM_AWARDED",
    "SHIELD_BLOCKED",
    "DAGGER_USED",
    "HOST_WARNING",
    "DEBATE_CLOSED",
    "NOMINATION_TALLY",
    "REVOTE_CALLED",
    "REVOTE_RESOLVED",
    "COUNCIL_PROPOSAL",
    "COUNCIL_VERDICT",
    "RECRUIT_CHOICE_MADE",
    "RECRUIT_OFFERED",
    "RECRUIT_ACCEPTED",
    "RECRUIT_DECLINED",
    "ULTIMATUM_ISSUED",
    "ROLE_RECRUITED",
    "PLAYER_ELIMINATED",
    "TRAITOR_KILL",
    "MURDER_SKIPPED",
    "BANISHMENT_SKIPPED",
    "END_VOTE_CAST",
    "FINALE_STARTED",
    "GAME_WON",
}

CONFIG_FLAGS = (
    "max_rounds",
    "recruit_on_banish",
    "max_recruits",
    "recruit_choice",
    "recruit_window",
    "agent_memory",
    "traitor_mandate",
    "traitor_role_goals",
    "language",
    "finale_traitors",
    "finale_faithful",
    "finale_total",
    "finale_max_votes",
    "quiet_murder_rounds",
    "quiet_banishment_rounds",
    "endgame_vote",
    "blind_finale_banishments",
    "discussion_budget",
    "warning_turns",
    "nomination_enabled",
    "revote_enabled",
    "nomination_keep",
    "council_deliberation",
    "shield",
    "dagger",
    "seer",
    "on_trial",
)


# ----------------------------------------------------------------------
# Sanitization
# ----------------------------------------------------------------------


def sanitize_text(value: str) -> str:
    """Remove endpoint, path, key, and host-performance shapes from one string."""
    value = IPV4.sub("HOST_A", value)
    value = LOCAL_PATHS.sub("[local-path]", value)
    value = DIGEST.sub("[digest]", value)
    value = LOCAL_HOST.sub("[host]", value)
    value = API_KEY.sub("[redacted]", value)
    value = RUNTIME_METRIC.sub("[runtime-metric-omitted]", value)
    return value


def sanitize(value: Any) -> Any:
    """Recursively sanitize a JSON-like structure."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {sanitize_text(str(k)): sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    return value


def etype(event: Any) -> str:
    kind = getattr(event, "type", None)
    return kind.value if hasattr(kind, "value") else str(kind)


def compact(value: Any, limit: int = 2000) -> Any:
    """Trim very long strings before they reach the browser."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit].rstrip() + "…"
    if isinstance(value, dict):
        return {k: compact(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v, limit) for v in value]
    return value


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def pretty_name(player_id: str) -> str:
    return " ".join(part.capitalize() for part in player_id.replace("-", "_").split("_"))


def family_for(run_id: str) -> str:
    match = re.match(r"^(uk|us|india|nz|australia)-s\d+", run_id)
    if match:
        return match.group(0)
    if run_id.startswith("smoke"):
        return "smoke"
    if run_id.startswith("game-"):
        return "small games"
    return "other"


def channel_for(message: dict[str, Any]) -> str:
    sender = str(message.get("sender", ""))
    phase = str(message.get("phase", ""))
    channel = str(message.get("channel", "public"))
    if sender == "host":
        return "host"
    if channel == "public":
        return "public"
    if phase == "traitor_night":
        return "traitor"
    if channel == "system":
        return "host"
    return "private"


def load_personas(configs_dir: Path, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map player id -> persona payload from the config's persona list."""
    game = config.get("game", {}) if isinstance(config, dict) else {}
    entries = game.get("personas") or []
    personas: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, str):
            continue
        rel = entry if entry.endswith(".yaml") else f"{entry}.yaml"
        path = configs_dir / "personas" / rel
        data = read_yaml(path)
        if not data:
            continue
        player_id = entry.split("/")[-1]
        if player_id in personas:
            continue
        description = str(data.get("description") or "").strip()
        # A placeholder description is just the player id or the raw name.
        if description.lower() == player_id.lower() or len(description) < 4:
            description = ""
        personality = data.get("personality") or {}
        traits = {
            str(k): float(v)
            for k, v in personality.items()
            if isinstance(v, (int, float))
        }
        goals = data.get("goals") or {}
        persona: dict[str, Any] = {"traits": traits}
        if description:
            persona["description"] = description
        if isinstance(goals, dict) and goals:
            persona["goals"] = goals
        personas[player_id] = persona
    return personas


def parse_diagnosis(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("## "):
            if current:
                sections.append(current)
            current = {"title": raw[3:].strip(), "lines": []}
            continue
        if current is None:
            continue
        line = raw.rstrip()
        if line:
            current["lines"].append(line)
    if current:
        sections.append(current)
    return sections or None


def benchmark_score_from_markdown(path: Path) -> float | None:
    if not path.exists():
        return None
    match = re.search(r"overall alignment:\s*([0-9.]+)", path.read_text(encoding="utf-8"))
    return float(match.group(1)) if match else None


# ----------------------------------------------------------------------
# Builders
# ----------------------------------------------------------------------


def build_roles(
    player_ids: Iterable[str],
    final_roles: dict[str, str],
    events: list[Any],
    rounds: int,
) -> dict[str, dict[str, str]]:
    initial = dict(final_roles)
    recruited: dict[str, int] = {}
    for event in events:
        kind = etype(event)
        payload = event.payload or {}
        if kind == "ROLE_ASSIGNED":
            role = str(payload.get("role") or "").lower()
            if role in {"faithful", "traitor"}:
                initial[str(event.actor)] = role
        elif kind == "ROLE_RECRUITED":
            recruited[str(event.actor)] = int(event.round)

    out: dict[str, dict[str, str]] = {}
    for player_id in player_ids:
        role = initial.get(player_id, "faithful")
        per_round: dict[str, str] = {}
        for round_number in range(1, rounds + 1):
            if player_id in recruited and recruited[player_id] <= round_number:
                role = "traitor"
            per_round[str(round_number)] = role
        out[player_id] = per_round
    return out


def build_deaths(
    player_ids: Iterable[str],
    exits: dict[str, dict[str, Any]],
    rounds: int,
) -> dict[str, dict[str, bool]]:
    out: dict[str, dict[str, bool]] = {}
    for player_id in player_ids:
        exit_info = exits.get(player_id)
        exit_round = int(exit_info["round"]) if exit_info else None
        out[player_id] = {
            str(round_number): exit_round is None or exit_round > round_number
            for round_number in range(1, rounds + 1)
        }
    return out


def build_rounds(
    events: list[Any],
    missions: list[dict[str, Any]],
    rounds: int,
) -> list[dict[str, Any]]:
    digests: dict[int, dict[str, Any]] = {
        round_number: {
            "r": round_number,
            "missions": 0,
            "items": [],
            "hostWarnings": 0,
            "votes": [],
            "endVotes": [],
        }
        for round_number in range(1, rounds + 1)
    }

    for mission in missions:
        round_number = int(mission.get("round_number") or 0)
        if round_number in digests and mission.get("completed"):
            digests[round_number]["missions"] += 1

    for event in events:
        round_number = int(getattr(event, "round", 0) or 0)
        if round_number not in digests:
            continue
        kind = etype(event)
        payload = event.payload or {}
        actor = str(event.actor) if event.actor else None
        targets = [str(t) for t in (event.targets or [])]
        digest = digests[round_number]

        if kind == "ITEM_AWARDED":
            digest["items"].append({"item": payload.get("item"), "holder": actor})
        elif kind == "HOST_WARNING":
            digest["hostWarnings"] += 1
        elif kind == "NOMINATION_TALLY":
            digest["nominations"] = {
                "nominees": payload.get("nominees", []),
                "counts": payload.get("counts", {}),
                "accusations": payload.get("accusations", {}),
            }
        elif kind == "REVOTE_CALLED":
            digest.setdefault("revote", {})["called"] = True
        elif kind == "REVOTE_RESOLVED":
            digest.setdefault("revote", {})["resolved"] = payload.get("counts", payload)
        elif kind == "COUNCIL_PROPOSAL":
            council = digest.setdefault("council", {"proposals": []})
            council["proposals"].append(
                {
                    "by": actor,
                    "target": payload.get("target") or (targets[0] if targets else None),
                    "reason": payload.get("reason"),
                }
            )
        elif kind == "COUNCIL_VERDICT":
            digest.setdefault("council", {"proposals": []})["verdict"] = payload
        elif kind == "TRAITOR_KILL":
            digest.setdefault("murder", {})["victim"] = (
                targets[0] if targets else payload.get("target")
            )
            if digest.get("council") or payload.get("proposals"):
                digest.setdefault("council", {"proposals": []})["verdict"] = {
                    "final": payload.get("final"),
                    "dissent": payload.get("dissent", []),
                    "unanimous": payload.get("unanimous"),
                    "tiebreak": payload.get("tiebreak"),
                }
        elif kind == "MURDER_SKIPPED":
            digest.setdefault("murder", {})["skipped"] = True
        elif kind == "RECRUIT_CHOICE_MADE":
            digest.setdefault("recruit", {})["choice"] = payload.get("choice")
        elif kind == "RECRUIT_OFFERED":
            recruit = digest.setdefault("recruit", {})
            recruit["target"] = payload.get("target") or (targets[0] if targets else None)
            recruit["by"] = payload.get("by")
        elif kind == "RECRUIT_ACCEPTED":
            digest.setdefault("recruit", {})["accepted"] = True
        elif kind == "RECRUIT_DECLINED":
            digest.setdefault("recruit", {})["declined"] = True
        elif kind == "ULTIMATUM_ISSUED":
            recruit = digest.setdefault("recruit", {})
            recruit["ultimatum"] = True
            recruit["ultimatumTarget"] = payload.get("target") or (
                targets[0] if targets else None
            )
        elif kind == "VOTE_CAST":
            if targets:
                digest["votes"].append(
                    {
                        "from": actor,
                        "to": targets[0],
                        "weight": payload.get("weight") or 1,
                    }
                )
        elif kind == "PLAYER_ELIMINATED":
            digest["elimination"] = {
                "player": actor,
                "method": payload.get("method"),
                "tally": payload.get("votes", {}),
            }
        elif kind == "BANISHMENT_SKIPPED":
            digest["banishmentSkipped"] = True
        elif kind == "END_VOTE_CAST":
            digest["endVotes"].append({"player": actor, "choice": payload.get("choice")})
        elif kind == "FINALE_STARTED":
            digest["finaleStarted"] = True
        elif kind == "SHIELD_BLOCKED":
            digest["shieldBlocked"] = True
        elif kind == "DAGGER_USED":
            digest["daggerUsed"] = True

    # Stable order; proposals before votes.
    return [digests[round_number] for round_number in range(1, rounds + 1)]


def build_messages(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for raw in transcript.get("messages", []):
        channel = channel_for(raw)
        text = str(raw.get("content") or "")
        if not text:
            continue
        messages.append(
            {
                "r": int(raw.get("round") or 0),
                "phase": str(raw.get("phase") or ""),
                "channel": channel,
                "from": str(raw.get("sender") or ""),
                "to": [str(r) for r in (raw.get("recipients") or [])],
                "text": text[:4000],
            }
        )
    return messages


def build_player_stats(
    events: list[Any],
    messages: list[dict[str, Any]],
    player_ids: list[str],
) -> dict[str, dict[str, Any]]:
    stats = {
        player_id: {
            "public": 0,
            "private": 0,
            "traitor": 0,
            "host": 0,
            "accusationsMade": 0,
            "accusationsReceived": 0,
            "votesCast": 0,
            "votesReceived": 0,
            "conversations": Counter(),
        }
        for player_id in player_ids
    }

    for message in messages:
        sender = message["from"]
        channel = message["channel"]
        if sender in stats:
            stats[sender][channel] = stats[sender].get(channel, 0) + 1
        if channel != "private":
            continue
        peers = message["to"]
        for peer in peers:
            if sender in stats and peer in stats and sender != peer:
                key = tuple(sorted((sender, peer)))
                stats[sender]["conversations"][key] += 1

    for event in events:
        kind = etype(event)
        payload = event.payload or {}
        if kind == "NOMINATION_TALLY":
            for accuser, target in (payload.get("accusations") or {}).items():
                if accuser in stats:
                    stats[accuser]["accusationsMade"] += 1
                if target in stats:
                    stats[target]["accusationsReceived"] += 1
        elif kind == "VOTE_CAST":
            actor = str(event.actor) if event.actor else None
            targets = [str(t) for t in (event.targets or [])]
            if actor in stats:
                stats[actor]["votesCast"] += 1
            if targets and targets[0] in stats:
                stats[targets[0]]["votesReceived"] += 1

    out: dict[str, dict[str, Any]] = {}
    for player_id, values in stats.items():
        conversations = [
            {
                "id": pair[1] if pair[0] == player_id else pair[0],
                "count": count,
            }
            for pair, count in values["conversations"].most_common(6)
        ]
        out[player_id] = {
            **{k: values[k] for k in (
                "public",
                "private",
                "traitor",
                "host",
                "accusationsMade",
                "accusationsReceived",
                "votesCast",
                "votesReceived",
            )},
            "conversations": conversations,
        }
    return out


def build_players(
    game: dict[str, Any],
    metrics: dict[str, Any],
    config: dict[str, Any],
    configs_dir: Path,
    events: list[Any],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rounds = int(metrics.get("rounds") or game.get("round_number") or 0)
    player_map = game.get("players") or {}
    player_ids = list(player_map.keys())
    final_roles = {pid: str((game.get("roles") or {}).get(pid, "faithful")) for pid in player_ids}
    role_by_round = build_roles(player_ids, final_roles, events, rounds)
    personas = load_personas(configs_dir, config)

    exits: dict[str, dict[str, Any]] = {}
    for elimination in metrics.get("eliminations") or []:
        pid = str(elimination.get("player"))
        if pid:
            exits[pid] = {
                "round": int(elimination.get("round") or 0),
                "method": str(elimination.get("method") or "unknown"),
            }
    alive_by_round = build_deaths(player_ids, exits, rounds)
    stats = build_player_stats(events, messages, player_ids)
    outcomes = metrics.get("outcomes") or {}
    ambitions = game.get("ambitions") or {}
    items = game.get("items") or {}

    players: list[dict[str, Any]] = []
    for pid in player_ids:
        role = str(final_roles.get(pid, "faithful"))
        persona = personas.get(pid)
        entry: dict[str, Any] = {
            "id": pid,
            "name": str(player_map[pid].get("name") or pretty_name(pid)),
            "role": role,
            "ambition": ambitions.get(pid),
            "outcome": str(outcomes.get(pid, "unknown")),
            "items": list(items.get(pid) or []),
            "roleByRound": role_by_round[pid],
            "aliveByRound": alive_by_round[pid],
            "stats": stats.get(pid, {}),
        }
        if pid in exits:
            entry["exit"] = {
                "round": exits[pid]["round"],
                "method": exits[pid]["method"],
            }
            # Attach the tally that decided the exit, when the event carries it.
            for event in events:
                if etype(event) == "PLAYER_ELIMINATED" and str(event.actor) == pid:
                    entry["exit"]["tally"] = (event.payload or {}).get("votes", {})
                    break
        if persona:
            entry["persona"] = persona
        players.append(entry)
    return players


def public_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    llm = metrics.get("llm") or {}
    quality = metrics.get("quality") or {}
    messages = metrics.get("messages") or {}
    events = metrics.get("events") or {}
    return sanitize(
        {
            "calls": llm.get("calls", 0),
            "failedCalls": llm.get("failed_calls", 0),
            "providerCalls": llm.get("provider_calls", 0),
            "retries": llm.get("retries", 0),
            "transportRetries": llm.get("transport_retries", 0),
            "parseRetryCalls": llm.get("parse_retry_calls", 0),
            "inputTokens": llm.get("input_tokens", 0),
            "outputTokens": llm.get("output_tokens", 0),
            "totalTokens": llm.get("total_tokens", 0),
            "promptCharsTotal": llm.get("prompt_chars_total", 0),
            "byActionType": llm.get("by_action_type", {}),
            "model": llm.get("model"),
            "provider": llm.get("provider"),
            "quality": quality,
            "messages": messages,
            "events": {
                "total": events.get("total", 0),
                "byType": events.get("by_type", {}),
            },
            "rejectedActions": metrics.get("rejected_actions", 0),
            "outcomes": metrics.get("outcomes", {}),
            "survivingTraitors": metrics.get("surviving_traitors", []),
            "winner": metrics.get("winner"),
            "winningTeam": metrics.get("winning_team"),
            "finale": metrics.get("finale", False),
            "soloTraitorWin": metrics.get("solo_traitor_win", False),
            "seed": metrics.get("random_seed"),
            "experimentId": metrics.get("experiment_id"),
            "rounds": metrics.get("rounds"),
            "players": metrics.get("players"),
            "traitors": metrics.get("traitors"),
        }
    )


def build_summary(
    run_dir: Path,
    configs_dir: Path,
    flagship: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metrics = read_json(run_dir / "metrics.json")
    game = read_json(run_dir / "game.json")
    config = read_yaml(run_dir / "config.yaml")
    game_cfg = config.get("game", {}) if isinstance(config, dict) else {}
    llm_cfg = config.get("llm", {}) if isinstance(config, dict) else {}

    transcript_path = run_dir / "transcript.json"
    transcript = read_json(transcript_path) if transcript_path.exists() else {"messages": []}
    events_path = run_dir / "events.jsonl"
    events = load_events(events_path) if events_path.exists() else []

    run_id = run_dir.name
    rounds = int(metrics.get("rounds") or game.get("round_number") or 0)
    messages = build_messages(transcript)
    players = build_players(
        game, metrics, config, configs_dir, events, messages
    )
    round_digests = build_rounds(
        events, game.get("missions") or [], rounds
    )
    public = public_metrics(metrics)

    model_parameters = {
        key: llm_cfg.get(key)
        for key in ("temperature", "max_tokens", "reasoning_effort")
        if llm_cfg.get(key) is not None
    }
    flags = {
        key: game_cfg.get(key)
        for key in CONFIG_FLAGS
        if game_cfg.get(key) is not None
    }

    benchmark = None
    benchmark_score = benchmark_score_from_markdown(run_dir / "benchmark.md")
    if flagship and flagship.get("season_file"):
        season_path = configs_dir / str(flagship["season_file"])
        try:
            season = load_season(season_path)
            result = benchmark_run(events, season)
            benchmark = sanitize(
                {
                    "season": dataclasses.asdict(season),
                    "result": result,
                    "overall": result.get("overall", {}).get("score"),
                }
            )
            benchmark_score = result.get("overall", {}).get("score", benchmark_score)
        except Exception as exc:  # a missing/invalid season file must not kill the site
            print(f"  ! benchmark skipped for {run_id}: {exc}", file=sys.stderr)
            benchmark = None

    diagnosis = parse_diagnosis(run_dir / "diagnosis.md")

    winner = metrics.get("winner") or game.get("winner")
    winning_team = metrics.get("winning_team") or game.get("winning_team")
    outcome = {
        "winner": winner,
        "winningTeam": winning_team,
        "reason": "",
        "finale": bool(metrics.get("finale") or game.get("finale")),
        "solo": bool(metrics.get("solo_traitor_win")),
        "survivingTraitors": metrics.get("surviving_traitors", []),
    }
    for event in events:
        if etype(event) == "GAME_WON":
            outcome["reason"] = str((event.payload or {}).get("reason") or "")
            break

    message_counts = metrics.get("messages") or {}
    channel_counts = Counter(message["channel"] for message in messages)

    summary: dict[str, Any] = {
        "id": run_id,
        "slug": flagship.get("slug") if flagship else None,
        "label": flagship.get("label") if flagship else None,
        "blurb": flagship.get("blurb") if flagship else None,
        "game": str(game_cfg.get("name") or metrics.get("experiment_id") or run_id),
        "season": (
            benchmark["season"]["id"]
            if benchmark
            else str(game_cfg.get("name") or "")
        ),
        "family": family_for(run_id),
        "flagship": bool(flagship),
        "seed": metrics.get("random_seed"),
        "holdout": int(metrics.get("random_seed") or 0) % 10 >= 7,
        "players": players,
        "rounds": rounds,
        "roundDigests": round_digests,
        "outcome": outcome,
        "metrics": public,
        "benchmark": benchmark,
        "benchmarkScore": benchmark_score,
        "diagnosis": diagnosis,
        "counts": {
            "messages": sum(message_counts.get(k, 0) for k in ("public", "private")),
            "public": message_counts.get("public", 0),
            "private": message_counts.get("private", 0),
            "traitor": channel_counts.get("traitor", 0),
            "host": channel_counts.get("host", 0),
            "calls": public.get("calls", 0),
            "inputTokens": public.get("inputTokens", 0),
            "outputTokens": public.get("outputTokens", 0),
            "totalTokens": public.get("totalTokens", 0),
            "rejectedActions": public.get("rejectedActions", 0),
        },
        "config": {
            "name": str(game_cfg.get("name") or run_id),
            "players": int(game_cfg.get("players") or len(players)),
            "traitors": int(game_cfg.get("traitors") or 0),
            "faithful": int(game_cfg.get("faithful") or 0),
            "maxRounds": int(game_cfg.get("max_rounds") or rounds),
            "language": str(game_cfg.get("language") or "english"),
            "provider": str(llm_cfg.get("provider") or ""),
            "model": str(llm_cfg.get("model") or ""),
            "modelParameters": model_parameters,
            "flags": flags,
        },
        "hasMessages": bool(messages),
        "hasBenchmark": benchmark is not None or benchmark_score is not None,
        "hasDiagnosis": diagnosis is not None,
    }
    return sanitize(summary), messages


def manifest_row(summary: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    quality = summary["metrics"].get("quality") or {}
    diversity = quality.get("diversity") or {}
    secrecy = quality.get("secrecy") or {}
    similarity = quality.get("speech_similarity") or {}
    private = quality.get("private_chats") or {}
    first_public = next((m for m in messages if m["channel"] == "public"), None)
    eliminations = [
        {
            "id": player["id"],
            "round": player["exit"]["round"],
            "method": player["exit"]["method"],
        }
        for player in summary["players"]
        if player.get("exit")
    ]
    return {
        "id": summary["id"],
        "slug": summary.get("slug"),
        "label": summary.get("label"),
        "blurb": summary.get("blurb"),
        "game": summary["game"],
        "season": summary["season"],
        "family": summary["family"],
        "flagship": summary["flagship"],
        "holdout": summary["holdout"],
        "seed": summary["seed"],
        "players": len(summary["players"]),
        "rounds": summary["rounds"],
        "traitorCount": sum(
            1 for player in summary["players"]
            if player.get("roleByRound", {}).get("1") == "traitor"
        ),
        "traitors": [
            player["id"] for player in summary["players"]
            if player.get("roleByRound", {}).get("1") == "traitor"
        ],
        "survivors": [
            player["id"] for player in summary["players"] if not player.get("exit")
        ],
        "eliminations": eliminations,
        "sampleQuote": first_public["text"][:180] if first_public else None,
        "sampleSpeaker": first_public["from"] if first_public else None,
        "winner": summary["outcome"].get("winner"),
        "winningTeam": summary["outcome"].get("winningTeam"),
        "reason": summary["outcome"].get("reason"),
        "finale": summary["outcome"].get("finale"),
        "solo": summary["outcome"].get("solo"),
        "provider": summary["config"].get("provider"),
        "model": summary["config"].get("model"),
        "calls": summary["counts"].get("calls", 0),
        "totalTokens": summary["counts"].get("totalTokens", 0),
        "messages": summary["counts"].get("messages", 0),
        "traitorMessages": summary["counts"].get("traitor", 0),
        "hallucination": quality.get("hallucination_score"),
        "duplication": diversity.get("duplication_score"),
        "secrecyFlags": secrecy.get("flags_total"),
        "rejectedActions": summary["counts"].get("rejectedActions", 0),
        "reciprocity": private.get("reciprocity_rate"),
        "similarityContent": (similarity.get("content_words") or {}).get("mean"),
        "similarityPhrasing": (similarity.get("phrasing") or {}).get("mean"),
        "benchmarkScore": summary.get("benchmarkScore"),
        "hasBenchmark": summary.get("hasBenchmark"),
        "hasDiagnosis": summary.get("hasDiagnosis"),
        "hasMessages": summary.get("hasMessages"),
    }


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--configs-dir", default="configs")
    parser.add_argument("--out", default="../web/src/data")
    parser.add_argument("--check", action="store_true", help="scan output for secrets")
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir).resolve()
    configs_dir = Path(args.configs_dir).resolve()
    out_dir = Path(args.out).resolve()
    slugs = read_yaml(Path(__file__).with_name("site_slugs.yaml")).get("flagships", {})

    candidates = sorted(
        path
        for path in runs_dir.iterdir()
        if path.is_dir()
        and (path / "metrics.json").exists()
        and (path / "game.json").exists()
    )
    print(f"Exporting {len(candidates)} run(s) to {out_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    runs_out = out_dir / "runs"
    seasons_out = out_dir / "seasons"
    runs_out.mkdir(exist_ok=True)
    seasons_out.mkdir(exist_ok=True)

    rows: list[dict[str, Any]] = []
    generated_from: list[str] = []
    total_players = total_messages = total_calls = total_tokens = 0

    for run_dir in candidates:
        metrics = read_json(run_dir / "metrics.json")
        if metrics.get("status") != "completed":
            continue
        run_id = run_dir.name
        flagship = slugs.get(run_id)
        try:
            summary, messages = build_summary(run_dir, configs_dir, flagship)
        except Exception as exc:
            print(f"  ! {run_id}: {exc}", file=sys.stderr)
            continue

        run_out = runs_out / run_id
        run_out.mkdir(exist_ok=True)
        (run_out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if flagship and messages:
            (run_out / "messages.json").write_text(
                json.dumps({"messages": messages}, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            if summary.get("benchmark"):
                season_id = summary["benchmark"]["season"]["id"]
                (seasons_out / f"{season_id}.json").write_text(
                    json.dumps(summary["benchmark"]["season"], ensure_ascii=False, indent=2)
                    + "\n",
                    encoding="utf-8",
                )

        row = manifest_row(summary, messages)
        rows.append(row)
        generated_from.append(run_id)
        total_players += row["players"]
        total_messages += row["messages"]
        total_calls += row["calls"]
        total_tokens += row["totalTokens"]
        print(
            f"  {run_id:<32} {row['players']:>2}p {row['rounds']:>2}r "
            f"{row['winner'] or '?':<8} {row['messages']:>4}m {row['calls']:>5}c"
        )

    model_labels: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row['provider']}/{row['model']}"
        model_labels.setdefault(
            key,
            {
                "provider": row["provider"],
                "model": row["model"],
                "label": row["model"].split("/")[-1] if row["model"] else "unknown",
            },
        )

    manifest = {
        "version": 1,
        "generatedFrom": generated_from,
        "totals": {
            "runs": len(rows),
            "playerGames": total_players,
            "messages": total_messages,
            "calls": total_calls,
            "tokens": total_tokens,
        },
        "models": model_labels,
        "runs": rows,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.check:
        failures: list[str] = []
        for path in sorted(out_dir.rglob("*.json")):
            text = path.read_text(encoding="utf-8")
            for label, pattern in SECRET_PATTERNS:
                for match in pattern.finditer(text):
                    failures.append(f"{path.relative_to(out_dir)}: {label}: {match.group(0)[:80]}")
        if failures:
            print("\nSanitization check failed:", file=sys.stderr)
            for failure in failures[:50]:
                print(f"  {failure}", file=sys.stderr)
            return 1
        print("Sanitization check passed.")

    print(
        f"Totals: {manifest['totals']['runs']} runs, "
        f"{total_players} player-games, {total_messages} messages, "
        f"{total_calls} calls, {total_tokens} tokens"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
