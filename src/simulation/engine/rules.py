"""Action validation rules (spec section 8).

Pure functions over (action, state, phase usage). The engine calls
these before applying any action.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulation.actions.actions import Action, ActionType
from simulation.engine.state import GamePhase, GameState, Role
from simulation.experiments.config import GameConfig

# Which action types each phase accepts (config-driven ordering lives in
# GameConfig; the action vocabulary per phase lives here).
PHASE_ALLOWED_ACTIONS: dict[GamePhase, frozenset[ActionType]] = {
    GamePhase.SETUP: frozenset(),
    GamePhase.MISSION: frozenset(),
    GamePhase.PUBLIC_DISCUSSION: frozenset({ActionType.PUBLIC_MESSAGE}),
    GamePhase.PRIVATE_CHAT: frozenset({ActionType.PRIVATE_MESSAGE}),
    GamePhase.ROUND_TABLE: frozenset({ActionType.PUBLIC_MESSAGE}),
    GamePhase.VOTING: frozenset({ActionType.VOTE}),
    GamePhase.ELIMINATION: frozenset({ActionType.RECRUIT}),
    GamePhase.TRAITOR_NIGHT: frozenset({ActionType.TRAITOR_KILL}),
    GamePhase.GAME_END: frozenset(),
}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str = ""

    @staticmethod
    def accepted() -> "ValidationResult":
        return ValidationResult(ok=True)

    @staticmethod
    def rejected(reason: str) -> "ValidationResult":
        return ValidationResult(ok=False, reason=reason)


class RuleValidator:
    def __init__(self, config: GameConfig) -> None:
        self._config = config

    def validate(
        self,
        action: Action,
        state: GameState,
        usage: dict[tuple[str, ActionType], int],
    ) -> ValidationResult:
        """Is this action legal for this agent, in this phase, right now?"""
        # 1. Phase correctness.
        allowed = PHASE_ALLOWED_ACTIONS.get(state.phase, frozenset())
        if action.action not in allowed:
            return ValidationResult.rejected(
                f"action '{action.action.value}' not allowed in phase '{state.phase.value}'"
            )

        # 2. Agent alive and known.
        if action.actor_id not in state.players:
            return ValidationResult.rejected(f"unknown actor '{action.actor_id}'")
        if action.actor_id not in state.alive_players:
            return ValidationResult.rejected(f"actor '{action.actor_id}' is not alive")

        # 3. Role legality.
        role = state.roles.get(action.actor_id)
        if action.action is ActionType.TRAITOR_KILL and role is not Role.TRAITOR:
            return ValidationResult.rejected("only traitors may kill at night")
        if action.action is ActionType.RECRUIT:
            if not self._config.game.recruit_on_banish:
                return ValidationResult.rejected("recruitment is disabled for this game")
            if role is not Role.TRAITOR:
                return ValidationResult.rejected("only traitors may recruit")

        # 4. Target checks.
        if action.target is not None:
            if action.target not in state.players:
                return ValidationResult.rejected(f"unknown target '{action.target}'")
            if action.target not in state.alive_players:
                return ValidationResult.rejected(f"target '{action.target}' is not alive")
            if action.action is ActionType.VOTE and action.target == action.actor_id:
                if not self._config.game.allow_self_vote:
                    return ValidationResult.rejected("self-vote is not allowed")
            if action.action is ActionType.TRAITOR_KILL:
                if action.target == action.actor_id:
                    return ValidationResult.rejected("cannot kill self")
                if state.roles.get(action.target) is Role.TRAITOR:
                    return ValidationResult.rejected("cannot kill a fellow traitor")
            if action.action is ActionType.RECRUIT:
                if action.target == action.actor_id:
                    return ValidationResult.rejected("cannot recruit yourself")
                if state.roles.get(action.target) is not Role.FAITHFUL:
                    return ValidationResult.rejected("can only recruit a faithful player")

        # 5. Phase limits (per-agent usage within the current phase).
        limit = self._action_limit(action)
        if limit is not None:
            used = usage.get((action.actor_id, action.action), 0)
            if used >= limit:
                return ValidationResult.rejected(
                    f"limit of {limit} '{action.action.value}' per phase reached"
                )

        return ValidationResult.accepted()

    def _action_limit(self, action: Action) -> int | None:
        comm = self._config.communication
        if action.action is ActionType.PUBLIC_MESSAGE:
            return comm.public_messages_per_agent
        if action.action is ActionType.PRIVATE_MESSAGE:
            return comm.private_messages_per_agent
        if action.action is ActionType.VOTE:
            return 1  # one vote per agent per round
        if action.action in (ActionType.TRAITOR_KILL, ActionType.RECRUIT):
            return 1
        return None
