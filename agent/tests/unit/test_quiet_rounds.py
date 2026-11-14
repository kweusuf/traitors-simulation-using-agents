"""Seasonal cadence: configured quiet rounds skip the murder night and
the round-table banishment (phase 24)."""

from __future__ import annotations

import asyncio

from simulation.actions.actions import Action, ActionType
from simulation.engine.phase_engine import PhaseContext
from simulation.engine.state import GamePhase
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.experiments.config import GameConfig, load_config
from simulation.experiments.quality import analyse
from simulation.experiments.replay import render_transcript
from simulation.persistence.database import Database
from simulation.persistence.event_log import Event, EventType
from simulation.persistence.sink import EventSink


def make_env(players: int = 6, traitors: int = 2, **game_overrides):
    config = GameConfig(
        game={"players": players, "traitors": traitors, **game_overrides},
        seed=42,
    )
    db = Database()
    env = TraitorsEnvironment(config, EventSink("game-001", db=db), db=db, seed=42)
    env.initialize()
    return env


def recording_callback():
    """Callback that records every request and answers with a valid action."""
    calls: list[tuple[str, ActionType]] = []

    async def callback(agent_id: str, action_type: ActionType, targets: list[str]):
        calls.append((agent_id, action_type))
        if action_type is ActionType.TRAITOR_MESSAGE:
            return Action(
                action=ActionType.TRAITOR_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} plots the kill",
            )
        return Action(
            action=action_type,
            actor_id=agent_id,
            target=targets[0] if targets else None,
        )

    return callback, calls


def context_for(env) -> PhaseContext:
    callback, calls = recording_callback()
    context = PhaseContext(
        engine=env.engine, config=env.config, request_action=callback
    )
    return context, calls


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


def test_quiet_rounds_default_off() -> None:
    config = GameConfig()
    assert config.game.quiet_murder_rounds == []
    assert config.game.quiet_banishment_rounds == []


def test_season_and_long_game_configs_set_the_show_cadence() -> None:
    season = load_config("configs/traitors/season_uk_s01.yaml")
    assert season.game.quiet_murder_rounds == [1]
    assert season.game.quiet_banishment_rounds == [8]

    long_game = load_config("configs/traitors/long_game.yaml")
    assert long_game.game.quiet_murder_rounds == [1]
    assert long_game.game.quiet_banishment_rounds == []


# ----------------------------------------------------------------------
# Quiet murder round
# ----------------------------------------------------------------------


def test_quiet_murder_round_asks_nothing_and_emits_the_skip() -> None:
    env = make_env(quiet_murder_rounds=[1])
    engine = env.engine
    context, calls = context_for(env)

    engine.start_round()
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    result = asyncio.run(env.phases()["traitor_night"].run(context))

    assert result == {"victim": None}
    assert calls == []  # no council, no nomination, no kill request
    skips = [e for e in engine.sink.events if e.type is EventType.MURDER_SKIPPED]
    assert len(skips) == 1
    assert skips[0].payload == {"reason": "quiet_round"}
    types = [e.type for e in engine.sink.events]
    assert EventType.TRAITOR_KILL not in types
    assert EventType.PRIVATE_MESSAGE not in types  # no council line
    assert not engine.state.eliminated_players


# ----------------------------------------------------------------------
# Quiet banishment round
# ----------------------------------------------------------------------


def test_quiet_banishment_round_casts_no_votes_and_emits_the_skip() -> None:
    env = make_env(quiet_banishment_rounds=[1])
    engine = env.engine
    context, calls = context_for(env)

    engine.start_round()
    engine.begin_phase(GamePhase.VOTING)
    asyncio.run(env.phases()["voting"].run(context))
    engine.end_phase()
    engine.begin_phase(GamePhase.ELIMINATION)
    asyncio.run(env.phases()["elimination"].run(context))

    assert calls == []  # nobody was asked for a vote
    assert engine.state.votes == {}
    assert not engine.state.eliminated_players
    skips = [e for e in engine.sink.events if e.type is EventType.BANISHMENT_SKIPPED]
    assert len(skips) == 1
    assert skips[0].payload == {"reason": "quiet_round"}
    assert EventType.VOTE_CAST not in [e.type for e in engine.sink.events]


# ----------------------------------------------------------------------
# Normal rounds keep working
# ----------------------------------------------------------------------


def test_normal_rounds_still_murder_and_vote() -> None:
    env = make_env(quiet_murder_rounds=[2], quiet_banishment_rounds=[2])
    engine = env.engine
    context, calls = context_for(env)

    engine.start_round()
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    night = asyncio.run(env.phases()["traitor_night"].run(context))
    assert night["victim"] is not None
    assert any(e.type is EventType.TRAITOR_KILL for e in engine.sink.events)

    engine.begin_phase(GamePhase.VOTING)
    asyncio.run(env.phases()["voting"].run(context))
    assert engine.state.votes  # every living player voted
    assert any(e.type is EventType.VOTE_CAST for e in engine.sink.events)
    assert not any(e.type is EventType.MURDER_SKIPPED for e in engine.sink.events)
    assert not any(e.type is EventType.BANISHMENT_SKIPPED for e in engine.sink.events)


def test_a_later_quiet_round_is_honoured() -> None:
    env = make_env(quiet_murder_rounds=[2])
    engine = env.engine
    context, calls = context_for(env)

    engine.start_round()  # round 1: normal
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    asyncio.run(env.phases()["traitor_night"].run(context))
    assert engine.state.eliminated_players  # the round 1 kill happened

    engine.start_round()  # round 2: quiet
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    result = asyncio.run(env.phases()["traitor_night"].run(context))
    assert result == {"victim": None}
    assert engine.state.round_number == 2
    skips = [e for e in engine.sink.events if e.type is EventType.MURDER_SKIPPED]
    assert len(skips) == 1


# ----------------------------------------------------------------------
# Transcripts and quality
# ----------------------------------------------------------------------


def test_transcript_renders_both_skips() -> None:
    events = [
        Event(
            event_id="e1",
            game_id="game-001",
            sequence=1,
            round=1,
            type=EventType.ROUND_STARTED,
        ),
        Event(
            event_id="e2",
            game_id="game-001",
            sequence=2,
            round=1,
            type=EventType.MURDER_SKIPPED,
            payload={"reason": "quiet_round"},
        ),
        Event(
            event_id="e3",
            game_id="game-001",
            sequence=3,
            round=1,
            type=EventType.BANISHMENT_SKIPPED,
            payload={"reason": "quiet_round"},
        ),
    ]
    text = render_transcript(events)
    assert "no murder this round (quiet_round)" in text
    assert "no banishment this round (quiet_round)" in text


def test_quality_analysis_tolerates_the_skip_events() -> None:
    env = make_env(quiet_murder_rounds=[1], quiet_banishment_rounds=[1])
    engine = env.engine
    context, _ = context_for(env)

    engine.start_round()
    engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    asyncio.run(env.phases()["traitor_night"].run(context))
    engine.begin_phase(GamePhase.VOTING)
    asyncio.run(env.phases()["voting"].run(context))

    report = analyse(engine.sink.events)
    assert isinstance(report["hallucination_score"], float)
    assert report["messages_checked"] == 0
