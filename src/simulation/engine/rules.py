"""Action validation rules (spec section 8).

Pure functions over (action, state, phase usage). The engine calls
these before applying any action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from simulation.actions.actions import Action, ActionType
from simulation.engine.state import GamePhase, GameState, Role
from simulation.experiments.config import GameConfig

# Which action types each phase accepts (config-driven ordering lives in
# GameConfig; the action vocabulary per phase lives here). The Wave B
# actions are added on top of this base set only when their config flag
# is on, so a default config keeps exactly the vocabulary it had before.
PHASE_ALLOWED_ACTIONS: dict[GamePhase, frozenset[ActionType]] = {
    GamePhase.SETUP: frozenset(),
    GamePhase.MISSION: frozenset(),
    GamePhase.PUBLIC_DISCUSSION: frozenset({ActionType.PUBLIC_MESSAGE}),
    GamePhase.PRIVATE_CHAT: frozenset({ActionType.PRIVATE_MESSAGE}),
    GamePhase.ROUND_TABLE: frozenset({ActionType.PUBLIC_MESSAGE}),
    GamePhase.VOTING: frozenset({ActionType.VOTE}),
    GamePhase.ELIMINATION: frozenset({ActionType.RECRUIT}),
    GamePhase.TRAITOR_NIGHT: frozenset({ActionType.TRAITOR_KILL, ActionType.TRAITOR_MESSAGE}),
    # END_VOTE exists in this phase for every game; the validator's own
    # endgame checks decide whether it is legal right now, so the
    # rejection message can name the flag, the finale and the two
    # accepted values instead of only the phase mismatch.
    GamePhase.END_VOTE: frozenset({ActionType.END_VOTE}),
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

    def allowed_actions(self, phase: GamePhase) -> frozenset[ActionType]:
        """The action vocabulary for a phase under this config.

        `SEER_CHECK` and `NOMINATE` exist in the enum for every game but
        are only legal where their flag enables them, which keeps
        default-off configs byte-for-byte identical in behaviour. The
        same goes for the recruit-as-a-choice actions: `RECRUIT`,
        `RECRUIT_DECISION` and `RECRUIT_RESPONSE` enter the night only
        while `recruit_choice` is on, so the traitor night keeps its old
        vocabulary for every other game.
        """
        allowed = set(PHASE_ALLOWED_ACTIONS.get(phase, frozenset()))
        game = self._config.game
        if game.seer and phase is GamePhase.PRIVATE_CHAT:
            allowed.add(ActionType.SEER_CHECK)
        if game.on_trial and phase is GamePhase.TRAITOR_NIGHT:
            allowed.add(ActionType.NOMINATE)
        if game.recruit_choice and phase is GamePhase.TRAITOR_NIGHT:
            allowed.update(
                {
                    ActionType.RECRUIT,
                    ActionType.RECRUIT_DECISION,
                    ActionType.RECRUIT_RESPONSE,
                }
            )
        return frozenset(allowed)

    def validate(
        self,
        action: Action,
        state: GameState,
        usage: dict[tuple[str, ActionType], int],
        *,
        shortlist: Optional[Sequence[str]] = None,
        seer_used: Optional[set[str]] = None,
        recruit_window: bool = False,
        recruit_offered: Optional[str] = None,
    ) -> ValidationResult:
        """Is this action legal for this agent, in this phase, right now?

        `shortlist` is the murder shortlist the engine computed for this
        round (only meaningful with `on_trial`); `seer_used` is the set
        of players who already spent their once-per-game seer check,
        because phase usage resets every phase and this limit does not.
        `recruit_window` is True only on a night when a round-table
        banishment opened a recruitment window, and `recruit_offered` is
        the player currently weighing that window's offer.
        """
        # 1. Phase correctness.
        allowed = self.allowed_actions(state.phase)
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
            if not (
                self._config.game.recruit_on_banish
                or self._config.game.recruit_choice
            ):
                return ValidationResult.rejected("recruitment is disabled for this game")
            if role is not Role.TRAITOR:
                return ValidationResult.rejected("only traitors may recruit")
        if action.action is ActionType.TRAITOR_MESSAGE:
            if role is not Role.TRAITOR:
                return ValidationResult.rejected("only traitors have that channel")
            peers = [
                p
                for p in state.alive_players
                if p != action.actor_id and state.roles.get(p) is Role.TRAITOR
            ]
            if not peers:
                return ValidationResult.rejected(
                    "no fellow traitors alive to talk to"
                )
        if action.action is ActionType.NOMINATE:
            if role is not Role.TRAITOR:
                return ValidationResult.rejected("only traitors may nominate at night")
        if action.action is ActionType.SEER_CHECK:
            if "seer" not in state.items.get(action.actor_id, []):
                return ValidationResult.rejected(
                    "only the holder of the seer item may check a role"
                )
            if seer_used and action.actor_id in seer_used:
                return ValidationResult.rejected("seer check already used")

        # 4. Endgame vote (phase 25): only during the finale, only with
        # the flag on, and only ever `end` or `banish`. The message names
        # both accepted values so the correction retry can fix the reply.
        if action.action is ActionType.END_VOTE:
            if not self._config.game.endgame_vote:
                return ValidationResult.rejected("end_vote is disabled for this game")
            if not state.finale:
                return ValidationResult.rejected(
                    "end_vote is only allowed while the finale is running"
                )
            choice = (action.content or "").strip().lower()
            if choice not in ("end", "banish"):
                return ValidationResult.rejected(
                    "end_vote content must be 'end' or 'banish'"
                )

        # 4b. Recruitment as a choice (phase 26): the traitors' vote needs
        # the night phase, an open window and a traitor actor; the answer
        # belongs to the offered player alone. The engine re-checks the
        # content as the enforcement backstop to the pre-flight check.
        if action.action is ActionType.RECRUIT_DECISION:
            if role is not Role.TRAITOR:
                return ValidationResult.rejected(
                    "only traitors may decide on recruitment"
                )
            if not recruit_window:
                return ValidationResult.rejected(
                    "no recruitment window is open tonight"
                )
            choice = (action.content or "").strip().lower()
            if choice not in ("recruit", "murder"):
                return ValidationResult.rejected(
                    "recruit_decision content must be 'recruit' or 'murder'"
                )
        if action.action is ActionType.RECRUIT_RESPONSE:
            if recruit_offered is None or action.actor_id != recruit_offered:
                return ValidationResult.rejected(
                    "only the offered player may answer a recruitment offer"
                )
            choice = (action.content or "").strip().lower()
            if choice not in ("accept", "decline"):
                return ValidationResult.rejected(
                    "recruit_response content must be 'accept' or 'decline'"
                )

        # 5. Target checks.
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
                if self._config.game.on_trial and shortlist is not None:
                    if action.target not in shortlist:
                        return ValidationResult.rejected(
                            f"target '{action.target}' is not on the murder shortlist"
                        )
            if action.action is ActionType.RECRUIT:
                if action.target == action.actor_id:
                    return ValidationResult.rejected("cannot recruit yourself")
                if state.roles.get(action.target) is not Role.FAITHFUL:
                    return ValidationResult.rejected("can only recruit a faithful player")
            if action.action is ActionType.NOMINATE:
                if action.target == action.actor_id:
                    return ValidationResult.rejected("cannot nominate yourself")
                if state.roles.get(action.target) is Role.TRAITOR:
                    return ValidationResult.rejected(
                        "cannot nominate a fellow traitor"
                    )
            if action.action is ActionType.SEER_CHECK:
                if action.target == action.actor_id:
                    return ValidationResult.rejected("cannot check yourself")

        # 6. Phase limits (per-agent usage within the current phase).
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
        if action.action in (
            ActionType.TRAITOR_KILL,
            ActionType.RECRUIT,
            ActionType.TRAITOR_MESSAGE,
            ActionType.SEER_CHECK,
            ActionType.NOMINATE,
            ActionType.END_VOTE,
            ActionType.RECRUIT_DECISION,
            ActionType.RECRUIT_RESPONSE,
        ):
            return 1
        return None
