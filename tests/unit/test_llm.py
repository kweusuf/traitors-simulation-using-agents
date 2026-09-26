"""Unit tests for the LLM gateway, fake provider, and agent decision loop
(spec section 11, 17, 18, 21, 30)."""

from __future__ import annotations

import asyncio

import pytest

from simulation.actions.actions import Action, ActionType
from simulation.actions.validator import ActionParseError
from simulation.agents.agent import Agent
from simulation.agents.persona import Persona
from simulation.agents.runtime import AgentRuntime, make_action_callback
from simulation.agents.prompts import PromptBuilder
from simulation.communication.channels import Channel, Message
from simulation.communication.visibility import AgentView, InformationProjector
from simulation.engine.game_engine import GameEngine
from simulation.engine.state import GamePhase, Role
from simulation.experiments.config import GameConfig
from simulation.models.base import ChatMessage
from simulation.models.fake import FakeLLMProvider
from simulation.models.gateway import LLMGateway
from simulation.models.llm import ModelConfig
from simulation.persistence.sink import EventSink


def make_view(agent_id: str = "alice") -> AgentView:
    return AgentView(
        agent_id=agent_id,
        game_id="game-001",
        round_number=1,
        phase=GamePhase.PUBLIC_DISCUSSION,
        own_role=Role.FAITHFUL,
        known_roles={agent_id: Role.FAITHFUL},
        alive_players=["alice", "bob", "charlie"],
        eliminated_players=[],
        public_transcript=[
            Message(
                message_id="m1",
                sender_id="bob",
                recipients=[],
                channel=Channel.PUBLIC,
                content="hello everyone",
            )
        ],
        private_conversations=[],
        winner=None,
    )


def make_runtime(script: dict, max_retries: int = 1) -> tuple[AgentRuntime, FakeLLMProvider]:
    agent = Agent("alice", "Alice", Persona(description="Careful player."))
    agent.assign_role(Role.FAITHFUL)
    provider = FakeLLMProvider(script)
    gateway = LLMGateway(provider, max_concurrency=2)
    runtime = AgentRuntime(
        agents={"alice": agent},
        gateway=gateway,
        model_config=ModelConfig(),
        prompt_builder=PromptBuilder(),
        max_retries=max_retries,
    )
    return runtime, provider


# ----------------------------------------------------------------------
# Gateway
# ----------------------------------------------------------------------


def test_gateway_caps_concurrency() -> None:
    async def scenario() -> tuple[int, int]:
        provider = FakeLLMProvider(
            {"__default__": [{"action": "vote", "target": "bob"}] * 10},
            delay_seconds=0.02,
        )
        gateway = LLMGateway(provider, max_concurrency=2)
        config = ModelConfig()
        await asyncio.gather(
            *[
                gateway.generate([ChatMessage(role="user", content="x")], Action, config)
                for _ in range(8)
            ]
        )
        return provider.peak_in_flight, gateway.in_flight_peak

    provider_peak, gateway_peak = asyncio.run(scenario())
    assert provider_peak <= 2
    assert gateway_peak <= 2


def test_gateway_rejects_bad_concurrency() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        LLMGateway(FakeLLMProvider(), max_concurrency=0)


# ----------------------------------------------------------------------
# Fake provider
# ----------------------------------------------------------------------


def test_fake_provider_plays_back_script_per_agent() -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        provider = FakeLLMProvider(
            {
                "alice": [{"action": "vote", "target": "bob"}],
                "bob": ["not json at all"],
            }
        )
        gateway = LLMGateway(provider)
        system_alice = ChatMessage(role="system", content="You are alice, a player.")
        system_bob = ChatMessage(role="system", content="You are bob, a player.")
        first = await gateway.generate([system_alice], Action, ModelConfig())
        second = await gateway.generate([system_bob], Action, ModelConfig())
        return [first.content, second.content], provider.remaining.get("alice", [])

    contents, _ = asyncio.run(scenario())
    assert '"target": "bob"' in contents[0]
    assert contents[1] == "not json at all"


def test_fake_provider_exhaustion_raises() -> None:
    async def scenario() -> None:
        provider = FakeLLMProvider({"alice": []})
        await provider.generate(
            [ChatMessage(role="system", content="You are alice, a player.")],
            Action,
            ModelConfig(),
        )

    with pytest.raises(RuntimeError, match="no scripted response"):
        asyncio.run(scenario())


def test_fake_provider_falls_back_to_default_queue() -> None:
    async def scenario() -> str:
        provider = FakeLLMProvider({"__default__": [{"action": "vote", "target": "bob"}]})
        resp = await provider.generate(
            [ChatMessage(role="system", content="unknown identity")],
            Action,
            ModelConfig(),
        )
        return resp.content

    assert '"action": "vote"' in asyncio.run(scenario())


# ----------------------------------------------------------------------
# Agent decision loop
# ----------------------------------------------------------------------


def test_decide_returns_structured_action_and_sees_the_view() -> None:
    runtime, provider = make_runtime(
        {"alice": [{"action": "public_message", "content": "bob seems off", "confidence": 0.7}]}
    )
    action = asyncio.run(
        runtime.decide("alice", make_view(), ActionType.PUBLIC_MESSAGE, [])
    )
    assert action.action is ActionType.PUBLIC_MESSAGE
    assert action.actor_id == "alice"
    assert action.content == "bob seems off"
    # The prompt was built from the projected view.
    sent = provider.calls[0]
    assert "Alive players: alice, bob, charlie" in sent[1].content
    assert "hello everyone" in sent[1].content
    assert "Your role: faithful" in sent[1].content


def test_decide_retries_on_malformed_output() -> None:
    runtime, provider = make_runtime(
        {
            "alice": [
                "I think I will vote for bob.",  # prose, not JSON
                {"action": "vote", "target": "bob", "confidence": 0.8},
            ]
        }
    )
    action = asyncio.run(
        runtime.decide("alice", make_view(), ActionType.VOTE, ["bob", "charlie"])
    )
    assert action.action is ActionType.VOTE
    assert len(provider.calls) == 2
    correction = provider.calls[1]
    assert any("Invalid action" in m.content for m in correction)


def test_decide_retries_on_illegal_target() -> None:
    runtime, provider = make_runtime(
        {
            "alice": [
                {"action": "vote", "target": "mallory"},
                {"action": "vote", "target": "charlie"},
            ]
        }
    )
    action = asyncio.run(
        runtime.decide("alice", make_view(), ActionType.VOTE, ["bob", "charlie"])
    )
    assert action.target == "charlie"
    assert len(provider.calls) == 2


def test_decide_raises_after_exhausted_retries() -> None:
    runtime, _ = make_runtime(
        {"alice": ["still not json", "and again not json"]}, max_retries=1
    )
    with pytest.raises(ActionParseError, match="after 2 attempts"):
        asyncio.run(runtime.decide("alice", make_view(), ActionType.VOTE, ["bob"]))


def test_decide_requires_assigned_role() -> None:
    agent = Agent("alice", "Alice", Persona())
    runtime = AgentRuntime(
        agents={"alice": agent},
        gateway=LLMGateway(FakeLLMProvider()),
        model_config=ModelConfig(),
    )
    with pytest.raises(RuntimeError, match="no assigned role"):
        asyncio.run(runtime.decide("alice", make_view(), ActionType.VOTE, ["bob"]))


def test_action_callback_projects_view_per_agent() -> None:
    config = GameConfig(game={"players": 6, "traitors": 2})
    engine = GameEngine(config, EventSink("game-001"), seed=42)
    engine.start()
    projector = InformationProjector(engine.router)

    agents = {
        pid: Agent(pid, pid.capitalize(), Persona(description="p."))
        for pid in engine.player_ids
    }
    for pid in engine.player_ids:
        agents[pid].assign_role(engine.state.roles[pid])

    provider = FakeLLMProvider(
        {"__default__": [{"action": "public_message", "content": "hi"}] * 4}
    )
    runtime = AgentRuntime(
        agents=agents,
        gateway=LLMGateway(provider),
        model_config=ModelConfig(),
    )
    callback = make_action_callback(runtime, engine, projector)

    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    for pid in ["alice", "bob", "charlie"]:
        action = asyncio.run(callback(pid, ActionType.PUBLIC_MESSAGE, []))
        assert engine.submit_action(action).ok

    # Each agent got their own role in their system prompt.
    roles_seen = []
    for call in provider.calls:
        system = next(m for m in call if m.role == "system")
        line = next(l for l in system.content.splitlines() if l.startswith("Your role"))
        roles_seen.append(line)
    assert len(roles_seen) == 3
