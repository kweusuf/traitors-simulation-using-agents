"""Unit tests for the prompt builder (spec section 20)."""

from __future__ import annotations

from simulation.actions.actions import ActionType
from simulation.agents.goals import Goals
from simulation.agents.persona import Persona
from simulation.agents.prompts import PromptBuilder
from simulation.communication.channels import Channel, Message
from simulation.communication.visibility import AgentView
from simulation.engine.state import GamePhase, Role


def make_view(**overrides) -> AgentView:
    defaults = dict(
        agent_id="alice",
        game_id="game-001",
        round_number=2,
        phase=GamePhase.PUBLIC_DISCUSSION,
        own_role=Role.FAITHFUL,
        known_roles={"alice": Role.FAITHFUL},
        alive_players=["alice", "bob", "charlie"],
        eliminated_players=["david"],
        public_transcript=[
            Message(
                message_id="m1",
                sender_id="eve",
                recipients=[],
                channel=Channel.PUBLIC,
                content="bob voted oddly",
            )
        ],
        private_conversations=[
            Message(
                message_id="m2",
                sender_id="bob",
                recipients=["alice"],
                channel=Channel.PRIVATE,
                content="i trust you",
            )
        ],
        winner=None,
    )
    defaults.update(overrides)
    return AgentView(**defaults)


def build(view: AgentView | None = None, **kwargs) -> list:
    builder = PromptBuilder()
    params: dict = dict(
        action_type=ActionType.PUBLIC_MESSAGE,
        legal_targets=[],
        memory_items=[{"content": "bob was accused"}],
    )
    params.update(kwargs)
    return builder.build(
        agent_id="alice",
        role=Role.FAITHFUL,
        persona=Persona(
            description="Careful player.",
            personality={"analytical": 0.9, "trust": 0.3},
        ),
        goals=Goals(primary="survive", secondary=["identify_traitors"]),
        view=view or make_view(),
        **params,
    )


def test_build_returns_system_and_user_messages() -> None:
    messages = build()
    assert [m.role for m in messages] == ["system", "user"]
    system, user = messages[0].content, messages[1].content

    # System: identity, role, personality, goals.
    assert "You are alice" in system
    assert "Your role: faithful" in system
    assert "weigh evidence" in system
    assert "Primary goal: survive" in system
    assert "identify_traitors" in system

    # User: phase, action type, view, transcript, memories, JSON hint.
    assert "Current phase: public_discussion" in system or "public_discussion" in user
    assert "Required action type: public_message" in user
    assert "Alive players: alice, bob, charlie" in user
    assert "Eliminated: david" in user
    assert "bob voted oddly" in user
    assert "i trust you" in user
    assert "bob was accused" in user
    assert "JSON only" in user


def test_legal_targets_listed() -> None:
    messages = build(action_type=ActionType.VOTE, legal_targets=["bob", "charlie"])
    user = messages[1].content
    assert "Legal targets: bob, charlie" in user


def test_no_legal_targets_states_none() -> None:
    user = build()[1].content
    assert "Legal targets: none" in user


def test_prompt_never_contains_absent_private_content() -> None:
    # View without the private conversation: the prompt must not carry it.
    view = make_view(private_conversations=[])
    user = build(view=view)[1].content
    assert "i trust you" not in user


def test_extra_instruction_appended() -> None:
    user = build(extra_instruction="Choose carefully.")[1].content
    assert "Choose carefully." in user
