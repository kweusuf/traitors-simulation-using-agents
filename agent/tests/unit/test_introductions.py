"""The opening introductions phase runs once, at the top of round 1."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import simulation.environments.traitors.phases as phases


def _context(enabled: bool, round_number: int):
    return SimpleNamespace(
        config=SimpleNamespace(game=SimpleNamespace(introductions=enabled)),
        engine=SimpleNamespace(state=SimpleNamespace(round_number=round_number)),
    )


def _run(enabled: bool, round_number: int, monkeypatch):
    calls: list[str] = []

    async def fake_ask(context, action_type, extra_instruction=None):
        calls.append(extra_instruction)

    monkeypatch.setattr(phases, "_ask_all_alive", fake_ask)
    asyncio.run(phases.IntroductionsPhase().run(_context(enabled, round_number)))
    return calls


def test_introductions_are_off_by_default(monkeypatch) -> None:
    assert _run(False, 1, monkeypatch) == []


def test_introductions_only_run_in_round_one(monkeypatch) -> None:
    assert _run(True, 2, monkeypatch) == []


def test_introductions_ask_everyone_with_the_brief(monkeypatch) -> None:
    calls = _run(True, 1, monkeypatch)
    assert len(calls) == 1
    assert "Introduce yourself" in calls[0]
