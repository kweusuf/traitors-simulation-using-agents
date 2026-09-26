"""Action output parsing and pre-flight legality checks (spec section 21).

The LLM must return a structured action. Malformed output raises
`ActionParseError` so the caller can retry with a correction prompt;
never parse arbitrary prose to determine game state.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from simulation.actions.actions import Action, ActionType


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
        data = payload
    else:
        raise ActionParseError(f"unsupported response type: {type(payload).__name__}")

    if not isinstance(data, dict):
        raise ActionParseError("response must be a JSON object")
    # Never trust a model-supplied actor: stamp the real agent id.
    data["actor_id"] = actor_id
    try:
        return Action.model_validate(data)
    except Exception as exc:
        raise ActionParseError(f"invalid action: {exc}") from exc


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
    if legal_targets is not None and action.target is not None:
        if action.target not in legal_targets:
            return (
                f"target '{action.target}' is not legal; "
                f"choose one of: {sorted(legal_targets)}"
            )
    return None
