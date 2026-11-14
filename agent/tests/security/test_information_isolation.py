"""Security-style tests for information isolation (spec section 10, 29).

These are the leakage tests: hidden information must never appear in an
agent-specific observation.
"""

from __future__ import annotations

import pytest

from simulation.communication.channels import Channel, Message
from simulation.communication.router import MessageRouter
from simulation.communication.visibility import InformationProjector
from simulation.engine.state import GamePhase, GameState, PlayerState, Role


def make_state() -> GameState:
    roles = {
        "alice": Role.TRAITOR,
        "bob": Role.TRAITOR,
        "charlie": Role.FAITHFUL,
        "david": Role.FAITHFUL,
        "eve": Role.FAITHFUL,
        "frank": Role.FAITHFUL,
    }
    state = GameState(game_id="game-001", round_number=2, phase=GamePhase.PRIVATE_CHAT)
    for pid in roles:
        state.players[pid] = PlayerState(player_id=pid, name=pid.capitalize())
    state.alive_players = set(roles)
    state.roles = roles
    return state


def make_router() -> MessageRouter:
    router = MessageRouter("game-001")
    router.deliver(
        Message(
            message_id="m1",
            sender_id="eve",
            recipients=[],
            channel=Channel.PUBLIC,
            content="bob voted oddly",
            round_number=2,
            phase="public_discussion",
        )
    )
    router.deliver(
        Message(
            message_id="m2",
            sender_id="bob",
            recipients=["eve"],
            channel=Channel.PRIVATE,
            content="alice suspects me",
            round_number=2,
            phase="private_chat",
        )
    )
    router.deliver(
        Message(
            message_id="m3",
            sender_id="alice",
            recipients=["bob"],
            channel=Channel.ROLE_PRIVATE,
            content="we kill david tonight",
            round_number=2,
            phase="private_chat",
        )
    )
    return router


def test_faithful_never_see_living_traitor_roles() -> None:
    state = make_state()
    projector = InformationProjector(make_router())
    for agent in ["charlie", "david", "eve", "frank"]:
        view = projector.project(state, agent)
        assert view.known_roles == {agent: Role.FAITHFUL}


def test_traitor_sees_own_team() -> None:
    state = make_state()
    projector = InformationProjector(make_router())
    view = projector.project(state, "alice")
    assert view.known_roles == {"alice": Role.TRAITOR, "bob": Role.TRAITOR}


def test_private_and_role_private_messages_do_not_leak() -> None:
    state = make_state()
    router = make_router()
    projector = InformationProjector(router)

    for agent in ["charlie", "david", "frank"]:
        view = projector.project(state, agent)
        dump = view.model_dump_json() + view.render()
        assert "alice suspects me" not in dump
        assert "we kill david tonight" not in dump
        assert view.private_conversations == []

    # eve only sees the message she participates in, not the traitor chat.
    eve_view = projector.project(state, "eve")
    assert [m.content for m in eve_view.private_conversations] == ["alice suspects me"]
    assert "we kill david tonight" not in eve_view.model_dump_json()

    # alice (traitor) sees her own role chat but not bob's private message.
    alice_view = projector.project(state, "alice")
    assert [m.content for m in alice_view.private_conversations] == [
        "we kill david tonight"
    ]
    assert "alice suspects me" not in alice_view.model_dump_json()


def test_public_transcript_visible_to_everyone() -> None:
    state = make_state()
    projector = InformationProjector(make_router())
    for agent in state.players:
        view = projector.project(state, agent)
        assert [m.content for m in view.public_transcript] == ["bob voted oddly"]


def test_eliminated_roles_are_revealed_but_living_ones_are_not() -> None:
    state = make_state()
    state.players["bob"].alive = False
    state.alive_players.discard("bob")
    state.eliminated_players.add("bob")
    projector = InformationProjector(make_router())

    view = projector.project(state, "charlie")
    assert view.known_roles == {"charlie": Role.FAITHFUL, "bob": Role.TRAITOR}
    assert "alice" not in view.known_roles


def test_reveal_can_be_disabled() -> None:
    state = make_state()
    state.players["bob"].alive = False
    state.alive_players.discard("bob")
    state.eliminated_players.add("bob")
    projector = InformationProjector(
        make_router(), reveal_on_elimination=False, reveal_on_end=False
    )
    view = projector.project(state, "charlie")
    assert view.known_roles == {"charlie": Role.FAITHFUL}


def test_all_roles_revealed_after_game_end() -> None:
    state = make_state()
    state.winner = "faithful"
    state.winning_team = Role.FAITHFUL
    projector = InformationProjector(make_router())
    view = projector.project(state, "charlie")
    assert view.known_roles == state.roles


def test_projector_rejects_unknown_agent() -> None:
    state = make_state()
    projector = InformationProjector(make_router())
    with pytest.raises(ValueError, match="unknown agent"):
        projector.project(state, "mallory")


def test_view_render_contains_no_hidden_role_for_living_traitor() -> None:
    state = make_state()
    projector = InformationProjector(make_router())
    render = projector.project(state, "frank").render()
    assert "Your role: faithful" in render
    assert "Known roles" not in render
    assert "Private knowledge of roles" not in render
    # Player names are public; living traitor roles are not.
    assert "alice=traitor" not in render
    assert "bob=traitor" not in render
