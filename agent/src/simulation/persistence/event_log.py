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
    # Deterministic host (audit fix, phase 1): engine-code narration of
    # the debate clock. `HOST_WARNING` fires when the open turns run
    # out, `DEBATE_CLOSED` when the closing turns are spent and the
    # vote is forced.
    HOST_WARNING = "HOST_WARNING"
    DEBATE_CLOSED = "DEBATE_CLOSED"
    # Hosted round table (audit fix, phase 2): the host names the
    # nominated suspects, announces who faces the revote, and reports
    # its result.
    NOMINATION_TALLY = "NOMINATION_TALLY"
    REVOTE_CALLED = "REVOTE_CALLED"
    REVOTE_RESOLVED = "REVOTE_RESOLVED"
    ROLE_ASSIGNED = "ROLE_ASSIGNED"
    ROLE_RECRUITED = "ROLE_RECRUITED"
    ROUND_STARTED = "ROUND_STARTED"
    PHASE_STARTED = "PHASE_STARTED"
    PHASE_ENDED = "PHASE_ENDED"
    MISSION_STARTED = "MISSION_STARTED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    ITEM_AWARDED = "ITEM_AWARDED"
    SHIELD_BLOCKED = "SHIELD_BLOCKED"
    DAGGER_USED = "DAGGER_USED"
    SEER_CHECK = "SEER_CHECK"
    MURDER_SHORTLIST = "MURDER_SHORTLIST"
    # Sequential traitor council (audit fix, phase 3): one round-1
    # proposal per traitor, each carrying the name they put forward and
    # the reason they gave for it.
    COUNCIL_PROPOSAL = "COUNCIL_PROPOSAL"
    PUBLIC_MESSAGE = "PUBLIC_MESSAGE"
    PRIVATE_MESSAGE = "PRIVATE_MESSAGE"
    VOTE_CAST = "VOTE_CAST"
    VOTE_TIE = "VOTE_TIE"
    PLAYER_ELIMINATED = "PLAYER_ELIMINATED"
    TRAITOR_KILL = "TRAITOR_KILL"
    MURDER_SKIPPED = "MURDER_SKIPPED"
    BANISHMENT_SKIPPED = "BANISHMENT_SKIPPED"
    FINALE_STARTED = "FINALE_STARTED"
    END_VOTE_CAST = "END_VOTE_CAST"
    # Recruitment as a choice (phase 26).
    RECRUIT_CHOICE_MADE = "RECRUIT_CHOICE_MADE"
    RECRUIT_OFFERED = "RECRUIT_OFFERED"
    RECRUIT_ACCEPTED = "RECRUIT_ACCEPTED"
    RECRUIT_DECLINED = "RECRUIT_DECLINED"
    ULTIMATUM_ISSUED = "ULTIMATUM_ISSUED"
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
