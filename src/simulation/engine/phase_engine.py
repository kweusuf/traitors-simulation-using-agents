"""Generic phase engine (spec section 7).

Phases implement a small protocol; the ordering comes from config. The
engine drives rounds, emits phase events, snapshots state, and stops as
soon as a winner is declared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol

from simulation.actions.actions import Action, ActionType
from simulation.engine.game_engine import GameEngine
from simulation.engine.state import GamePhase
from simulation.experiments.config import GameConfig

# Phases ask for one structured action from an agent. The callback is
# supplied by whoever wires agents in (AgentRuntime in Phase 6,
# scripted sources in tests).
ActionCallback = Callable[[str, ActionType, list[str]], Awaitable[Action]]


class PhaseResult(Protocol):
    pass


class Phase(Protocol):
    name: str

    async def run(self, context: "PhaseContext") -> Optional[PhaseResult]:
        ...


@dataclass
class PhaseContext:
    engine: GameEngine
    config: GameConfig
    request_action: Optional[ActionCallback] = None

    async def ask(
        self,
        agent_id: str,
        action_type: ActionType,
        legal_targets: Optional[list[str]] = None,
    ) -> Action:
        """Request one structured action; raises if no callback is wired."""
        if self.request_action is None:
            raise RuntimeError("no action callback wired into PhaseContext")
        return await self.request_action(agent_id, action_type, legal_targets or [])


class PhaseEngine:
    def __init__(self, config: GameConfig, engine: GameEngine, phases: dict[str, Phase]) -> None:
        unknown = [name for name in config.phases if name not in phases]
        if unknown:
            raise ValueError(f"no implementation registered for phases: {unknown}")
        self.config = config
        self.engine = engine
        self.phases = phases
        self.order = list(config.phases)

    async def run(self, context: PhaseContext) -> None:
        """Run the game until a winner is declared or rounds run out."""
        if not self.engine.started:
            self.engine.start()
        max_rounds = self.config.game.max_rounds

        for _ in range(max_rounds):
            if self.engine.is_over:
                break
            self.engine.start_round()
            for name in self.order:
                if self.engine.is_over:
                    break
                self.engine.begin_phase(_phase_enum(name))
                await self.phases[name].run(context)
                self.engine.end_phase()

        if not self.engine.is_over:
            self.engine.apply_round_limit()

        self.engine.begin_phase(GamePhase.GAME_END)
        self.engine.finish()


def _phase_enum(name: str) -> GamePhase:
    return GamePhase(name)
