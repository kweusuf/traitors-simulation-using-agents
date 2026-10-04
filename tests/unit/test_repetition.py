"""A model must not keep saying what it already said.

Byte-identical messages are easy to spot and not the expensive case. The
costly one is eight players converging on the same sentence in slightly
different words: it reads as agreement while carrying no new information,
and it is what a small model falls into under repetition pressure.

The gate therefore compares content words, not strings, and quotes the
rejected text back so the model can see what to avoid.
"""

from __future__ import annotations

import asyncio

from simulation.actions.actions import ActionType
from simulation.actions.repetition import (
    find_repeat,
    normalize,
    repeat_reason,
    similarity,
)
from simulation.agents.agent import Agent
from simulation.agents.persona import Persona
from simulation.agents.prompts import PromptBuilder
from simulation.agents.runtime import AgentRuntime
from simulation.communication.channels import Channel, Message
from simulation.communication.visibility import AgentView
from simulation.engine.state import GamePhase, Role
from simulation.models.fake import FakeLLMProvider
from simulation.models.gateway import LLMGateway
from simulation.models.llm import ModelConfig

WORDING = "I think Iris is lying about the mission and we should watch her"
OTHER = "Wilf handled the voting badly and I want to hear his side"


# ----------------------------------------------------------------------
# The comparison itself
# ----------------------------------------------------------------------


def test_identical_text_scores_one_regardless_of_case() -> None:
    assert similarity(WORDING, WORDING.upper()) == 1.0
    assert similarity(WORDING, f"  {WORDING}  ") == 1.0


def test_a_paraphrase_is_caught_when_asked_for() -> None:
    """Near-duplicate detection works, but is not the default.

    The score is well above a paraphrase threshold, so lowering
    `repetition_threshold` does what it says. It is not the default because
    on a real run that setting would reject half the messages.
    """
    other = "I believe Iris is lying about that mission and we should watch her"
    assert similarity(WORDING, other) >= 0.6
    assert find_repeat(other, [WORDING], 0.6) is not None


def test_the_default_requires_an_exact_match() -> None:
    """What the calibration on a real run forced.

    48% of a completed run's messages overlap an earlier one at 0.6,
    because players keep returning to the same claims. Catching paraphrase
    by default would discard half a real game, so the default is exactness.
    """
    other = "I believe Iris is lying about that mission and we should watch her"
    assert find_repeat(other, [WORDING], 1.0) is None
    assert find_repeat(WORDING, [WORDING], 1.0) is not None


def test_a_genuinely_different_claim_scores_low() -> None:
    """A different read must not be caught, or the gate eats real content."""
    assert similarity(WORDING, OTHER) < 0.25


def test_word_order_does_not_reach_an_exact_match() -> None:
    """A reordering scores high on overlap but is not an exact repeat.

    Worth knowing because it is the shape a paraphrase gate would catch and
    the exact-match default will not: if the collapse ever shows up
    reworded rather than copied, this is the case that gets through.
    """
    other = "we should watch her and Iris is lying about the mission"
    assert similarity(WORDING, other) >= 0.6
    assert find_repeat(other, [WORDING], 1.0) is None


def test_word_overlap_cannot_see_a_single_word_flip() -> None:
    """A known blind spot, pinned so it stays deliberate.

    "lying" swapped for "truthful" changes the word set by exactly one
    token, in either direction, so a bag-of-words measure scores a
    disagreement the same as a restatement. Word overlap is the right
    tool for the failure it targets - the room converging on a sentence -
    but it cannot adjudicate a single-word change of mind, and a threshold
    low enough to catch restatements will reject some disagreements too.
    Stated here rather than left to be discovered in a run summary.
    """
    disagree = (
        "I think Iris is truthful about the mission and we should watch her"
    )
    assert similarity(WORDING, disagree) == similarity(
        WORDING, "I believe Iris is lying about the mission and we watch her"
    )
    assert similarity(WORDING, disagree) >= 0.6, "so this pair is caught too"


def test_two_short_answers_are_not_repeats() -> None:
    """Below the word floor, content words are noise, not evidence."""
    assert similarity("No.", "No.") == 1.0, "exact is still exact"
    assert similarity("No.", "Not really.") < 0.6


def test_empty_text_scores_zero() -> None:
    assert similarity("", WORDING) == 0.0
    assert similarity(WORDING, "   ") == 0.0


def test_normalize_collapses_case_and_whitespace() -> None:
    assert normalize("I  Think\nIris") == "i think iris"


# ----------------------------------------------------------------------
# find_repeat
# ----------------------------------------------------------------------


def test_find_repeat_returns_the_closest_prior_message() -> None:
    found = find_repeat(WORDING, [WORDING, OTHER], 0.6)
    assert found is not None
    assert found[0] == WORDING
    assert found[1] >= 0.6


def test_find_repeat_returns_none_below_threshold() -> None:
    assert find_repeat("The mission vote was decided early", [WORDING], 0.6) is None


def test_min_words_protects_a_short_reply() -> None:
    """Below the floor, only a literal repeat counts."""
    # Exact, so caught at any length.
    assert find_repeat("Banish Iris", ["Banish Iris"], 0.6, min_words=5)
    # Two content words each, sharing one: scored at 0.33 on the Jaccard,
    # but the floor means it is never reached - a two-word reply that
    # overlaps a prior one is not evidence of anything.
    assert find_repeat("Wilf vote", ["Iris vote"], 0.6, min_words=5) is None
    assert similarity("Wilf vote", "Iris vote") < 0.6


def test_no_history_is_never_a_repeat() -> None:
    assert find_repeat(WORDING, [], 0.6) is None


def test_reason_quotes_the_rejected_text() -> None:
    """The model has to see its own words to write something else."""
    reason = repeat_reason(WORDING, 0.92)
    assert WORDING[:30] in reason
    assert "already said" in reason
    assert "nobody else has said" in reason


# ----------------------------------------------------------------------
# Helpers for the decide-loop tests
# ----------------------------------------------------------------------


def make_view(history: list[str], sender: str = "alice") -> AgentView:
    return AgentView(
        agent_id="alice",
        game_id="game-001",
        round_number=2,
        phase=GamePhase.PUBLIC_DISCUSSION,
        own_role=Role.FAITHFUL,
        known_roles={"alice": Role.FAITHFUL},
        alive_players=["alice", "bob", "charlie"],
        eliminated_players=[],
        public_transcript=[
            Message(
                message_id=f"m{i}",
                sender_id=sender,
                recipients=[],
                channel=Channel.PUBLIC,
                content=text,
            )
            for i, text in enumerate(history)
        ],
        private_conversations=[],
        winner=None,
    )


def make_runtime(script: dict, **kwargs) -> tuple[AgentRuntime, FakeLLMProvider]:
    agent = Agent("alice", "Alice", Persona(description="Careful player."))
    agent.assign_role(Role.FAITHFUL)
    provider = FakeLLMProvider(script)
    runtime = AgentRuntime(
        agents={"alice": agent},
        gateway=LLMGateway(provider, max_concurrency=1),
        model_config=ModelConfig(),
        prompt_builder=PromptBuilder(),
        **kwargs,
    )
    return runtime, provider


def say(text: str) -> dict:
    return {"action": "public_message", "content": text}


# ----------------------------------------------------------------------
# The gate in the decide loop
# ----------------------------------------------------------------------


def test_a_repeat_is_rejected_and_the_model_is_asked_again() -> None:
    """The whole mechanism: reject, quote it back, accept the new answer."""
    runtime, _ = make_runtime(
        {"alice": [say(WORDING), say(OTHER)]},
        reject_repetition=True,
    )
    view = make_view([WORDING])
    action = asyncio.run(
        runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, [])
    )
    assert action.content == OTHER
    assert runtime.repeats_rejected == 1


def test_the_rejected_text_is_shown_back_to_the_model() -> None:
    """Otherwise the retry has nothing to work from."""
    runtime, provider = make_runtime(
        {"alice": [say(WORDING), say(OTHER)]},
        reject_repetition=True,
    )
    view = make_view([WORDING])
    asyncio.run(runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, []))
    assert len(provider.calls) == 2, "a rejection must trigger a second call"
    correction = " ".join(m.content for m in provider.calls[1])
    assert "already said" in correction
    assert WORDING[:30] in correction


def test_a_repeat_is_accepted_on_the_last_attempt() -> None:
    """A model stuck in a phrase must not cost the game a whole turn.

    With one retry the second attempt is final, so the repeated message goes
    through rather than raising. It stays visible in the summary.
    """
    runtime, _ = make_runtime(
        {"alice": [say(WORDING), say(WORDING)]},
        reject_repetition=True,
        max_retries=1,
    )
    view = make_view([WORDING])
    action = asyncio.run(
        runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, [])
    )
    assert action.content == WORDING
    assert runtime.repeats_rejected == 2


def test_the_gate_is_off_by_default() -> None:
    """An existing config must play exactly as it did before."""
    runtime, _ = make_runtime({"alice": [say(WORDING)]})
    assert runtime.reject_repetition is False
    view = make_view([WORDING])
    action = asyncio.run(
        runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, [])
    )
    assert action.content == WORDING
    assert runtime.repeats_rejected == 0


def test_self_scope_leaves_echoing_the_room_alone() -> None:
    """Matching the last speaker is legitimate conversation."""
    runtime, _ = make_runtime(
        {"alice": [say(WORDING)]}, reject_repetition=True
    )
    view = make_view([WORDING], sender="bob")
    action = asyncio.run(
        runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, [])
    )
    assert action.content == WORDING
    assert runtime.repeats_rejected == 0


def test_room_scope_catches_echoing_the_last_speaker() -> None:
    """The template collapse: nobody repeats themselves, all agree."""
    runtime, _ = make_runtime(
        {"alice": [say(WORDING), say(OTHER)]},
        reject_repetition=True,
        repetition_scope="room",
    )
    view = make_view([WORDING], sender="bob")
    action = asyncio.run(
        runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, [])
    )
    assert action.content == OTHER
    assert runtime.repeats_rejected == 1


def test_a_vote_is_never_judged_a_repetition() -> None:
    """"banish" twice is two votes, not a repeated turn."""
    runtime, _ = make_runtime(
        {"alice": [{"action": "vote", "target": "bob"}]},
        reject_repetition=True,
        repetition_scope="room",
    )
    view = make_view(["I banish Iris", "I banish Iris"], sender="bob")
    action = asyncio.run(runtime.decide("alice", view, ActionType.VOTE, ["bob"]))
    assert action.target == "bob"
    assert runtime.repeats_rejected == 0


def test_a_first_speaker_is_never_a_repeat() -> None:
    """Empty history cannot produce a rejection."""
    runtime, _ = make_runtime({"alice": [say(WORDING)]}, reject_repetition=True)
    view = make_view([])
    action = asyncio.run(
        runtime.decide("alice", view, ActionType.PUBLIC_MESSAGE, [])
    )
    assert action.content == WORDING
    assert runtime.repeats_rejected == 0
