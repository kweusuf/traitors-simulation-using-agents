"""Sequential traitor council (audit fix, phase 3).

The show's traitors argue a kill out loud: somebody names a suspect,
the others push back, and the team lands on one victim. The engine used
to ask everyone at once, so nobody could read anybody else, and a 1-1-1
split was settled by submission order without anybody being told. These
tests cover the two-round council that replaces it: round-1 proposals
on the traitor channel, round 2 hold-or-switch from everyone at once, a
majority of the final picks, and the proposal/switch/dissent record
that the clash metrics read.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from simulation.actions.actions import Action, ActionType
from simulation.communication.channels import Channel
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import GamePhase, Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.experiments.config import GameConfig
from simulation.experiments.replay import render_transcript
from simulation.persistence.database import Database
from simulation.persistence.event_log import EventType
from simulation.persistence.sink import EventSink


def make_env(players: int = 6, traitors: int = 3, seed: int = 42, **game_overrides):
    config = GameConfig(
        game={"players": players, "traitors": traitors, **game_overrides},
        seed=seed,
    )
    db = Database()
    env = TraitorsEnvironment(config, EventSink("game-001", db=db), db=db, seed=seed)
    env.initialize()
    return env


def events_of_type(env, type_) -> list:
    return [e for e in env.engine.sink.events if e.type is type_]


def team_of(env, role: Role) -> list[str]:
    return sorted(p for p, r in env.engine.state.roles.items() if r is role)


def run_night(env, callback) -> dict:
    context = PhaseContext(
        engine=env.engine, config=env.config, request_action=callback
    )
    env.engine.begin_phase(GamePhase.TRAITOR_NIGHT)
    return asyncio.run(env.phases()["traitor_night"].run(context))


def council_callback(
    proposals: Optional[dict[str, str]] = None,
    finals: Optional[dict[str, str]] = None,
):
    """Scripted two-round council.

    Round 1 proposes from `proposals`, round 2 finalises from `finals`
    (told apart by the host line each round carries); anyone missing
    from a script picks the first legal target. Every ask is recorded
    on `callback.calls` as (actor, action, host line).
    """
    proposals = proposals or {}
    finals = finals or {}
    calls: list[tuple[str, ActionType, str]] = []

    async def callback(
        agent_id: str,
        action_type: ActionType,
        targets: list[str],
        extra_instruction: Optional[str] = None,
    ):
        line = extra_instruction or ""
        calls.append((agent_id, action_type, line))
        if action_type is ActionType.TRAITOR_MESSAGE:
            return Action(
                action=action_type,
                actor_id=agent_id,
                content=f"{agent_id} argues the kill",
            )
        script = proposals if "round 1" in line else finals
        target = script.get(agent_id) or next(iter(targets), None)
        return Action(
            action=action_type,
            actor_id=agent_id,
            target=target,
            content=f"{agent_id} names {target}",
        )

    callback.calls = calls
    return callback


# ----------------------------------------------------------------------
# The flag and the default it protects
# ----------------------------------------------------------------------


def test_council_deliberation_is_off_by_default() -> None:
    assert GameConfig().game.council_deliberation is False


def test_flag_off_keeps_the_blind_concurrent_council() -> None:
    env = make_env(players=6, traitors=3)
    faithful = team_of(env, Role.FAITHFUL)[0]
    traitors = team_of(env, Role.TRAITOR)
    callback = council_callback(finals={t: faithful for t in traitors})

    result = run_night(env, callback)

    assert events_of_type(env, EventType.COUNCIL_PROPOSAL) == []
    assert env.engine.council_proposals() == {}
    kill = events_of_type(env, EventType.TRAITOR_KILL)[0]
    # The base payload is untouched, so nothing downstream changed.
    assert set(kill.payload) == {"choices", "counts"}
    # One argument each (concurrent), then one choice each.
    kinds = [action for _, action, _ in callback.calls]
    assert kinds == [ActionType.TRAITOR_MESSAGE] * 3 + [ActionType.TRAITOR_KILL] * 3
    assert result["victim"] == faithful


# ----------------------------------------------------------------------
# Round 1: proposals, in turn, on the traitor channel
# ----------------------------------------------------------------------


def test_round_one_is_asked_in_turn_and_listens_to_the_room() -> None:
    env = make_env(players=6, traitors=3, council_deliberation=True)
    traitors = team_of(env, Role.TRAITOR)
    faithful = team_of(env, Role.FAITHFUL)
    callback = council_callback(
        proposals={traitors[0]: faithful[0], traitors[1]: faithful[0]},
        finals={t: faithful[0] for t in traitors},
    )

    run_night(env, callback)

    kills = [
        (actor, line)
        for actor, action, line in callback.calls
        if action is ActionType.TRAITOR_KILL
    ]
    # Round 1 runs one traitor at a time, in id order: three proposals.
    assert [actor for actor, _ in kills[:3]] == traitors
    assert all("Council, round 1" in line for _, line in kills[:3])
    assert all("Council, round 2" in line for _, line in kills[3:])
    assert len(kills) == 6

    # The second traitor is told what the first put on the table, and
    # the third about both: nobody proposes blind.
    assert f"{traitors[0]} proposes {faithful[0]}" in kills[1][1]
    assert f"{traitors[1]} proposes {faithful[0]}" in kills[2][1]
    # The blind free-for-all line is gone: round 1 is the argument.
    assert all(action is not ActionType.TRAITOR_MESSAGE for _, action, _ in callback.calls)

    assert len(events_of_type(env, EventType.COUNCIL_PROPOSAL)) == 3
    assert events_of_type(env, EventType.ACTION_REJECTED) == []


def test_proposals_land_on_the_traitor_channel_only() -> None:
    env = make_env(players=6, traitors=3, council_deliberation=True)
    traitors = team_of(env, Role.TRAITOR)
    faithful = team_of(env, Role.FAITHFUL)
    callback = council_callback(finals={t: faithful[0] for t in traitors})

    run_night(env, callback)

    council = [
        m for m in env.engine.router.messages if m.channel is Channel.ROLE_PRIVATE
    ]
    assert len(council) == 3  # one proposal per traitor, nothing else
    assert {m.sender_id for m in council} == set(traitors)
    for player in faithful:
        assert [
            m
            for m in env.engine.router.visible_to(player)
            if m.channel is Channel.ROLE_PRIVATE
        ] == []


def test_round_two_names_every_proposal_and_your_own() -> None:
    env = make_env(players=6, traitors=3, council_deliberation=True)
    traitors = team_of(env, Role.TRAITOR)
    faithful = team_of(env, Role.FAITHFUL)
    callback = council_callback(
        proposals={
            traitors[0]: faithful[0],
            traitors[1]: faithful[1],
            traitors[2]: faithful[2],
        },
        finals={t: faithful[0] for t in traitors},
    )

    run_night(env, callback)

    verdicts = [
        line
        for _, action, line in callback.calls
        if action is ActionType.TRAITOR_KILL and "Council, round 2" in line
    ]
    assert len(verdicts) == 3
    for actor, line in zip(traitors, verdicts):
        assert "majority of the final picks wins" in line
        assert f"{traitors[0]} proposes {faithful[0]}" in line
        assert "Your own proposal was" in line
    # Each line names that traitor's own proposal, not someone else's.
    own = {
        actor: line.split("Your own proposal was ")[1].split(".")[0]
        for actor, line in zip(traitors, verdicts)
    }
    assert own == {
        traitors[0]: faithful[0],
        traitors[1]: faithful[1],
        traitors[2]: faithful[2],
    }


def test_lone_traitor_skips_the_council() -> None:
    env = make_env(players=4, traitors=1, council_deliberation=True)
    callback = council_callback(finals={})

    run_night(env, callback)

    assert [action for _, action, _ in callback.calls] == [ActionType.TRAITOR_KILL]
    assert events_of_type(env, EventType.COUNCIL_PROPOSAL) == []
