"""Three ways a memory can be wrong about what matters, and how each is kept.

The distinction these cover is strength against truth. A fact of the game
does not fade with age, a remark is remembered as firmly as the model was
sure of it when it said it, and a belief that moves is recorded as having
moved rather than silently replaced.
"""
import asyncio

from simulation.agents.beliefs import Beliefs
from simulation.environments.traitors.memory import (
    DEATH,
    IMMUTABLE_KINDS,
    POINTER,
    MemoryWrite,
)
from simulation.memory.short_term import ShortTermMemory


def _memory() -> ShortTermMemory:
    return ShortTermMemory("g", "aaron")


def test_a_fact_of_the_game_outlives_a_loud_remark() -> None:
    """Decay is not the mechanism that keeps a murder remembered.

    Before `decayable` existed, a murder survived because its salience was
    large enough against the decay curve. That is arithmetic, and arithmetic
    eventually runs out: at these constants a 4.0 falls under the floor in
    about six rounds. The elimination is not an impression that matters less
    over time, so it is not written as one.
    """
    mem = _memory()
    asyncio.run(mem.remember({"content": "Tom was murdered in the night.",
                              "round": 1, "kind": "murder",
                              "salience": DEATH, "decayable": False}))
    asyncio.run(mem.remember({"content": "I think the weather is odd.",
                              "round": 1, "kind": "pointer",
                              "salience": POINTER}))
    late = mem.recalled(now_round=12, limit=10)
    kept = [i["content"] for i in late]
    assert "Tom was murdered in the night." in kept
    assert "I think the weather is odd." not in kept


def test_durable_facts_ignore_the_floor_entirely() -> None:
    """Even a fact below the floor survives, which is the point of the flag."""
    mem = _memory()
    asyncio.run(mem.remember({"content": "Rayan's role was faithful.",
                              "round": 1, "kind": "murder",
                              "salience": 0.4, "decayable": False}))
    assert mem.recalled(now_round=30, limit=10, floor=0.9)


def test_immutable_kinds_do_not_need_the_flag() -> None:
    """A durable kind survives on its own, so callers cannot forget to set it."""
    write = MemoryWrite(content="x", kind="banishment", subjects=(), salience=DEATH)
    assert write.durable
    assert "banishment" in IMMUTABLE_KINDS

    pointer = MemoryWrite(content="x", kind="pointer", subjects=(), salience=POINTER)
    assert not pointer.durable


def test_nominations_are_left_decayable() -> None:
    """Otherwise the prompt fills with who was floated and nothing else."""
    nomination = MemoryWrite(content="x", kind="nomination", subjects=(),
                             salience=2.0)
    assert not nomination.durable


def test_confidence_is_carried_but_does_not_reorder() -> None:
    """How sure the writer was is not how much it matters.

    A hesitant truth and a confident rumour are both one memory of some
    weight; ranking them by confidence would quietly turn a hedging
    statement into evidence.
    """
    mem = _memory()
    asyncio.run(mem.remember({"content": "certain", "round": 1,
                              "salience": 2.0, "confidence": 0.95}))
    asyncio.run(mem.remember({"content": "hesitant", "round": 1,
                              "salience": 2.0, "confidence": 0.20}))
    ranked = mem.recalled(now_round=1, limit=10)
    assert [i["content"] for i in ranked] == ["certain", "hesitant"]
    # It is still available to read, it just does not promote itself.
    confident = next(i for i in ranked if i["content"] == "certain")
    assert confident["confidence"] == 0.95


def test_a_belief_records_that_it_moved() -> None:
    beliefs = Beliefs()
    beliefs.update("bob", "traitor", 0.45, round_number=1)
    beliefs.update("bob", "traitor", 0.65, round_number=3)

    shifts = beliefs.shifts("bob")
    assert len(shifts) == 1
    assert (shifts[0].from_confidence, shifts[0].to_confidence) == (0.45, 0.65)
    assert shifts[0].round_number == 3
    assert beliefs.hardening("bob") == 1


def test_restating_a_belief_is_not_a_movement() -> None:
    """Otherwise history fills with updates that changed nothing."""
    beliefs = Beliefs()
    beliefs.update("bob", "traitor", 0.5, round_number=1)
    beliefs.update("bob", "traitor", 0.5, round_number=2)
    assert beliefs.shifts("bob") == []


def test_the_current_belief_is_unchanged_by_tracking_history() -> None:
    """What the prompt reads must not change; only what can be read does."""
    beliefs = Beliefs()
    beliefs.update("bob", "traitor", 0.45, round_number=1)
    beliefs.update("bob", "traitor", 0.65, round_number=3)
    current = beliefs.get("bob")
    assert current.confidence == 0.65
    assert current.updated_round == 3
    assert len(beliefs.prompt_lines()) == 1
