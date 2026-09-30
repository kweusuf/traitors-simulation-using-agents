"""Unit tests for resuming a crashed run (audit fix, phase 5).

The behaviour worth pinning down here is not "it starts again" but the
two things that make a resume trustworthy: it must never re-banish
somebody whose elimination phase was cut off mid-flight, and the event
log it produces afterwards has to fold to a board that matches what the
players were told.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from simulation.engine.state import Role
from simulation.experiments.config import GameConfig, load_config
from simulation.experiments.resume import (
    DEFAULT_IDLE_SECONDS,
    ResumeError,
    find_resume_point,
    load_point,
    rebuild_state,
    truncate_to,
)
from simulation.experiments.runner import GameRunner
from simulation.models.base import ChatMessage
from simulation.models.fake import PromptScriptProvider
from simulation.models.llm import LLMResponse, ModelConfig
from simulation.persistence.database import Database
from simulation.persistence.event_log import Event, EventType

PERSONAS_DIR = Path("configs/personas")
PHASES = ["mission", "public_discussion", "round_table", "private_chat", "voting"]


def fake_config(**overrides) -> GameConfig:
    config = load_config("configs/traitors/basic.yaml")
    config.llm.provider = "fake"
    config.llm.retries = 0
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def make_runner(tmp_path: Path, config: GameConfig, provider=None) -> GameRunner:
    return GameRunner(
        config,
        runs_dir=tmp_path / "runs",
        db=Database(),
        personas_dir=PERSONAS_DIR,
        provider=provider,
    )


def ev(sequence: int, type_: EventType, **kwargs) -> Event:
    return Event(
        event_id=f"evt-{sequence:05d}",
        game_id="game-001",
        sequence=sequence,
        round=kwargs.get("round", 1),
        phase=kwargs.get("phase", ""),
        type=type_,
        actor=kwargs.get("actor"),
        targets=kwargs.get("targets", []),
        payload=kwargs.get("payload", {}),
    )


def opening() -> list[Event]:
    """A round-one log ending cleanly after the public discussion."""
    return [
        ev(0, EventType.GAME_STARTED, payload={"players": ["ana", "ben", "cara"]}),
        ev(1, EventType.PHASE_STARTED, payload={"phase": "mission"}),
        ev(2, EventType.ROUND_STARTED),
        ev(3, EventType.PHASE_STARTED, payload={"phase": "public_discussion"}),
        ev(4, EventType.PUBLIC_MESSAGE, actor="ana", targets=["ben", "cara"]),
        ev(5, EventType.PHASE_ENDED, payload={"phase": "public_discussion"}),
    ]


# ----------------------------------------------------------------------
# Finding the resume point
# ----------------------------------------------------------------------


def test_resume_point_is_the_last_completed_phase() -> None:
    events = opening()
    point = find_resume_point(events, PHASES)

    assert point.index == len(events)  # nothing had to be discarded
    assert point.phase_index == PHASES.index("public_discussion") + 1
    assert point.state.round_number == 0  # start_round will open round 1


def test_resume_point_discards_a_phase_cut_off_mid_flight() -> None:
    events = opening() + [
        ev(6, EventType.PHASE_STARTED, payload={"phase": "voting"}),
        ev(7, EventType.VOTE_CAST, actor="ana", targets=["ben"]),
    ]
    point = find_resume_point(events, PHASES)

    assert point.index == 6  # the half-cast vote is gone
    # A phase that never ended is redone from its start, not from where
    # it broke: the round table and the side conversations run again so
    # the vote is cast against the same board everyone last saw.
    assert point.phase_index == PHASES.index("round_table")


def test_a_half_run_elimination_never_banishes_twice() -> None:
    """The one that matters: no double elimination on replay."""
    events = opening() + [
        ev(6, EventType.PHASE_STARTED, payload={"phase": "voting"}),
        ev(7, EventType.VOTE_CAST, actor="ana", targets=["ben"]),
        ev(8, EventType.PHASE_ENDED, payload={"phase": "voting"}),
        ev(9, EventType.PHASE_STARTED, payload={"phase": "elimination"}),
        ev(10, EventType.PLAYER_ELIMINATED, actor="ben", payload={"method": "vote"}),
    ]
    point = find_resume_point(events, PHASES)

    # The banishment is inside an unfinished phase, so the resume starts
    # before it and the vote that caused it runs again from scratch.
    assert "ben" in point.state.alive_players
    assert point.state.eliminated_players == set()
    assert point.phase_index == PHASES.index("voting") + 1


def test_a_completed_elimination_stays_banished() -> None:
    events = opening() + [
        ev(6, EventType.PHASE_STARTED, payload={"phase": "voting"}),
        ev(7, EventType.PHASE_ENDED, payload={"phase": "voting"}),
        ev(8, EventType.PHASE_STARTED, payload={"phase": "elimination"}),
        ev(9, EventType.PLAYER_ELIMINATED, actor="ben", payload={"method": "vote"}),
        ev(10, EventType.PHASE_ENDED, payload={"phase": "elimination"}),
    ]
    point = find_resume_point(events, PHASES)

    assert "ben" not in point.state.alive_players
    assert point.state.eliminated_players == {"ben"}


def test_resume_refuses_a_finished_game() -> None:
    events = opening() + [ev(6, EventType.GAME_ENDED)]
    with pytest.raises(ResumeError, match="already finished"):
        find_resume_point(events, PHASES)


def test_resume_refuses_a_log_with_no_finished_phase() -> None:
    events = opening()[:-1]
    with pytest.raises(ResumeError, match="fresh run"):
        find_resume_point(events, PHASES)


def test_resume_refuses_an_empty_log() -> None:
    with pytest.raises(ResumeError, match="empty"):
        find_resume_point([], PHASES)


# ----------------------------------------------------------------------
# Rebuilding the board
# ----------------------------------------------------------------------


def test_rebuild_recovers_roles_ambitions_and_the_board() -> None:
    events = [
        ev(0, EventType.GAME_STARTED, payload={"players": ["ana", "ben", "cara"]}),
        ev(
            1,
            EventType.ROLE_ASSIGNED,
            actor="ana",
            payload={"role": "traitor", "ambition": "solo"},
        ),
        ev(
            2,
            EventType.ROLE_ASSIGNED,
            actor="ben",
            payload={"role": "faithful", "ambition": "team"},
        ),
        ev(
            3,
            EventType.ROLE_ASSIGNED,
            actor="cara",
            payload={"role": "faithful", "ambition": "team"},
        ),
        ev(4, EventType.PLAYER_ELIMINATED, actor="ben", payload={"method": "vote"}),
        ev(5, EventType.ROLE_RECRUITED, actor="cara", payload={"by": "ana"}),
    ]
    state = rebuild_state(events)

    assert state.roles["ana"] is Role.TRAITOR
    assert state.roles["cara"] is Role.TRAITOR  # recruited
    assert state.ambitions == {"ana": "solo", "ben": "team", "cara": "team"}
    assert state.alive_players == {"ana", "cara"}
    assert state.eliminated_players == {"ben"}


def test_items_follow_awards_and_spends_in_order() -> None:
    events = [
        ev(0, EventType.GAME_STARTED, payload={"players": ["ana", "ben"]}),
        ev(1, EventType.ITEM_AWARDED, actor="ana", payload={"item": "shield"}),
        ev(2, EventType.ITEM_AWARDED, actor="ana", payload={"item": "dagger"}),
        ev(3, EventType.SHIELD_BLOCKED, actor="ana", payload={"target": "ben"}),
    ]
    state = rebuild_state(events)

    assert state.items == {"ana": ["dagger"]}


def test_ambition_is_recorded_so_a_future_log_is_fully_resumable() -> None:
    """`ROLE_ASSIGNED` must carry the seeded ambition from now on."""
    config = fake_config()
    from simulation.engine.game_engine import GameEngine
    from simulation.persistence.sink import EventSink

    sink = EventSink("game-001")
    GameEngine(config, sink, seed=7).start()
    assigned = [e for e in sink.events if e.type is EventType.ROLE_ASSIGNED]

    assert assigned
    assert all(e.payload.get("ambition") in ("solo", "team") for e in assigned)


# ----------------------------------------------------------------------
# Truncation
# ----------------------------------------------------------------------


def test_truncate_rewrites_the_log_and_reports_the_last_sequence(tmp_path) -> None:
    from simulation.persistence.jsonl import EventLog

    path = tmp_path / "events.jsonl"
    for event in opening():
        EventLog(path).append(event)

    last = truncate_to(path, 3)

    assert last == 2
    assert len(EventLog(path).read_all()) == 3


def test_truncate_leaves_a_consistent_log_alone(tmp_path) -> None:
    from simulation.persistence.jsonl import EventLog

    path = tmp_path / "events.jsonl"
    for event in opening():
        EventLog(path).append(event)
    before = path.read_bytes()

    assert truncate_to(path, 99) == 5
    assert path.read_bytes() == before


# ----------------------------------------------------------------------
# Recovering ambitions for logs that predate the field
# ----------------------------------------------------------------------


def legacy_log(traitors: tuple[str, ...] = ("alice", "bob")) -> list[Event]:
    """A pre-feature log: roles recorded, ambitions never written."""
    roster = ("alice", "bob", "charlie", "david")
    events = [ev(0, EventType.GAME_STARTED, payload={"players": list(roster)})]
    for index, pid in enumerate(roster, start=1):
        role = "traitor" if pid in traitors else "faithful"
        events.append(
            ev(index, EventType.ROLE_ASSIGNED, actor=pid, payload={"role": role})
        )
    events.append(
        ev(len(roster) + 1, EventType.PHASE_ENDED, payload={"phase": "mission"})
    )
    return events


def test_seed_replay_recovers_the_ambitions_the_engine_drew() -> None:
    """The replay must match a live engine exactly, not merely look right."""
    from simulation.engine.game_engine import GameEngine
    from simulation.persistence.sink import EventSink

    config = fake_config()
    config.game.traitor_names = ["alice", "bob"]
    sink = EventSink("game-001")
    engine = GameEngine(config, sink, seed=42)
    engine.start()

    roster = list(engine.player_ids)
    live = dict(engine.state.ambitions)
    traitors = [p for p in roster if engine.state.roles[p] is Role.TRAITOR]
    assert sorted(traitors) == ["alice", "bob"]

    point = find_resume_point(legacy_log(), PHASES)
    assert point.lost_ambitions == ["alice", "bob"]
    point.recover_ambitions(42, roster, traitors)

    assert not point.lost_ambitions
    # Same seed, same roster order: every disposition is the one the live
    # engine drew, not merely a plausible-looking one.
    for pid in roster:
        assert point.state.ambitions[pid] == live[pid]


def test_ambition_replay_declines_to_guess_when_roles_were_drawn() -> None:
    """Without pinned traitors the stream is offset, so we do not guess."""
    point = find_resume_point(legacy_log(), PHASES)
    point.recover_ambitions(42, ["alice", "bob", "charlie", "david"], [])

    assert point.lost_ambitions == ["alice", "bob"]
    assert not point.state.ambitions


def test_ambition_replay_declines_when_the_pins_do_not_match_the_log() -> None:
    point = find_resume_point(legacy_log(), PHASES)
    point.recover_ambitions(42, ["alice", "bob", "charlie", "david"], ["charlie"])

    assert point.lost_ambitions == ["alice", "bob"]
    assert not point.state.ambitions


# ----------------------------------------------------------------------
# End to end: crash, then resume
# ----------------------------------------------------------------------


def _cool(run_dir: Path, seconds: int = 3600) -> None:
    """Backdate the log so the idle guard treats the run as dead."""
    path = run_dir / "events.jsonl"
    old = time.time() - seconds
    os.utime(path, (old, old))


class CrashingProvider(PromptScriptProvider):
    """A fake provider that dies after `crash_after` calls.

    This is the shape of the real failure: the transport gives up
    mid-phase, the exception unwinds the run, and the log is left
    holding every event written up to that point.
    """

    def __init__(self, crash_after: int) -> None:
        super().__init__()
        self.crash_after = crash_after
        self.calls_made = 0

    async def generate(self, messages, response_schema, config=None):
        self.calls_made += 1
        if self.calls_made > self.crash_after:
            raise TimeoutError("simulated transport timeout")
        return await super().generate(messages, response_schema, config)


def test_a_crashed_run_resumes_and_finishes(tmp_path) -> None:
    config = fake_config()
    crash_at = 25
    crashed = CrashingProvider(crash_at)

    with pytest.raises(Exception):
        make_runner(tmp_path, config, provider=crashed).run(
            game_id="game-001", seed=42
        )

    run_dir = tmp_path / "runs" / "game-001"
    _cool(run_dir)  # the log is fresh; pretend the crash was hours ago
    partial = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert partial, "the crashed run should have written events"
    assert not any(e["type"] == "GAME_ENDED" for e in partial)

    # Resume with a healthy provider: the game continues and completes.
    result = make_runner(tmp_path, config).run(game_id="game-001", seed=42, resume=True)

    assert result.winner in ("faithful", "traitor", "round_limit_winner")
    events = result.events
    assert events[-1].type is EventType.GAME_ENDED

    # The log stayed a single well-formed stream: sequences are unique
    # and strictly increasing, and every event kept the original id.
    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)
    assert len({e.event_id for e in events}) == len(events)

    # Every role survived the crash, and nobody is banished twice.
    assigned = [e for e in events if e.type is EventType.ROLE_ASSIGNED]
    assert len({e.actor for e in assigned}) == len(assigned)
    banished = [e.actor for e in events if e.type is EventType.PLAYER_ELIMINATED]
    assert len(set(banished)) == len(banished)

    for name in ("game.json", "metrics.json", "transcript.txt"):
        assert (run_dir / name).exists(), f"missing {name} after resume"


def test_resume_keeps_the_roles_and_board_the_log_already_recorded(tmp_path) -> None:
    config = fake_config()
    with pytest.raises(Exception):
        make_runner(tmp_path, config, provider=CrashingProvider(25)).run(
            game_id="game-001", seed=42
        )

    run_dir = tmp_path / "runs" / "game-001"
    # `load_point` truncates, which touches the file; cool it again so the
    # runner's own resume check sees a quiet log too.
    _cool(run_dir)
    point, _ = load_point(run_dir, list(config.phases))
    _cool(run_dir)
    result = make_runner(tmp_path, config).run(game_id="game-001", seed=42, resume=True)

    from simulation.experiments.replay import ReplayState

    folded = ReplayState.from_events(result.events)

    # The roles the resumed game played out are the ones the pre-crash
    # log already dealt: resume continues a game, it does not redeal.
    early = ReplayState.from_events(result.events[: point.index])
    assert folded.roles == early.roles
    assert set(folded.alive).issubset(early.alive)

def test_resume_does_not_reuse_event_ids(tmp_path) -> None:
    config = fake_config()
    with pytest.raises(Exception):
        make_runner(tmp_path, config, provider=CrashingProvider(25)).run(
            game_id="game-001", seed=42
        )
    _cool(tmp_path / "runs" / "game-001")
    result = make_runner(tmp_path, config).run(game_id="game-001", seed=42, resume=True)

    ids = [e.event_id for e in result.events]
    assert len(set(ids)) == len(ids), "resumed events collided with the old log"


def test_a_refused_resume_leaves_the_telemetry_log_untouched(tmp_path) -> None:
    """A failed resume must not touch the run directory at all.

    The telemetry recorder truncates `llm_calls.jsonl` when it is built,
    so anything that can refuse a resume has to run before the recorder
    exists. Otherwise the attempt to recover a crashed run destroys the
    telemetry that explains how it crashed.
    """
    config = fake_config()
    with pytest.raises(Exception):
        make_runner(tmp_path, config, provider=CrashingProvider(25)).run(
            game_id="game-001", seed=42
        )
    run_dir = tmp_path / "runs" / "game-001"
    events = (run_dir / "events.jsonl").read_text()
    calls = (run_dir / "llm_calls.jsonl").read_text()
    assert calls, "the crashed run should have recorded some calls"

    with pytest.raises(ResumeError, match="still looks alive"):
        make_runner(tmp_path, config).run(game_id="game-001", seed=42, resume=True)

    assert (run_dir / "events.jsonl").read_text() == events
    assert (run_dir / "llm_calls.jsonl").read_text() == calls


def test_resume_refuses_to_rewrite_a_log_that_is_still_being_written(tmp_path) -> None:
    """Two writers on one log would interleave events and corrupt replay."""
    config = fake_config()
    with pytest.raises(Exception):
        make_runner(tmp_path, config, provider=CrashingProvider(25)).run(
            game_id="game-001", seed=42
        )
    run_dir = tmp_path / "runs" / "game-001"
    before = (run_dir / "events.jsonl").read_text()

    with pytest.raises(ResumeError, match="still looks alive"):
        make_runner(tmp_path, config).run(game_id="game-001", seed=42, resume=True)

    # Refused means untouched: the log is exactly as the crash left it.
    assert (run_dir / "events.jsonl").read_text() == before


def test_resume_proceeds_once_the_log_has_gone_quiet(tmp_path) -> None:
    config = fake_config()
    with pytest.raises(Exception):
        make_runner(tmp_path, config, provider=CrashingProvider(25)).run(
            game_id="game-001", seed=42
        )
    _cool(tmp_path / "runs" / "game-001", seconds=DEFAULT_IDLE_SECONDS + 60)

    result = make_runner(tmp_path, config).run(game_id="game-001", seed=42, resume=True)
    assert result.events[-1].type is EventType.GAME_ENDED
