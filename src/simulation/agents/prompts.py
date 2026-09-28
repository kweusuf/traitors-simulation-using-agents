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
from simulation.engine.state import GamePhase, Role
from simulation.models.base import ChatMessage

ACTION_JSON_HINT = (
    'Respond with JSON only, e.g. {"action": "...", "target": "...", '
    '"content": "...", "confidence": 0.8}. No other text.'
)

# For an action whose content is a fixed answer rather than prose, the
# example shows one of the accepted values instead of filler text.
_CONTENT_EXAMPLE: dict[ActionType, str] = {
    ActionType.END_VOTE: "end",
    ActionType.RECRUIT_DECISION: "recruit",
    ActionType.RECRUIT_RESPONSE: "accept",
}


def _window(messages, limit: int) -> tuple[list, int]:
    """Newest `limit` entries plus how many were dropped (0 = no limit)."""
    if not limit or len(messages) <= limit:
        return list(messages), 0
    return list(messages[-limit:]), len(messages) - limit


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
        # The end vote's and the recruitment answers' content is their
        # answer, not prose.
        example["content"] = _CONTENT_EXAMPLE.get(
            action_type, "your message here"
        )
    else:
        example["content"] = ""
    example["confidence"] = 0.8
    return (
        f"Respond with JSON only, e.g. {json.dumps(example)}. "
        "Include every field shown in the example. No other text."
    )


class PromptBuilder:
    """Builds prompts, optionally showing only the tail of the transcript.

    `transcript_limit` bounds how many transcript lines each prompt
    carries (0 keeps everything). Late-game prompts reach tens of
    thousands of characters and every one of them is re-evaluated on
    every call, so this is the main lever on call latency.
    """

    def __init__(self, transcript_limit: int = 0) -> None:
        self.transcript_limit = transcript_limit

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
            "Role secrecy (hard rule): your role is hidden from every other "
            "player. In any public message you must never say or hint that "
            "you are a traitor, never name anyone as a traitor, and never "
            "repeat the roles you privately know as if the group could see "
            "them. Never claim a role you were not given.",
            "",
            "Originality (hard rule): every message must be yours alone. "
            "React to one specific thing a named player actually said or "
            "did, take a position on it, and never repeat or rephrase "
            "wording already visible in the transcript above. A generic "
            "observation about caution, patience or how noisy the group "
            "is could have been written by anyone and tells you nothing.",
            "",
        ]
        if role is Role.TRAITOR:
            lines.append(
                "As a traitor you may switch sides: work with your traitor "
                "allies to banish every faithful player and win as a team, or "
                "side with the faithful against a rival traitor and win alone "
                "as the last traitor standing. Both paths keep your own "
                "identity secret: you act through your votes and your "
                "reasoning in discussion, never by announcing that you or "
                "anyone else is a traitor. The other traitors may back you "
                "or may plot against you."
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
        if "shield" in view.items:
            lines.append(
                "You hold the shield: it blocks the next murder attempt on "
                "you, the attempt is not refunded to the traitors, and you "
                "choose whether to disclose that you have it."
            )
        if "dagger" in view.items:
            lines.append(
                "You hold the dagger: your vote counts twice, and it is "
                "spent the first time you vote."
            )
        if "seer" in view.items:
            lines.append(
                "You hold the seer: you may check one player's true role "
                "once, using the seer_check action during the private_chat "
                "phase. The answer arrives as a private message from the "
                "host and nobody else sees it."
            )
        if action_type is ActionType.SEER_CHECK:
            lines.append(
                "seer_check tells you one player's true role: name the one "
                "living player you most need to read. This is your only "
                "check, so spend it on the player whose allegiance would "
                "change your game."
            )
        if action_type is ActionType.NOMINATE:
            lines.append(
                "Nominate the player you would most want gone: only the "
                "nominated group can be murdered tonight, so put the players "
                "your team cannot afford to keep in front of the knife."
            )
        if action_type is ActionType.RECRUIT:
            if view.phase is GamePhase.TRAITOR_NIGHT:
                lines.append(
                    "Recruitment window: tonight the traitors offer one "
                    "living faithful player a place on the team instead of "
                    "murdering. Name the player who most strengthens the "
                    "traitors; they are told of the offer and may refuse."
                )
            else:
                lines.append(
                    "You are a banished traitor making one final choice: recruit a "
                    "living faithful player onto the traitor team. They become a "
                    "traitor immediately and are told their new role."
                )
        if action_type is ActionType.RECRUIT_DECISION:
            lines.append(
                "Recruitment decision: a traitor left the tower at the round "
                "table, so the traitors may recruit one living faithful player "
                "tonight instead of murdering. Recruiting costs tonight's "
                "murder: nobody dies and the kill is not moved to anyone else. "
                "A lone traitor can force a recruit even if the offer is "
                "refused, because refusing a lone traitor's offer is fatal. "
                "Answer with content 'recruit' or 'murder'."
            )
        if action_type is ActionType.RECRUIT_RESPONSE:
            lines.append(
                "The traitors have offered you a place among them. The offer "
                "is real: accepting makes you a traitor immediately and you "
                "win as one of them. You are allowed to decline. If the "
                "traitors are down to a single player, refusing is fatal: "
                "that lone traitor murders you tonight instead. Answer with "
                "content 'accept' or 'decline'."
            )
        if action_type is ActionType.END_VOTE:
            lines.append(
                "The endgame vote: you may end the game now, or force one "
                "more banishment. Ending is only right when you are "
                "confident that no traitor remains among you; if you still "
                "suspect anyone, force the banishment. Answer with content "
                "'end' or 'banish'."
            )
        if action_type is ActionType.PUBLIC_MESSAGE:
            lines.append(
                "This message goes to every player. Speak as one player among "
                "many: no claims about your own role, no naming anyone as a "
                "traitor, and no repeating role information you only privately "
                "know. Name the player you are responding to and say something "
                "this conversation has not heard yet; do not echo phrasing "
                "from the transcript."
            )
        if action_type is ActionType.PRIVATE_MESSAGE:
            lines.append(
                "This message goes to one player only. Address them by name "
                "and continue something the two of you actually said; do not "
                "reuse phrasing from the public transcript."
            )
        if action_type is ActionType.TRAITOR_MESSAGE:
            lines.append(
                "Traitors only, no faithful player can read this. Argue the "
                "kill: is tonight's victim the biggest threat to your team, "
                "or the death that makes the most convenient suspect? Agree "
                "who takes the blame at the round table and which innocent "
                "you will push the faithful to banish."
            )
        if action_type is ActionType.TRAITOR_KILL:
            lines.append(
                "Pick tonight's victim for a reason that fits your plan: the "
                "player most dangerous to your team, or a player whose death "
                "puts an innocent in the frame for the next banishment. Say "
                "the reason in reason_summary so it matches what you argued "
                "in the traitor council."
            )
        if (
            action_type is ActionType.PUBLIC_MESSAGE
            and view.own_role is Role.TRAITOR
            and view.phase is GamePhase.ROUND_TABLE
        ):
            lines.append(
                "Round table: choose one innocent to take the fall and build "
                "the case against them in public, so the faithful banish one "
                "of their own. Stay inside the secrecy rule, never hint that "
                "you are coordinating, and if suspicion is turning on you, "
                "defend yourself before pushing anyone else."
            )
        if action_type is ActionType.VOTE and view.own_role is Role.TRAITOR:
            lines.append(
                "Vote with the faithful against the innocent you have been "
                "framing, unless the vote is on you or a fellow traitor: then "
                "vote for whichever faithful keeps both of you safe."
            )
        if legal_targets:
            lines.append("Legal targets: " + ", ".join(sorted(legal_targets)))
        else:
            lines.append(
                'Legal targets: none (this action takes no target; '
                'set "target" to null).'
            )

        if view.public_transcript:
            shown, dropped = _window(
                view.public_transcript, self.transcript_limit
            )
            lines.append("")
            lines.append("Public transcript:")
            for msg in shown:
                lines.append(f"  [{msg.round_number}] {msg.sender_id}: {msg.content}")
            if dropped:
                lines.append(
                    f"  (earlier {dropped} public messages are omitted; "
                    "eliminations are listed above)"
                )
        if view.private_conversations:
            shown, dropped = _window(
                view.private_conversations, self.transcript_limit
            )
            lines.append("")
            lines.append("Your private conversations:")
            for msg in shown:
                peers = ", ".join(
                    r for r in msg.recipients if r != view.agent_id
                ) or "everyone"
                lines.append(
                    f"  [{msg.round_number}] {msg.sender_id} -> {peers}: {msg.content}"
                )
            if dropped:
                lines.append(f"  (earlier {dropped} private messages are omitted)")
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
