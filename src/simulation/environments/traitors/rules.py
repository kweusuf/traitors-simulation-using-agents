"""Traitors-specific rule helpers: legal action targets per phase.

The engine's RuleValidator remains the enforcement backstop; these
helpers tell agents (and the environment) what is choosable right now.
"""

from __future__ import annotations

from typing import Optional

from simulation.actions.actions import ActionType
from simulation.engine.state import GamePhase, GameState, Role


def legal_targets(
    state: GameState,
    actor_id: str,
    action_type: ActionType,
    allow_self_vote: bool = False,
    shortlist: Optional[list[str]] = None,
) -> list[str]:
    """Deterministic, sorted list of legal targets for one action request.

    `shortlist` narrows TRAITOR_KILL to the murder shortlist when
    `on_trial` is on; callers pass None while it is off (an empty
    shortlist would otherwise mean "nobody may be killed").
    """
    alive = sorted(state.alive_players)

    if action_type is ActionType.PUBLIC_MESSAGE:
        return []

    if action_type is ActionType.ACCUSE:
        # The open nomination (audit fix, phase 2): name one living player
        # who is not you. Either role may name a suspect; the accusation
        # is a read, never a role claim.
        return [p for p in alive if p != actor_id]

    if action_type is ActionType.PRIVATE_MESSAGE:
        return [p for p in alive if p != actor_id]

    if action_type is ActionType.VOTE:
        targets = alive if allow_self_vote else [p for p in alive if p != actor_id]
        return sorted(targets)

    if action_type is ActionType.TRAITOR_KILL:
        if state.roles.get(actor_id) is not Role.TRAITOR:
            return []
        targets = [
            p
            for p in alive
            if p != actor_id and state.roles.get(p) is not Role.TRAITOR
        ]
        if shortlist is not None:
            targets = [p for p in targets if p in shortlist]
        return sorted(targets)

    if action_type is ActionType.RECRUIT:
        if state.roles.get(actor_id) is not Role.TRAITOR:
            return []
        return sorted(
            p
            for p in alive
            if p != actor_id and state.roles.get(p) is Role.FAITHFUL
        )

    if action_type is ActionType.NOMINATE:
        if state.roles.get(actor_id) is not Role.TRAITOR:
            return []
        return sorted(
            p
            for p in alive
            if p != actor_id and state.roles.get(p) is not Role.TRAITOR
        )

    if action_type is ActionType.SEER_CHECK:
        if "seer" not in state.items.get(actor_id, []):
            return []
        return sorted(p for p in alive if p != actor_id)

    return []


def action_types_for_phase(phase: GamePhase) -> list[ActionType]:
    from simulation.engine.rules import PHASE_ALLOWED_ACTIONS

    return sorted(
        PHASE_ALLOWED_ACTIONS.get(phase, frozenset()),
        key=lambda a: a.value,
    )
