"""Memory interface (spec section 14).

Async interface so future backends (embedding retrieval, Mem0, Letta,
structured memory) can be dropped in. No vector database in MVP.
"""

from __future__ import annotations

from typing import Any, Protocol


class Memory(Protocol):
    async def remember(self, event: dict[str, Any]) -> None:
        """Store one memory item (content, kind, round, sequence)."""
        ...

    async def retrieve(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most relevant recent items."""
        ...

    async def summarize(self) -> str:
        """Return a compact summary of retained memory."""
        ...
