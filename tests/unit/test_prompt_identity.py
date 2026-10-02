"""The reader must be able to tell its own words from everyone else's.

Nine messages in the English run opened with the sender's own name, as in
"Ivan, you noted that..." sent by Ivan. The transcript rendered every line
identically, so the model could not tell what it had already said from what
others had.
"""

from __future__ import annotations

from simulation.actions.actions import ActionType
from simulation.agents.goals import Goals
from simulation.agents.persona import Persona
from simulation.agents.prompts import PromptBuilder
from simulation.communication.channels import Channel, Message
from simulation.communication.visibility import AgentView
from simulation.engine.state import GamePhase, Role


def public(sender: str, content: str) -> Message:
    return Message(
        message_id=f"m-{sender}",
        sender_id=sender,
        recipients=[],
        channel=Channel.PUBLIC,
        content=content,
    )


def base_view(**overrides) -> AgentView:
    defaults = dict(
        agent_id="ivan",
        game_id="game-001",
        round_number=2,
        phase=GamePhase.PUBLIC_DISCUSSION,
        own_role=Role.FAITHFUL,
        known_roles={"ivan": Role.FAITHFUL},
        alive_players=["ivan", "theo"],
        eliminated_players=[],
        public_transcript=[],
        private_conversations=[],
        items=[],
    )
    defaults.update(overrides)
    return AgentView(**defaults)


def test_transcript_marks_the_readers_own_messages() -> None:
    view = base_view(
        public_transcript=[
            public("ivan", "Ivan, you noted that Theo was calm."),
            public("theo", "I never said that."),
        ]
    )
    prompt = PromptBuilder().build_user(
        view, ActionType.PUBLIC_MESSAGE, legal_targets=[], agent_id="ivan"
    )
    assert "ivan (you):" in prompt
    assert "theo:" in prompt, "another player's line should stay unmarked"


def test_transcript_marks_nothing_when_there_is_no_reader() -> None:
    """No agent id means no marker on the line, so older call sites work."""
    view = base_view(public_transcript=[public("ivan", "Something.")])
    prompt = PromptBuilder().build_user(
        view, ActionType.PUBLIC_MESSAGE, legal_targets=[]
    )
    assert "ivan: Something." in prompt
    assert "ivan (you)" not in prompt


def test_public_message_forbids_replying_to_oneself_and_echoing() -> None:
    prompt = PromptBuilder().build_user(
        base_view(), ActionType.PUBLIC_MESSAGE, legal_targets=[]
    )
    assert "cannot reply to yourself" in prompt
    assert "do not open by paraphrasing whoever spoke last" in prompt
    assert "(you)" in prompt, "the instruction should point at the marker"
    assert "Take a position rather than validating the room" in prompt


def test_public_message_instruction_does_not_leak_into_private_messages() -> None:
    private = PromptBuilder().build_user(
        base_view(), ActionType.PRIVATE_MESSAGE, legal_targets=["theo"]
    )
    assert "cannot reply to yourself" not in private


def test_build_marks_own_messages_end_to_end() -> None:
    """The full path, so the marker cannot silently stop being passed."""
    messages = PromptBuilder().build(
        agent_id="ivan",
        role=Role.FAITHFUL,
        persona=Persona(description="careful"),
        goals=Goals(),
        view=base_view(public_transcript=[public("ivan", "My own line.")]),
        action_type=ActionType.PUBLIC_MESSAGE,
        legal_targets=[],
    )
    user = next(m for m in messages if m.role == "user")
    assert "ivan (you): My own line." in user.content