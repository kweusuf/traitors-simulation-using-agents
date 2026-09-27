"""Unit tests for domain models (spec section 6, 8, 9, 23)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from simulation.actions.actions import MVP_ACTIONS, Action, ActionType
from simulation.communication.channels import Channel, Message
from simulation.engine.state import GamePhase, GameState, PlayerState, Role
from simulation.persistence.event_log import Event, EventType


def test_role_and_phase_enums_cover_spec_values() -> None:
    assert {r.value for r in Role} == {"traitor", "faithful"}
    expected_phases = {
        "mission",
        "public_discussion",
        "private_chat",
        "round_table",
        "voting",
        "elimination",
        "traitor_night",
        "game_end",
    }
    assert expected_phases <= {p.value for p in GamePhase}


def test_mvp_action_subset() -> None:
    assert MVP_ACTIONS == {
        ActionType.PUBLIC_MESSAGE,
        ActionType.PRIVATE_MESSAGE,
        ActionType.VOTE,
        ActionType.TRAITOR_KILL,
    }
    # Full vocabulary: MVP four plus the post-MVP RECRUIT and
    # TRAITOR_MESSAGE actions, and the debate actions reserved for
    # later milestones.
    assert ActionType.RECRUIT not in MVP_ACTIONS
    assert ActionType.TRAITOR_MESSAGE not in MVP_ACTIONS
    assert len(ActionType) == 10


def test_vote_requires_target() -> None:
    with pytest.raises(ValidationError):
        Action(action=ActionType.VOTE, actor_id="alice")


def test_message_actions_require_content() -> None:
    with pytest.raises(ValidationError):
        Action(action=ActionType.PUBLIC_MESSAGE, actor_id="alice")
    with pytest.raises(ValidationError):
        Action(action=ActionType.PRIVATE_MESSAGE, actor_id="alice", target="bob")


def test_confidence_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        Action(action=ActionType.VOTE, actor_id="alice", target="bob", confidence=1.5)
    with pytest.raises(ValidationError):
        Action(action=ActionType.VOTE, actor_id="alice", target="bob", confidence=-0.1)
    ok = Action(action=ActionType.VOTE, actor_id="alice", target="bob", confidence=0.81)
    assert ok.confidence == 0.81


def test_action_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Action.model_validate(
            {"action": "vote", "actor_id": "a", "target": "b", "bogus": 1}
        )


def test_message_model_round_trip() -> None:
    msg = Message(
        message_id="msg-1",
        sender_id="bob",
        recipients=["eve"],
        channel=Channel.PRIVATE,
        content="Alice suspects me.",
        round_number=2,
        phase="private_chat",
    )
    parsed = Message.model_validate_json(msg.model_dump_json())
    assert parsed.channel is Channel.PRIVATE
    assert parsed.recipients == ["eve"]
    assert parsed.content == "Alice suspects me."


def test_channel_enum_coverage() -> None:
    assert {c.value for c in Channel} == {"public", "private", "role_private", "system"}


def test_event_vocabulary_matches_spec() -> None:
    spec_events = {
        "GAME_STARTED",
        "ROLE_ASSIGNED",
        "PHASE_STARTED",
        "MISSION_STARTED",
        "MISSION_COMPLETED",
        "PUBLIC_MESSAGE",
        "PRIVATE_MESSAGE",
        "VOTE_CAST",
        "VOTE_TIE",
        "PLAYER_ELIMINATED",
        "TRAITOR_KILL",
        "PHASE_ENDED",
        "GAME_WON",
    }
    assert spec_events <= {e.value for e in EventType}


def test_event_serialization_round_trip() -> None:
    evt = Event(
        event_id="evt-123",
        game_id="game-001",
        sequence=7,
        round=2,
        phase="private_chat",
        type=EventType.PRIVATE_MESSAGE,
        actor="bob",
        targets=["eve"],
        payload={"content": "Alice suspects me."},
    )
    raw = json.loads(evt.model_dump_json())
    assert raw["type"] == "PRIVATE_MESSAGE"
    assert raw["payload"]["content"] == "Alice suspects me."
    parsed = Event.model_validate(raw)
    assert parsed == evt


def test_game_state_defaults() -> None:
    state = GameState(game_id="game-001")
    assert state.phase is GamePhase.SETUP
    assert state.players == {}
    assert state.alive_players == set()
    assert state.winner is None
    state.players["alice"] = PlayerState(player_id="alice", name="Alice")
    assert state.players["alice"].alive
