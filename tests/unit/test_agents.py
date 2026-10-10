"""Unit tests for persona, goals, beliefs, relationships, agent (spec 11-16)."""

from __future__ import annotations

import asyncio

import pytest

from simulation.agents.agent import Agent
from simulation.agents.beliefs import Beliefs
from simulation.agents.goals import (
    MANDATE_CANDID,
    MANDATE_CORE,
    MANDATE_PRACTISED,
    MANDATE_PRACTISED_AT,
    Goals,
    inject_role_goals,
    mandate_lines,
)
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
    # The persona's objective does not survive onto a drawn traitor. It used
    # to, and the result was imran carrying "stay_apart_from_the_herd" in the
    # same task list as "ensure_traitor_team_wins".
    assert "identify_traitors" not in traitor.secondary
    assert traitor.secondary == ["ensure_traitor_team_wins"]

    faithful = inject_role_goals(Goals(primary="survive"), Role.FAITHFUL)
    assert faithful.secondary == ["identify_traitors"]

    # A faithful player still keeps theirs; only the traitor side changed.
    kept = inject_role_goals(
        Goals(secondary=["keep_your_allies_close"]), Role.FAITHFUL
    )
    assert kept.secondary == ["keep_your_allies_close", "identify_traitors"]


def test_deception_aptitude_splits_the_cast_the_way_the_writing_does() -> None:
    """The mandate is scaled on this, so it has to separate the two groups.

    It is a property of the personas as written, not a judgement: three exist
    to deceive, nineteen were written for a faithful game. A persona-blind
    deal can hand the tower to any three of them.
    """

    def aptitude(name: str) -> float:
        persona, goals = load_persona_bundle(f"configs/personas/uk_s01/{name}.yaml")
        return persona.deception_aptitude(goals)

    written_as_deceivers = [aptitude(n) for n in ("wilf", "amanda", "alyssa")]
    assert min(written_as_deceivers) >= MANDATE_PRACTISED_AT
    # The seed-1 deal, which every random-deal run so far has replayed.
    for name in ("imran", "kieran", "meryl"):
        assert aptitude(name) < MANDATE_PRACTISED_AT
    # Coarse by design, but not flat: the least analytic persona in the cast
    # is the one least able to keep an invented detail straight.
    assert aptitude("meryl") < aptitude("kieran")

    # A persona with no goal signal at all still gets a number, not a crash.
    assert 0.0 <= Persona().deception_aptitude() <= 1.0


def test_mandate_is_scaled_in_method_but_never_in_whether_to_lie() -> None:
    candid, practised = mandate_lines(0.1), mandate_lines(0.9)
    # The same core either way: the failure being fixed is refusing to play.
    assert candid[0] == practised[0] == MANDATE_CORE
    assert "must lie" in MANDATE_CORE
    assert "you do not give up the game" in MANDATE_CORE
    # Only the method differs, and it differs in the documented direction.
    assert MANDATE_CANDID in candid and MANDATE_CANDID not in practised
    assert MANDATE_PRACTISED in practised and MANDATE_PRACTISED not in candid
    assert "withhold" in MANDATE_CANDID
    assert "Plant suspicions you know are false" in MANDATE_PRACTISED


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
