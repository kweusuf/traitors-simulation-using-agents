"""Event sink: single write path for events to JSONL, SQLite, and memory.

Assigns monotonic sequences so replays stay deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from simulation.persistence.database import Database
from simulation.persistence.event_log import Event, EventType
from simulation.persistence.jsonl import EventLog
from simulation.persistence.repositories import EventRepository


class EventSink:
    def __init__(
        self,
        game_id: str,
        jsonl_path: Optional[str | Path] = None,
        db: Optional[Database] = None,
    ) -> None:
        self.game_id = game_id
        self._log = EventLog(jsonl_path) if jsonl_path is not None else None
        self._repo = EventRepository(db) if db is not None else None
        self._sequence = 0
        self.events: list[Event] = []

    def emit(
        self,
        type_: EventType,
        *,
        round_number: int,
        phase: str,
        actor: Optional[str] = None,
        targets: Optional[list[str]] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> Event:
        event = Event(
            event_id=f"evt-{self._sequence:05d}",
            game_id=self.game_id,
            sequence=self._sequence,
            round=round_number,
            phase=phase,
            type=type_,
            actor=actor,
            targets=targets or [],
            payload=payload or {},
        )
        self._sequence += 1
        self.events.append(event)
        if self._log is not None:
            self._log.append(event)
        if self._repo is not None:
            self._repo.append(self.game_id, event)
        return event

    @property
    def next_sequence(self) -> int:
        return self._sequence
