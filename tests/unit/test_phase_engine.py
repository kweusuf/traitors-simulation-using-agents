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
    payload = won[-1].payload
    assert payload["team"] == "traitor"
    assert payload["reason"] == "round_limit"
    assert payload["finale"] is False
    assert payload["solo"] is False  # both traitors survived
    assert payload["surviving_traitors"] == ["bob", "eve"]


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


def test_resume_reopens_the_round_instead_of_announcing_it_again() -> None:
    """A resumed run continues the interrupted round; it does not restart it.

    The bug this pins: `restore_resume` leaves the engine one round behind
    the round the crash happened in, and the resume path used to call
    `start_round`, which opened that same round a second time. The log
    then held two `ROUND_STARTED` for one round, and replaying the whole
    round spent the calls its completed phases had already made.
    """
    phases = ["mission", "public_discussion", "voting", "elimination"]
    engine, log, phase_engine, context = build(phases, max_rounds=2)
    # Restored into round 2, whose ROUND_STARTED the crashed run already
    # wrote: mission finished and the crash came during public_discussion.
    engine._started = True
    engine.state.round_number = 1
    engine.sink.emit(EventType.ROUND_STARTED, round_number=2, phase="")

    resume_phase = phases.index("public_discussion")
    asyncio.run(phase_engine.run(context, resume_at=(1, resume_phase)))

    # Round 2 keeps the single announcement it already had; only round 3
    # is announced by the resumed run.
    announced = [
        e.round for e in engine.sink.events if e.type is EventType.ROUND_STARTED
    ]
    assert announced == [2, 3]
    # Only the phases at and after the cut ran again in round 2: mission,
    # which had already finished, was not replayed.
    assert [name for _, name in log] == [
        "public_discussion", "voting", "elimination",  # remainder of round 2
        "mission", "public_discussion", "voting", "elimination",  # round 3
    ]


def _finale_engine(phases: list[str], extra_phases: list[str] = (), **game_overrides):
    """An engine restored into finale round 5, whose ROUND_STARTED is on record."""
    game = {"players": 4, "traitors": 1, "max_rounds": 1, "finale_max_votes": 1}
    game.update(game_overrides)
    config = GameConfig(game=game, phases=phases, seed=1)
    db = Database()
    engine = GameEngine(config, EventSink("game-finale", db=db), db=db, seed=1)
    log: list = []
    registry = {
        name: RecordingPhase(name, log) for name in [*phases, *extra_phases]
    }
    phase_engine = PhaseEngine(config, engine, registry)
    context = PhaseContext(engine=engine, config=config)

    engine._started = True
    engine.state.finale = True
    engine.sink.emit(
        EventType.FINALE_STARTED,
        round_number=5,
        phase="",
        payload={"traitors": [], "faithful": []},
    )
    # Restored one round behind the finale round the crash happened in, whose
    # ROUND_STARTED the crashed run already wrote.
    engine.state.round_number = 4
    engine.sink.emit(EventType.ROUND_STARTED, round_number=5, phase="")
    return engine, log, phase_engine, context


def test_resume_inside_a_finale_reopens_the_round_too() -> None:
    """The round-reopen fix must cover the finale loops, which open their own.

    `run` breaks out of the normal loop the moment `state.finale` is set, so a
    resume that lands inside a finale never reaches the normal `reopen_round`
    branch. The finale loops used to call `start_round` unconditionally and so
    wrote the same duplicate `ROUND_STARTED` the normal loop was fixed for.
    """
    phases = ["round_table", "private_chat", "voting", "elimination"]
    engine, log, phase_engine, context = _finale_engine(phases)

    asyncio.run(phase_engine.run(context, resume_at=(4, 0)))

    # No second ROUND_STARTED for round 5, and only its finale phases ran.
    announced = [
        e.round for e in engine.sink.events if e.type is EventType.ROUND_STARTED
    ]
    assert announced == [5]
    assert [name for _, name in log] == phases
    assert {rnd for rnd, _ in log} == {5}


def test_resume_skips_finale_phases_that_already_ended() -> None:
    """A resumed finale round picks up at the cut, not from its first phase.

    The finale order is not `config.phases`, so `phase_index` cannot place the
    cut; `resume_completed` names what already ended so those phases are not
    run (and paid for) again.
    """
    phases = ["round_table", "private_chat", "voting", "elimination"]
    engine, log, phase_engine, context = _finale_engine(phases)

    asyncio.run(
        phase_engine.run(
            context,
            resume_at=(4, 0),
            resume_completed=("round_table", "private_chat"),
        )
    )

    # Only the phases after the cut re-ran, all in the interrupted round.
    assert [name for _, name in log] == ["voting", "elimination"]
    assert {rnd for rnd, _ in log} == {5}
    announced = [
        e.round for e in engine.sink.events if e.type is EventType.ROUND_STARTED
    ]
    assert announced == [5]


def test_resume_after_a_finale_end_vote_runs_only_the_banishment_tail() -> None:
    """A completed end_vote means a banishment was asked for.

    The reopened round's vote record is empty, so the decision cannot be read
    back off it; `resume_completed` is what says the end vote already happened
    and only its voting/elimination tail is left.
    """
    phases = ["round_table", "private_chat", "voting", "elimination"]
    engine, log, phase_engine, context = _finale_engine(
        phases, extra_phases=["end_vote"], endgame_vote=True
    )

    asyncio.run(
        phase_engine.run(
            context,
            resume_at=(4, 0),
            resume_completed=("round_table", "private_chat", "end_vote"),
        )
    )

    assert [name for _, name in log] == ["voting", "elimination"]
    assert {rnd for rnd, _ in log} == {5}
    announced = [
        e.round for e in engine.sink.events if e.type is EventType.ROUND_STARTED
    ]
    assert announced == [5]


