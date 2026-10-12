"""One-glance timeline for a run: what happened, round by round (spec 43).

Every game writes a `timeline.md` beside its other artifacts, whether it
concluded or not. The transcript answers "what was said"; this answers
"what happened" in a screenful - the deal, one section per round with the
milestones in order (mission, items, banishment, murder, recruitment,
quiet rounds), the endgame, and the verdict.

Deterministic by construction: the document is a pure function of
`events.jsonl` (plus `metrics.json` when it exists), so regenerating it
for the same run always produces the same text. That is what makes it
safe to backfill older runs from their logs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from simulation.persistence.event_log import Event, EventType

# Event types that carry no milestone: phase bookkeeping, the speech
# itself (the transcript's job), per-vote detail (summarised instead),
# and the hosted debate clock. Skipping them keeps the doc glanceable.
_NOISE = {
    EventType.GAME_STARTED,
    EventType.ROUND_STARTED,
    EventType.PHASE_STARTED,
    EventType.PHASE_ENDED,
    EventType.SNAPSHOT_CREATED,
    EventType.MISSION_STARTED,
    EventType.PUBLIC_MESSAGE,
    EventType.PRIVATE_MESSAGE,
    EventType.VOTE_CAST,
    EventType.HOST_WARNING,
    EventType.DEBATE_CLOSED,
    EventType.COUNCIL_PROPOSAL,
    EventType.ROLE_ASSIGNED,
    EventType.ACTION_REJECTED,
}

_MAX_VOTE_CELLS = 4  # vote counts shown per elimination line


def _join(names: Any) -> str:
    """`[a, b]` -> `a, b`; anything else -> str of it."""
    if isinstance(names, (list, tuple)):
        return ", ".join(str(n) for n in names)
    return str(names)


def _vote_line(payload: dict[str, Any]) -> str:
    """Vote distribution of a ballot, highest first, capped for scanning."""
    votes = payload.get("votes")
    if not isinstance(votes, dict) or not votes:
        return ""
    ordered = sorted(votes.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    cells = [f"{voter} {count}" for voter, count in ordered[:_MAX_VOTE_CELLS]]
    if len(ordered) > _MAX_VOTE_CELLS:
        cells.append("…")
    return " (" + ", ".join(cells) + ")"


def _milestone(event: Event) -> Optional[str]:
    """One timeline bullet for one event, or None if it carries none."""
    etype = event.type
    payload = event.payload or {}
    actor = event.actor or ""
    target = _join(event.targets) if event.targets else ""

    if etype is EventType.MISSION_COMPLETED:
        return "Mission: " + ("succeeded" if payload.get("success") else "failed")
    if etype is EventType.ITEM_AWARDED:
        return f"Item awarded: {payload.get('item', '?')} -> {actor}"
    if etype is EventType.SEER_CHECK:
        checked = target or str(payload.get("target", "?"))
        return f"Seer checked {checked}" if actor else None
    if etype is EventType.PLAYER_ELIMINATED:
        method = payload.get("method")
        if method == "night":
            return f"{actor} murdered in the night"
        if method == "vote":
            return f"{actor} banished by the vote{_vote_line(payload)}"
        return f"{actor} eliminated ({method})"
    if etype is EventType.VOTE_TIE:
        return "Round-table tie: nobody banished"
    if etype is EventType.BANISHMENT_SKIPPED:
        return f"Quiet round: no banishment ({payload.get('reason', '?')})"
    if etype is EventType.TRAITOR_KILL:
        detail = ""
        if payload.get("dissent"):
            detail = f" (dissent: {_join(payload['dissent'])})"
        return f"Traitors killed {target}{detail}"
    if etype is EventType.MURDER_SKIPPED:
        return f"Quiet night: no murder ({payload.get('reason', '?')})"
    if etype is EventType.SHIELD_BLOCKED:
        return f"Shield blocked the murder on {payload.get('victim', actor)}"
    if etype is EventType.DAGGER_USED:
        return f"Dagger spent: {actor}'s vote counted double"
    if etype is EventType.NOMINATION_TALLY:
        return f"Nominations: {target} face the room"
    if etype is EventType.REVOTE_CALLED:
        return f"Revote forced between {target}"
    if etype is EventType.REVOTE_RESOLVED:
        if payload.get("tie"):
            return "Revote tied again: nobody banished"
        return f"Revote banished {_join(payload.get('top') or event.targets)}"
    if etype is EventType.RECRUIT_CHOICE_MADE:
        return f"Traitors chose: {payload.get('choice', '?')}"
    if etype is EventType.RECRUIT_OFFERED:
        return f"Recruit offer -> {payload.get('target', target)}"
    if etype is EventType.RECRUIT_ACCEPTED:
        # `by` is the offerer; older logs omit it, and the first target
        # of the acceptance event is the same player.
        by = payload.get("by") or (event.targets[0] if event.targets else "?")
        return f"{actor} accepted the offer and became a traitor (by {by})"
    if etype is EventType.RECRUIT_DECLINED:
        return f"{actor} declined the recruit offer"
    if etype is EventType.ULTIMATUM_ISSUED:
        return f"Ultimatum: {actor} murders {payload.get('target', target)}"
    if etype is EventType.ROLE_RECRUITED:
        # Usually preceded by RECRUIT_ACCEPTED, which narrates the same
        # flip; show it only when the accepting event is missing.
        return None
    if etype is EventType.FINALE_STARTED:
        traitors = _join(payload.get("traitors") or [])
        faithful = _join(payload.get("faithful") or [])
        return (
            f"Finale begins: rapid-fire voting "
            f"({traitors} traitors, {faithful} faithful)"
        )
    if etype is EventType.GAME_WON:
        bits = [f"{payload.get('team', '?')} win"]
        if payload.get("reason"):
            bits.append(f"by {payload['reason']}")
        if payload.get("solo"):
            bits.append("solo")
        if payload.get("surviving_traitors"):
            bits.append(f"surviving traitors: {_join(payload['surviving_traitors'])}")
        return "; ".join(bits)
    if etype is EventType.GAME_ENDED:
        return None if payload.get("winner") else "Game ended without a winner"
    if etype is EventType.END_VOTE_CAST:
        return None  # aggregated per round by _end_votes_line
    return etype.value  # unknown milestone: name it rather than drop it


def _mission_line(batch: list[Event]) -> str:
    """Consecutive mission results as one line: 'Missions: 2 succeeded, 1 failed'."""
    won = sum(1 for e in batch if (e.payload or {}).get("success"))
    lost = len(batch) - won
    cells = []
    if won:
        cells.append(f"{won} succeeded" if len(batch) > 1 else "succeeded")
    if lost:
        cells.append(f"{lost} failed" if len(batch) > 1 else "failed")
    noun = "Missions" if len(batch) > 1 else "Mission"
    return f"{noun}: {', '.join(cells)}"


def _end_votes_line(events: list[Event]) -> Optional[str]:
    """Collapse a round's end-or-banish ballots into one line."""
    choices: dict[str, int] = {}
    for event in events:
        if event.type is EventType.END_VOTE_CAST:
            choice = str((event.payload or {}).get("choice", "?"))
            choices[choice] = choices.get(choice, 0) + 1
    if not choices:
        return None
    cells = ", ".join(f"{count} {choice}" for choice, count in sorted(choices.items()))
    return f"End-or-banish vote: {cells}"


def _setup_section(events: list[Event]) -> list[str]:
    """Round 0: the deal. Traitors are read from ROLE_ASSIGNED."""
    traitors: list[str] = []
    players: list[str] = []
    for event in events:
        if event.round != 0:
            continue
        if event.type is EventType.GAME_STARTED:
            players = [str(p) for p in (event.payload or {}).get("players") or []]
        elif event.type is EventType.ROLE_ASSIGNED and event.actor:
            if (event.payload or {}).get("role") == "traitor":
                traitors.append(event.actor)
    lines = ["## Round 0 - setup", ""]
    if players:
        lines.append(f"- {len(players)} players dealt")
    if traitors:
        lines.append(f"- Traitors: {_join(traitors)}")
    return lines + [""]


def build_timeline(
    game_id: str,
    events: list[Event],
    metrics: Optional[dict[str, Any]] = None,
) -> str:
    """Render one run's milestones as markdown.

    Works for an unfinished run too: with no `GAME_WON` the header says
    so instead of inventing an outcome, which is the case that matters
    when a run crashed mid-game.
    """
    metrics = metrics or {}
    ended = next((e for e in events if e.type is EventType.GAME_ENDED), None)
    won = next((e for e in events if e.type is EventType.GAME_WON), None)
    rounds_played = max((e.round for e in events), default=0)

    lines = [f"# Timeline: {game_id}", ""]

    identity = []
    if metrics.get("experiment_id"):
        identity.append(f"experiment: {metrics['experiment_id']}")
    if metrics.get("random_seed") is not None:
        identity.append(f"seed: {metrics['random_seed']}")
    if metrics.get("model"):
        identity.append(f"model: {metrics['model']}")
    if identity:
        lines.append("- " + ", ".join(identity))
        lines.append("")

    if won is not None:
        payload = won.payload or {}
        outcome = f"**Winner: {payload.get('team', '?')}**"
        if payload.get("reason"):
            outcome += f" ({payload['reason']}"
            if payload.get("solo"):
                outcome += ", solo"
            outcome += ")"
        if payload.get("surviving_traitors"):
            outcome += f"; surviving traitors: {_join(payload['surviving_traitors'])}"
        rounds = (ended.payload or {}).get("rounds") if ended else rounds_played
        lines.append(f"- {outcome}; {rounds} rounds")
    else:
        lines.append(
            f"- **Unfinished**: no winner recorded after {rounds_played} rounds "
            f"({len(events)} events)"
        )
    rejected = sum(1 for e in events if e.type is EventType.ACTION_REJECTED)
    if rejected:
        lines.append(f"- Rejected actions: {rejected}")
    lines.append("")

    lines.extend(_setup_section(events))

    # Group the milestones by round, preserving log order inside each.
    by_round: dict[int, list[Event]] = {}
    for event in events:
        if event.round == 0 or event.type in _NOISE:
            continue
        by_round.setdefault(event.round, []).append(event)

    for round_number in sorted(by_round):
        round_events = by_round[round_number]
        # A night murder is narrated once: the traitors' choice as
        # TRAITOR_KILL, or the ultimatum as ULTIMATUM_ISSUED. The
        # PLAYER_ELIMINATED echo only repeats it, so drop the echo when
        # the choice is on record (it still shows when neither is).
        decided = {
            target
            for event in round_events
            if event.type in (EventType.TRAITOR_KILL, EventType.ULTIMATUM_ISSUED)
            for target in (event.targets or ((event.payload or {}).get("target"),))
            if target
        }
        lines.append(f"## Round {round_number}")
        lines.append("")
        mission_batch: list[Event] = []
        for event in round_events:
            if (
                event.type is EventType.PLAYER_ELIMINATED
                and (event.payload or {}).get("method") == "night"
                and event.actor in decided
            ):
                continue
            if event.type is EventType.MISSION_COMPLETED:
                mission_batch.append(event)
                continue
            if mission_batch:
                lines.append(f"- {_mission_line(mission_batch)}")
                mission_batch = []
            bullet = _milestone(event)
            if bullet:
                lines.append(f"- {bullet}")
        if mission_batch:
            lines.append(f"- {_mission_line(mission_batch)}")
        end_votes = _end_votes_line(round_events)
        if end_votes:
            lines.append(f"- {end_votes}")
        lines.append("")

    return "\n".join(lines)


def write_timeline(
    run_dir: Path,
    events: list[Event],
    metrics: Optional[dict[str, Any]] = None,
) -> Path:
    """Write `run_dir/timeline.md` and return its path."""
    path = run_dir / "timeline.md"
    path.write_text(build_timeline(run_dir.name, events, metrics), encoding="utf-8")
    return path


def load_metrics(run_dir: Path) -> dict[str, Any]:
    """`metrics.json` if the run has one (unfinished runs have none)."""
    path = run_dir / "metrics.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


