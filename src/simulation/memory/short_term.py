"""Short-term memory: recent event buffer with optional SQLite persistence.

Retrieval is recency + keyword match (no embeddings in MVP).
"""

from __future__ import annotations

from typing import Any, Optional

from simulation.persistence.repositories import MemoryRepository

DEFAULT_BUFFER_SIZE = 200


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
