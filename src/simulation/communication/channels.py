"""Communication channels and message model (spec section 9).

Visibility is structural: a Message lists its recipients explicitly and
the router only ever delivers to them. Privacy is not delegated to
prompts.
"""

from __future__ import annotations

from enum import Enum

from simulation.models.base import StrictModel


class Channel(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    ROLE_PRIVATE = "role_private"
    SYSTEM = "system"


class Message(StrictModel):
    message_id: str
    sender_id: str
    recipients: list[str]
    channel: Channel
    content: str
    round_number: int = 0
    phase: str = ""
