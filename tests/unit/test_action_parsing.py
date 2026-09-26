"""Unit tests for structured action parsing and pre-flight checks (spec 21)."""

from __future__ import annotations

import json

import pytest

from simulation.actions.actions import ActionType
from simulation.actions.validator import (
    ActionParseError,
    check_action_constraints,
    parse_action,
)


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
