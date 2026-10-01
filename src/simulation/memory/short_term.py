"""Short-term memory: recent event buffer with optional SQLite persistence.

Retrieval is recency + keyword match (no embeddings in MVP).
"""

from __future__ import annotations

from typing import Any, Optional

from simulation.persistence.repositories import MemoryRepository

DEFAULT_BUFFER_SIZE = 200
# Decay and floor for `recalled`. A memory at salience 1.0 (an ordinary
# remark) falls under the floor in two rounds at this decay; a named
# accusation at 3.0 is still there eight rounds later.
DEFAULT_DECAY = 0.6
DEFAULT_FLOOR = 0.5


class ShortTermMemory:
    def __init__(
        self,
        game_id: str,
        agent_id: str,
        repo: Optional[MemoryRepository] = None,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ) -> None:
        self.game_id = game_id
        self.agent_id = agent_id
        self._repo = repo
        self._buffer_size = buffer_size
        self._items: list[dict[str, Any]] = []

    async def remember(self, event: dict[str, Any]) -> None:
        item = {
            "round": int(event.get("round", 0)),
            "kind": str(event.get("kind", "event")),
            "content": str(event["content"]),
            "sequence": int(event.get("sequence", 0)),
            # Who this memory is about, and how much it mattered when it
            # happened. Both are read by `recalled`, which is where decay
            # and the per-observer weighting are applied.
            "subjects": tuple(event.get("subjects") or ()),
            "salience": float(event.get("salience", 1.0)),
        }
        self._items.append(item)
        if len(self._items) > self._buffer_size:
            self._items = self._items[-self._buffer_size :]
        if self._repo is not None:
            self._repo.append(
                self.game_id,
                self.agent_id,
                item["round"],
                item["kind"],
                item["content"],
                item["sequence"],
            )

    def recalled(
        self,
        now_round: int,
        limit: int = 6,
        decay: float = DEFAULT_DECAY,
        floor: float = DEFAULT_FLOOR,
        factor=None,
    ) -> list[dict[str, Any]]:
        """The memories still worth having, strongest first.

        Strength is `salience * decay ** age`, optionally scaled per
        observer by `factor(subject, round)`. Nothing is deleted: the full
        history stays in the buffer and in the event log, so decay can be
        retuned without replaying the game.

        `factor` is how one observer weights another player's words -
        high for someone they are close to, low for someone they are at
        odds with. It is called as `factor(subject, round)`.
        """
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for item in self._items:
            age = max(0, now_round - int(item["round"]))
            strength = float(item["salience"]) * (decay ** age)
            if factor is not None and item["subjects"]:
                for subject in item["subjects"]:
                    strength *= max(0.0, float(factor(subject, int(item["round"]))))
                    if strength <= 0.0:
                        break
            if strength < floor:
                continue
            scored.append((strength, int(item["sequence"]), item))
        # Strongest first; a tie falls to the more recent memory, so two
        # equally weighted memories still read in the order they happened.
        scored.sort(key=lambda row: (-row[0], -row[1]))
        return [
            {**item, "strength": round(strength, 3)}
            for strength, _, item in scored[:limit]
        ]

    async def retrieve(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        items = self._items
        terms = [t for t in query.lower().split() if t]
        if terms:
            matched = [
                item
                for item in items
                if any(term in item["content"].lower() for term in terms)
            ]
            if matched:
                return matched[-limit:]
        return items[-limit:]

    async def summarize(self) -> str:
        """Compact deterministic summary (LLM summarization comes later)."""
        if not self._items:
            return "No memories yet."
        rounds = sorted({item["round"] for item in self._items})
        recent = self._items[-3:]
        lines = [
            f"{len(self._items)} memories across rounds {', '.join(map(str, rounds))}.",
            "Recent: " + " | ".join(item["content"] for item in recent),
        ]
        return "\n".join(lines)

    @property
    def items(self) -> list[dict[str, Any]]:
        return list(self._items)
