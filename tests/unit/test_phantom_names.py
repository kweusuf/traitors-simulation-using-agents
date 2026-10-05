"""A model must not invent players inside the prose, not just in `target`."""

from __future__ import annotations

from simulation.actions.validator import phantom_reason, resolve_content_names
from simulation.engine.state import Role

ROSTER = ["ann", "bea", "cal", "matt", "amos", "wilf", "aaron"]
CONTEXT = (
    "Alive players: ann, bea, cal, matt, amos, wilf, aaron. "
    "The room has been circling the same questions all round and the "
    "watchers keep deflecting."
)


def test_a_near_miss_name_is_repaired() -> None:
    fixed, repairs, phantoms = resolve_content_names(
        "Matty, your final answer was clever.", ROSTER, CONTEXT
    )
    assert "matt" in fixed
    assert repairs == ["'Matty' -> 'matt'"]
    assert phantoms == []


def test_an_invented_player_is_reported_not_repaired() -> None:
    """The real case: a phantom with no repair target."""
    fixed, repairs, phantoms = resolve_content_names(
        "Iris, you have been quiet all round.", ROSTER, CONTEXT
    )
    assert "Iris" in phantoms
    assert fixed.startswith("Iris"), "a phantom has nothing to correct it to"
    assert repairs == []


def test_ordinary_capitalised_words_are_not_phantoms() -> None:
    """The false-positive risk: a sentence may legitimately start so."""
    for word in ["Round", "Watching", "Analysis", "Noise", "Performance"]:
        _, _, phantoms = resolve_content_names(
            f"{word} shows us something.", ROSTER, CONTEXT
        )
        assert phantoms == [], f"{word} was wrongly called a phantom"


def test_a_real_name_written_differently_is_repaired() -> None:
    fixed, repairs, phantoms = resolve_content_names(
        "Amos and Wilf disagree.", ROSTER, CONTEXT
    )
    assert fixed == "amos and wilf disagree."
    assert phantoms == []


def test_an_ambiguous_near_miss_is_not_guessed() -> None:
    """Two players one edit away: refuse to pick, report instead.

    Choosing between two real players would be worse than asking again,
    so the name is left alone and reported as not-a-player.
    """
    roster = ["cara", "core"]
    fixed, repairs, phantoms = resolve_content_names(
        "Care, your turn.", roster, "Alive players: cara, core."
    )
    assert repairs == [], "guessed between two real players"
    assert fixed.startswith("Care"), "the text must not be rewritten"
    assert phantoms == ["Care"], "an unresolvable name should be reported"


def test_a_mid_sentence_capital_is_not_addressing_anyone() -> None:
    """The rule that keeps ordinary prose out of the detector."""
    _, _, phantoms = resolve_content_names(
        "The room is quiet. Watching the watchers is exhausting.",
        ROSTER,
        CONTEXT,
    )
    assert phantoms == []


def test_a_sentence_starting_with_a_word_is_not_a_phantom() -> None:
    """Position alone is not evidence: 'Watching shows...' is ordinary."""
    _, _, phantoms = resolve_content_names(
        "Analysis of the last round suggests nothing new.", ROSTER, CONTEXT
    )
    assert phantoms == []


def test_sentence_initial_discourse_markers_are_not_phantoms() -> None:
    """The two real false positives: 'However,' and 'Meanwhile,' cost retries.

    Seen in uk-s01-ledger, where each burned a full retry on a ~25s call.
    A discourse marker before a comma is ordinary prose, never an address.
    """
    for sentence in [
        "However, I think we should look closer.",
        "Meanwhile, the room has gone quiet.",
        "Moreover, nobody has answered the question.",
        "Frankly, that story does not hold together.",
    ]:
        _, _, phantoms = resolve_content_names(sentence, ROSTER, CONTEXT)
        assert phantoms == [], f"{sentence!r} was wrongly called a phantom"


def test_a_real_phantom_after_a_discourse_marker_is_still_caught() -> None:
    """The exclusion must not swallow a genuine phantom later in the text.

    Note the detector's shape: it flags a name in address position at a
    sentence start, so the phantom needs its own sentence. Mid-sentence
    addresses ("Listen Iris, ...") were never covered, before or after
    this fix - a separate gap, not a regression.
    """
    _, _, phantoms = resolve_content_names(
        "However, that story fails. Iris, you have been quiet.", ROSTER, CONTEXT
    )
    assert phantoms == ["Iris"]


def test_rejection_is_opt_in_so_a_false_positive_cannot_cost_a_turn() -> None:
    """The detector is uncalibrated; logging is safe, rejecting is not."""
    from simulation.agents.agent import Agent
    from simulation.agents.persona import Persona
    from simulation.agents.runtime import AgentRuntime
    from simulation.models.llm import ModelConfig

    agent = Agent("ann", "Ann", Persona(description="careful"))
    agent.assign_role(Role.FAITHFUL)
    runtime = AgentRuntime(
        agents={"ann": agent},
        gateway=object(),
        model_config=ModelConfig(),
    )
    assert runtime.reject_invented_players is False


def test_phantom_reason_lists_the_roster() -> None:
    reason = phantom_reason(["Iris"], ROSTER)
    assert "Iris" in reason
    assert "matt" in reason
    assert "Do not invent anyone" in reason


def test_no_roster_means_no_checks() -> None:
    """A game with no players configured must not crash the turn."""
    assert resolve_content_names("Iris spoke.", [], "") == ("Iris spoke.", [], [])