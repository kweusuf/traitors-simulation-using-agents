"""Traitors phase implementations (spec section 7).

Each phase gathers structured actions from the relevant alive players
through the PhaseContext callback, submits them to the engine, and lets
the engine own all state transitions. Phases never generate natural
language themselves.
"""

from __future__ import annotations

from typing import Optional

from simulation.actions.actions import ActionType
from simulation.actions.validator import ActionParseError
from simulation.engine.phase_engine import PhaseContext, PhaseResult
from simulation.engine.state import Role
from simulation.environments.traitors.rules import legal_targets


async def _ask(
    context: PhaseContext, actor: str, action_type: ActionType, targets: list[str]
):
    """Ask one agent for an action, tolerating exhausted output retries.

    `AgentRuntime` has already spent its correction attempts by the time
    `ActionParseError` reaches here, so the turn is skipped and recorded
    as a rejected action instead of ending the run (spec section 21).
    """
    try:
        return await context.ask(actor, action_type, targets)
    except ActionParseError as exc:
        context.engine.record_unparseable_action(actor, action_type, str(exc))
        return None


async def _ask_all_alive(context: PhaseContext, action_type: ActionType) -> None:
    """One action per alive player, in deterministic id order."""
    engine = context.engine
    allow_self = engine.config.game.allow_self_vote
    for actor in sorted(engine.state.alive_players):
        targets = legal_targets(engine.state, actor, action_type, allow_self)
        action = await _ask(context, actor, action_type, targets)
        if action is not None:
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
    """Resolve the tally; ties eliminate nobody (spec section 29).

    When the round table is about to banish a traitor and recruitment is
    on, that traitor is asked for one RECRUIT action first: they pick a
    living faithful player, who is converted before the win check runs.
    """

    name = "elimination"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        engine = context.engine
        tally = engine.tally_votes()
        if not tally.tie and tally.top:
            banished = tally.top[0]
            if engine.recruitment_opportunity(banished):
                targets = legal_targets(engine.state, banished, ActionType.RECRUIT)
                if targets:
                    action = await _ask(context, banished, ActionType.RECRUIT, targets)
                    if action is not None:
                        engine.submit_action(action)
        tally = engine.resolve_votes()
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
            action = await _ask(context, actor, ActionType.TRAITOR_KILL, targets)
            if action is not None:
                engine.submit_action(action)
        victim = engine.resolve_night()
        return {"victim": victim}
