"""Append-only JSONL event log (spec section 23).

The JSONL log is the debugging and replay source of truth: it is
append-only, one JSON object per line, in sequence order.
"""

from __future__ import annotations

import json
from pathlib import Path

from simulation.persistence.event_log import Event


class EventLog:
    """Append-only JSONL writer/reader for a single game."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, event: Event) -> None:
        line = event.model_dump_json()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def append_many(self, events: list[Event]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(event.model_dump_json() + "\n")

    def read_all(self) -> list[Event]:
        """Read events in file order, failing loudly on corruption."""
        events: list[Event] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(Event.model_validate(json.loads(line)))
                except Exception as exc:
                    raise ValueError(
                        f"corrupt event log {self.path} at line {line_no}"
                    ) from exc
        return events
