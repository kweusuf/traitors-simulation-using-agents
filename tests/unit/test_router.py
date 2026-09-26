"""Unit tests for message routing (spec section 9)."""

from __future__ import annotations

from simulation.actions.actions import Action, ActionType
from simulation.communication.channels import Channel, Message
from simulation.communication.router import MessageRouter, is_visible
from simulation.engine.game_engine import GameEngine
from simulation.engine.state import GamePhase
from simulation.experiments.config import GameConfig
from simulation.persistence.sink import EventSink


def _msg(sender: str, recipients: list[str], channel: Channel, content: str) -> Message:
    return Message(
        message_id=f"m-{sender}-{channel.value}",
        sender_id=sender,
        recipients=recipients,
        channel=channel,
        content=content,
    )


def test_public_and_private_delivery_rules() -> None:
    router = MessageRouter("game-001")
    router.deliver(_msg("eve", [], Channel.PUBLIC, "I trust bob"))
    router.deliver(_msg("eve", ["alice"], Channel.PRIVATE, "meet me at night"))

    import pytest

    with pytest.raises(ValueError, match="requires recipients"):
        router.deliver(_msg("eve", [], Channel.PRIVATE, "orphan"))
    with pytest.raises(ValueError, match="must not carry recipients"):
        router.deliver(_msg("eve", ["alice"], Channel.PUBLIC, "targeted"))


def test_visibility_filter() -> None:
    public = _msg("eve", [], Channel.PUBLIC, "public line")
    private = _msg("bob", ["eve"], Channel.PRIVATE, "secret")
    role_private = _msg("alice", ["bob"], Channel.ROLE_PRIVATE, "we kill tonight")

    assert is_visible(public, "alice")
    assert is_visible(private, "bob")
    assert is_visible(private, "eve")
    assert not is_visible(private, "alice")
    assert not is_visible(role_private, "charlie")
    assert is_visible(role_private, "alice")
    assert is_visible(role_private, "bob")


def test_router_visible_to_partitions_readers() -> None:
    router = MessageRouter("game-001")
    router.deliver(_msg("eve", [], Channel.PUBLIC, "public line"))
    router.deliver(_msg("bob", ["eve"], Channel.PRIVATE, "secret"))
    router.deliver(_msg("alice", ["bob"], Channel.ROLE_PRIVATE, "team chat"))

    def contents(agent: str) -> set[str]:
        return {m.content for m in router.visible_to(agent)}

    assert contents("charlie") == {"public line"}
    assert contents("eve") == {"public line", "secret"}
    assert contents("bob") == {"public line", "secret", "team chat"}
    assert contents("alice") == {"public line", "team chat"}


def test_engine_routes_accepted_messages_to_router() -> None:
    config = GameConfig(game={"players": 6, "traitors": 2})
    engine = GameEngine(config, EventSink("game-001"), seed=42)
    engine.start()

    engine.begin_phase(GamePhase.PUBLIC_DISCUSSION)
    engine.submit_action(
        Action(action=ActionType.PUBLIC_MESSAGE, actor_id="alice", content="hi all")
    )
    assert [m.content for m in engine.router.visible_to("bob")] == ["hi all"]

    engine.begin_phase(GamePhase.PRIVATE_CHAT)
    engine.submit_action(
        Action(
            action=ActionType.PRIVATE_MESSAGE,
            actor_id="bob",
            target="eve",
            content="alice is suspicious",
        )
    )
    assert {m.content for m in engine.router.visible_to("alice")} == {"hi all"}
    assert {m.content for m in engine.router.visible_to("eve")} == {
        "hi all",
        "alice is suspicious",
    }

    # Second private message is within the limit of 2; the third is rejected.
    engine.submit_action(
        Action(
            action=ActionType.PRIVATE_MESSAGE,
            actor_id="bob",
            target="alice",
            content="trust me",
        )
    )
    rejected = engine.submit_action(
        Action(
            action=ActionType.PRIVATE_MESSAGE,
            actor_id="bob",
            target="charlie",
            content="never delivered",
        )
    )
    assert not rejected.ok
    # Rejected actions never reach the router.
    assert {m.content for m in engine.router.visible_to("charlie")} == {"hi all"}
