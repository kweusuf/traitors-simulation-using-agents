"""Unit tests for memory buffers (spec section 14)."""

from __future__ import annotations

import asyncio

from simulation.memory.long_term import LongTermMemory
from simulation.memory.short_term import ShortTermMemory
from simulation.persistence.database import Database
from simulation.persistence.repositories import MemoryRepository


def test_short_term_recency_order_and_limit() -> None:
    memory = ShortTermMemory("game-001", "alice")
    for i in range(15):
        asyncio.run(memory.remember({"content": f"event {i}", "round": 1, "sequence": i}))
    items = asyncio.run(memory.retrieve("", limit=5))
    assert [i["content"] for i in items] == [
        "event 10",
        "event 11",
        "event 12",
        "event 13",
        "event 14",
    ]


def test_short_term_keyword_query() -> None:
    memory = ShortTermMemory("game-001", "alice")
    asyncio.run(memory.remember({"content": "bob voted for alice", "round": 1}))
    asyncio.run(memory.remember({"content": "eve trusts charlie", "round": 1}))
    hits = asyncio.run(memory.retrieve("bob"))
    assert [i["content"] for i in hits] == ["bob voted for alice"]


def test_short_term_buffer_is_bounded() -> None:
    memory = ShortTermMemory("game-001", "alice", buffer_size=3)
    for i in range(10):
        asyncio.run(memory.remember({"content": f"e{i}", "sequence": i}))
    assert len(memory.items) == 3
    assert [i["content"] for i in memory.items] == ["e7", "e8", "e9"]


def test_short_term_persists_to_repository() -> None:
    with Database() as db:
        repo = MemoryRepository(db)
        memory = ShortTermMemory("game-001", "alice", repo=repo)
        asyncio.run(memory.remember({"content": "hello", "round": 1, "sequence": 1}))
        stored = repo.get("game-001", "alice")
        assert [m["content"] for m in stored] == ["hello"]


def test_short_term_summarize_is_deterministic() -> None:
    memory = ShortTermMemory("game-001", "alice")
    assert asyncio.run(memory.summarize()) == "No memories yet."
    asyncio.run(memory.remember({"content": "a", "round": 1}))
    asyncio.run(memory.remember({"content": "b", "round": 2}))
    summary = asyncio.run(memory.summarize())
    assert summary == asyncio.run(memory.summarize())
    assert "2 memories across rounds 1, 2" in summary
    assert "Recent: a | b" in summary


def test_long_term_memory_stores_summaries() -> None:
    memory = LongTermMemory("game-001", "alice")
    asyncio.run(memory.remember({"content": "round 1 rollup", "round": 1}))
    asyncio.run(memory.remember({"content": "round 2 rollup", "round": 2}))
    assert len(memory.summaries) == 2
    assert "round 2 rollup" in asyncio.run(memory.summarize())
    hits = asyncio.run(memory.retrieve("rollup"))
    assert len(hits) == 2
