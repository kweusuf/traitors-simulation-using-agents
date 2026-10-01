"""Structured action model (spec section 8).

Agents produce these; the engine validates and applies them. The engine
never parses free text to determine game state when a structured action
exists.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import Field, create_model, model_validator

from simulation.models.base import StrictModel


class ActionType(str, Enum):
    PUBLIC_MESSAGE = "public_message"
    PRIVATE_MESSAGE = "private_message"
    VOTE = "vote"
    TRAITOR_KILL = "traitor_kill"
    TRAITOR_MESSAGE = "traitor_message"
    RECRUIT = "recruit"
    ACCUSE = "accuse"
    DEFEND = "defend"
    REBUT = "rebut"
    SHARE_INFORMATION = "share_information"
    WITHHOLD_INFORMATION = "withhold_information"
    # Wave B mechanics (plan section 4): a one-shot private role check
    # and the on-trial murder nomination.
    SEER_CHECK = "seer_check"
    NOMINATE = "nominate"
    # Finale end-or-banish vote (phase 25): targetless, content is
    # `end` or `banish`, and only legal inside the finale.
    END_VOTE = "end_vote"
    # Recruitment as a choice (phase 26): the traitors vote to recruit or
    # murder on the night after a banishment, and the offered player
    # answers. Both are targetless and carry their answer in `content`.
    RECRUIT_DECISION = "recruit_decision"
    RECRUIT_RESPONSE = "recruit_response"


# MVP supports only these actions (spec section 8).
MVP_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.PUBLIC_MESSAGE,
        ActionType.PRIVATE_MESSAGE,
        ActionType.VOTE,
        ActionType.TRAITOR_KILL,
    }
)

ACTIONS_REQUIRING_TARGET = frozenset(
    {
        ActionType.PRIVATE_MESSAGE,
        ActionType.VOTE,
        ActionType.TRAITOR_KILL,
        ActionType.RECRUIT,
        ActionType.ACCUSE,
        ActionType.SHARE_INFORMATION,
        ActionType.WITHHOLD_INFORMATION,
        ActionType.SEER_CHECK,
        ActionType.NOMINATE,
    }
)

ACTIONS_REQUIRING_CONTENT = frozenset(
    {
        ActionType.PUBLIC_MESSAGE,
        ActionType.PRIVATE_MESSAGE,
        ActionType.TRAITOR_MESSAGE,
        ActionType.ACCUSE,
        ActionType.DEFEND,
        ActionType.REBUT,
        ActionType.SHARE_INFORMATION,
        ActionType.WITHHOLD_INFORMATION,
        # The end vote carries its answer in `content` ("end"/"banish").
        ActionType.END_VOTE,
        # Recruitment answers also ride in `content` ("recruit"/"murder"
        # and "accept"/"decline").
        ActionType.RECRUIT_DECISION,
        ActionType.RECRUIT_RESPONSE,
    }
)

# Actions whose `content` is one of a fixed set of answers rather than
# prose. The engine reads these as bare tokens, so the response schema
# pins the field to exactly the set below.
#
# Why this is spelled out rather than left implicit: for a closed-choice
# field the model answers with whatever value the prompt's worked example
# holds, because it copies the example. The end vote's example held
# "end", and every season run ended its finale with all five players -
# four faithful and the traitor alike - answering `end`. On a unanimous
# `end` the game stops, so a surviving traitor takes the prize. The two
# recruitment answers carried the same default in the same direction
# ("recruit", "accept"). These answers now reach the model as an enum on
# the schema (see `action_schema`) and as text in the instruction, never
# as a worked value.
CONTENT_CHOICES: dict[ActionType, tuple[str, ...]] = {
    ActionType.END_VOTE: ("end", "banish"),
    ActionType.RECRUIT_DECISION: ("recruit", "murder"),
    ActionType.RECRUIT_RESPONSE: ("accept", "decline"),
}


class Action(StrictModel):
    """A single structured decision from an agent."""

    action: ActionType
    actor_id: str
    target: Optional[str] = None
    content: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason_summary: Optional[str] = None

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        """JSON schema handed to a model for structured output (spec section 21).

        The generated schema is what constrains the model's answer, so
        every field a message action cannot work without is required
        there even though the Python model keeps defaults (the framework
        stamps `actor_id`, and a vote may legitimately carry no text).
        Pydantic's own schema leaves them optional, which is exactly how
        a small model ends up dropping `content` from a private message.
        """
        schema = handler(core_schema)
        if isinstance(schema, dict):
            schema["required"] = ["action", "target", "content"]
        return schema

    @model_validator(mode="after")
    def _check_required_fields(self) -> "Action":
        if self.action in ACTIONS_REQUIRING_TARGET and not self.target:
            raise ValueError(f"action '{self.action.value}' requires a target")
        if self.action in ACTIONS_REQUIRING_CONTENT and not (
            self.content and self.content.strip()
        ):
            raise ValueError(f"action '{self.action.value}' requires content")
        return self


def action_schema(action_type: ActionType) -> type[Action]:
    """The JSON schema handed to the model for one action.

    A closed-choice action gets a schema whose `content` is an enum of its
    accepted answers. Two things follow, and both are the point:

    - the field has no default for the model to copy, so the answer comes
      from the agent rather than from the worked example, and
    - prose is impossible there. Left free, the model answers a
      closed-choice field with a paragraph, which the engine rejects and
      retries; the enum keeps the reply on the accepted tokens.

    Every other action keeps the plain schema, so nothing changes for the
    free-text turns.
    """
    choices = CONTENT_CHOICES.get(action_type)
    if not choices:
        return Action
    # `Literal[tuple]` is `Literal` over the tuple's members (PEP 586), so
    # the enum is built from the same tuple the prompt hint renders.
    name = "".join(part.title() for part in action_type.value.split("_"))
    return create_model(
        f"{name}Action",
        __base__=Action,
        content=(Literal[choices], ...),
    )
