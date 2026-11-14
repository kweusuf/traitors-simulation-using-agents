"""Unit tests for structured action parsing and pre-flight checks (spec 21)."""

from __future__ import annotations

import json

import pytest

from simulation.actions.actions import ActionType
from simulation.actions.validator import (
    ActionParseError,
    check_action_constraints,
    parse_action,
    resolve_target,
)


def _resolved(target: str, legal: list[str]) -> tuple[str, object]:
    action = parse_action(
        {"action": "private_message", "target": target, "content": "hi"}, "alice"
    )
    fixed, note = resolve_target(action, legal)
    return fixed.target, note


def test_capitalised_target_is_repaired() -> None:
    target, note = _resolved("Meryl", ["meryl", "wilf"])
    assert target == "meryl"
    assert note and "case" in note


def test_transposed_letter_is_repaired() -> None:
    # The real failure from the Hinglish run: 'clare' for claire.
    target, note = _resolved("clare", ["claire", "wilf", "amos"])
    assert target == "claire"
    assert note and "slip" in note


def test_dropped_letter_is_repaired() -> None:
    assert _resolved("clire", ["claire", "wilf"])[0] == "claire"


def test_surrounding_whitespace_is_repaired() -> None:
    assert _resolved("  wilf  ", ["matt", "wilf"])[0] == "wilf"


def test_an_already_legal_target_is_left_alone() -> None:
    target, note = _resolved("wilf", ["matt", "wilf"])
    assert target == "wilf"
    assert note is None


def test_a_genuinely_unknown_target_is_not_repaired() -> None:
    """The important negative: a name that is nobody must still fail."""
    target, note = _resolved("ivan", ["matt", "wilf", "amos"])
    assert target == "ivan", "an unknown name was silently rewritten"
    assert note is None


def test_an_ambiguous_typo_is_left_for_the_rejection() -> None:
    """Two legal targets one edit apart means the model did not say which.

    Rewriting here would pick a real player at random, which is worse
    than making the model answer again.
    """
    target, note = _resolved("clare", ["clare", "claire"])
    assert target == "clare"
    assert note is None


def test_short_names_are_not_fuzzy_matched() -> None:
    """At four characters a typo is as likely to be a different player."""
    assert _resolved("wiif", ["wilf"])[0] == "wiif"


def test_repair_survives_the_legality_check() -> None:
    """The point of the whole thing: a repaired target then passes."""
    action = parse_action({"action": "vote", "target": "Meryl"}, "alice")
    fixed, _note = resolve_target(action, ["matt", "meryl"])
    assert check_action_constraints(fixed, {ActionType.VOTE}, ["matt", "meryl"]) is None


def test_the_sentinel_target_is_still_rejected() -> None:
    """'none' is not a near-miss for any player, so it must still fail."""
    target, note = _resolved("none", ["matt", "wilf"])
    assert target == "none"
    assert note is None


def test_parse_json_string() -> None:
    payload = json.dumps(
        {"action": "vote", "target": "bob", "confidence": 0.81,
         "reason_summary": "voting pattern"}
    )
    action = parse_action(payload, "alice")
    assert action.action is ActionType.VOTE
    assert action.target == "bob"
    assert action.actor_id == "alice"


def test_parse_dict() -> None:
    action = parse_action(
        {"action": "public_message", "content": "hello"}, "alice"
    )
    assert action.action is ActionType.PUBLIC_MESSAGE
    assert action.content == "hello"


def test_actor_id_cannot_be_spoofed() -> None:
    action = parse_action(
        {"action": "vote", "target": "bob", "actor_id": "bob"}, "alice"
    )
    assert action.actor_id == "alice"


def test_malformed_json_raises() -> None:
    with pytest.raises(ActionParseError, match="not valid JSON"):
        parse_action("I vote for bob because reasons", "alice")


def test_missing_target_raises() -> None:
    with pytest.raises(ActionParseError, match="invalid action"):
        parse_action({"action": "vote"}, "alice")


def test_non_object_response_raises() -> None:
    with pytest.raises(ActionParseError, match="JSON object"):
        parse_action('["vote", "bob"]', "alice")
    with pytest.raises(ActionParseError, match="unsupported response type"):
        parse_action(42, "alice")


@pytest.mark.parametrize(
    "sentinel", ["none", "None", "N/A", "null", "-", "", "  none  "]
)
def test_sentinel_target_dropped_for_targetless_actions(sentinel: str) -> None:
    # Models echo the prompt's "Legal targets: none" wording as a target.
    action = parse_action(
        {"action": "public_message", "target": sentinel, "content": "hello"},
        "alice",
    )
    assert action.target is None


def test_sentinel_target_still_rejected_for_target_actions() -> None:
    action = parse_action(
        {"action": "private_message", "target": "none", "content": "hi"}, "alice"
    )
    reason = check_action_constraints(
        action, {ActionType.PRIVATE_MESSAGE}, ["bob", "charlie"]
    )
    assert reason is not None
    assert "'none' is not legal" in reason


def test_parse_dict_is_not_mutated() -> None:
    payload = {"action": "public_message", "target": "none", "content": "hi"}
    parse_action(payload, "alice")
    assert payload["target"] == "none"


def test_constraint_check_reports_wrong_action_type() -> None:
    action = parse_action({"action": "vote", "target": "bob"}, "alice")
    reason = check_action_constraints(action, {ActionType.PUBLIC_MESSAGE})
    assert reason is not None
    assert "not allowed" in reason
    assert "public_message" in reason


def test_constraint_check_reports_illegal_target() -> None:
    action = parse_action({"action": "vote", "target": "mallory"}, "alice")
    reason = check_action_constraints(action, {ActionType.VOTE}, ["bob", "charlie"])
    assert reason is not None
    assert "mallory" in reason
    assert "bob" in reason


def test_constraint_check_passes_valid_action() -> None:
    action = parse_action({"action": "vote", "target": "bob"}, "alice")
    assert (
        check_action_constraints(action, {ActionType.VOTE}, ["bob", "charlie"]) is None
    )
