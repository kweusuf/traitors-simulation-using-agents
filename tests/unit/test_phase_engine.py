"""Unit tests for the generic phase engine (spec section 7)."""

from __future__ import annotations

import asyncio

from simulation.engine.game_engine import GameEngine
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import GamePhase, Role
from simulation.experiments.config import GameConfig
from simulation.persistence.database import Database
from simulation.persistence.event_log import EventType
from simulation.persistence.sink import EventSink


class RecordingPhase:
    """Stub phase that records calls and optionally declares a winner."""

    def __init__(self, name: str, log: list, winner_in: tuple[int, str] | None = None) -> None:
        self.name = name
        self._log = log
        self._winner_in = winner_in

    async def run(self, context: PhaseContext) -> None:
        engine = context.engine
        self._log.append((engine.state.round_number, self.name))
        if self._winner_in == (engine.state.round_number, self.name):
            # Eliminate every traitor so the faithful win condition fires.
            traitors = [
                p for p, r in engine.state.roles.items() if r is Role.TRAITOR
            ]
            for p in traitors:
                if not engine.is_over:
                    engine.eliminate(p, method="vote")


def build(phases: list[str], winner_in=None, max_rounds: int = 3, seed: int = 42):
    config = GameConfig(
        game={"players": 6, "traitors": 2, "max_rounds": max_rounds},
        phases=phases,
        seed=seed,
    )
    db = Database()
    engine = GameEngine(config, EventSink("game-001", db=db), db=db, seed=seed)
    log: list = []
    registry = {
        name: RecordingPhase(name, log, winner_in) for name in phases
    }
    phase_engine = PhaseEngine(config, engine, registry)
    context = PhaseContext(engine=engine, config=config)
    return engine, log, phase_engine, context


def test_phase_order_follows_config_for_all_rounds() -> None:
    phases = ["mission", "public_discussion", "voting", "elimination"]
    engine, log, phase_engine, context = build(phases, max_rounds=2)
    asyncio.run(phase_engine.run(context))

    expected = phases * 2
    assert [name for _, name in log] == expected
    assert [rnd for rnd, _ in log] == [1] * 4 + [2] * 4
    assert engine.state.round_number == 2


def test_phase_events_and_snapshots_emitted() -> None:
    phases = ["mission", "voting"]
    engine, _, phase_engine, context = build(phases, max_rounds=1)
    asyncio.run(phase_engine.run(context))

    types = [e.type for e in engine.sink.events]
    assert types[0] is EventType.GAME_STARTED
    assert types[-1] is EventType.GAME_ENDED
    started = [e for e in engine.sink.events if e.type is EventType.PHASE_STARTED]
    ended = [e for e in engine.sink.events if e.type is EventType.PHASE_ENDED]
    # 2 configured phases + GAME_END.
    assert len(started) == 3
    assert len(ended) == 2
    assert started[-1].payload["phase"] == "game_end"
    snapshots = [e for e in engine.sink.events if e.type is EventType.SNAPSHOT_CREATED]
    assert len(snapshots) == 2
    # GAME_WON via round limit, after the last phase and before GAME_ENDED.
    won_idx = types.index(EventType.GAME_WON)
    assert won_idx < len(types) - 1
    assert types[-1] is EventType.GAME_ENDED
    assert engine.sink.events[won_idx].payload["reason"] == "round_limit"


def test_game_stops_early_when_winner_declared() -> None:
    phases = ["public_discussion", "voting", "elimination"]
    # Winner declared during round 1 voting phase.
    engine, log, phase_engine, context = build(
        phases, winner_in=(1, "voting"), max_rounds=3
    )
    asyncio.run(phase_engine.run(context))

    assert [name for _, name in log] == ["public_discussion", "voting"]
    assert engine.state.winner is not None
    types = [e.type for e in engine.sink.events]
    assert EventType.ROUND_STARTED not in types[types.index(EventType.GAME_WON) + 1 :]


def test_round_limit_declares_configured_winner() -> None:
    phases = ["mission", "voting"]
    config = GameConfig(
        game={"players": 6, "traitors": 2, "max_rounds": 2, "round_limit_winner": "traitor"},
        phases=phases,
        seed=1,
    )
    db = Database()
    engine = GameEngine(config, EventSink("game-002", db=db), db=db, seed=1)
    registry = {name: RecordingPhase(name, []) for name in phases}
    phase_engine = PhaseEngine(config, engine, registry)
    asyncio.run(phase_engine.run(PhaseContext(engine=engine, config=config)))

    assert engine.state.winning_team is Role.TRAITOR
    assert engine.state.phase is GamePhase.GAME_END
    won = [e for e in engine.sink.events if e.type is EventType.GAME_WON]
    assert won[-1].payload == {"team": "traitor", "reason": "round_limit"}


def test_phase_engine_rejects_unknown_phase_implementation() -> None:
    config = GameConfig(phases=["mission", "voting"])
    engine = GameEngine(config, EventSink("game-003"), seed=1)
    try:
        PhaseEngine(config, engine, {"mission": RecordingPhase("mission", [])})
    except ValueError as exc:
        assert "voting" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing phase implementation")


def test_duplicate_phase_runs_once_per_config_entry() -> None:
    # Three `mission` entries per round = three tasks a day (long_game).
    phases = ["mission", "mission", "mission", "voting", "elimination"]
    engine, log, phase_engine, context = build(phases, max_rounds=2)
    asyncio.run(phase_engine.run(context))

    assert [name for _, name in log] == phases * 2
    assert [rnd for rnd, _ in log] == [1] * 5 + [2] * 5
    started = [
        e for e in engine.sink.events if e.type is EventType.PHASE_STARTED
    ]
    missions = [e for e in started if e.payload["phase"] == "mission"]
    assert len(missions) == 6  # 3 per round over 2 rounds
