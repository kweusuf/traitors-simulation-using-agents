"""Message router (spec section 9).

Delivery is structural: a message carries an explicit recipient list,
is stored once, and reads are filtered by `is_visible`. Non-recipients
never receive another agent's private message.
"""

from __future__ import annotations

from typing import Optional

from simulation.communication.channels import Channel, Message
from simulation.persistence.database import Database
from simulation.persistence.repositories import MessageRepository


class MessageRouter:
    def __init__(self, game_id: str, db: Optional[Database] = None) -> None:
        self.game_id = game_id
        self._repo = MessageRepository(db) if db is not None else None
        self.messages: list[Message] = []

    def deliver(self, message: Message) -> None:
        """Persist a message; visibility is enforced on every read."""
        if message.channel in (Channel.PRIVATE, Channel.ROLE_PRIVATE) and not message.recipients:
            raise ValueError(f"{message.channel.value} message requires recipients")
        if message.channel in (Channel.PUBLIC, Channel.SYSTEM) and message.recipients:
            raise ValueError(f"{message.channel.value} message must not carry recipients")
        self.messages.append(message)
        if self._repo is not None:
            self._repo.insert(self.game_id, message)

    def visible_to(self, agent_id: str) -> list[Message]:
        """Every message this agent may read, in delivery order."""
        return [m for m in self.messages if is_visible(m, agent_id)]


def is_visible(message: Message, agent_id: str) -> bool:
    """Structural visibility rule (spec section 9)."""
    if message.channel in (Channel.PUBLIC, Channel.SYSTEM):
        return True
    if agent_id == message.sender_id:
        return True
    return agent_id in message.recipients
