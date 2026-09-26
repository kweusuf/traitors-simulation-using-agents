"""Traitors-specific rule helpers: legal action targets per phase.

The engine's RuleValidator remains the enforcement backstop; these
helpers tell agents (and the environment) what is choosable right now.
"""

from __future__ import annotations

from simulation.actions.actions import ActionType
from simulation.engine.state import GamePhase, GameState, Role


def legal_targets(
    state: GameState,
    actor_id: str,
    action_type: ActionType,
    allow_self_vote: bool = False,
) -> list[str]:
    """Deterministic, sorted list of legal targets for one action request."""
    alive = sorted(state.alive_players)

    if action_type is ActionType.PUBLIC_MESSAGE:
        return []

    if action_type is ActionType.PRIVATE_MESSAGE:
        return [p for p in alive if p != actor_id]

    if action_type is ActionType.VOTE:
        targets = alive if allow_self_vote else [p for p in alive if p != actor_id]
        return sorted(targets)

    if action_type is ActionType.TRAITOR_KILL:
        if state.roles.get(actor_id) is not Role.TRAITOR:
            return []
        return sorted(
            p
            for p in alive
            if p != actor_id and state.roles.get(p) is not Role.TRAITOR
        )

    if action_type is ActionType.RECRUIT:
        if state.roles.get(actor_id) is not Role.TRAITOR:
            return []
        return sorted(
            p
            for p in alive
            if p != actor_id and state.roles.get(p) is Role.FAITHFUL
        )

    return []


def action_types_for_phase(phase: GamePhase) -> list[ActionType]:
    from simulation.engine.rules import PHASE_ALLOWED_ACTIONS

    return sorted(
        PHASE_ALLOWED_ACTIONS.get(phase, frozenset()),
        key=lambda a: a.value,
    )
