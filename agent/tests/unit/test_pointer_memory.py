"""Pointer quality: the fallback has to hold when the model does not help.

`usable_pointer` is the only reason a missing or useless pointer is safe, so
these cover each way it can be handed something bad. The one that matters
most is the copied pointer: a model answering "summarise this" by echoing
the opening clause has added a line without distilling anything, and the
memory would read no better while costing more.
"""
from simulation.environments.traitors.memory import (
    MAX_POINTER_CHARS,
    _first_clause,
    usable_pointer,
)

MESSAGE = (
    "Tom, you have been the one patching holes in the story while everyone "
    "else moves on. I do not buy it and I want an answer before we vote."
)


def test_absent_pointer_falls_back() -> None:
    assert usable_pointer(None, MESSAGE) == _first_clause(MESSAGE)
    assert usable_pointer("", MESSAGE) == _first_clause(MESSAGE)


def test_verbatim_prefix_is_rejected() -> None:
    """A pointer copied off the head of the message is not a distillation."""
    copied = " ".join(MESSAGE.split())[:80]
    assert usable_pointer(copied, MESSAGE) == _first_clause(MESSAGE)


def test_older_copy_with_different_spacing_is_still_a_copy() -> None:
    """Comparison is normalized, so reflowing whitespace cannot smuggle it."""
    copied = "  ".join(MESSAGE.split()[:12])
    assert usable_pointer(copied, MESSAGE) == _first_clause(MESSAGE)


def test_case_differences_do_not_evade_detection() -> None:
    copied = MESSAGE[:60].upper()
    assert usable_pointer(copied, MESSAGE) == _first_clause(MESSAGE)


def test_a_real_pointer_is_kept() -> None:
    good = "Pressed Tom on the hole in his story; wants an answer before the vote."
    assert usable_pointer(good, MESSAGE) == good


def test_pointer_is_capped() -> None:
    long = "word " * 200
    assert len(usable_pointer(long, MESSAGE)) <= MAX_POINTER_CHARS


def test_never_returns_empty() -> None:
    """A memory row must always have content, whatever the model sent."""
    assert usable_pointer(None, "a")
    assert usable_pointer("   ", "a")


def _state() -> "GameState":
    from simulation.engine.state import GameState, PlayerState, Role

    return GameState(
        game_id="t",
        players={"aaron": PlayerState(player_id="aaron", name="Aaron"),
                 "tom": PlayerState(player_id="tom", name="Tom")},
        alive_players={"aaron", "tom"},
        roles={"aaron": Role.FAITHFUL, "tom": Role.TRAITOR},
    )


def test_pointer_memory_covers_a_message_no_marker_matched() -> None:
    """A message nothing fired on still leaves a trace.

    Regression: the pointer branch reads the message body, and the branches
    above it bind that body inside their own `if`. An untargeted message with
    no accusation or bequest marker therefore reached the branch with the
    name unbound and raised UnboundLocalError partway through a live run,
    after the transcript had already been paid for.
    """
    from simulation.environments.traitors.memory import writes_for

    event = {
        "type": "PUBLIC_MESSAGE",
        "actor": "aaron",
        "targets": [],
        "round": 2,
        "sequence": 7,
        # No marker words: nothing above the pointer branch fires on this.
        "payload": {"content": "The weather has been unusually mild this week."},
    }
    writes = writes_for(_state(), event, remember_everything=True)
    assert [w.kind for w in writes] == ["pointer"]
    assert writes[0].content == "The weather has been unusually mild this week."


def test_pointer_memory_prefers_a_model_written_pointer() -> None:
    from simulation.environments.traitors.memory import writes_for

    event = {
        "type": "PUBLIC_MESSAGE",
        "actor": "aaron",
        "targets": [],
        "round": 2,
        "sequence": 7,
        "payload": {
            "content": "Tom, you have been quiet all game and I want to know why.",
            "gist": "Pressed Tom on staying quiet.",
        },
    }
    writes = writes_for(_state(), event, remember_everything=True)
    assert writes[0].content == "Pressed Tom on staying quiet."