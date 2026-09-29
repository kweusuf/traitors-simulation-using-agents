"""Traitors phase implementations (spec section 7).

Each phase gathers structured actions from the relevant alive players
through the PhaseContext callback, submits them to the engine, and lets
the engine own all state transitions. Phases never generate natural
language themselves.
"""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING, Optional

from simulation.actions.actions import ActionType
from simulation.actions.validator import ActionParseError
from simulation.engine.phase_engine import PhaseContext, PhaseResult
from simulation.engine.state import Role
from simulation.environments.traitors.rules import legal_targets

if TYPE_CHECKING:  # avoids importing the engine at module load time
    from simulation.engine.game_engine import GameEngine


async def _ask(
    context: PhaseContext,
    actor: str,
    action_type: ActionType,
    targets: list[str],
    extra_instruction: Optional[str] = None,
):
    """Ask one agent for an action, tolerating exhausted output retries.

    `AgentRuntime` has already spent its correction attempts by the time
    `ActionParseError` reaches here, so the turn is skipped and recorded
    as a rejected action instead of ending the run (spec section 21).
    """
    try:
        return await context.ask(actor, action_type, targets, extra_instruction)
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


# The host's line for a nominated suspect's one defence, so the answer
# lands on the record the room is reacting to.
_REBUTTAL_INSTRUCTION = (
    "Host: you have been nominated. Answer the room's accusation directly "
    "in this message: say why the case against you is wrong, and name who "
    "you think the real traitor is and why."
)


async def _run_debate(context: PhaseContext) -> None:
    """The speech part of one debate.

    With the clock off this is the plain one-message-each round. With it
    on the host paces the turns: open waves up to the budget, a warning,
    then the closing waves (see `_run_hosted_debate`).
    """
    game = context.config.game
    if not game.discussion_budget:
        await _ask_all_alive(context, ActionType.PUBLIC_MESSAGE)
        return
    await _run_hosted_debate(context, game.discussion_budget, game.warning_turns)


class MissionPhase:
    """Basic mission abstraction: the group attempt resolves successfully."""

    name = "mission"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        engine = context.engine
        engine.start_mission()
        engine.complete_mission(success=True)
        return None


class PublicDiscussionPhase:
    """Open discussion; also used for the round table.

    With `game.discussion_budget` set, the deterministic host runs a
    debate clock instead of the plain one-turn-each round: open turns up
    to the budget (one even share per player), then `HOST_WARNING`, then
    `warning_turns` closing turns for everyone, then `DEBATE_CLOSED`
    before the phase ends and the vote is forced. Budget 0 keeps the
    original single-turn behaviour exactly.
    """

    def __init__(self, name: str = "public_discussion") -> None:
        self.name = name

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        await _run_debate(context)
        return None


class RoundTablePhase:
    """The show's round table: debate, nomination, defence, then the vote.

    With `nomination_enabled` off this is the plain debate the
    `public_discussion` phase runs. With it on, the debate is followed by
    one open nomination per living player, the host's tally (which keeps
    `nomination_keep` suspects standing), and one defence from each of
    them, so a suspect answers the room before the ballot rather than
    reading about it afterwards.
    """

    name = "round_table"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        engine = context.engine
        game = context.config.game
        await _run_debate(context)
        if not game.nomination_enabled:
            return None
        # One nomination each, concurrently, submitted in id order so the
        # tally is deterministic.
        await _ask_all_alive(context, ActionType.ACCUSE)
        nominees = engine.tally_accusations(game.nomination_keep)
        if not nominees:
            return None
        # Only the nominated suspects are asked to answer, so a defence
        # never turns into a rejected action.
        actions = await asyncio.gather(
            *(
                _ask(
                    context,
                    nominee,
                    ActionType.REBUT,
                    [],
                    _REBUTTAL_INSTRUCTION,
                )
                for nominee in sorted(nominees)
            )
        )
        for action in actions:
            if action is not None:
                engine.submit_action(action)
        return None


async def _ask_wave(
    context: PhaseContext, actors: list[str], instruction: Optional[str]
) -> None:
    """One concurrent turn of `public_message` for each of `actors`.

    Actions are submitted in id order afterwards, exactly like
    `_ask_all_alive`, so the event log stays deterministic.
    """
    engine = context.engine
    actions = await asyncio.gather(
        *(
            _ask(
                context,
                actor,
                ActionType.PUBLIC_MESSAGE,
                legal_targets(engine.state, actor, ActionType.PUBLIC_MESSAGE),
                instruction,
            )
            for actor in actors
        )
    )
    for action in actions:
        if action is not None:
            engine.submit_action(action)


async def _run_hosted_debate(
    context: PhaseContext, budget: int, warning_turns: int
) -> None:
    """Open turns, host warning, closing turns, forced vote.

    Turns are spent in waves: each wave gives every player who still has
    open-budget quota one turn, until the budget (or the round-robin
    cut, when it does not divide evenly) stops it. The host then warns
    with the number of closing turns left, runs `warning_turns` closing
    waves for everyone, and closes the debate; the voting phase that
    follows is the forced vote.
    """
    engine = context.engine
    alive = sorted(engine.state.alive_players)
    if not alive:
        return
    quota = math.ceil(budget / len(alive))
    spoken = {actor: 0 for actor in alive}
    turns_left = budget
    while turns_left > 0:
        wave = [actor for actor in alive if spoken[actor] < quota][:turns_left]
        if not wave:
            break
        await _ask_wave(
            context,
            wave,
            f"Host: the debate is open, {turns_left} speaking turns left. "
            "Make your point count.",
        )
        for actor in wave:
            spoken[actor] += 1
        turns_left -= len(wave)

    if warning_turns > 0:
        engine.host_warning(warning_turns * len(alive))
        for index in range(warning_turns):
            remaining = (warning_turns - index) * len(alive)
            await _ask_wave(
                context,
                alive,
                f"Host: time is almost up ({remaining} closing turns left). "
                "Give your final point; the debate closes after this.",
            )
    engine.debate_closed()


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
    """The banishment ballot, plus the show's restricted revote on a tie.

    With `revote_enabled` on, a tied first ballot is followed by a second
    ballot restricted to the tied suspects: nobody who is tied votes, and
    every other voter must choose from the tied names. The elimination
    phase then banishes the revote's winner, or records that the table
    could not decide if the revote ties as well.
    """

    name = "voting"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        engine = context.engine
        if engine.banishment_is_quiet():
            # Seasonal cadence: the round table votes on nobody, so no
            # votes are requested at all and the tally stays empty.
            engine.skip_banishment()
            return None
        await _ask_all_alive(context, ActionType.VOTE)
        tally = engine.tally_votes()
        if engine.revote_available(tally):
            engine.call_revote(tally.top)
            await _ask_revote(context)
        return None


async def _ask_revote(context: PhaseContext) -> None:
    """One restricted ballot from every voter who is not tied.

    The tied players are not asked, because the validator would reject
    their ballot anyway; every other living player is asked once, with
    only the tied suspects as legal targets.
    """
    engine = context.engine
    targets = engine.revote_targets()
    voters = engine.revote_voters()
    instruction = (
        "Host: the vote is tied between "
        + " and ".join(targets)
        + ". You are not one of them, so you decide it: name one of them "
        "as your final choice. There is no further round after this."
    )
    actions = await asyncio.gather(
        *(
            _ask(context, voter, ActionType.VOTE, list(targets), instruction)
            for voter in voters
        )
    )
    for action in actions:
        if action is not None:
            engine.submit_action(action)
    engine.resolve_revote()


class EndVotePhase:
    """Finale end-or-banish vote: every living player answers.

    Targetless; the answer rides in `content` (`end` or `banish`). One
    `banish` from anyone moves the game on to the normal voting and
    elimination phases, and only a unanimous `end` finishes it here.
    """

    name = "end_vote"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        engine = context.engine
        await _ask_all_alive(context, ActionType.END_VOTE)
        engine.resolve_end_vote()
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
        banished = engine.pending_banishment()
        if banished is not None and engine.recruitment_opportunity(banished):
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
    takes the blame) - unless the sequential council is on, where round
    1 of the proposals is that argument. With `on_trial` on, they next
    nominate one player each and the union of those names is the murder
    shortlist: only a shortlisted player can die tonight. Then the kill
    is a majority vote with an earliest-choice tiebreak.

    With `council_deliberation` on and more than one traitor alive, the
    kill itself becomes two rounds instead of one blind ballot: round 1
    is a proposal from each traitor in turn (each landing on the traitor
    channel before the next is asked), round 2 is everyone holding or
    switching at once, and the majority of the final picks wins.

    With `recruit_choice` on and a recruitment window open (a traitor was
    banished at the round table), the council instead votes to recruit or
    murder. A recruit night skips the nomination and the kill entirely:
    the traitors offer one living faithful player, who accepts or
    declines before the window closes.
    """

    name = "traitor_night"

    async def run(self, context: PhaseContext) -> Optional[PhaseResult]:
        engine = context.engine
        if engine.murder_is_quiet():
            # Seasonal cadence: the traitor night does nothing at all,
            # which means no council, no nomination and no kill.
            engine.skip_night_murder()
            return {"victim": None}
        traitors = sorted(
            p for p in engine.state.alive_players
            if engine.state.roles.get(p) is Role.TRAITOR
        )
        open_council = len(traitors) > 1
        if open_council and engine.config.game.council_deliberation:
            # Round 1 of the sequential council replaces the blind
            # free-for-all: each traitor argues with the proposal they
            # make, in turn, instead of everyone speaking at once with
            # nobody listening. A recruit night keeps the free line,
            # because arguing recruit versus murder is all it is for.
            open_council = engine.recruit_window_open()
        if open_council:
            council = await asyncio.gather(
                *(
                    _ask(context, actor, ActionType.TRAITOR_MESSAGE, [])
                    for actor in traitors
                )
            )
            for action in council:
                if action is not None:
                    engine.submit_action(action)
        if engine.recruit_window_open():
            result = await _run_recruit_night(context, engine, traitors)
            if result is not None:
                return result
            # The vote chose murder: fall through to the normal night.
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
        if engine.config.game.council_deliberation and len(traitors) > 1:
            # Audit fix, phase 3: two rounds instead of one blind ballot.
            victim = await _run_council(context, engine, traitors, shortlist)
            return {"victim": victim}
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


async def _run_council(
    context: PhaseContext,
    engine: "GameEngine",
    traitors: list[str],
    shortlist: Optional[list[str]],
) -> Optional[str]:
    """The night's kill in two rounds: proposals, then hold-or-switch.

    Round 1 asks one traitor at a time and freezes each pick as a
    proposal on the traitor channel, so the second and third traitor
    write having read the others instead of guessing at them. Round 2
    collects every final pick from that same moment - a ballot, not a
    bandwagon - and `resolve_night` counts it, reporting the switches,
    the dissent and any tie it had to break.
    """
    for actor in traitors:
        targets = legal_targets(
            engine.state, actor, ActionType.TRAITOR_KILL, shortlist=shortlist
        )
        if not targets:
            continue
        action = await _ask(
            context,
            actor,
            ActionType.TRAITOR_KILL,
            targets,
            _proposal_instruction(engine),
        )
        if action is not None:
            engine.record_council_proposal(action)
    engine.open_council_round_two()
    proposals = engine.council_proposals()
    choices = await asyncio.gather(
        *(
            _ask(
                context,
                actor,
                ActionType.TRAITOR_KILL,
                targets,
                _verdict_instruction(engine, actor, proposals),
            )
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
    return engine.resolve_night()


def _proposal_line(engine: "GameEngine") -> str:
    """What the council has already put on the table, in actor order."""
    proposals = engine.council_proposals()
    return ", ".join(
        f"{traitor} proposes {target}"
        for traitor, target in sorted(proposals.items())
    )


def _proposal_instruction(engine: "GameEngine") -> str:
    """Round 1's host line: propose with a reason, it is not final yet."""
    line = (
        "Council, round 1: propose tonight's victim by naming one player "
        "and put your reason in the content. This is a proposal, not the "
        "final call - in round 2 you hold it or change it after hearing "
        "the others."
    )
    heard = _proposal_line(engine)
    if heard:
        return f"{line} So far tonight: {heard}."
    return f"{line} You speak first, so the argument is yours to open."


def _verdict_instruction(
    engine: "GameEngine", actor: str, proposals: dict[str, str]
) -> str:
    """Round 2's host line: hold or switch, and the majority wins."""
    listed = ", ".join(
        f"{traitor} proposes {target}"
        for traitor, target in sorted(proposals.items())
    )
    mine = proposals.get(actor)
    hold = f" Your own proposal was {mine}." if mine else ""
    return (
        "Council, round 2: everyone has spoken. Hold your proposal or "
        "switch to another name, and say why in the content - every "
        "traitor answers at the same time and the majority of the final "
        f"picks wins.{hold} On the table: {listed or 'no proposals'}."
    )


async def _run_recruit_night(
    context: PhaseContext, engine: "GameEngine", traitors: list[str]
) -> Optional[PhaseResult]:
    """Run a night's recruit-or-murder choice.

    Returns a phase result when the traitors chose to recruit (the night
    has no murder), or None when they chose to murder, so the caller runs
    the normal night. The window is spent either way.
    """
    decisions = await asyncio.gather(
        *(_ask(context, actor, ActionType.RECRUIT_DECISION, []) for actor in traitors)
    )
    for action in decisions:
        if action is not None:
            engine.submit_action(action)
    if engine.resolve_recruit_choice() != "recruit":
        engine.spend_recruit_window()
        return None
    offers = await asyncio.gather(
        *(
            _ask(
                context,
                actor,
                ActionType.RECRUIT,
                legal_targets(engine.state, actor, ActionType.RECRUIT),
            )
            for actor in traitors
        )
    )
    for action in offers:
        if action is not None:
            engine.submit_action(action)
    target = engine.resolve_recruit_offer()
    victim: Optional[str] = None
    if target is not None:
        response = await _ask(context, target, ActionType.RECRUIT_RESPONSE, [])
        if response is not None:
            engine.submit_action(response)
        if target not in engine.state.alive_players:
            # A lone traitor's ultimatum landed: report the night's death.
            victim = target
    engine.spend_recruit_window()
    return {"victim": victim}
