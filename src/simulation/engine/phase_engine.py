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
        resume_completed: tuple[str, ...] = (),
    ) -> None:
        """Run the game until a winner is declared or rounds run out.

        `resume_at` is `(round_number, phase_index)` for a run being
        continued after a crash: the engine already holds that round's
        state, so the loop opens the round and starts partway through the
        phase order instead of replaying the phases already recorded.
        The round budget is fresh from the resume point, because a crash
        should not cost the game the rounds it ate.

        `resume_completed` names the phases that already ended in the round
        being resumed. The normal loop does not need it (`phase_index`
        already says where to cut), but the finale loops order their phases
        differently from the config, so they use it to skip work the crashed
        run already did.
        """
        if not self.engine.started:
            self.engine.start()
        max_rounds = self.config.game.max_rounds
        resume_round, resume_phase = resume_at if resume_at else (None, 0)
        opened = 0

        for _ in range(max_rounds):
            if self.engine.is_over or self.engine.state.finale:
                break
            if opened == 0 and resume_at is not None:
                # Resuming mid-round. `restore_resume` left the round
                # number one behind the round the crash happened in, and
                # that round is already announced on the record, so it is
                # reopened rather than restarted. Only the phases at and
                # after the cut run again: the ones already recorded are
                # not replayed, and no second `ROUND_STARTED` is written.
                self.engine.reopen_round(resume_round + 1)
                start = resume_phase
            else:
                self.engine.start_round()
                start = 0
            for name in self.order[start:]:
                if self.engine.is_over or self.engine.state.finale:
                    break
                self.engine.begin_phase(_phase_enum(name))
                await self.phases[name].run(context)
                self.engine.end_phase()
            opened += 1

        if self.engine.state.finale:
            # A run restored into a finale round breaks out of the loop above
            # before it can reopen the round, because `state.finale` is
            # already set. The finale loops own their round opening, so the
            # resume point travels with them: otherwise they call
            # `start_round`, which emits a second `ROUND_STARTED` for a round
            # the crashed run already announced - the same duplicate the
            # normal loop was fixed for. `opened == 0` means the resume landed
            # in the finale; a finale entered during this run is fresh and
            # must open normally.
            landed_in_finale = opened == 0
            await self._run_finale(
                context,
                resume_at if landed_in_finale else None,
                resume_completed if landed_in_finale else (),
            )

        if not self.engine.is_over:
            self.engine.apply_round_limit()

        self.engine.begin_phase(GamePhase.GAME_END)
        self.engine.finish()

    async def _run_finale(
        self,
        context: PhaseContext,
        resume_at: Optional[tuple[int, int]] = None,
        resume_completed: tuple[str, ...] = (),
    ) -> None:
        """Rapid-fire rounds: discussion, side conversations, vote, repeat.

        No missions, no night murder, no recruitment. A tie eliminates
        nobody and the vote simply runs again. After `finale_max_votes`
        consecutive rounds without a banishment, `round_limit_winner`
        is declared instead.

        With `endgame_vote` on, the vote is replaced by the end-or-banish
        vote (see `_run_endgame_finale`).

        `resume_at` is set when the run is being continued inside a finale
        round: the first iteration reopens that round quietly instead of
        announcing it a second time, and picks up at the first finale phase
        that did not already end (`resume_completed`), so a completed phase
        is not run twice. If every finale phase already ended, the round is
        complete and the next one is opened normally.
        """
        if self.config.game.endgame_vote:
            await self._run_endgame_finale(context, resume_at, resume_completed)
            return
        stalled = 0
        first = True
        while not self.engine.is_over and stalled < self.config.game.finale_max_votes:
            before = len(self.engine.state.alive_players)
            if first and resume_at is not None:
                self.engine.reopen_round(resume_at[0] + 1)
                start = _phases_already_run(resume_completed, self.FINALE_PHASES)
                if start >= len(self.FINALE_PHASES):
                    # The interrupted round is fully recorded already.
                    self.engine.start_round()
                    start = 0
            else:
                self.engine.start_round()
                start = 0
            first = False
            for name in self.FINALE_PHASES[start:]:
                self.engine.begin_phase(_phase_enum(name))
                await self.phases[name].run(context)
                self.engine.end_phase()
                if self.engine.is_over:
                    return
            stalled = 0 if len(self.engine.state.alive_players) < before else stalled + 1
        if not self.engine.is_over:
            self.engine.apply_round_limit(reason="finale_vote_limit")

    async def _run_endgame_finale(
        self,
        context: PhaseContext,
        resume_at: Optional[tuple[int, int]] = None,
        resume_completed: tuple[str, ...] = (),
    ) -> None:
        """Finale with the end-or-banish vote (phase 25).

        Each iteration runs the round table and private chat, then one
        `end_vote` phase where every living player answers `end` or
        `banish`. A unanimous `end` finishes the game inside the phase;
        any `banish` runs the normal voting and elimination phases and
        the loop repeats. The game also ends on its own when only two
        players remain, which the engine's win check handles.

        `resume_at` behaves as in `_run_finale`: the resumed finale round
        is reopened, not announced again, and its already-ended phases are
        skipped. A completed `end_vote` that did not end the game requested
        a banishment, so its voting/elimination tail still runs even though
        the reopened round's vote record is empty.
        """
        stalled = 0
        first = True
        while not self.engine.is_over and stalled < self.config.game.finale_max_votes:
            before = len(self.engine.state.alive_players)
            resumed_after_end_vote = False
            tail: tuple[str, ...] = ()
            if first and resume_at is not None:
                done = set(resume_completed)
                start = _phases_already_run(resume_completed, self.ENDGAME_PHASES)
                # A completed end_vote that did not end the game requested a
                # banishment, so its voting/elimination tail still has to run
                # even though the reopened round's vote record is empty.
                tail = (
                    tuple(n for n in ("voting", "elimination") if n not in done)
                    if start >= len(self.ENDGAME_PHASES)
                    else ()
                )
                if start >= len(self.ENDGAME_PHASES) and not tail:
                    # The interrupted round is fully recorded; open the next.
                    self.engine.reopen_round(resume_at[0] + 1)
                    self.engine.start_round()
                    start = 0
                else:
                    self.engine.reopen_round(resume_at[0] + 1)
                    resumed_after_end_vote = start >= len(self.ENDGAME_PHASES)
            else:
                self.engine.start_round()
                start = 0
            first = False
            for name in self.ENDGAME_PHASES[start:]:
                self.engine.begin_phase(_phase_enum(name))
                await self.phases[name].run(context)
                self.engine.end_phase()
                if self.engine.is_over:
                    return
            if resumed_after_end_vote or self.engine.end_vote_requests_banishment():
                for name in tail if resumed_after_end_vote else ("voting", "elimination"):
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


def _phases_already_run(completed: tuple[str, ...], order: tuple[str, ...]) -> int:
    """How many leading phases of `order` are already in `completed`.

    A round runs its phases in `order`, so the ones a crashed run recorded
    form a prefix of it; the count is where the resumed run should pick up.
    """
    done = set(completed)
    count = 0
    for name in order:
        if name not in done:
            break
        count += 1
    return count
