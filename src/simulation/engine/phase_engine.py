"""Generic phase engine (spec section 7).

Phases implement a small protocol; the ordering comes from config. The
engine drives rounds, emits phase events, snapshots state, and stops as
soon as a winner is declared.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol

from simulation.actions.actions import Action, ActionType
from simulation.engine.game_engine import GameEngine
from simulation.engine.state import GamePhase
from simulation.experiments.config import GameConfig

# Phases ask for one structured action from an agent. The callback is
# supplied by whoever wires agents in (AgentRuntime in Phase 6,
# scripted sources in tests). A callback may accept a fourth
# `extra_instruction` argument (the host's timer line); ones that do
# not simply run without it, so every scripted callback keeps working.
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
    # Whether the wired callback accepts an `extra_instruction` fourth
    # argument. Probed once, because scripted callbacks in tests are
    # three-argument functions and a TypeError mid-phase would abort
    # the run.
    _accepts_extra_instruction: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        if self.request_action is None:
            return
        try:
            parameters = inspect.signature(self.request_action).parameters
        except (TypeError, ValueError):  # builtins or C callables
            return
        positional = [
            p
            for p in parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        self._accepts_extra_instruction = len(positional) >= 4 or any(
            p.kind is inspect.Parameter.VAR_POSITIONAL
            for p in parameters.values()
        )

    async def ask(
        self,
        agent_id: str,
        action_type: ActionType,
        legal_targets: Optional[list[str]] = None,
        extra_instruction: Optional[str] = None,
    ) -> Action:
        """Request one structured action; raises if no callback is wired.

        `extra_instruction` is the deterministic host's line for this
        turn (the debate clock's timer); it reaches the prompt only when
        the callback can forward it.
        """
        if self.request_action is None:
            raise RuntimeError("no action callback wired into PhaseContext")
        targets = legal_targets or []
        if extra_instruction and self._accepts_extra_instruction:
            return await self.request_action(  # type: ignore[call-arg]
                agent_id, action_type, targets, extra_instruction
            )
        return await self.request_action(agent_id, action_type, targets)


class PhaseEngine:
    # One rapid-fire finale iteration: talk, side conversations, vote.
    FINALE_PHASES = ("round_table", "private_chat", "voting", "elimination")
    # One endgame-vote iteration: talk, side conversations, then the
    # end-or-banish vote. The normal voting and elimination phases only
    # run when somebody asks for a banishment.
    ENDGAME_PHASES = ("round_table", "private_chat", "end_vote")

    def __init__(self, config: GameConfig, engine: GameEngine, phases: dict[str, Phase]) -> None:
        unknown = [name for name in config.phases if name not in phases]
        if unknown:
            raise ValueError(f"no implementation registered for phases: {unknown}")
        if config.game.finale_traitors:
            needed = list(self.FINALE_PHASES)
            if config.game.endgame_vote:
                needed.append("end_vote")
            missing = [n for n in needed if n not in phases]
            if missing:
                raise ValueError(f"finale needs phase implementations for: {missing}")
        self.config = config
        self.engine = engine
        self.phases = phases
        self.order = list(config.phases)

    async def run(
        self,
        context: PhaseContext,
        resume_at: Optional[tuple[int, int]] = None,
    ) -> None:
        """Run the game until a winner is declared or rounds run out.

        `resume_at` is `(round_number, phase_index)` for a run being
        continued after a crash: the engine already holds that round's
        state, so the loop opens the round and starts partway through the
        phase order instead of replaying the phases already recorded.
        The round budget is fresh from the resume point, because a crash
        should not cost the game the rounds it ate.
        """
        if not self.engine.started:
            self.engine.start()
        max_rounds = self.config.game.max_rounds
        resume_round, resume_phase = resume_at if resume_at else (None, 0)
        opened = 0

        for _ in range(max_rounds):
            if self.engine.is_over or self.engine.state.finale:
                break
            self.engine.start_round()
            start = (
                resume_phase
                if opened == 0 and self.engine.state.round_number == resume_round
                else 0
            )
            for name in self.order[start:]:
                if self.engine.is_over or self.engine.state.finale:
                    break
                self.engine.begin_phase(_phase_enum(name))
                await self.phases[name].run(context)
                self.engine.end_phase()
            opened += 1

        if self.engine.state.finale:
            await self._run_finale(context)

        if not self.engine.is_over:
            self.engine.apply_round_limit()

        self.engine.begin_phase(GamePhase.GAME_END)
        self.engine.finish()

    async def _run_finale(self, context: PhaseContext) -> None:
        """Rapid-fire rounds: discussion, side conversations, vote, repeat.

        No missions, no night murder, no recruitment. A tie eliminates
        nobody and the vote simply runs again. After `finale_max_votes`
        consecutive rounds without a banishment, `round_limit_winner`
        is declared instead.

        With `endgame_vote` on, the vote is replaced by the end-or-banish
        vote (see `_run_endgame_finale`).
        """
        if self.config.game.endgame_vote:
            await self._run_endgame_finale(context)
            return
        stalled = 0
        while not self.engine.is_over and stalled < self.config.game.finale_max_votes:
            before = len(self.engine.state.alive_players)
            self.engine.start_round()
            for name in self.FINALE_PHASES:
                self.engine.begin_phase(_phase_enum(name))
                await self.phases[name].run(context)
                self.engine.end_phase()
                if self.engine.is_over:
                    return
            stalled = 0 if len(self.engine.state.alive_players) < before else stalled + 1
        if not self.engine.is_over:
            self.engine.apply_round_limit(reason="finale_vote_limit")

    async def _run_endgame_finale(self, context: PhaseContext) -> None:
        """Finale with the end-or-banish vote (phase 25).

        Each iteration runs the round table and private chat, then one
        `end_vote` phase where every living player answers `end` or
        `banish`. A unanimous `end` finishes the game inside the phase;
        any `banish` runs the normal voting and elimination phases and
        the loop repeats. The game also ends on its own when only two
        players remain, which the engine's win check handles.
        """
        stalled = 0
        while not self.engine.is_over and stalled < self.config.game.finale_max_votes:
            before = len(self.engine.state.alive_players)
            self.engine.start_round()
            for name in self.ENDGAME_PHASES:
                self.engine.begin_phase(_phase_enum(name))
                await self.phases[name].run(context)
                self.engine.end_phase()
                if self.engine.is_over:
                    return
            if self.engine.end_vote_requests_banishment():
                for name in ("voting", "elimination"):
                    self.engine.begin_phase(_phase_enum(name))
                    await self.phases[name].run(context)
                    self.engine.end_phase()
                    if self.engine.is_over:
                        return
            stalled = 0 if len(self.engine.state.alive_players) < before else stalled + 1
        if not self.engine.is_over:
            self.engine.apply_round_limit(reason="finale_vote_limit")


def _phase_enum(name: str) -> GamePhase:
    return GamePhase(name)
