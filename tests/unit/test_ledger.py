"""A player keeps a ranked read, and the ballot follows it.

Banishment in the recorded runs tracked chance: 2 of 3 traitors caught from
17 eliminations, against ~2.3 expected by random. The cause was not a missing
transcript - it reached the model at 21,431 characters - but that the ballot
asked for a bare `target` with no instruction to deduce, and no record of the
player's own earlier conclusions to consult.

These tests pin the mechanism that fixes it: a ledger the player writes,
which is then shown back to them on every turn.
"""

from __future__ import annotations

import asyncio

import pytest

from simulation.actions.actions import ActionType
from simulation.agents.agent import Agent
from simulation.agents.beliefs import Beliefs
from simulation.agents.ledger import (
    DEFAULT_ALLY_SIZE,
    DEFAULT_SUSPECT_SIZE,
    Ledger,
    apply_ledger,
    ledger_lines,
    ledger_schema,
    parse_ledger,
)
from simulation.agents.persona import Persona
from simulation.agents.prompts import PromptBuilder
from simulation.agents.runtime import AgentRuntime
from simulation.communication.channels import Channel, Message
from simulation.communication.visibility import AgentView
from simulation.engine.state import GamePhase, Role
from simulation.models.fake import FakeLLMProvider
from simulation.models.gateway import LLMGateway
from simulation.models.llm import ModelConfig


def make_view(round_number: int = 2) -> AgentView:
    return AgentView(
        agent_id="alice",
        game_id="game-001",
        round_number=round_number,
        phase=GamePhase.PUBLIC_DISCUSSION,
        own_role=Role.FAITHFUL,
        known_roles={"alice": Role.FAITHFUL},
        alive_players=["alice", "bob", "wilf"],
        eliminated_players=[],
        public_transcript=[
            Message(
                message_id="m1",
                sender_id="bob",
                recipients=[],
                channel=Channel.PUBLIC,
                content="I think wilf is hiding something.",
            )
        ],
        private_conversations=[],
        winner=None,
    )


def make_runtime(script: dict, **kwargs) -> tuple[AgentRuntime, FakeLLMProvider]:
    agent = Agent("alice", "Alice", Persona(description="Careful player."))
    agent.assign_role(Role.FAITHFUL)
    provider = FakeLLMProvider(script)
    runtime = AgentRuntime(
        agents={"alice": agent},
        gateway=LLMGateway(provider, max_concurrency=1),
        model_config=ModelConfig(),
        prompt_builder=PromptBuilder(),
        **kwargs,
    )
    return runtime, provider


def ledger_reply(suspects=(), allies=()) -> dict:
    return {
        "suspects": [
            {"player": p, "confidence": c, "evidence": "e"} for p, c in suspects
        ],
        "allies": [
            {"player": p, "confidence": c, "evidence": "e"} for p, c in allies
        ],
    }


# ----------------------------------------------------------------------
# The ledger itself
# ----------------------------------------------------------------------


def test_the_innocent_list_is_shorter_than_the_suspect_list() -> None:
    """A long list of friends is not a defence, it is a preference."""
    assert DEFAULT_ALLY_SIZE < DEFAULT_SUSPECT_SIZE


def test_the_schema_bounds_both_lists() -> None:
    """The bound is the thing a small model ignores, so it is in the schema."""
    bounded = ledger_schema(3, 2)
    for field, size in (("suspects", 3), ("allies", 2)):
        with pytest.raises(Exception):
            bounded.model_validate(
                {
                    field: [
                        {"player": str(i), "confidence": 0.5}
                        for i in range(size + 2)
                    ]
                }
            )


def test_both_directions_are_recorded_as_beliefs() -> None:
    """An ally is a belief of `faithful` - a conclusion, not the truth."""
    beliefs = Beliefs()
    apply_ledger(
        Ledger.model_validate(
            ledger_reply(suspects=[("wilf", 0.8)], allies=[("bob", 0.9)])
        ),
        beliefs,
        3,
    )
    assert beliefs.get("wilf").suspected_role == "traitor"
    assert beliefs.get("bob").suspected_role == "faithful"


def test_the_ledger_renders_ranked_and_both_lists() -> None:
    beliefs = Beliefs()
    apply_ledger(
        Ledger.model_validate(
            ledger_reply(
                suspects=[("wilf", 0.4), ("matt", 0.9)], allies=[("bob", 0.95)]
            )
        ),
        beliefs,
        3,
    )
    lines = ledger_lines(beliefs, 3, 2)


# ----------------------------------------------------------------------
# The ledger in the decide loop
# ----------------------------------------------------------------------


def speak(content: str = "my read is wilf") -> dict:
    return {"action": "public_message", "content": content}


def queue_ledger(provider: FakeLLMProvider, reply: dict) -> None:
    """Put a ledger reply where the ledger call will find it.

    The fake provider picks its script key by matching "You are <id>," in the
    system prompt. The ledger's system prompt is not the player's persona, so
    it resolves to the default queue instead - which is also how it behaves
    against a real model, where nothing keys on the prompt at all.

    Appended rather than prepended: two ledger calls in one test are consumed
    in order, so the queue has to read back the way it was written.
    """
    provider.script.setdefault("__default__", []).append(reply)


def test_the_ledger_is_written_before_the_prompt_is_built() -> None:
    """The prompt must show this round's read, not last round's."""
    runtime, provider = make_runtime(
        {"alice": [speak()]}, suspicion_ledger=True
    )
    queue_ledger(provider, ledger_reply(suspects=[("wilf", 0.8)]))
    asyncio.run(
        runtime.decide("alice", make_view(), ActionType.PUBLIC_MESSAGE, [])
    )
    assert runtime.agents["alice"].ledger is not None
    assert runtime.ledger_calls == 1


def test_the_ledger_reaches_the_prompt() -> None:
    """Without this the whole mechanism is invisible and inert."""
    runtime, provider = make_runtime(
        {"alice": [speak()]}, suspicion_ledger=True
    )
    queue_ledger(provider, ledger_reply(suspects=[("wilf", 0.8)]))
    asyncio.run(
        runtime.decide("alice", make_view(), ActionType.PUBLIC_MESSAGE, [])
    )
    action_prompt = " ".join(m.content for m in provider.calls[-1])
    assert "wilf" in action_prompt
    assert "You have a position" in action_prompt


def test_the_ledger_is_written_once_per_round() -> None:
    """Recomputing it before every action would be a call per statement."""
    runtime, provider = make_runtime(
        {"alice": [speak("one"), speak("two")]}, suspicion_ledger=True
    )
    queue_ledger(provider, ledger_reply())
    view = make_view()
    asyncio.run(runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, []))
    asyncio.run(runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, []))
    assert runtime.ledger_calls == 1


def test_a_new_round_refreshes_the_ledger() -> None:
    runtime, provider = make_runtime(
        {"alice": [speak("round two"), speak("round three")]},
        suspicion_ledger=True,
    )
    queue_ledger(provider, ledger_reply(suspects=[("wilf", 0.7)]))
    queue_ledger(provider, ledger_reply(suspects=[("bob", 0.9)]))
    asyncio.run(
        runtime.decide("alice", make_view(2), ActionType.PUBLIC_MESSAGE, [])
    )
    asyncio.run(
        runtime.decide("alice", make_view(3), ActionType.PUBLIC_MESSAGE, [])
    )
    assert runtime.ledger_calls == 2
    assert runtime.agents["alice"].ledger.top.player == "bob"


def test_a_player_who_is_not_in_the_game_is_dropped() -> None:
    """Otherwise the model chases somebody who does not exist."""
    runtime, provider = make_runtime(
        {"alice": [speak()]}, suspicion_ledger=True
    )
    queue_ledger(provider, ledger_reply(suspects=[("iris", 0.9), ("wilf", 0.5)]))
    asyncio.run(
        runtime.decide("alice", make_view(), ActionType.PUBLIC_MESSAGE, [])
    )
    names = [s.player for s in runtime.agents["alice"].ledger.suspects]
    assert names == ["wilf"]


def test_the_ledger_is_off_by_default() -> None:
    """An existing config must play exactly as it did before."""
    runtime, provider = make_runtime({"alice": [speak()]})
    view = make_view()
    asyncio.run(
        runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, [])
    )
    assert runtime.suspicion_ledger is False
    assert runtime.ledger_calls == 0
    assert len(provider.calls) == 1, "no ledger generation was made"


def test_a_failing_ledger_does_not_cost_the_turn() -> None:
    """A stale position beats no position, and the turn must still happen."""
    runtime, _ = make_runtime(
        {
            "alice": [
                "this is not the ledger shape",
                speak("still spoke"),
            ]
        },
        suspicion_ledger=True,
    )
    action = asyncio.run(
        runtime.decide("alice", make_view(), ActionType.PUBLIC_MESSAGE, [])
    )
    assert action.content == "still spoke"


def test_one_player_never_sees_another_players_ledger() -> None:
    """The builder is shared, so the per-player lines go on a copy."""
    runtime, provider = make_runtime(
        {"alice": [speak()]}, suspicion_ledger=True
    )
    queue_ledger(provider, ledger_reply(suspects=[("wilf", 0.9)]))
    asyncio.run(
        runtime.decide("alice", make_view(), ActionType.PUBLIC_MESSAGE, [])
    )
    assert runtime.prompt_builder._ledger_lines == []


def test_a_bare_list_reply_is_accepted() -> None:
    """A small model returns the entries without the wrapper; that is fine."""
    ledger = parse_ledger(
        '[{"player": "wilf", "confidence": 0.7, "evidence": "x"}]'
    )
    assert ledger.top is not None
    assert ledger.top.player == "wilf"


def test_unparseable_reply_leaves_the_ledger_empty() -> None:
    assert parse_ledger("not json at all").suspects == []