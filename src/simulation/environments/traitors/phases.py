"""Traitors phase implementations (spec section 7).

Each phase gathers structured actions from the relevant alive players
through the PhaseContext callback, submits them to the engine, and lets
the engine own all state transitions. Phases never generate natural
language themselves.
"""

from __future__ import annotations

import asyncio
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
    """One action per alive player.

    Turns are decided concurrently, capped by the gateway's
    `max_concurrency`, instead of one model call after another: asking
    twenty players serially is what made a round take an hour. Actions
    are submitted afterwards in id order, and every prompt is built
    from the phase-start state, so the event log keeps exactly the same
    deterministic sequence the serial version produced.
    """
    engine = context.engine
    allow_self = engine.config.game.allow_self_vote
    actions = await asyncio.gather(
        *(
            _ask(
                context,
                actor,
                action_type,
                legal_targets(engine.state, actor, action_type, allow_self),
            )
            for actor in sorted(engine.state.alive_players)
        )
    )
    for action in actions:
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
        engine = context.engine
        # The seer check runs first, so the answer is already in the
        # holder's private messages while they write this phase's chats.
        # Only a holder who has not used the one-shot check is asked,
        # so an accepted check never turns into a rejected action.
        if engine.config.game.seer:
            requests = [
                (actor, legal_targets(engine.state, actor, ActionType.SEER_CHECK))
                for actor in sorted(engine.state.alive_players)
                if "seer" in engine.state.items.get(actor, [])
                and actor not in engine.seer_checks_done
            ]
            checks = await asyncio.gather(
                *(
                    _ask(context, actor, ActionType.SEER_CHECK, targets)
                    for actor, targets in requests
                    if targets
                )
            )
            for action in checks:
                if action is not None:
                    engine.submit_action(action)
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
    """The traitor council talks, nominates, then picks the night victim.

    First every living traitor gets one message on the traitor channel
    so they can argue the merits (biggest threat versus most chaos, who
    takes the blame). With `on_trial` on, they next nominate one player
    each and the union of those names is the murder shortlist: only a
    shortlisted player can die tonight. Then the kill is a majority vote
    with an earliest-choice tiebreak.
    """

    name = "traitor_night"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        engine = context.engine
        traitors = sorted(
            p for p in engine.state.alive_players
            if engine.state.roles.get(p) is Role.TRAITOR
        )
        if len(traitors) > 1:
            council = await asyncio.gather(
                *(
                    _ask(context, actor, ActionType.TRAITOR_MESSAGE, [])
                    for actor in traitors
                )
            )
            for action in council:
                if action is not None:
                    engine.submit_action(action)
        # The shortlist is frozen before any kill is requested, so the
        # kill's legal targets (and the validator) see exactly who may
        # die tonight.
        if engine.config.game.on_trial:
            requests = [
                (actor, legal_targets(engine.state, actor, ActionType.NOMINATE))
                for actor in traitors
            ]
            nominations = await asyncio.gather(
                *(
                    _ask(context, actor, ActionType.NOMINATE, targets)
                    for actor, targets in requests
                    if targets
                )
            )
            for action in nominations:
                if action is not None:
                    engine.submit_action(action)
            engine.resolve_nominations()
        shortlist = engine.murder_shortlist if engine.config.game.on_trial else None
        # Same concurrent decision, submitted in id order so the
        # earliest-choice tiebreak stays deterministic.
        choices = await asyncio.gather(
            *(
                _ask(context, actor, ActionType.TRAITOR_KILL, targets)
                for actor in traitors
                if (
                    targets := legal_targets(
                        engine.state, actor, ActionType.TRAITOR_KILL, shortlist=shortlist
                    )
                )
            )
        )
        for action in choices:
            if action is not None:
                engine.submit_action(action)
        victim = engine.resolve_night()
        return {"victim": victim}
