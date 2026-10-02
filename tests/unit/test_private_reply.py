"""A private reply has to land in the thread it is answering.

The first version of the reply wave handed the whole room back as legal
targets, so the model kept opening new conversations instead of answering
the one it had just been shown: only a fraction of reply messages went
back to the player who had written. These pin the routing down, which is
the part that fix missed.
"""

from __future__ import annotations

import asyncio

from simulation.actions.actions import Action, ActionType
from simulation.communication.channels import Channel
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import GamePhase
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.experiments.config import GameConfig
from simulation.persistence.database import Database
from simulation.persistence.sink import EventSink


def make_env(players: int = 6, traitors: int = 2, seed: int = 42):
    config = GameConfig(
        game={"players": players, "traitors": traitors, "max_rounds": 5},
        seed=seed,
    )
    db = Database()
    env = TraitorsEnvironment(config, EventSink("game-001", db=db), db=db, seed=seed)
    env.initialize()
    return env, db


def private_messages(engine):
    return [m for m in engine.router.messages if m.channel is Channel.PRIVATE]


def run_private_chat(env, callback):
    engine = env.engine
    context = PhaseContext(
        engine=engine, config=env.config, request_action=callback
    )
    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    asyncio.run(env.phases()["private_chat"].run(context))
    return engine


def echo_callback(offered=None):
    """Answer the first legal target, and record what was offered."""

    async def callback(agent_id, action_type, targets):
        if offered is not None:
            offered.setdefault(agent_id, []).append(list(targets))
        if not targets:
            return Action(
                action=ActionType.PUBLIC_MESSAGE,
                actor_id=agent_id,
                content="nothing to say",
            )
        return Action(
            action=action_type,
            actor_id=agent_id,
            target=targets[0],
            content=f"{agent_id} to {targets[0]}",
        )

    return callback


def test_the_reply_wave_only_offers_correspondents() -> None:
    """A player's reply targets are who wrote to them, not the whole room."""
    env, _ = make_env()
    engine = env.engine
    offered: dict[str, list[list[str]]] = {}
    run_private_chat(env, echo_callback(offered))

    privates = private_messages(engine)
    assert privates, "no private traffic at all"

    wrote_to: dict[str, set[str]] = {}
    for m in privates:
        recipient = (m.recipients or [None])[0]
        if recipient and recipient != m.sender_id:
            wrote_to.setdefault(recipient, set()).add(m.sender_id)

    checked = 0
    for agent, waves in offered.items():
        if len(waves) < 2:
            continue
        assert set(waves[1]) <= wrote_to.get(agent, set()), (
            f"{agent} could reply to {waves[1]} but was written to by "
            f"{sorted(wrote_to.get(agent, set()))}"
        )
        checked += 1
    assert checked, "the reply wave never fired, so this proves nothing"


def test_a_reply_wave_never_offers_a_dead_player_or_itself() -> None:
    env, _ = make_env()
    engine = env.engine
    offered: dict[str, list[list[str]]] = {}
    run_private_chat(env, echo_callback(offered))
    for agent, waves in offered.items():
        for targets in waves:
            assert agent not in targets
            for t in targets:
                assert t in engine.state.alive_players


def test_private_conversations_go_both_ways() -> None:
    """The metric the fix exists to move: reciprocity above zero."""
    env, _ = make_env()
    engine = env.engine
    run_private_chat(env, echo_callback())
    directions = {
        (m.sender_id, (m.recipients or [None])[0]) for m in private_messages(engine)
    }
    two_way = {frozenset(d) for d in directions if (d[1], d[0]) in directions}
    assert two_way, "no private conversation went both ways"