"""Structured action model (spec section 8).

Agents produce these; the engine validates and applies them. The engine
never parses free text to determine game state when a structured action
exists.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import Field, model_validator

from simulation.models.base import StrictModel


class ActionType(str, Enum):
    PUBLIC_MESSAGE = "public_message"
    PRIVATE_MESSAGE = "private_message"
    VOTE = "vote"
    TRAITOR_KILL = "traitor_kill"
    ACCUSE = "accuse"
    DEFEND = "defend"
    SHARE_INFORMATION = "share_information"
    WITHHOLD_INFORMATION = "withhold_information"


# MVP supports only these actions (spec section 8).
MVP_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.PUBLIC_MESSAGE,
        ActionType.PRIVATE_MESSAGE,
        ActionType.VOTE,
        ActionType.TRAITOR_KILL,
    }
)

_ACTIONS_REQUIRING_TARGET = frozenset(
    {
        ActionType.PRIVATE_MESSAGE,
        ActionType.VOTE,
        ActionType.TRAITOR_KILL,
        ActionType.ACCUSE,
        ActionType.SHARE_INFORMATION,
        ActionType.WITHHOLD_INFORMATION,
    }
)

_ACTIONS_REQUIRING_CONTENT = frozenset(
    {
        ActionType.PUBLIC_MESSAGE,
        ActionType.PRIVATE_MESSAGE,
        ActionType.ACCUSE,
        ActionType.DEFEND,
        ActionType.SHARE_INFORMATION,
        ActionType.WITHHOLD_INFORMATION,
    }
)


class Action(StrictModel):
    """A single structured decision from an agent."""

    action: ActionType
    actor_id: str
    target: Optional[str] = None
    content: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason_summary: Optional[str] = None

    @model_validator(mode="after")
    def _check_required_fields(self) -> "Action":
        if self.action in _ACTIONS_REQUIRING_TARGET and not self.target:
            raise ValueError(f"action '{self.action.value}' requires a target")
        if self.action in _ACTIONS_REQUIRING_CONTENT and not (
            self.content and self.content.strip()
        ):
            raise ValueError(f"action '{self.action.value}' requires content")
        return self
