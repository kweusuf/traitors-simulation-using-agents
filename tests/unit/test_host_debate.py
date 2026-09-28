"""Hosted debate clock (audit fix, phase 1).

The deterministic host opens the debate, spends the open budget in
round-robin waves, warns with the closing turns left, runs the closing
turns, and closes the debate before the forced vote. Budget 0 keeps the
original one-turn-each phases byte-for-byte.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from simulation.actions.actions import Action, ActionType
from simulation.engine.phase_engine import PhaseContext
from simulation.engine.rules import public_message_limit
from simulation.engine.state import GamePhase
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.experiments.config import GameConfig
from simulation.experiments.replay import render_transcript
from simulation.persistence.database import Database
from simulation.persistence.event_log import EventType
from simulation.persistence.sink import EventSink


def make_env(players: int = 6, traitors: int = 2, seed: int = 42, **game_overrides):
    config = GameConfig(
        game={"players": players, "traitors": traitors, **game_overrides},
        seed=seed,
    )
    db = Database()
    env = TraitorsEnvironment(config, EventSink("game-001", db=db), db=db, seed=seed)
    env.initialize()
    return env


def recording_callback():
    """Callback that records every ask, including the host's timer line."""
    calls: list[tuple[str, ActionType, Optional[str]]] = []

    async def callback(
        agent_id: str,
        action_type: ActionType,
        targets: list[str],
        extra_instruction=None,
    ):
        calls.append((agent_id, action_type, extra_instruction))
        if action_type is ActionType.TRAITOR_MESSAGE:
            return Action(
                action=ActionType.TRAITOR_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} plots",
            )
        if action_type is ActionType.PUBLIC_MESSAGE:
            return Action(
                action=ActionType.PUBLIC_MESSAGE,
                actor_id=agent_id,
                content=f"{agent_id} argues",
            )
        if action_type is ActionType.PRIVATE_MESSAGE:
            peer = next(t for t in targets if t != agent_id)
            return Action(
                action=ActionType.PRIVATE_MESSAGE,
                actor_id=agent_id,
                target=peer,
                content=f"{agent_id} whispers to {peer}",
            )
        pick = next((t for t in targets if t != agent_id), None)
        return Action(
            action=action_type,
            actor_id=agent_id,
            target=pick if pick is not None else (targets[0] if targets else None),
        )

    return callback, calls


def run_phase(env, phase_name: str, callback) -> None:
    context = PhaseContext(
        engine=env.engine, config=env.config, request_action=callback
    )
    env.engine.begin_phase(GamePhase(phase_name))
    asyncio.run(env.phases()[phase_name].run(context))


def events_of_type(env, type_) -> list:
    return [e for e in env.engine.sink.events if e.type is type_]


def public_messages(env) -> list:
    return events_of_type(env, EventType.PUBLIC_MESSAGE)


def host_events(env) -> list:
    return [
        e
        for e in env.engine.sink.events
        if e.type in (EventType.HOST_WARNING, EventType.DEBATE_CLOSED)
    ]


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


def test_debate_clock_is_off_by_default() -> None:
    config = GameConfig()
    assert config.game.discussion_budget == 0
    assert config.game.warning_turns == 1
    assert config.game.nomination_enabled is False
    assert config.game.revote_enabled is False


def test_budget_zero_keeps_the_old_one_turn_each_phase() -> None:
    env = make_env(players=4)
    callback, calls = recording_callback()
    run_phase(env, "public_discussion", callback)

    assert len(public_messages(env)) == 4
    assert host_events(env) == []
    assert all(
        instruction is None for _, _, instruction in calls
    ), "no host timer line when the clock is off"
    assert public_message_limit(env.config, 4) == 1


# ----------------------------------------------------------------------
# The clock: open turns, warning, closing turns, close
# ----------------------------------------------------------------------


def test_hosted_debate_runs_open_warning_and_closing_turns() -> None:
    env = make_env(players=6, discussion_budget=6, warning_turns=1)
    callback, _ = recording_callback()
    run_phase(env, "public_discussion", callback)

    # Six open turns (one each), then six closing turns.
    assert len(public_messages(env)) == 12
    warning = events_of_type(env, EventType.HOST_WARNING)
    closed = events_of_type(env, EventType.DEBATE_CLOSED)
    assert len(warning) == len(closed) == 1
    assert warning[0].payload["turns_left"] == 6  # 1 wave x 6 players
    assert warning[0].actor == "host"
    assert closed[0].payload["forced_vote"] is True

    # Ordering: all six open messages, the warning, six closing
    # messages, then the close.
    sequence = [
        e.type
        for e in env.engine.sink.events
        if e.type
        in (EventType.PUBLIC_MESSAGE, EventType.HOST_WARNING, EventType.DEBATE_CLOSED)
    ]
    assert sequence == (
        [EventType.PUBLIC_MESSAGE] * 6
        + [EventType.HOST_WARNING]
        + [EventType.PUBLIC_MESSAGE] * 6
        + [EventType.DEBATE_CLOSED]
    )


def test_open_turns_are_capped_by_the_budget() -> None:
    env = make_env(players=6, discussion_budget=3, warning_turns=1)
    callback, _ = recording_callback()
    run_phase(env, "round_table", callback)

    # 3 open turns (the round-robin cut) + 6 closing turns.
    messages = public_messages(env)
    assert len(messages) == 3 + 6
    # The first wave went to the alphabetically first players.
    assert [e.actor for e in messages[:3]] == ["alice", "bob", "charlie"]
    warning = events_of_type(env, EventType.HOST_WARNING)
    assert warning[0].payload["turns_left"] == 6


def test_budget_round_robin_continues_in_later_waves() -> None:
    env = make_env(players=4, discussion_budget=6, warning_turns=0)
    callback, _ = recording_callback()
    run_phase(env, "public_discussion", callback)

    # quota = ceil(6/4) = 2, so wave 1 is everyone, wave 2 spends the
    # remaining 2 turns on the first two players.
    messages = public_messages(env)
    assert [e.actor for e in messages] == [
        "alice", "bob", "charlie", "david", "alice", "bob",
    ]
    # warning_turns 0: no warning line, but the debate still closes.
    assert events_of_type(env, EventType.HOST_WARNING) == []
    assert len(events_of_type(env, EventType.DEBATE_CLOSED)) == 1


def test_timer_line_reaches_the_turn_it_describes() -> None:
    env = make_env(players=4, discussion_budget=4, warning_turns=1)
    callback, calls = recording_callback()
    run_phase(env, "public_discussion", callback)

    instructions = [instruction for _, _, instruction in calls]
    open_lines = [i for i in instructions if i and "debate is open" in i]
    closing_lines = [i for i in instructions if i and "time is almost up" in i]
    assert len(open_lines) == 4
    assert len(closing_lines) == 4
    assert all("4 closing turns left" in i for i in closing_lines)
    # Every ask got a host line: the clock is on for the whole phase.
    assert len(instructions) == 8
    assert all(i for i in instructions)


def test_scripted_three_argument_callbacks_still_work_with_the_clock() -> None:
    """Callbacks without the fourth parameter run the clock unchanged."""
    env = make_env(players=4, discussion_budget=4, warning_turns=1)

    async def old_style(agent_id, action_type, targets):
        return Action(
            action=ActionType.PUBLIC_MESSAGE, actor_id=agent_id, content="hi"
        )

    run_phase(env, "public_discussion", old_style)
    assert len(public_messages(env)) == 8  # 4 open + 4 closing
    assert host_events(env)


# ----------------------------------------------------------------------
# Validator integration
# ----------------------------------------------------------------------


def test_validator_accepts_the_closing_turn_and_still_caps_the_phase() -> None:
    env = make_env(players=4, discussion_budget=4, warning_turns=1)
    engine = env.engine
    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    # limit = ceil(4/4) + 1 = 2 with the clock on.
    assert public_message_limit(env.config, 4) == 2
    for index in range(2):
        result = engine.submit_action(
            Action(
                action=ActionType.PUBLIC_MESSAGE,
                actor_id="alice",
                content=f"point {index}",
            )
        )
        assert result.ok, result.reason
    rejected = engine.submit_action(
        Action(
            action=ActionType.PUBLIC_MESSAGE, actor_id="alice", content="third"
        )
    )
    assert not rejected.ok
    assert "limit of 2" in rejected.reason


def test_validator_keeps_the_baseline_limit_without_the_clock() -> None:
    env = make_env(players=4)
    engine = env.engine
    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    assert public_message_limit(env.config, 4) == 1
    first = engine.submit_action(
        Action(action=ActionType.PUBLIC_MESSAGE, actor_id="alice", content="hi")
    )
    assert first.ok
    second = engine.submit_action(
        Action(action=ActionType.PUBLIC_MESSAGE, actor_id="alice", content="again")
    )
    assert not second.ok


def test_quiet_rounds_and_the_clock_coexist() -> None:
    """A quiet round-table still skips voting after a hosted debate."""
    env = make_env(
        players=4, discussion_budget=4, warning_turns=1, quiet_banishment_rounds=[1]
    )
    env.engine.start_round()
    callback, _ = recording_callback()
    run_phase(env, "round_table", callback)
    assert len(public_messages(env)) == 8

    voting = PhaseContext(engine=env.engine, config=env.config, request_action=callback)
    env.engine.begin_phase(GamePhase.VOTING)
    asyncio.run(env.phases()["voting"].run(voting))
    assert events_of_type(env, EventType.BANISHMENT_SKIPPED)
    assert events_of_type(env, EventType.VOTE_CAST) == []


# ----------------------------------------------------------------------
# Replay
# ----------------------------------------------------------------------


def test_transcript_renders_the_host_lines() -> None:
    env = make_env(players=4, discussion_budget=4, warning_turns=1)
    callback, _ = recording_callback()
    run_phase(env, "public_discussion", callback)

    narrative = render_transcript(env.engine.sink.events)
    assert "Host: time is almost up (4 closing turns left)" in narrative
    assert "Host: debate closed, the vote is forced" in narrative


def test_full_game_with_the_clock_on_completes() -> None:
    """A whole scripted game runs the clock every debate, with no rejects."""
    from simulation.engine.phase_engine import PhaseEngine

    env = make_env(
        players=6,
        traitors=2,
        max_rounds=3,
        discussion_budget=6,
        warning_turns=1,
    )
    callback, _ = recording_callback()
    context = PhaseContext(
        engine=env.engine, config=env.config, request_action=callback
    )
    phase_engine = PhaseEngine(env.config, env.engine, env.phases())
    asyncio.run(phase_engine.run(context))

    engine = env.engine
    assert engine.state.winner is not None
    warnings = events_of_type(env, EventType.HOST_WARNING)
    closes = events_of_type(env, EventType.DEBATE_CLOSED)
    # public_discussion and round_table both run the clock each round.
    assert len(warnings) >= 2
    assert len(closes) == len(warnings)
    assert events_of_type(env, EventType.ACTION_REJECTED) == []
    # The forced vote of round 1 happened after round 1's closes.
    votes = events_of_type(env, EventType.VOTE_CAST)
    assert votes
    first_close = min(env.engine.sink.events.index(e) for e in closes)
    first_vote = min(env.engine.sink.events.index(e) for e in votes)
    assert first_vote > first_close
