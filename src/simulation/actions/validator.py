"""Action output parsing and pre-flight legality checks (spec section 21).

The LLM must return a structured action. Malformed output raises
`ActionParseError` so the caller can retry with a correction prompt;
never parse arbitrary prose to determine game state.
"""

from __future__ import annotations

import json
import re
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


def _fold(name: str) -> str:
    return name.strip().casefold()


def _edit_distance(a: str, b: str, cap: int = 2) -> int:
    """Damerau-Levenshtein, abandoned once it exceeds `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (ca != cb),
                )
            )
            # transposition, the error that turns "clare" into "caler"
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[-1] = min(cur[-1], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def resolve_target(
    action: Action, legal_targets: Optional[list[str]]
) -> tuple[Action, Optional[str]]:
    """Map a near-miss target onto the player the model obviously meant.

    Returns the (possibly rewritten) action and a note describing the
    repair, or `None` when nothing changed.

    The model writes 'Meryl' against a lowercase legal list, or 'clare'
    for claire, and each of those costs a failed turn with three attempts
    at minutes apiece. But the correction must never be *ambiguous*: if
    two legal targets are equally close, the model did not really say
    which one, so nothing is rewritten and the ordinary rejection
    stands. Guessing between two real players is worse than asking again.
    """
    if not legal_targets or action.target is None:
        return action, None
    target = action.target
    if target in legal_targets:
        return action, None

    folded = _fold(target)
    lowered = {_fold(name): name for name in legal_targets}

    # Case and surrounding whitespace only.
    if folded in lowered:
        return _with_target(action, lowered[folded]), (
            f"target '{target}' corrected to '{lowered[folded]}' (case)"
        )

    # A single-character slip, and only when exactly one legal target is
    # that close. Short names are excluded: at four characters a typo is
    # as likely to be a different player as a different spelling.
    if len(folded) >= 5:
        close = [
            name
            for name in legal_targets
            if _edit_distance(folded, _fold(name), cap=1) <= 1
        ]
        if len(close) == 1:
            return _with_target(action, close[0]), (
                f"target '{target}' corrected to '{close[0]}' (one-character slip)"
            )
    return action, None


def _with_target(action: Action, target: str) -> Action:
    return action.model_copy(update={"target": target})


def resolve_content_names(
    content: str, known_players: Iterable[str], context: str = ""
) -> tuple[str, list[str], list[str]]:
    """Repair and detect names that are not players.

    Returns `(content, repairs, phantoms)`. Two separate problems:

    - A *near miss* - `Matty` for matt, `Aamon` for amos - is repaired
      when exactly one known player is a single edit away. That is
      unambiguous and costs nothing to fix.
    - A *phantom* - a name no player resembles - has no repair target. In
      one run a model invented a player in round 1 and the room spent the
      rest of the game investigating her, while three real traitors went
      unchallenged. There is nothing to correct it to, so it is reported
      and the turn retried with the roster in front of the model.

    `context` is the rendered prompt. A capitalised token is only treated
    as a phantom if its lowercase form appears nowhere in that context, so
    an ordinary word that merely started a sentence is left alone.
    """
    known = list(known_players)
    if not known:
        return content, [], []
    folded = {name.strip().casefold(): name for name in known}
    lowercase_context = {
        w.casefold() for w in re.findall(r"[A-Za-z]{3,}", context)
    }

    # A capitalised token is only a *suspect* when it is being used to
    # address somebody: immediately before a comma. Position alone cannot
    # decide, because "Iris, you have been quiet" and "Watching the
    # watchers" share a shape; the comma is what marks one as a person
    # being spoken to. First-word-of-sentence was tried and rejected - it
    # flagged every ordinary sentence-initial capital.
    suspect = {
        m.group(1)
        for m in re.finditer(
            r"(?:^|(?<=[.!?]\s)|(?<=\n)|(?<=[\"'“]\s))([A-Z][a-zA-Z]{2,})\s*,", content
        )
    }

    repairs: list[str] = []
    phantoms: list[str] = []

    def fix(match: "re.Match[str]") -> str:
        token = match.group(0)
        low = token.casefold()
        if low in folded:
            return folded[low]
        # Single-edit near miss, and only when exactly one player fits.
        if len(low) >= 4:
            close = [
                name
                for name in known
                if _edit_distance(low, name.casefold(), cap=1) <= 1
            ]
            if len(close) == 1:
                repairs.append(f"'{token}' -> '{close[0]}'")
                return close[0]
        if token in suspect and low not in lowercase_context:
            phantoms.append(token)
        return token

    fixed = re.sub(r"\b[A-Z][a-zA-Z]{2,}\b", fix, content)
    return fixed, repairs, phantoms


def phantom_reason(phantoms: list[str], known_players: Iterable[str]) -> str:
    return (
        "your message names "
        + ", ".join(f"'{p}'" for p in phantoms)
        + ", which is not a player in this game. Use only these names: "
        + ", ".join(sorted(known_players))
        + ". Do not invent anyone."
    )


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
