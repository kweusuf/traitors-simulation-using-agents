"""Unit tests for the one-glance timeline digest (`timeline.md`)."""

from __future__ import annotations

from simulation.experiments.timeline import build_timeline, write_timeline
from simulation.persistence.event_log import Event, EventType

ROSTER = ["alice", "bob", "charlie", "david"]


def ev(
    seq: int,
    type_: EventType,
    *,
    round: int = 0,
    phase: str = "",
    actor: str | None = None,
    targets: list[str] | None = None,
    **payload,
) -> Event:
    return Event(
        event_id=f"e{seq}",
        game_id="game-001",
        sequence=seq,
        round=round,
        phase=phase,
        type=type_,
        actor=actor,
        targets=targets or [],
        payload=payload,
    )


def finished_events() -> list[Event]:
    """A short finished game: one banishment, one night kill, a win."""
    return [
        ev(1, EventType.GAME_STARTED, players=ROSTER),
        ev(2, EventType.ROLE_ASSIGNED, actor="alice", role="traitor"),
        ev(3, EventType.ROLE_ASSIGNED, actor="bob", role="faithful"),
        ev(4, EventType.ROUND_STARTED, round=1),
        ev(5, EventType.MISSION_STARTED, round=1, phase="mission"),
        ev(6, EventType.MISSION_COMPLETED, round=1, phase="mission", success=True),
        ev(7, EventType.MISSION_COMPLETED, round=1, phase="mission", success=False),
        ev(
            8,
            EventType.PLAYER_ELIMINATED,
            round=1,
            phase="voting",
            actor="charlie",
            method="vote",
            votes={"alice": 2, "charlie": 2},
        ),
        ev(9, EventType.TRAITOR_KILL, round=1, phase="traitor_night",
           targets=["david"]),
        ev(10, EventType.PLAYER_ELIMINATED, round=1, phase="elimination",
           actor="david", method="night"),
        ev(11, EventType.GAME_WON, round=1, phase="end_vote",
           team="traitor", reason="elimination", surviving_traitors=["alice"]),
        ev(12, EventType.GAME_ENDED, round=1, phase="game_end",
           winner="traitor", winning_team="traitor", rounds=1),
    ]


def test_timeline_reports_identity_and_verdict() -> None:
    report = build_timeline(
        "game-001",
        finished_events(),
        {"random_seed": 42, "model": "fake", "experiment_id": "exp-1"},
    )
    assert report.startswith("# Timeline: game-001")
    assert "seed: 42" in report
    assert "**Winner: traitor**" in report
    assert "surviving traitors: alice" in report
    assert "1 rounds" in report


def test_timeline_groups_milestones_by_round_and_drops_noise() -> None:
    report = build_timeline("game-001", finished_events())
    # Setup section from round 0.
    assert "## Round 0 - setup" in report
    assert "Traitors: alice" in report
    # Mission results are batched, not one line per mission.
    assert "- Missions: 1 succeeded, 1 failed" in report
    # Ballot detail survives; speech and bookkeeping do not.
    assert "charlie banished by the vote" in report
    assert "Traitors killed david" in report
    assert "PUBLIC_MESSAGE" not in report
    assert "MISSION_STARTED" not in report
    # The night murder is narrated once, not twice.
    assert report.count("david") == 1  # only the TRAITOR_KILL line names him


def test_timeline_unfinished_run_says_so() -> None:
    events = finished_events()[:9]  # stops mid-game: no GAME_WON / GAME_ENDED
    report = build_timeline("game-001", events)
    assert "**Unfinished**" in report
    assert "no winner recorded" in report
    # The rounds that did happen are still there.
    assert "## Round 1" in report


def test_timeline_is_deterministic() -> None:
    events = finished_events()
    assert build_timeline("game-001", events) == build_timeline("game-001", events)


def test_write_timeline_writes_file(tmp_path) -> None:
    path = write_timeline(tmp_path, finished_events(), {"random_seed": 7})
    assert path.name == "timeline.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text == build_timeline(tmp_path.name, finished_events(),
                                  {"random_seed": 7})
