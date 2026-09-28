"""Action output parsing and pre-flight legality checks (spec section 21).

The LLM must return a structured action. Malformed output raises
`ActionParseError` so the caller can retry with a correction prompt;
never parse arbitrary prose to determine game state.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from simulation.actions.actions import ACTIONS_REQUIRING_TARGET, Action, ActionType


class ActionParseError(ValueError):
    """Raised when model output is not a usable structured action."""


def parse_action(payload: Any, actor_id: str) -> Action:
    """Parse model output (JSON string or dict) into an Action."""
    if isinstance(payload, Action):
        data: Any = payload.model_dump()
    elif isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ActionParseError(f"response is not valid JSON: {exc}") from exc
    elif isinstance(payload, dict):
        data = dict(payload)
    else:
        raise ActionParseError(f"unsupported response type: {type(payload).__name__}")

    if not isinstance(data, dict):
        raise ActionParseError("response must be a JSON object")
    # Never trust a model-supplied actor: stamp the real agent id.
    data["actor_id"] = actor_id
    _clear_target_for_targetless_action(data)
    try:
        return Action.model_validate(data)
    except Exception as exc:
        raise ActionParseError(f"invalid action: {exc}") from exc


def _clear_target_for_targetless_action(data: dict[str, Any]) -> None:
    """Drop whatever target the model sent when the action takes none.

    The structured-output schema forces a `target` on every response, so
    a public message arrives carrying either a copied player name or a
    `'none'` placeholder. Neither means anything for an action without a
    target, and leaving either in place would fail the legal-target check
    against an empty target list.
    """
    try:
        action_type = ActionType(data.get("action"))
    except ValueError:
        return  # unknown action type: validation reports it
    if action_type not in ACTIONS_REQUIRING_TARGET:
        data["target"] = None


def check_action_constraints(
    action: Action,
    allowed_types: Iterable[ActionType],
    legal_targets: Optional[list[str]] = None,
) -> Optional[str]:
    """Structural pre-check before handing the action to the engine.

    Returns a human-readable reason when the action is unusable, else None.
    """
    allowed = set(allowed_types)
    if action.action not in allowed:
        return (
            f"action '{action.action.value}' not allowed; "
            f"use one of: {sorted(a.value for a in allowed)}"
        )
    if action.action is ActionType.END_VOTE:
        # Structural check so the correction retry can fix a bad answer;
        # the engine's validator re-checks it as the enforcement backstop.
        choice = (action.content or "").strip().lower()
        if choice not in ("end", "banish"):
            return "end_vote content must be 'end' or 'banish'"
    if action.action is ActionType.RECRUIT_DECISION:
        choice = (action.content or "").strip().lower()
        if choice not in ("recruit", "murder"):
            return "recruit_decision content must be 'recruit' or 'murder'"
    if action.action is ActionType.RECRUIT_RESPONSE:
        choice = (action.content or "").strip().lower()
        if choice not in ("accept", "decline"):
            return "recruit_response content must be 'accept' or 'decline'"
    if legal_targets is not None and action.target is not None:
        if action.target not in legal_targets:
            return (
                f"target '{action.target}' is not legal; "
                f"choose one of: {sorted(legal_targets)}"
            )
    return None
