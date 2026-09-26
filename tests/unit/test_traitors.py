"""Unit tests for the Traitors environment: phases, legal actions, one
deterministic scripted round (spec section 7, 38)."""

from __future__ import annotations

import asyncio

from simulation.actions.actions import Action, ActionType
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import GamePhase, Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.environments.traitors.rules import action_types_for_phase, legal_targets
from simulation.experiments.config import GameConfig
from simulation.persistence.database import Database
from simulation.persistence.event_log import EventType
from simulation.persistence.repositories import EliminationRepository
from simulation.persistence.sink import EventSink


def make_env(players: int = 4, traitors: int = 1, max_rounds: int = 5, seed: int = 42):
    config = GameConfig(
        game={"players": players, "traitors": traitors, "max_rounds": max_rounds},
        seed=seed,
    )
    db = Database()
    env = TraitorsEnvironment(config, EventSink("game-001", db=db), db=db, seed=seed)
    env.initialize()
    return env, db


# ----------------------------------------------------------------------
# Legal targets
# ----------------------------------------------------------------------


def test_legal_targets_per_action() -> None:
    env, _ = make_env()
    state = env.state
    traitor = next(p for p, r in state.roles.items() if r is Role.TRAITOR)
    faithful = next(p for p, r in state.roles.items() if r is Role.FAITHFUL)

    assert legal_targets(state, faithful, ActionType.PUBLIC_MESSAGE) == []
    assert faithful not in legal_targets(state, faithful, ActionType.PRIVATE_MESSAGE)
    assert faithful not in legal_targets(state, faithful, ActionType.VOTE)
    assert set(legal_targets(state, faithful, ActionType.VOTE)) == (
        state.alive_players - {faithful}
    )

    # Kill only for traitors, never fellow traitors, never self.
    kill_targets = legal_targets(state, traitor, ActionType.TRAITOR_KILL)
    assert traitor not in kill_targets
    assert set(kill_targets) <= state.alive_players - {traitor}
    assert all(state.roles[t] is Role.FAITHFUL for t in kill_targets)
    assert legal_targets(state, faithful, ActionType.TRAITOR_KILL) == []


def test_action_types_for_phase_mapping() -> None:
    assert action_types_for_phase(GamePhase.PUBLIC_DISCUSSION) == [
        ActionType.PUBLIC_MESSAGE
    ]
    assert action_types_for_phase(GamePhase.VOTING) == [ActionType.VOTE]
    assert action_types_for_phase(GamePhase.TRAITOR_NIGHT) == [
        ActionType.TRAITOR_KILL
    ]
    assert action_types_for_phase(GamePhase.MISSION) == []


def test_environment_legal_actions_by_phase() -> None:
    env, _ = make_env()
    env.engine.begin_phase(GamePhase.VOTING)
    traitor = next(p for p, r in env.state.roles.items() if r is Role.TRAITOR)
    faithful = next(p for p, r in env.state.roles.items() if r is Role.FAITHFUL)

    vote_actions = env.legal_actions(faithful)
    assert ActionType.VOTE in vote_actions
    assert faithful not in vote_actions[ActionType.VOTE]

    env.engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    assert ActionType.TRAITOR_KILL not in env.legal_actions(faithful)
    kill_actions = env.legal_actions(traitor)
    assert ActionType.TRAITOR_KILL in kill_actions
    assert all(env.state.roles[t] is Role.FAITHFUL for t in kill_actions[ActionType.TRAITOR_KILL])


# ----------------------------------------------------------------------
# One scripted round through all phases
# ----------------------------------------------------------------------


def scripted_callback(engine):
    async def callback(agent_id: str, action_type: ActionType, targets: list[str]) -> Action:
        if action_type is ActionType.PUBLIC_MESSAGE:
            return Action(
                action=ActionType.PUBLIC_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} speaks",
                confidence=0.5,
            )
        if action_type is ActionType.PRIVATE_MESSAGE:
            target = targets[0]
            return Action(
                action=ActionType.PRIVATE_MESSAGE,
                actor_id=agent_id,
                target=target,
                content=f"{agent_id} whispers to {target}",
            )
        if action_type is ActionType.VOTE:
            # Everyone piles onto the first alive player; that player votes second.
            ordered = sorted(engine.state.alive_players)
            pick = ordered[0] if agent_id != ordered[0] else ordered[1]
            return Action(action=ActionType.VOTE, actor_id=agent_id, target=pick)
        if action_type is ActionType.TRAITOR_KILL:
            return Action(
                action=ActionType.TRAITOR_KILL, actor_id=agent_id, target=targets[0]
            )
        raise AssertionError(f"unexpected action type {action_type}")

    return callback


def test_full_scripted_round() -> None:
    env, db = make_env(players=4, traitors=1, max_rounds=5)
    engine = env.engine
    phase_engine = PhaseEngine(env.config, engine, env.phases())
    context = PhaseContext(
        engine=engine, config=env.config, request_action=scripted_callback(engine)
    )
    asyncio.run(phase_engine.run(context))

    state = engine.state
    # Round 1 ran; the game ended in round 1.
    assert state.round_number == 1
    assert state.winner in ("faithful", "traitor")
    assert state.phase is GamePhase.GAME_END

    # Mission recorded.
    assert state.missions[0].completed and state.missions[0].outcome is True

    # The pile-on vote eliminated the first alphabetical player.
    assert "alice" in state.eliminated_players

    types = [e.type for e in engine.sink.events]
    assert EventType.PUBLIC_MESSAGE in types
    assert EventType.PRIVATE_MESSAGE in types
    assert EventType.VOTE_CAST in types
    assert EventType.GAME_WON in types
    assert types[-1] is EventType.GAME_ENDED

    if state.winner == "traitor":
        # The traitor killed at night, reaching parity.
        assert EventType.TRAITOR_KILL in types
        assert len(state.eliminated_players) == 2
        assert state.winning_team is Role.TRAITOR
    else:
        # The pile-on vote happened to hit the traitor; no night phase ran.
        assert EventType.TRAITOR_KILL not in types
        assert len(state.eliminated_players) == 1
        assert state.winning_team is Role.FAITHFUL

    # Eliminations persisted (mirrored in SQLite).
    stored = EliminationRepository(db).get("game-001")
    assert len(stored) == len(state.eliminated_players)


def test_private_messages_routed_with_isolation() -> None:
    env, _ = make_env(players=4, traitors=1)
    engine = env.engine
    phase_engine = PhaseEngine(env.config, engine, env.phases())
    context = PhaseContext(
        engine=engine, config=env.config, request_action=scripted_callback(engine)
    )

    # Run only through private_chat in round 1.
    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    asyncio.run(env.phases()["public_discussion"].run(context))
    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    asyncio.run(env.phases()["private_chat"].run(context))

    privates = [m for m in engine.router.messages if m.channel.value == "private"]
    assert len(privates) == 4  # one per alive player
    for message in privates:
        others = [
            pid for pid in engine.state.alive_players if pid not in (
                message.sender_id, *message.recipients
            )
        ]
        for outsider in others:
            assert message not in engine.router.visible_to(outsider)


def test_phase_registry_covers_configured_phases() -> None:
    env, _ = make_env()
    registry = env.phases()
    config = GameConfig()  # default ordering
    assert set(config.phases) <= set(registry)
