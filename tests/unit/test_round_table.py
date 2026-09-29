"""Hosted round table (audit fix, phase 2).

The show's round table is not one polite message each and a cold ballot:
someone is named, the accused answers, and a table that cannot decide
between suspects votes again, on those suspects only. These tests cover
the nomination tally, the defence, the secrecy carve-out that makes an
accusation legal, and the restricted revote that replaces the dead round
the old engine recorded on a tie.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from simulation.actions.actions import Action, ActionType
from simulation.engine.phase_engine import PhaseContext, PhaseEngine
from simulation.engine.state import GamePhase, Role
from simulation.environments.traitors.game import TraitorsEnvironment
from simulation.experiments.config import GameConfig
from simulation.experiments.replay import render_transcript
from simulation.models.base import ChatMessage
from simulation.models.fake import PromptScriptProvider
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


def run_phase(env, phase_name: str, callback) -> None:
    context = PhaseContext(
        engine=env.engine, config=env.config, request_action=callback
    )
    env.engine.begin_phase(GamePhase(phase_name))
    asyncio.run(env.phases()[phase_name].run(context))


def events_of_type(env, type_) -> list:
    return [e for e in env.engine.sink.events if e.type is type_]


def nominating_callback(
    nominee: str,
    votes: Optional[dict[str, str]] = None,
    revotes: Optional[dict[str, str]] = None,
):
    """Callback that nominates one fixed suspect and votes by script.

    `votes` maps a voter to their target on the first ballot; `revotes`
    does the same for the restricted second ballot (told apart by the
    host's timer line, which only a revote carries). Anyone missing from
    a script picks the first legal target that is not themselves, which
    keeps the tests independent of id ordering.
    """
    votes = votes or {}
    revotes = revotes or {}

    async def callback(
        agent_id: str,
        action_type: ActionType,
        targets: list[str],
        extra_instruction=None,
    ):
        if action_type is ActionType.PUBLIC_MESSAGE:
            return Action(
                action=action_type, actor_id=agent_id, content=f"{agent_id} speaks"
            )
        if action_type is ActionType.ACCUSE:
            target = nominee if (nominee in targets and nominee != agent_id) else None
            if target is None:
                target = next((t for t in targets if t != agent_id), None)
            return Action(
                action=action_type,
                actor_id=agent_id,
                target=target,
                content=f"{agent_id} suspects {target}",
            )
        if action_type is ActionType.REBUT:
            return Action(
                action=action_type,
                actor_id=agent_id,
                content=f"{agent_id} answers the room",
            )
        if action_type is ActionType.PRIVATE_MESSAGE:
            peer = next((t for t in targets if t != agent_id), None)
            return Action(
                action=action_type,
                actor_id=agent_id,
                target=peer,
                content=f"{agent_id} whispers to {peer}",
            )
        if action_type is ActionType.VOTE:
            script = (
                revotes
                if (extra_instruction or "").startswith("Host: the vote is tied")
                else votes
            )
            target = script.get(agent_id)
            if target is None or target not in targets:
                target = next((t for t in targets if t != agent_id), None)
            return Action(action=action_type, actor_id=agent_id, target=target)
        pick = votes.get(agent_id) or next(
            (t for t in targets if t != agent_id), None
        )
        return Action(action=action_type, actor_id=agent_id, target=pick)

    return callback


# ----------------------------------------------------------------------
# Nominations and rebuttals
# ----------------------------------------------------------------------


def test_nomination_off_keeps_the_plain_round_table() -> None:
    env = make_env(players=4)
    run_phase(env, "round_table", nominating_callback("bob"))

    assert events_of_type(env, EventType.NOMINATION_TALLY) == []
    assert events_of_type(env, EventType.PUBLIC_MESSAGE)
    assert env.engine.round_table_nominees() == []


def test_nominations_are_tallied_and_only_nominees_answer() -> None:
    env = make_env(players=6, nomination_enabled=True, nomination_keep=2)
    seen: list[tuple[str, ActionType]] = []

    inner = nominating_callback("bob")

    async def callback(agent_id, action_type, targets, extra_instruction=None):
        seen.append((agent_id, action_type))
        return await inner(agent_id, action_type, targets, extra_instruction)

    run_phase(env, "round_table", callback)

    # Every living player nominated someone, and the top suspect stood.
    assert ("charlie", ActionType.ACCUSE) in seen
    tallies = events_of_type(env, EventType.NOMINATION_TALLY)
    assert len(tallies) == 1
    assert tallies[0].payload["nominees"][0] == "bob"
    assert tallies[0].payload["counts"]["bob"] == 5
    assert tallies[0].actor == "host"
    assert env.engine.round_table_nominees()[0] == "bob"

    # Only the nominees were asked to defend, and the defence went public.
    asked = [agent for agent, action in seen if action is ActionType.REBUT]
    assert "bob" in asked
    assert set(asked) == set(env.engine.round_table_nominees())
    rebuttals = [
        e
        for e in events_of_type(env, EventType.PUBLIC_MESSAGE)
        if e.actor == "bob" and "answers the room" in e.payload.get("content", "")
    ]
    assert rebuttals


def test_nomination_keep_limits_the_shortlist() -> None:
    env = make_env(players=6, nomination_enabled=True, nomination_keep=1)
    run_phase(env, "round_table", nominating_callback("david"))

    tally = events_of_type(env, EventType.NOMINATION_TALLY)[0]
    assert tally.payload["nominees"] == ["david"]
    assert len(env.engine.round_table_nominees()) == 1


def test_nominations_are_public_messages_with_content() -> None:
    env = make_env(players=4, nomination_enabled=True)
    run_phase(env, "round_table", nominating_callback("bob"))

    accusations = [
        e
        for e in events_of_type(env, EventType.PUBLIC_MESSAGE)
        if "suspects" in e.payload.get("content", "")
    ]
    assert len(accusations) == 4
    assert all(e.payload["channel"] == "public" for e in accusations)


def test_validator_gates_accusation_and_rebuttal() -> None:
    env = make_env(players=4, nomination_enabled=True)
    engine = env.engine
    engine.begin_phase(GamePhase.ROUND_TABLE)

    self_nomination = engine.submit_action(
        Action(
            action=ActionType.ACCUSE,
            actor_id="alice",
            target="alice",
            content="me?",
        )
    )
    assert not self_nomination.ok
    assert "nominate yourself" in self_nomination.reason

    no_nomination_open = engine.submit_action(
        Action(action=ActionType.REBUT, actor_id="alice", content="not me")
    )
    assert not no_nomination_open.ok
    assert "no nomination is open" in no_nomination_open.reason


def test_rebuttal_belongs_to_the_nominees_alone() -> None:
    env = make_env(players=4, nomination_enabled=True)
    engine = env.engine
    engine.begin_phase(GamePhase.ROUND_TABLE)
    engine.submit_action(
        Action(
            action=ActionType.ACCUSE,
            actor_id="alice",
            target="bob",
            content="bob has been quiet",
        )
    )
    assert engine.tally_accusations(keep=1) == ["bob"]

    ok = engine.submit_action(
        Action(action=ActionType.REBUT, actor_id="bob", content="actually it is alice")
    )
    assert ok.ok, ok.reason
    wrong_actor = engine.submit_action(
        Action(action=ActionType.REBUT, actor_id="alice", content="not me")
    )
    assert not wrong_actor.ok
    assert "not a nominated suspect" in wrong_actor.reason


def test_nomination_actions_are_only_offered_with_the_flag() -> None:
    off = make_env(players=4)
    on = make_env(players=4, nomination_enabled=True)
    off.engine.begin_phase(GamePhase.ROUND_TABLE)
    on.engine.begin_phase(GamePhase.ROUND_TABLE)

    offered_off = off.engine.validator.allowed_actions(GamePhase.ROUND_TABLE)
    offered_on = on.engine.validator.allowed_actions(GamePhase.ROUND_TABLE)
    assert ActionType.ACCUSE not in offered_off
    assert ActionType.REBUT not in offered_off
    assert ActionType.ACCUSE in offered_on
    assert ActionType.REBUT in offered_on


# ----------------------------------------------------------------------
# The restricted revote
# ----------------------------------------------------------------------


def run_ballot(
    env,
    votes: Optional[dict[str, str]] = None,
    revotes: Optional[dict[str, str]] = None,
) -> None:
    callback = nominating_callback("bob", votes, revotes)
    run_phase(env, "voting", callback)
    run_phase(env, "elimination", callback)


TIE_VOTES = {
    # bob 2, charlie 2, eve 1, frank 1 -> bob and charlie are tied.
    "alice": "bob",
    "bob": "charlie",
    "charlie": "bob",
    "david": "charlie",
    "eve": "frank",
    "frank": "eve",
}


def test_revote_off_keeps_a_tie_as_a_dead_round() -> None:
    env = make_env(players=6)
    run_ballot(env, TIE_VOTES)

    assert events_of_type(env, EventType.VOTE_TIE)
    assert events_of_type(env, EventType.REVOTE_CALLED) == []
    assert events_of_type(env, EventType.PLAYER_ELIMINATED) == []


def test_revote_settles_a_tie_with_a_banishment() -> None:
    env = make_env(players=6, revote_enabled=True)
    revote = {"alice": "bob", "david": "bob", "eve": "bob", "frank": "bob"}

    run_ballot(env, TIE_VOTES, revote)

    called = events_of_type(env, EventType.REVOTE_CALLED)
    assert len(called) == 1
    assert called[0].payload["targets"] == ["bob", "charlie"]
    # The tied suspects are not voters; everybody else is.
    assert called[0].payload["voters"] == ["alice", "david", "eve", "frank"]

    resolved = events_of_type(env, EventType.REVOTE_RESOLVED)
    assert len(resolved) == 1 and resolved[0].payload["tie"] is False
    eliminated = events_of_type(env, EventType.PLAYER_ELIMINATED)
    assert [e.actor for e in eliminated] == ["bob"]
    assert eliminated[0].payload["method"] == "vote"
    # The second ballot is never a rejected action, even though the
    # one-vote-per-phase limit had already been spent on the first.
    assert events_of_type(env, EventType.ACTION_REJECTED) == []


def test_second_tie_eliminates_nobody() -> None:
    env = make_env(players=6, revote_enabled=True)
    split = {"alice": "bob", "david": "bob", "eve": "charlie", "frank": "charlie"}
    run_ballot(env, TIE_VOTES, split)

    resolved = events_of_type(env, EventType.REVOTE_RESOLVED)
    assert resolved[0].payload["tie"] is True
    assert events_of_type(env, EventType.VOTE_TIE)
    assert events_of_type(env, EventType.PLAYER_ELIMINATED) == []


def test_tied_suspect_cannot_vote_in_their_own_revote() -> None:
    env = make_env(players=6, revote_enabled=True)
    engine = env.engine
    engine.begin_phase(GamePhase.VOTING)
    engine.call_revote(["bob", "charlie"])

    tied_ballot = engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="bob", target="charlie")
    )
    assert not tied_ballot.ok
    assert "tied suspect" in tied_ballot.reason

    off_target = engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="alice", target="eve")
    )
    assert not off_target.ok
    assert "must land on one of the tied suspects" in off_target.reason

    ok = engine.submit_action(
        Action(action=ActionType.VOTE, actor_id="alice", target="bob")
    )
    assert ok.ok, ok.reason


def test_revote_banishment_of_a_traitor_can_recruit() -> None:
    """A revote banishment is a round-table banishment, recruits and all."""
    env = make_env(players=6, traitors=2, revote_enabled=True, recruit_on_banish=True)
    engine = env.engine
    engine.start_round()
    traitor = next(
        p for p in sorted(env.state.alive_players) if env.state.roles[p] is Role.TRAITOR
    )
    faithful = sorted(
        p for p in env.state.alive_players if env.state.roles[p] is Role.FAITHFUL
    )
    other = faithful[0]
    neutrals = [
        p for p in sorted(env.state.alive_players) if p not in (traitor, other)
    ][:4]

    engine.begin_phase(GamePhase.VOTING)
    for voter in neutrals[:2]:
        engine.submit_action(
            Action(action=ActionType.VOTE, actor_id=voter, target=traitor)
        )
    for voter in neutrals[2:]:
        engine.submit_action(
            Action(action=ActionType.VOTE, actor_id=voter, target=other)
        )
    assert engine.tally_votes().tie
    engine.call_revote([traitor, other])
    for voter in engine.revote_voters():
        engine.submit_action(
            Action(action=ActionType.VOTE, actor_id=voter, target=traitor)
        )
    engine.resolve_revote()

    recruited = next(p for p in faithful if p != other)

    async def callback(agent_id, action_type, targets, extra_instruction=None):
        return Action(
            action=action_type,
            actor_id=agent_id,
            target=recruited,
            content="join us",
        )

    run_phase(env, "elimination", callback)

    assert traitor not in env.state.alive_players
    assert events_of_type(env, EventType.REVOTE_CALLED)[0].payload["targets"] == sorted(
        [traitor, other]
    )
    assert env.state.roles[recruited] is Role.TRAITOR


def test_transcript_renders_the_hosted_round_table() -> None:
    env = make_env(players=6, nomination_enabled=True, revote_enabled=True)
    run_phase(env, "round_table", nominating_callback("bob"))
    run_ballot(
        env,
        TIE_VOTES,
        {"alice": "bob", "david": "bob", "eve": "bob", "frank": "bob"},
    )

    narrative = render_transcript(env.engine.sink.events)
    assert "Host: nominations tallied" in narrative
    assert "Host: facing the room" in narrative
    assert "face a revote" in narrative
    assert "is banished" in narrative


def test_full_game_with_the_hosted_round_table_completes() -> None:
    """A whole scripted game runs nominations and revotes without rejects."""
    env = make_env(
        players=6,
        traitors=2,
        max_rounds=3,
        nomination_enabled=True,
        revote_enabled=True,
        finale_traitors=1,
        finale_faithful=1,
    )
    callback = nominating_callback("bob")
    context = PhaseContext(
        engine=env.engine, config=env.config, request_action=callback
    )
    asyncio.run(PhaseEngine(env.config, env.engine, env.phases()).run(context))

    assert env.engine.state.winner is not None
    assert events_of_type(env, EventType.ACTION_REJECTED) == []
    assert events_of_type(env, EventType.NOMINATION_TALLY)



# ----------------------------------------------------------------------
# The fake model can play this round table
# ----------------------------------------------------------------------


def test_prompt_script_provider_answers_the_round_table() -> None:
    provider = PromptScriptProvider()

    def prompt(action: str, targets: str) -> list[ChatMessage]:
        return [
            ChatMessage(role="system", content="You are alice, a player."),
            ChatMessage(
                role="user",
                content=(
                    "Alive players: alice, bob, charlie, david\n"
                    f"Required action type: {action}.\n"
                    f"Legal targets: {targets}"
                ),
            ),
        ]

    accusation = json.loads(
        asyncio.run(provider.generate(prompt("accuse", "bob, charlie"))).content
    )
    assert accusation["action"] == "accuse"
    assert accusation["target"] in ("bob", "charlie")
    assert accusation["content"], "a nomination carries its reason"

    rebuttal = json.loads(
        asyncio.run(provider.generate(prompt("rebut", "bob, charlie"))).content
    )
    assert rebuttal["action"] == "rebut"
    assert rebuttal["target"] is None, "the defence answers its case, no target"
    assert rebuttal["content"], "a defence carries its reason"


def test_prompt_script_provider_reads_the_prompt_on_a_retry() -> None:
    """A correction retry's hint is the last user message, not the prompt.

    Parsing from the tail would lose the action type and the legal
    targets on every retry, which is how the hosted round table first
    failed on the fake backend: a rejected nomination raised, and the
    retry then could not find the action it was being asked for.
    """
    provider = PromptScriptProvider()
    messages = [
        ChatMessage(role="system", content="You are alice, a player."),
        ChatMessage(
            role="user",
            content=(
                "Alive players: alice, bob\n"
                "Required action type: accuse.\n"
                "Legal targets: bob"
            ),
        ),
        ChatMessage(role="assistant", content='{"action": "public_message"}'),
        ChatMessage(
            role="user",
            content="Invalid action: expected accuse. Try again.",
        ),
    ]
    accusation = json.loads(asyncio.run(provider.generate(messages)).content)
    assert accusation["action"] == "accuse"
    assert accusation["target"] == "bob"
    assert accusation["content"]

