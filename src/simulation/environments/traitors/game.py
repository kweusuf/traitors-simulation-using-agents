"""Traitors environment (spec section 38).

Wraps the generic engine with Traitors-specific phases so that a future
Mafia/Werewolf/Avalon environment can reuse the same agent runtime and
LLM gateway behind the same interface.
"""

from __future__ import annotations

from typing import Optional

from simulation.actions.actions import Action, ActionType
from simulation.communication.visibility import AgentView, InformationProjector
from simulation.engine.game_engine import GameEngine
from simulation.engine.phase_engine import Phase
from simulation.engine.state import GamePhase, GameState, Role
from simulation.environments.traitors.phases import (
    EliminationPhase,
    MissionPhase,
    PrivateChatPhase,
    PublicDiscussionPhase,
    TraitorNightPhase,
    VotingPhase,
)
from simulation.environments.traitors.rules import action_types_for_phase, legal_targets
from simulation.experiments.config import GameConfig
from simulation.persistence.database import Database
from simulation.persistence.sink import EventSink


class TraitorsEnvironment:
    def __init__(
        self,
        config: GameConfig,
        sink: EventSink,
        db: Optional[Database] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.config = config
        self.engine = GameEngine(config, sink, db=db, seed=seed)
        self.projector = InformationProjector(self.engine.router)

    # -- Environment interface (spec section 38) -----------------------

    def initialize(self) -> GameState:
        self.engine.start()
        return self.engine.state

    def phases(self) -> dict[str, Phase]:
        return {
            "mission": MissionPhase(),
            "public_discussion": PublicDiscussionPhase("public_discussion"),
            "round_table": PublicDiscussionPhase("round_table"),
            "private_chat": PrivateChatPhase(),
            "voting": VotingPhase(),
            "elimination": EliminationPhase(),
            "traitor_night": TraitorNightPhase(),
        }

    def legal_actions(self, agent_id: str) -> dict[ActionType, list[str]]:
        """What this agent could usefully submit in the current phase."""
        state = self.engine.state
        allow_self = self.config.game.allow_self_vote
        result: dict[ActionType, list[str]] = {}
        for action_type in action_types_for_phase(state.phase):
            if action_type is ActionType.TRAITOR_MESSAGE:
                # Targetless and role-restricted: only traitors have it.
                if state.roles.get(agent_id) is not Role.TRAITOR:
                    continue
                result[action_type] = []
                continue
            if action_type in (ActionType.TRAITOR_KILL, ActionType.RECRUIT):
                # Role-restricted: empty means "not available to this agent".
                targets = legal_targets(state, agent_id, action_type)
                if not targets:
                    continue
            else:
                targets = legal_targets(state, agent_id, action_type, allow_self)
            result[action_type] = targets
        return result

    def observe(self, agent_id: str) -> AgentView:
        return self.projector.project(self.engine.state, agent_id)

    def apply_action(self, action: Action):
        return self.engine.submit_action(action)

    def check_terminal(self) -> bool:
        return self.engine.is_over

    # -- Convenience ----------------------------------------------------

    @property
    def state(self) -> GameState:
        return self.engine.state

    @property
    def current_phase(self) -> GamePhase:
        return self.engine.state.phase
