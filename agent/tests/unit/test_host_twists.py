"""Rule-based host twists: weak-tower recruitment and no-murder cooldowns."""

from __future__ import annotations

from simulation.engine.game_engine import GameEngine
from simulation.engine.state import Role
from simulation.experiments.config import GameConfig
from simulation.persistence.sink import EventSink


def make_engine(**overrides) -> GameEngine:
    config = GameConfig(
        game={
            "players": 9,
            "traitors": 3,
            "recruit_choice": True,
            "recruit_window": True,
            "max_recruits": 0,
            **overrides,
        },
        seed=7,
    )
    engine = GameEngine(config, EventSink("game-001"), seed=7)
    engine.start()
    return engine


def _traitors(engine: GameEngine) -> list[str]:
    return [
        p for p in sorted(engine.state.alive_players)
        if engine.state.roles.get(p) is Role.TRAITOR
    ]


def test_weak_tower_skips_the_recruit_cooldown() -> None:
    engine = make_engine(recruit_weak_tower_threshold=1)
    traitors = _traitors(engine)
    engine.eliminate(traitors[0], method="vote")
    engine.eliminate(traitors[1], method="vote")
    engine.spend_recruit_window()  # clear the window the banishment opened
    assert engine.recruit_window_open() is True
    assert engine.recruit_window_host_imposed() is True


def test_without_the_twist_the_window_stays_closed() -> None:
    engine = make_engine(recruit_weak_tower_threshold=0)
    traitors = _traitors(engine)
    engine.eliminate(traitors[0], method="vote")
    engine.eliminate(traitors[1], method="vote")
    engine.spend_recruit_window()
    assert engine.recruit_window_open() is False
    assert engine.recruit_window_host_imposed() is False


def test_murder_cooldown_fires_and_repeats() -> None:
    engine = make_engine(murder_cooldown_after_rounds=3)
    while engine.state.round_number < 3:
        engine.start_round()
    assert engine.murder_cooldown_active() is True

    engine.apply_murder_cooldown()
    assert engine.state.last_murder_cooldown_round == 3
    assert engine.murder_cooldown_active() is False

    while engine.state.round_number < 6:
        engine.start_round()
    assert engine.murder_cooldown_active() is True
