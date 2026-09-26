"""Event vocabulary and event model for event-sourced logging (spec section 23).

JSONL writing and SQLite persistence land in Phase 3. Events carry a
monotonic `sequence` instead of a wall-clock timestamp so that replays
stay deterministic.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import Field

from simulation.models.base import StrictModel


class EventType(str, Enum):
    GAME_STARTED = "GAME_STARTED"
    ROLE_ASSIGNED = "ROLE_ASSIGNED"
    ROLE_RECRUITED = "ROLE_RECRUITED"
    ROUND_STARTED = "ROUND_STARTED"
    PHASE_STARTED = "PHASE_STARTED"
    PHASE_ENDED = "PHASE_ENDED"
    MISSION_STARTED = "MISSION_STARTED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    PUBLIC_MESSAGE = "PUBLIC_MESSAGE"
    PRIVATE_MESSAGE = "PRIVATE_MESSAGE"
    VOTE_CAST = "VOTE_CAST"
    VOTE_TIE = "VOTE_TIE"
    PLAYER_ELIMINATED = "PLAYER_ELIMINATED"
    TRAITOR_KILL = "TRAITOR_KILL"
    FINALE_STARTED = "FINALE_STARTED"
    ACTION_REJECTED = "ACTION_REJECTED"
    SNAPSHOT_CREATED = "SNAPSHOT_CREATED"
    GAME_WON = "GAME_WON"
    GAME_ENDED = "GAME_ENDED"


class Event(StrictModel):
    event_id: str
    game_id: str
    sequence: int = Field(ge=0)
    round: int = Field(default=0, ge=0)
    phase: str = ""
    type: EventType
    actor: Optional[str] = None
    targets: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
