"""Traitors phase implementations (spec section 7).

Each phase gathers structured actions from the relevant alive players
through the PhaseContext callback, submits them to the engine, and lets
the engine own all state transitions. Phases never generate natural
language themselves.
"""

from __future__ import annotations

from typing import Optional

from simulation.actions.actions import ActionType
from simulation.engine.phase_engine import PhaseContext, PhaseResult
from simulation.engine.state import Role
from simulation.environments.traitors.rules import legal_targets


async def _ask_all_alive(context: PhaseContext, action_type: ActionType) -> None:
    """One action per alive player, in deterministic id order."""
    engine = context.engine
    allow_self = engine.config.game.allow_self_vote
    for actor in sorted(engine.state.alive_players):
        targets = legal_targets(engine.state, actor, action_type, allow_self)
        action = await context.ask(actor, action_type, targets)
        engine.submit_action(action)


class MissionPhase:
    """Basic mission abstraction: the group attempt resolves successfully."""

    name = "mission"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        engine = context.engine
        engine.start_mission()
        engine.complete_mission(success=True)
        return None


class PublicDiscussionPhase:
    """Open discussion; also used for the round table."""

    def __init__(self, name: str = "public_discussion") -> None:
        self.name = name

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        await _ask_all_alive(context, ActionType.PUBLIC_MESSAGE)
        return None


class PrivateChatPhase:
    name = "private_chat"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        await _ask_all_alive(context, ActionType.PRIVATE_MESSAGE)
        return None


class VotingPhase:
    name = "voting"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        await _ask_all_alive(context, ActionType.VOTE)
        return None


class EliminationPhase:
    """Resolve the tally; ties eliminate nobody (spec section 29)."""

    name = "elimination"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        tally = context.engine.resolve_votes()
        return {"counts": tally.counts, "tie": tally.tie}


class TraitorNightPhase:
    """Alive traitors pick the night victim; majority, earliest-choice tiebreak."""

    name = "traitor_night"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        engine = context.engine
        traitors = sorted(
            p for p in engine.state.alive_players
            if engine.state.roles.get(p) is Role.TRAITOR
        )
        for actor in traitors:
            targets = legal_targets(engine.state, actor, ActionType.TRAITOR_KILL)
            if not targets:
                continue
            action = await context.ask(actor, ActionType.TRAITOR_KILL, targets)
            engine.submit_action(action)
        victim = engine.resolve_night()
        return {"victim": victim}
