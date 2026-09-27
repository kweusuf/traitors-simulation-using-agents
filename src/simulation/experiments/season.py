"""Benchmark a finished run against a real season's ground truth (phase 21).

A run's own quality score says whether the model stayed coherent, but
not whether the simulation reproduced the season it models. This module
compares the two directly, component by component, and reports a 0 to 1
alignment score for each so a change can be judged on fidelity to the
real broadcast instead of against itself.

Nothing here is invented. Where one side has no data for a comparison (a
round with no banishment, a run that never reached a finale), that side
is reported as None, the component is left out of its match rate, and
the component is left out of the overall weighted mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from simulation.persistence.event_log import Event, EventType

# Component weights for the overall score. Outcome and the structural
# shape of the game (who was banished, who was murdered, how the living
# counts tracked the season) carry the most weight, because those are
# what a viewer would notice; the bookkeeping components (roster,
# recruits, exit order) matter, but drift there is less visible. The
# weights sum to 1.0 and only the components that produced a score are
# used: a skipped component's weight is redistributed over the rest.
COMPONENT_WEIGHTS: dict[str, float] = {
    "outcome": 0.20,
    "traitor_roster": 0.10,
    "banishment_alignment": 0.12,
    "murder_alignment": 0.10,
    "traitor_hit_rate": 0.08,
    "survival_curve": 0.12,
    "final_counts": 0.08,
    "finale": 0.08,
    "recruitments": 0.06,
    "exit_order": 0.06,
}


@dataclass(frozen=True)
class CastMember:
    """One contestant in the ground-truth season."""

    id: str
    role: str  # "traitor" | "faithful"
    traitor_type: Optional[str] = None  # "original" | "recruited"
    exit_episode: Optional[int] = None


@dataclass(frozen=True)
class Elimination:
    """One episode's night murder and round-table banishment."""

    episode: int
    night_victim: Optional[str] = None
    banishment: tuple[str, ...] = ()


@dataclass(frozen=True)
class Recruitment:
    """One recruitment attempt the season recorded."""

    episode: int
    target: str
    accepted: bool


@dataclass(frozen=True)
class Season:
    """Validated ground truth for one broadcast season."""

    id: str
    name: str
    winner_faction: str
    final_counts: dict[str, int]
    cast: tuple[CastMember, ...]
    eliminations: tuple[Elimination, ...]
    recruitments: tuple[Recruitment, ...]
    sources: tuple[str, ...] = ()


def _extract_sources(raw: str) -> tuple[str, ...]:
    """The URL lines in the file's comments; provenance, not data.

    The season file records its sources in header comments, so they are
    read from the raw text rather than from the parsed YAML. Order is
    preserved and duplicates are dropped.
    """
    urls: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        for token in stripped.lstrip("# ").split():
            if token.startswith("http://") or token.startswith("https://"):
                urls.append(token.rstrip(".,;"))
    return tuple(dict.fromkeys(urls))


def load_season(path: str | Path) -> Season:
    """Load and validate a ground-truth season file.

    Fails with ValueError (never a silent default) so a benchmark can
    never score against a season it did not fully understand.
    """
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"season file not found: {source}")
    raw = source.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"season file {source} must contain a YAML mapping")

    block = data.get("season")
    if not isinstance(block, dict):
        raise ValueError(f"season file {source} is missing the 'season' block")
    season_id = block.get("id")
    if not isinstance(season_id, str) or not season_id:
        raise ValueError(f"season file {source} is missing 'season.id'")
    winner = block.get("winner_faction")
    if winner not in ("faithful", "traitor"):
        raise ValueError(
            f"season {season_id}: winner_faction must be 'faithful' or 'traitor'"
        )
    final_counts = block.get("final_counts")
    if not isinstance(final_counts, dict):
        raise ValueError(f"season {season_id}: missing 'final_counts'")
    counts: dict[str, int] = {}
    for side in ("traitors", "faithful"):
        value = final_counts.get(side)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"season {season_id}: final_counts.{side} must be an integer"
            )
        counts[side] = value

    raw_cast = data.get("cast")
    if not isinstance(raw_cast, list) or not raw_cast:
        raise ValueError(f"season {season_id}: 'cast' must be a non-empty list")
    cast: list[CastMember] = []
    for index, entry in enumerate(raw_cast):
        if not isinstance(entry, dict):
            raise ValueError(
                f"season {season_id}: cast entry {index} must be a mapping"
            )
        player_id = entry.get("id")
        role = entry.get("role")
        if not isinstance(player_id, str) or not isinstance(role, str):
            raise ValueError(
                f"season {season_id}: cast entry {index} needs 'id' and 'role'"
            )
        exit_episode = entry.get("exit_episode")
        if exit_episode is not None and not isinstance(exit_episode, int):
            raise ValueError(
                f"season {season_id}: cast entry {player_id} needs an integer "
                "'exit_episode' (or null)"
            )
        cast.append(
            CastMember(
                id=player_id,
                role=role,
                traitor_type=entry.get("traitor_type"),
                exit_episode=exit_episode,
            )
        )

    raw_eliminations = data.get("eliminations")
    if not isinstance(raw_eliminations, list):
        raise ValueError(f"season {season_id}: 'eliminations' must be a list")
    eliminations: list[Elimination] = []
    for index, entry in enumerate(raw_eliminations):
        if not isinstance(entry, dict) or not isinstance(entry.get("episode"), int):
            raise ValueError(
                f"season {season_id}: elimination {index} needs an integer 'episode'"
            )
        banishment = entry.get("banishment")
        if banishment is None:
            banned: tuple[str, ...] = ()
        elif isinstance(banishment, str):
            banned = (banishment,)
        elif isinstance(banishment, list) and all(
            isinstance(item, str) for item in banishment
        ):
            banned = tuple(banishment)
        else:
            raise ValueError(
                f"season {season_id}: elimination episode {entry['episode']} "
                "banishment must be a player id, a list of ids, or null"
            )
        eliminations.append(
            Elimination(
                episode=entry["episode"],
                night_victim=entry.get("night_victim"),
                banishment=banned,
            )
        )

    recruitments: list[Recruitment] = []
    for index, entry in enumerate(data.get("recruitments") or []):
        if not isinstance(entry, dict) or not isinstance(entry.get("target"), str):
            raise ValueError(
                f"season {season_id}: recruitment {index} needs a 'target' player"
            )
        episode = entry.get("episode")
        recruitments.append(
            Recruitment(
                episode=episode if isinstance(episode, int) else 0,
                target=entry["target"],
                accepted=bool(entry.get("accepted", False)),
            )
        )

    return Season(
        id=season_id,
        name=str(block.get("name") or season_id),
        winner_faction=winner,
        final_counts=counts,
        cast=tuple(cast),
        eliminations=tuple(eliminations),
        recruitments=tuple(recruitments),
        sources=_extract_sources(raw),
    )


# ----------------------------------------------------------------------
# Folding a run's events into the facts the comparison needs
# ----------------------------------------------------------------------


@dataclass
class _RunFacts:
    """The run-side values, read from the event log alone."""

    game_id: str = ""
    players: list[str] = field(default_factory=list)
    starting_traitors: list[str] = field(default_factory=list)
    recruited: list[str] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)
    banishments: dict[int, str] = field(default_factory=dict)  # round -> player
    night_victims: dict[int, str] = field(default_factory=dict)  # round -> player
    eliminations: dict[str, int] = field(default_factory=dict)  # player -> round
    finale: Optional[dict[str, list[str]]] = None
    winner: Optional[str] = None
    rounds: int = 0


def _fold(events: list[Event]) -> _RunFacts:
    """Read the run-side facts; a night kill is the eliminated player
    when the shield did not block it, otherwise the traitors' choice."""
    facts = _RunFacts()
    for event in events:
        payload = event.payload
        if event.type is EventType.GAME_STARTED:
            facts.game_id = event.game_id
            facts.players = list(payload.get("players", []))
        elif event.type is EventType.ROLE_ASSIGNED and event.actor:
            role = str(payload.get("role", ""))
            facts.roles[event.actor] = role
            if role == "traitor":
                facts.starting_traitors.append(event.actor)
        elif event.type is EventType.ROLE_RECRUITED and event.actor:
            facts.recruited.append(event.actor)
            facts.roles[event.actor] = "traitor"
        elif event.type is EventType.ROUND_STARTED:
            facts.rounds = max(facts.rounds, event.round)
        elif event.type is EventType.PLAYER_ELIMINATED and event.actor:
            method = str(payload.get("method", ""))
            facts.eliminations[event.actor] = event.round
            if method == "night":
                facts.night_victims[event.round] = event.actor
            elif method == "vote":
                facts.banishments[event.round] = event.actor
        elif event.type is EventType.TRAITOR_KILL and event.targets:
            facts.night_victims.setdefault(event.round, event.targets[0])
        elif event.type is EventType.FINALE_STARTED and facts.finale is None:
            facts.finale = {
                "traitors": list(payload.get("traitors", [])),
                "faithful": list(payload.get("faithful", [])),
            }
        elif event.type is EventType.GAME_WON:
            team = payload.get("team")
            facts.winner = str(team) if team else None
        elif event.type is EventType.GAME_ENDED:
            rounds = payload.get("rounds")
            if isinstance(rounds, int):
                facts.rounds = max(facts.rounds, rounds)
            if facts.winner is None and payload.get("winner") is not None:
                facts.winner = str(payload["winner"])
    return facts


def _round4(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _season_alive(season: Season, episode: int) -> tuple[int, int]:
    """Living (traitors, faithful) after that episode's eliminations.

    A cast member is alive until the episode they exit; the season's
    finale split is the composition at the finale, not the end state.
    """
    alive = [
        member
        for member in season.cast
        if member.exit_episode is None or member.exit_episode > episode
    ]
    traitors = sum(1 for member in alive if member.role == "traitor")
    return traitors, len(alive) - traitors


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------


def _outcome(facts: _RunFacts, season: Season) -> dict[str, Any]:
    run_winner = facts.winner
    matched = None if run_winner is None else run_winner == season.winner_faction
    return {
        "run": run_winner,
        "season": season.winner_faction,
        "matched": matched,
        "score": None if matched is None else (1.0 if matched else 0.0),
        "method": "run's winning faction equals season.winner_faction",
    }


def _final_counts(facts: _RunFacts, season: Season) -> dict[str, Any]:
    """The run's living split when the game ended, against the season's
    recorded finale split (season.final_counts)."""
    season_counts = {
        "traitors": season.final_counts.get("traitors"),
        "faithful": season.final_counts.get("faithful"),
    }
    if not facts.players and not facts.roles:
        return {
            "run": None,
            "season": season_counts,
            "matched": None,
            "score": None,
            "method": "run's living counts at game end vs season.final_counts",
        }
    living = [pid for pid in facts.players if pid not in facts.eliminations]
    run_counts = {
        "traitors": sum(1 for pid in living if facts.roles.get(pid) == "traitor"),
        "faithful": sum(1 for pid in living if facts.roles.get(pid) != "traitor"),
    }
    matched = {
        side: run_counts[side] == season_counts.get(side)
        for side in ("traitors", "faithful")
    }
    score = sum(1 for ok in matched.values() if ok) / len(matched)
    return {
        "run": run_counts,
        "season": season_counts,
        "matched": matched,
        "score": _round4(score),
        "method": "each living side is worth 0.5 when it matches season.final_counts",
    }


def _traitor_roster(facts: _RunFacts, season: Season) -> dict[str, Any]:
    originals = sorted(
        member.id
        for member in season.cast
        if member.role == "traitor" and member.traitor_type == "original"
    )
    run_start = sorted(set(facts.starting_traitors))
    matched = sorted(set(run_start) & set(originals))
    recall = len(matched) / len(originals) if originals else None
    return {
        "run": run_start,
        "season": originals,
        "matched": matched,
        "recall": _round4(recall),
        "score": _round4(recall),
        "method": "recall of season original traitors among the run's starting traitors",
    }


def _alignment(
    run_side: dict[int, str],
    season_side: dict[int, Any],
    *,
    method: str,
) -> dict[str, Any]:
    """Pair the run's per-round eliminations with the season's episode
    entries; episodes the season leaves empty are excluded."""
    pairs: list[dict[str, Any]] = []
    for round_number in sorted(run_side):
        expected = season_side.get(round_number)
        if expected is None or expected == () or expected == "":
            pairs.append(
                {
                    "round": round_number,
                    "run": run_side[round_number],
                    "season": None,
                    "matched": None,
                }
            )
            continue
        expected_list = list(expected) if isinstance(expected, (list, tuple)) else [
            expected
        ]
        pairs.append(
            {
                "round": round_number,
                "run": run_side[round_number],
                "season": expected_list,
                "matched": run_side[round_number] in expected_list,
            }
        )
    comparable = [pair for pair in pairs if pair["matched"] is not None]
    matched_count = sum(1 for pair in comparable if pair["matched"])
    match_rate = matched_count / len(comparable) if comparable else None
    return {
        "run": [pair["run"] for pair in pairs],
        "season": [pair["season"] for pair in pairs],
        "pairs": pairs,
        "comparable_rounds": len(comparable),
        "matched_rounds": matched_count,
        "match_rate": _round4(match_rate),
        "score": _round4(match_rate),
        "method": method,
    }


def _banishment_alignment(facts: _RunFacts, season: Season) -> dict[str, Any]:
    season_banishments = {
        entry.episode: entry.banishment
        for entry in season.eliminations
        if entry.banishment
    }
    return _alignment(
        facts.banishments,
        season_banishments,
        method=(
            "each round-table banishment paired by round index with the season "
            "banishment for that episode; episodes the season leaves empty are "
            "excluded from the match rate"
        ),
    )


def _murder_alignment(facts: _RunFacts, season: Season) -> dict[str, Any]:
    season_victims = {
        entry.episode: entry.night_victim
        for entry in season.eliminations
        if entry.night_victim
    }
    return _alignment(
        facts.night_victims,
        season_victims,
        method=(
            "each night victim paired by round index with the season night "
            "victim for that episode; episodes without a murder are excluded"
        ),
    )


def _traitor_hit_rate(facts: _RunFacts, season: Season) -> dict[str, Any]:
    run_banishments = len(facts.banishments)
    run_hits = sum(
        1 for player in facts.banishments.values() if facts.roles.get(player) == "traitor"
    )
    run_rate = run_hits / run_banishments if run_banishments else None

    roles = {member.id: member.role for member in season.cast}
    season_banished = [
        player for entry in season.eliminations for player in entry.banishment
    ]
    season_hits = sum(1 for player in season_banished if roles.get(player) == "traitor")
    season_rate = season_hits / len(season_banished) if season_banished else None

    if run_rate is None or season_rate is None:
        score = None
    else:
        score = max(0.0, 1.0 - abs(run_rate - season_rate))
    return {
        "run": {
            "banishments": run_banishments,
            "traitor_banishments": run_hits,
            "rate": _round4(run_rate),
        },
        "season": {
            "banishments": len(season_banished),
            "traitor_banishments": season_hits,
            "rate": _round4(season_rate),
        },
        "score": _round4(score),
        "method": "1 - |run traitor banishment share - season traitor banishment share|",
    }


def _survival_curve(facts: _RunFacts, season: Season) -> dict[str, Any]:
    season_episodes = max(
        (entry.episode for entry in season.eliminations), default=0
    )
    rounds: list[dict[str, Any]] = []
    distances: list[float] = []
    for round_number in range(1, min(facts.rounds, season_episodes) + 1):
        living = [
            pid
            for pid in facts.players
            if facts.eliminations.get(pid, round_number + 1) > round_number
        ]
        run_traitors = sum(1 for pid in living if facts.roles.get(pid) == "traitor")
        run_faithful = len(living) - run_traitors
        season_traitors, season_faithful = _season_alive(season, round_number)
        distance = abs(run_traitors - season_traitors) + abs(
            run_faithful - season_faithful
        )
        distances.append(distance)
        rounds.append(
            {
                "round": round_number,
                "run": {"traitors": run_traitors, "faithful": run_faithful},
                "season": {
                    "traitors": season_traitors,
                    "faithful": season_faithful,
                },
                "distance": distance,
            }
        )
    mean_distance = _mean([float(distance) for distance in distances])
    score = None if mean_distance is None else 1.0 / (1.0 + mean_distance)
    return {
        "run": {"rounds_compared": len(rounds)},
        "season": {"episodes": season_episodes},
        "rounds": rounds,
        "mean_distance": _round4(mean_distance),
        "score": _round4(score),
        "method": (
            "per round, |run living traitors - season| + |run living faithful - "
            "season|; score is 1 / (1 + mean distance) over the overlapping rounds"
        ),
    }


def _recruitments(facts: _RunFacts, season: Season) -> dict[str, Any]:
    run_count = len(facts.recruited)
    season_accepted = sum(1 for entry in season.recruitments if entry.accepted)
    difference = abs(run_count - season_accepted)
    score = (
        1.0
        if difference == 0
        else max(0.0, 1.0 - difference / max(run_count, season_accepted, 1))
    )
    return {
        "run": {"recruitments": run_count},
        "season": {"accepted": season_accepted},
        "difference": difference,
        "score": _round4(score),
        "method": "ROLE_RECRUITED count against the accepted ground-truth recruitments",
    }


def _finale(facts: _RunFacts, season: Season) -> dict[str, Any]:
    season_counts = {
        "traitors": season.final_counts.get("traitors"),
        "faithful": season.final_counts.get("faithful"),
    }
    if facts.finale is None:
        return {
            "run": None,
            "season": season_counts,
            "matched": None,
            "score": None,
            "method": "FINALE_STARTED payload counts; null when the run never reached a finale",
        }
    run_counts = {
        "traitors": len(facts.finale["traitors"]),
        "faithful": len(facts.finale["faithful"]),
    }
    matched = {
        side: run_counts[side] == season_counts.get(side)
        for side in ("traitors", "faithful")
    }
    score = sum(1 for ok in matched.values() if ok) / len(matched)
    return {
        "run": run_counts,
        "season": season_counts,
        "matched": matched,
        "score": _round4(score),
        "method": "each finale side is worth 0.5 when it matches season.final_counts",
    }


def _exit_order(facts: _RunFacts, season: Season) -> dict[str, Any]:
    season_exit = {
        member.id: member.exit_episode
        for member in season.cast
        if member.exit_episode is not None
    }
    shared = sorted(set(facts.eliminations) & set(season_exit))
    pairs = [
        {
            "player": player,
            "run": facts.eliminations[player],
            "season": season_exit[player],
            "difference": abs(facts.eliminations[player] - season_exit[player]),
        }
        for player in shared
    ]
    differences = [float(pair["difference"]) for pair in pairs]
    mean_difference = _mean(differences)
    within_one = sum(1 for diff in differences if diff <= 1)
    match_rate = within_one / len(pairs) if pairs else None
    return {
        "run": {player: facts.eliminations[player] for player in shared},
        "season": {player: season_exit[player] for player in shared},
        "pairs": pairs,
        "mean_difference": _round4(mean_difference),
        "within_one": within_one,
        "match_rate": _round4(match_rate),
        "score": _round4(match_rate),
        "method": (
            "for players who left in both, |run elimination round - season "
            "exit_episode|; score is the share matched within one round"
        ),
    }


def _overall(components: dict[str, dict[str, Any]]) -> dict[str, Any]:
    scored: dict[str, float] = {}
    skipped: list[str] = []
    for name, weight in COMPONENT_WEIGHTS.items():
        score = components[name].get("score")
        if score is None:
            skipped.append(name)
        else:
            scored[name] = float(score)
    total_weight = sum(COMPONENT_WEIGHTS[name] for name in scored)
    overall = (
        sum(COMPONENT_WEIGHTS[name] * score for name, score in scored.items())
        / total_weight
        if total_weight
        else None
    )
    return {
        "score": _round4(overall),
        "weights": dict(COMPONENT_WEIGHTS),
        "components_scored": sorted(scored),
        "components_skipped": sorted(skipped),
        "method": (
            "weighted mean over the components that produced a score; skipped "
            "components are excluded and their weight is redistributed"
        ),
    }


def benchmark_run(events: list[Event], season: Season) -> dict[str, Any]:
    """Alignment of one run against the season it models, component by
    component; every score is deterministic and no model is consulted."""
    facts = _fold(events)
    components: dict[str, dict[str, Any]] = {
        "outcome": _outcome(facts, season),
        "traitor_roster": _traitor_roster(facts, season),
        "banishment_alignment": _banishment_alignment(facts, season),
        "murder_alignment": _murder_alignment(facts, season),
        "traitor_hit_rate": _traitor_hit_rate(facts, season),
        "survival_curve": _survival_curve(facts, season),
        "final_counts": _final_counts(facts, season),
        "finale": _finale(facts, season),
        "recruitments": _recruitments(facts, season),
        "exit_order": _exit_order(facts, season),
    }
    return {**components, "overall": _overall(components)}


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------


def _cell(value: Any) -> str:
    """Compact table cell for any component value."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell(item) for item in value) if value else "-"
    if isinstance(value, dict):
        return ", ".join(f"{key}={_cell(item)}" for key, item in value.items()) or "-"
    return str(value)


def render_benchmark(
    game_id: str,
    season: Season,
    result: dict[str, Any],
    *,
    provenance: bool = True,
) -> str:
    """The benchmark.md text: a component table, plus the ground truth's
    identity and sources when provenance is requested."""
    overall = result["overall"]
    lines = [
        f"# Season benchmark: {game_id}",
        "",
        f"- season: {season.id}",
        f"- winner faction (ground truth): {season.winner_faction}",
        f"- overall alignment: {_cell(overall['score'])} "
        f"({len(overall['components_scored'])} of {len(COMPONENT_WEIGHTS)} "
        "components scored)",
    ]
    if provenance:
        lines.append(f"- season name: {season.name}")
        if season.sources:
            lines.append("- sources:")
            lines.extend(f"  - {url}" for url in season.sources)
        else:
            lines.append("- sources: none recorded in the season file")
    lines += [
        "",
        "| component | weight | run | season | score |",
        "|---|---|---|---|---|",
    ]
    for name, weight in COMPONENT_WEIGHTS.items():
        component = result[name]
        lines.append(
            f"| {name} | {weight:.2f} | {_cell(component.get('run'))} "
            f"| {_cell(component.get('season'))} | {_cell(component.get('score'))} |"
        )
    skipped = overall["components_skipped"]
    if skipped:
        lines += ["", f"Skipped (no data on one side): {', '.join(skipped)}."]
    if provenance:
        lines += ["", "## Methods", ""]
        for name in COMPONENT_WEIGHTS:
            lines.append(f"- {name}: {result[name]['method']}")
    return "\n".join(lines).rstrip() + "\n"
