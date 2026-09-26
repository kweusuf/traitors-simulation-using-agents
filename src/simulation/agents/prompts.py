"""Prompt construction, separate from agent logic (spec section 20).

Builds system + user messages from persona, goals, the agent-specific
information view, and recent memory. Everything the model sees comes
from an already-projected `AgentView`, so hidden information cannot
enter the prompt.
"""

from __future__ import annotations

import json
from typing import Optional

from simulation.actions.actions import (
    ACTIONS_REQUIRING_CONTENT,
    ACTIONS_REQUIRING_TARGET,
    ActionType,
)
from simulation.agents.goals import Goals
from simulation.agents.persona import Persona
from simulation.communication.visibility import AgentView
from simulation.engine.state import Role
from simulation.models.base import ChatMessage

ACTION_JSON_HINT = (
    'Respond with JSON only, e.g. {"action": "...", "target": "...", '
    '"content": "...", "confidence": 0.8}. No other text.'
)


def action_json_hint(action_type: ActionType) -> str:
    """Example JSON for one action type.

    The example is action-specific because a generic example lets the
    model drop fields (e.g. `content` on a private message) that the
    schema marks optional but the game rules require. `target` and
    `content` are always shown because the structured-output schema
    always requires them; actions without a target say so explicitly
    instead of leaving the model to guess.
    """
    example: dict[str, object] = {"action": action_type.value}
    example["target"] = "bob" if action_type in ACTIONS_REQUIRING_TARGET else None
    if action_type in ACTIONS_REQUIRING_CONTENT:
        example["content"] = "your message here"
    else:
        example["content"] = ""
    example["confidence"] = 0.8
    return (
        f"Respond with JSON only, e.g. {json.dumps(example)}. "
        "Include every field shown in the example. No other text."
    )


class PromptBuilder:
    def build_system(
        self,
        agent_id: str,
        role: Role,
        persona: Persona,
        goals: Goals,
    ) -> str:
        lines = [
            f"You are {agent_id}, a player in a text-based social deduction game.",
            f"Your role: {role.value}",
            "",
            *persona.instructions(),
            "",
            *goals.lines(),
            "",
        ]
        if role is Role.TRAITOR:
            lines.append(
                "As a traitor you may switch sides: work with your traitor "
                "allies to banish every faithful player and win as a team, or "
                "ally with the faithful to banish rival traitors and win alone "
                "as the last traitor standing. The other traitors may back "
                "you or may plot against you."
            )
            lines.append("")
        lines.append("Stay in character. Reason only from the information you are given.")
        return "\n".join(lines)

    def build_user(
        self,
        view: AgentView,
        action_type: ActionType,
        legal_targets: Optional[list[str]],
        memory_items: Optional[list[dict]] = None,
        extra_instruction: Optional[str] = None,
    ) -> str:
        lines = [
            view.render(),
            "",
            f"Current phase: {view.phase.value}.",
            f"Required action type: {action_type.value}.",
        ]
        if action_type is ActionType.RECRUIT:
            lines.append(
                "You are a banished traitor making one final choice: recruit a "
                "living faithful player onto the traitor team. They become a "
                "traitor immediately and are told their new role."
            )
        if legal_targets:
            lines.append("Legal targets: " + ", ".join(sorted(legal_targets)))
        else:
            lines.append(
                'Legal targets: none (this action takes no target; '
                'set "target" to null).'
            )

        if view.public_transcript:
            lines.append("")
            lines.append("Public transcript:")
            for msg in view.public_transcript:
                lines.append(f"  [{msg.round_number}] {msg.sender_id}: {msg.content}")
        if view.private_conversations:
            lines.append("")
            lines.append("Your private conversations:")
            for msg in view.private_conversations:
                peers = ", ".join(
                    r for r in msg.recipients if r != view.agent_id
                ) or "everyone"
                lines.append(
                    f"  [{msg.round_number}] {msg.sender_id} -> {peers}: {msg.content}"
                )
        if memory_items:
            lines.append("")
            lines.append("Your recent memories:")
            for item in memory_items:
                lines.append(f"  - {item['content']}")

        if extra_instruction:
            lines.append("")
            lines.append(extra_instruction)

        lines.append("")
        lines.append(action_json_hint(action_type))
        return "\n".join(lines)

    def build(
        self,
        *,
        agent_id: str,
        role: Role,
        persona: Persona,
        goals: Goals,
        view: AgentView,
        action_type: ActionType,
        legal_targets: Optional[list[str]] = None,
        memory_items: Optional[list[dict]] = None,
        extra_instruction: Optional[str] = None,
    ) -> list[ChatMessage]:
        return [
            ChatMessage(
                role="system",
                content=self.build_system(agent_id, role, persona, goals),
            ),
            ChatMessage(
                role="user",
                content=self.build_user(
                    view,
                    action_type,
                    legal_targets,
                    memory_items,
                    extra_instruction,
                ),
            ),
        ]
