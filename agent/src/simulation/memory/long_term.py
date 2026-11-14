"""Long-term memory: durable summaries that outlive the short-term buffer.

MVP keeps rollup summaries here; embedding-based recall is a future
backend behind the same `Memory` interface (spec section 14).
"""

from __future__ import annotations

from typing import Any


class LongTermMemory:
    def __init__(self, game_id: str, agent_id: str) -> None:
        self.game_id = game_id
        self.agent_id = agent_id
        self._summaries: list[dict[str, Any]] = []

    async def remember(self, event: dict[str, Any]) -> None:
        self._summaries.append(
            {
                "round": int(event.get("round", 0)),
                "kind": "summary",
                "content": str(event["content"]),
                "sequence": int(event.get("sequence", 0)),
            }
        )

    async def retrieve(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        terms = [t for t in query.lower().split() if t]
        if not terms:
            return self._summaries[-limit:]
        matched = [
            item
            for item in self._summaries
            if any(term in item["content"].lower() for term in terms)
        ]
        return matched[-limit:]

    async def summarize(self) -> str:
        if not self._summaries:
            return "No long-term summaries yet."
        return "\n".join(item["content"] for item in self._summaries[-5:])

    @property
    def summaries(self) -> list[dict[str, Any]]:
        return list(self._summaries)
