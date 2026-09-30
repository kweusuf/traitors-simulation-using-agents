"""Resume a crashed run from its own event log (audit fix, phase 5).

A season replay is hours of model calls, and the failure that kills it is
usually transient: a transport timeout the retries could not absorb takes
down a run whose event log holds a complete, playable history of
everything that happened up to that moment. Losing four hours to one bad
socket read is the real cost.

So resume treats the append-only log as the source of truth it is meant
to be. This module finds the last point the log is *consistent* - the
end of a completed phase - and rebuilds the engine state from it.
Anything after that point is a phase cut off mid-flight and is
discarded, because a half-run elimination phase would otherwise banish
somebody a second time on replay.

Truncation, not appending. `truncate_to` rewrites the log up to the
resume point rather than appending after it, so the fold below can never
see state the log does not vouch for. The model calls spent on the
discarded phase stay in `llm_calls.jsonl`: that file is append-only
telemetry and they really happened.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from simulation.engine.state import (
    GamePhase,
    GameState,
    MissionState,
    PlayerState,
    Role,
)
from simulation.persistence.event_log import Event, EventType
from simulation.persistence.jsonl import EventLog

# Mirrors the engine's ITEM_ORDER so a resumed game hands out the next
# item instead of restarting the rotation.
ITEM_ORDER = ("shield", "dagger", "seer")

# How long a log must be untouched before a resume will rewrite it. A
# season phase runs for minutes, so a log that changed within this
# window belongs to a process that is still alive.
DEFAULT_IDLE_SECONDS = 300


class ResumeError(RuntimeError):
    """The run cannot be resumed from its log."""


@dataclass
class ResumePoint:
    """A consistent cut in the log, plus the state to rebuild from it."""

    index: int  # number of events to keep
    round_number: int  # the round whose phases are next up
    phase_index: int  # index into the phase order to resume at
    state: GameState
    recruits_used: int = 0
    seer_checks_done: set[str] = field(default_factory=set)
    awarded_round: int = -1
    award_index: int = 0
    lost_ambitions: list[str] = field(default_factory=list)

    def recover_ambitions(
        self, seed: int, player_ids: list[str], traitor_names: list[str]
    ) -> list[str]:
        """Fill in ambitions for a log written before they were recorded.

        `ROLE_ASSIGNED` only started carrying the seeded ambition with
        this feature, so the three season runs that predate it would
        otherwise resume their traitors with no solo/team disposition -
        a real change in how they play, recovered from thin air.

        It is not thin air, though. `GameEngine.start` draws traitors
        from the roster only when the config does not pin them, and a
        season replay always pins them, so the seeded stream is exactly
        one `random()` per player in roster order. Replaying that
        stream reproduces the original dispositions exactly.

        Returns the players still missing an ambition afterwards, which
        is every traitor when the config drew its roles instead of
        pinning them and the replay is therefore unsafe.
        """
        if not self.lost_ambitions:
            return []
        if not traitor_names:
            return list(self.lost_ambitions)
        pinned = set(traitor_names)
        drawn = {
            pid for pid, role in self.state.roles.items() if role is Role.TRAITOR
        }
        if not pinned.issubset(drawn):
            # The run drew its traitors from the seed after all, so the
            # stream no longer lines up with roster order. Do not guess.
            return list(self.lost_ambitions)
        rng = random.Random(seed)
        replayed = {
            pid: "solo" if rng.random() < 0.5 else "team" for pid in player_ids
        }
        self.state.ambitions.update(replayed)
        self.lost_ambitions = sorted(
            pid
            for pid, role in self.state.roles.items()
            if role is Role.TRAITOR and not self.state.ambitions.get(pid)
        )
        return list(self.lost_ambitions)


def truncate_to(path: str | Path, keep: int) -> int:
    """Rewrite the log so only its first `keep` events survive.

    Returns the last surviving sequence number, or -1 when the log ends
    up empty. The file is only touched when something has to go, so a
    resume that discards nothing leaves the bytes alone.
    """
    events = EventLog(path).read_all()
    if keep >= len(events):
        return events[-1].sequence if events else -1
    kept = events[:keep]
    with Path(path).open("w", encoding="utf-8") as fh:
        for event in kept:
            fh.write(event.model_dump_json() + "\n")
    return kept[-1].sequence if kept else -1


def find_resume_point(events: list[Event], phase_order: list[str]) -> ResumePoint:
    """Locate the last completed phase and rebuild state up to it.

    A phase is complete when it emitted `PHASE_ENDED`: that is the only
    marker saying every mutation it was going to make is already on the
    record. The resume point is the phase after the last one.

    Raises `ResumeError` when the game already finished, or when the log
    holds no completed phase at all - a crash before the first phase
    finished has nothing to keep, so that is a fresh run, not a resume.
    """
    if not events:
        raise ResumeError("event log is empty; nothing to resume from")
    for terminal in (EventType.GAME_ENDED, EventType.GAME_WON):
        if any(e.type is terminal for e in events):
            raise ResumeError("this run already finished; nothing to resume")

    last_end = -1
    for index, event in enumerate(events):
        if event.type is EventType.PHASE_ENDED:
            last_end = index
    if last_end < 0:
        raise ResumeError(
            "no phase finished before the crash; this is a fresh run, "
            "not a resume"
        )

    cut = events[: last_end + 1]
    ended_phase = str(cut[-1].payload.get("phase", ""))
    state = rebuild_state(cut)
    point = ResumePoint(
        index=len(cut),
        round_number=state.round_number,
        phase_index=_next_phase_index(ended_phase, phase_order),
        state=state,
    )
    _fold_bookkeeping(cut, point)
    return point


def _next_phase_index(ended_phase: str, phase_order: list[str]) -> int:
    """Where in the phase order the next phase sits after `ended_phase`."""
    try:
        return phase_order.index(ended_phase) + 1
    except ValueError:
        # A phase outside the normal order (the finale and endgame loops
        # re-enter phases by name). Resuming at the start of the order is
        # the safe reading.
        return 0


def rebuild_state(events: list[Event]) -> GameState:
    """Fold an event log back into the board the engine was holding.

    Mirrors the live transitions: roles come from assignment and
    recruitment, eliminations clear the alive set, and items move
    between holders in the same order they were awarded and spent.
    """
    state = GameState(game_id="")
    for event in events:
        payload = event.payload
        if event.type is EventType.GAME_STARTED:
            state.game_id = event.game_id
            for pid in payload.get("players", []):
                state.players[pid] = PlayerState(
                    player_id=pid, name=pid.capitalize()
                )
                state.alive_players.add(pid)
        elif event.type is EventType.ROLE_ASSIGNED and event.actor:
            role = payload.get("role")
            if role is not None:
                state.roles[event.actor] = Role(role)
            if payload.get("ambition"):
                state.ambitions[event.actor] = str(payload["ambition"])
        elif event.type is EventType.ROLE_RECRUITED and event.actor:
            state.roles[event.actor] = Role.TRAITOR
        elif event.type is EventType.FINALE_STARTED:
            state.finale = True
        elif event.type is EventType.ROUND_STARTED:
            state.round_number = max(state.round_number, event.round)
        elif event.type is EventType.PLAYER_ELIMINATED and event.actor:
            state.players[event.actor].alive = False
            state.alive_players.discard(event.actor)
            state.eliminated_players.add(event.actor)
            if state.finale:
                # Blind finale: the role stays hidden until the game ends.
                state.finale_eliminated.add(event.actor)
        elif event.type is EventType.MISSION_COMPLETED:
            state.missions.append(
                MissionState(
                    round_number=event.round,
                    completed=True,
                    outcome=bool(payload.get("success")),
                )
            )
        elif event.type is EventType.ITEM_AWARDED and event.actor:
            item = str(payload.get("item", ""))
            if item:
                state.items.setdefault(event.actor, []).append(item)
        elif event.type in (
            EventType.SHIELD_BLOCKED,
            EventType.DAGGER_USED,
        ) and event.actor:
            spent = (
                "shield" if event.type is EventType.SHIELD_BLOCKED else "dagger"
            )
            held = state.items.get(event.actor, [])
            if spent in held:
                held.remove(spent)
    if state.round_number:
        # The fold leaves round_number on the last round that started.
        # The phase we resume belongs to that round, and the engine
        # increments on start_round, so step back one.
        state.round_number -= 1
    state.phase = GamePhase.SETUP
    return state


def _fold_bookkeeping(events: list[Event], point: ResumePoint) -> None:
    """Recover the counters the engine holds outside `GameState`."""
    awarded = 0
    for event in events:
        if event.type is EventType.ROLE_RECRUITED:
            point.recruits_used += 1
        elif event.type is EventType.SEER_CHECK and event.actor:
            point.seer_checks_done.add(event.actor)
        elif event.type is EventType.ITEM_AWARDED:
            awarded += 1
            point.awarded_round = max(point.awarded_round, event.round)
    point.award_index = awarded % len(ITEM_ORDER)
    # A player whose ROLE_ASSIGNED predates the ambition field cannot
    # have it rebuilt. Say so rather than inventing one.
    point.lost_ambitions = sorted(
        pid
        for pid, role in point.state.roles.items()
        if role is Role.TRAITOR and pid not in point.state.ambitions
    )


def load_point(
    run_dir: str | Path,
    phase_order: list[str],
    *,
    idle_seconds: int = DEFAULT_IDLE_SECONDS,
) -> tuple[ResumePoint, int]:
    """Truncate a run's log to its last consistent point and describe it.

    Returns the resume point and the last surviving event sequence, so
    the caller can continue numbering from there.

    `idle_seconds` guards the destructive step. Resuming rewrites the
    log, and a run that is still going writes to that same file; two
    writers on one log means interleaved events and a corrupted replay.
    A log untouched for that long is taken to be dead. The threshold is
    generous because the phases in these runs are measured in minutes.
    """
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        raise ResumeError(f"no event log at {path}")
    idle = time.time() - path.stat().st_mtime
    if idle < idle_seconds:
        raise ResumeError(
            f"{path} was written {int(idle)}s ago, which is under the "
            f"{idle_seconds}s idle threshold; this run still looks alive. "
            "Stop it first, or pass a longer idle_seconds."
        )
    events = EventLog(path).read_all()
    point = find_resume_point(events, phase_order)
    return point, truncate_to(path, point.index)
