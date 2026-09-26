"""Unit tests for persona, goals, beliefs, relationships, agent (spec 11-16)."""

from __future__ import annotations

import asyncio

import pytest

from simulation.agents.agent import Agent
from simulation.agents.beliefs import Beliefs
from simulation.agents.goals import Goals, inject_role_goals
from simulation.agents.persona import Persona, load_persona, load_persona_bundle
from simulation.agents.relationships import Relationships
from simulation.engine.state import Role


def test_persona_files_load() -> None:
    for name in [
        "analytical",
        "politician",
        "observer",
        "contrarian",
        "loyalist",
        "opportunist",
    ]:
        persona, goals = load_persona_bundle(f"configs/personas/{name}.yaml")
        assert persona.personality
        assert goals.primary == "survive"
        assert 0.0 <= min(persona.personality.values())
        assert max(persona.personality.values()) <= 1.0


def test_persona_instructions_translate_traits_without_forcing_strategy() -> None:
    persona = Persona(
        description="Bold player.",
        personality={
            "analytical": 0.9,
            "assertiveness": 0.5,
            "sociability": 0.4,
            "trust": 0.3,
            "risk_tolerance": 0.4,
        },
    )
    lines = persona.instructions()
    text = "\n".join(lines)
    assert "Bold player." in text
    assert "weigh evidence" in text  # analytical high
    assert "verify claims independently" in text  # trust low
    assert "prioritize personal safety" in text  # risk low
    # Tendencies, never commands that force a strategy.
    assert "always lie" not in text
    assert "must vote" not in text


def test_persona_rejects_out_of_range_traits() -> None:
    persona = Persona(personality={"analytical": 1.5})
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        persona.instructions()


def test_role_goal_injection() -> None:
    base = Goals(primary="survive", secondary=["identify_traitors"])
    traitor = inject_role_goals(base, Role.TRAITOR)
    assert traitor.primary == "survive"
    assert "ensure_traitor_team_wins" in traitor.secondary
    assert "identify_traitors" in traitor.secondary

    faithful = inject_role_goals(Goals(primary="survive"), Role.FAITHFUL)
    assert faithful.secondary == ["identify_traitors"]


def test_agent_role_assignment_injects_goals() -> None:
    agent = Agent("alice", "Alice", Persona(description="x"))
    assert agent.role_value == "unassigned"
    agent.assign_role(Role.TRAITOR)
    assert agent.role is Role.TRAITOR
    assert "ensure_traitor_team_wins" in agent.goals.secondary


def test_beliefs_update_and_prompt_lines() -> None:
    beliefs = Beliefs()
    beliefs.update("bob", "traitor", 0.7, round_number=2)
    beliefs.update("charlie", None, 0.2, round_number=2)
    assert beliefs.get("bob").suspected_role == "traitor"
    assert beliefs.get("bob").confidence == 0.7
    lines = beliefs.prompt_lines()
    assert any("bob: suspected traitor (confidence 0.70" in line for line in lines)

    with pytest.raises(ValueError, match="confidence"):
        beliefs.update("eve", "traitor", 1.4, round_number=1)
    with pytest.raises(ValueError, match="suspected_role"):
        beliefs.update("eve", "alien", 0.5, round_number=1)


def test_relationships_clamp_and_prompt() -> None:
    rels = Relationships()
    rels.update("bob", trust=0.72, suspicion=0.1, threat=0.55)
    assert rels.get("bob").trust == 0.72
    rels.update("bob", trust=1.7, suspicion=-0.4)
    bob = rels.get("bob")
    assert bob.trust == 1.0
    assert bob.suspicion == 0.0
    assert bob.threat == 0.55  # untouched field preserved
    assert any("bob: trust 1.00" in line for line in rels.prompt_lines())


def test_agent_memory_round_trip() -> None:
    agent = Agent("alice", "Alice", Persona())
    asyncio.run(agent.remember("bob was accused", round_number=2, sequence=5))
    asyncio.run(agent.remember("game started", round_number=1, sequence=1))
    items = asyncio.run(agent.recall("accused"))
    assert [i["content"] for i in items] == ["bob was accused"]
    recent = asyncio.run(agent.recall("", limit=1))
    assert [i["content"] for i in recent] == ["game started"]  # last remembered
