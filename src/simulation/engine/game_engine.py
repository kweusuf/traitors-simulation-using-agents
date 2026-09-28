"""Game engine: the authoritative source of truth (spec section 5).

Responsibilities: state, phases/rounds, role assignment, rule
enforcement, action routing, eliminations, win conditions, events,
snapshots. It never generates natural language (spec section 40).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from simulation.actions.actions import Action, ActionType
from simulation.communication.channels import Channel, Message
from simulation.engine.state import GamePhase, GameState, MissionState, PlayerState, Role
from simulation.engine.rules import RuleValidator, ValidationResult
from simulation.experiments.config import GameConfig
from simulation.communication.router import MessageRouter
from simulation.persistence.database import Database
from simulation.persistence.event_log import EventType
from simulation.persistence.repositories import (
    AgentRepository,
    EliminationRepository,
    SnapshotRepository,
    VoteRepository,
)
from simulation.persistence.sink import EventSink

DEFAULT_PLAYER_NAMES = [
    "alice",
    "bob",
    "charlie",
    "david",
    "eve",
    "frank",
    "grace",
    "heidi",
    "ivan",
    "judy",
]

# Fixed award order for mission items: the cycle steps through this
# order once per award, skipping items that are already held.
ITEM_ORDER = ("shield", "dagger", "seer")


@dataclass(frozen=True)
class TallyResult:
    counts: dict[str, int]
    top: list[str]
    tie: bool


class GameEngine:
    def __init__(
        self,
        config: GameConfig,
        sink: EventSink,
        db: Optional[Database] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.config = config
        self.sink = sink
        self.seed = seed if seed is not None else config.seed
        self.validator = RuleValidator(config)
        self.router = MessageRouter(sink.game_id, db)
        self._agents = AgentRepository(db) if db is not None else None
        self._snapshots = SnapshotRepository(db) if db is not None else None
        self._votes_repo = VoteRepository(db) if db is not None else None
        self._eliminations = EliminationRepository(db) if db is not None else None

        names = config.game.player_names or DEFAULT_PLAYER_NAMES
        if config.game.players > len(names):
            names = names + [f"player{i + 1}" for i in range(len(names), config.game.players)]
        self.player_ids: list[str] = names[: config.game.players]
        self._started = False

        self.state = GameState(game_id=sink.game_id)
        self._usage: dict[tuple[str, ActionType], int] = {}
        self._night_choices: list[Action] = []
        self._nominations: list[Action] = []
        # Endgame end-or-banish votes for the current finale round:
        # player_id -> "end" or "banish". Cleared every round.
        self._end_votes: dict[str, str] = {}
        # The murder shortlist (on_trial): who may be killed this night.
        # Empty while off or unresolved; cleared every round with the
        # night choices so a stale shortlist can never leak across rounds.
        self.murder_shortlist: list[str] = []
        self._pending_recruit: Optional[str] = None
        self.recruits_used = 0
        # Recruitment as a choice (phase 26): the window opens when a
        # traitor is banished at the round table and stays open until the
        # night spends it; the vote and offer live only for that night.
        self._recruit_window = False
        self._recruit_votes: dict[str, str] = {}
        self._recruit_offers: list[Action] = []
        self._recruit_offered: Optional[str] = None
        self._recruit_offerer: Optional[str] = None
        # One seeded RNG for the whole game, held as an attribute: role
        # assignment draws first, item awards draw later, and a second
        # engine with the same seed reproduces the same draws.
        self.rng = random.Random(self.seed)
        # Players who already spent their once-per-game seer check.
        self.seer_checks_done: set[str] = set()
        # voter -> tally weight (2 while a spent dagger doubles it).
        self._vote_weights: dict[str, int] = {}
        # The round that already awarded an item, so a round with three
        # missions awards exactly once, and the cycle position of the
        # last award (index into ITEM_ORDER).
        self._awarded_round = -1
        self._award_index = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        if self._started:
            raise RuntimeError("game already started")
        self._started = True
        rng = self.rng
        for pid in self.player_ids:
            self.state.players[pid] = PlayerState(player_id=pid, name=pid.capitalize())
        self.state.alive_players = set(self.player_ids)

        self._emit(
            EventType.GAME_STARTED,
            payload={
                "players": list(self.player_ids),
                "traitors": self.config.game.traitors,
                "seed": self.seed,
                "name": self.config.game.name,
            },
        )

        if self.config.game.traitor_names:
            # A real season replay: the traitors are known, not drawn.
            traitor_ids = set(self.config.game.traitor_names)
        else:
            traitor_ids = set(
                rng.sample(self.player_ids, self.config.game.traitors)
            )
        for pid in self.player_ids:
            role = Role.TRAITOR if pid in traitor_ids else Role.FAITHFUL
            self.state.roles[pid] = role
            self.state.ambitions[pid] = "solo" if rng.random() < 0.5 else "team"
            self._emit(
                EventType.ROLE_ASSIGNED, actor=pid, payload={"role": role.value}
            )
            if self._agents is not None:
                self._agents.upsert(
                    self.state.game_id, pid, pid.capitalize(), role.value, persona={}
                )
        self._check_finale_trigger()  # a config can open at the finale counts

    def start_round(self) -> None:
        self.state.round_number += 1
        self.state.votes.clear()
        self._vote_weights.clear()
        self._night_choices.clear()
        self._nominations.clear()
        self._end_votes.clear()
        self.murder_shortlist = []
        self._pending_recruit = None
        self._recruit_window = False
        self._recruit_votes.clear()
        self._recruit_offers.clear()
        self._recruit_offered = None
        self._recruit_offerer = None
        self._emit(EventType.ROUND_STARTED)

    def begin_phase(self, phase: GamePhase) -> None:
        self.state.phase = phase
        self._usage.clear()
        self._emit(EventType.PHASE_STARTED, payload={"phase": phase.value})

    def end_phase(self) -> None:
        self._emit(EventType.PHASE_ENDED, payload={"phase": self.state.phase.value})
        self.snapshot()

    def finish(self) -> None:
        self.state.phase = GamePhase.GAME_END
        self._emit(
            EventType.GAME_ENDED,
            payload={
                "winner": self.state.winner,
                "winning_team": self.state.winning_team.value
                if self.state.winning_team
                else None,
                "rounds": self.state.round_number,
            },
        )

    def snapshot(self) -> None:
        if self._snapshots is not None:
            self._snapshots.create(self.state)
            self._emit(EventType.SNAPSHOT_CREATED)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def submit_action(self, action: Action) -> ValidationResult:
        result = self.validator.validate(
            action,
            self.state,
            self._usage,
            shortlist=self.murder_shortlist,
            seer_used=self.seer_checks_done,
            recruit_window=self.recruit_window_open(),
            recruit_offered=self._recruit_offered,
        )
        if not result.ok:
            self._emit(
                EventType.ACTION_REJECTED,
                actor=action.actor_id,
                payload={
                    "action": action.action.value,
                    "target": action.target,
                    "reason": result.reason,
                },
            )
            return result

        self._usage[(action.actor_id, action.action)] = (
            self._usage.get((action.actor_id, action.action), 0) + 1
        )

        if action.action in (
            ActionType.PUBLIC_MESSAGE,
            ActionType.PRIVATE_MESSAGE,
            ActionType.TRAITOR_MESSAGE,
        ):
            self._record_message(action)
        elif action.action is ActionType.VOTE:
            assert action.target is not None
            weight = 1
            if "dagger" in self.state.items.get(action.actor_id, []):
                # The dagger is spent the first time its holder votes,
                # and that one vote counts twice in the tally.
                self._remove_item(action.actor_id, "dagger")
                weight = 2
                self._emit(
                    EventType.DAGGER_USED,
                    actor=action.actor_id,
                    payload={"player": action.actor_id},
                )
            self.state.votes[action.actor_id] = action.target
            self._vote_weights[action.actor_id] = weight
            self._emit(
                EventType.VOTE_CAST,
                actor=action.actor_id,
                targets=[action.target],
                payload={"confidence": action.confidence, "weight": weight},
            )
        elif action.action is ActionType.TRAITOR_KILL:
            self._night_choices.append(action)
        elif action.action is ActionType.NOMINATE:
            self._nominations.append(action)
        elif action.action is ActionType.SEER_CHECK:
            # Resolved on acceptance: the answer is a host message only
            # the holder can read, and the event records who asked whom.
            assert action.target is not None
            self.seer_checks_done.add(action.actor_id)
            self._deliver_seer_answer(action.actor_id, action.target)
        elif action.action is ActionType.RECRUIT:
            # The old path holds the target until the banishment resolves,
            # so the conversion lands before the win check (see
            # `eliminate`). The choice path instead collects every
            # traitor's offer for tonight's window (see
            # `resolve_recruit_offer`).
            assert action.target is not None
            if self.config.game.recruit_choice:
                self._recruit_offers.append(action)
            else:
                self._pending_recruit = action.target
        elif action.action is ActionType.RECRUIT_DECISION:
            # The validator already guaranteed content is recruit/murder.
            choice = (action.content or "").strip().lower()
            self._recruit_votes[action.actor_id] = choice
        elif action.action is ActionType.RECRUIT_RESPONSE:
            # The validator already guaranteed content is accept/decline
            # and that the actor is the offered player.
            self._resolve_recruit_response(action)
        elif action.action is ActionType.END_VOTE:
            # The validator already guaranteed the content is end/banish.
            choice = (action.content or "").strip().lower()
            self._end_votes[action.actor_id] = choice
            self._emit(
                EventType.END_VOTE_CAST,
                actor=action.actor_id,
                payload={"choice": choice},
            )

        return ValidationResult.accepted()

    def record_unparseable_action(
        self, actor: str, action_type: ActionType, reason: str
    ) -> None:
        """Record an action the agent failed to produce (spec section 21).

        The correction retries are exhausted before this is called. The
        turn is skipped rather than aborted so one malformed model reply
        cannot throw away a run, and the miss stays visible in the event
        log and in `metrics.json` instead of being swallowed.
        """
        self._emit(
            EventType.ACTION_REJECTED,
            actor=actor,
            payload={
                "action": action_type.value,
                "target": None,
                "reason": reason,
                "stage": "output_parse",
            },
        )

    def _record_message(self, action: Action) -> None:
        if action.action is ActionType.TRAITOR_MESSAGE:
            # The traitor channel: every living traitor but the sender.
            channel = Channel.ROLE_PRIVATE
            recipients = sorted(
                p
                for p in self.state.alive_players
                if p != action.actor_id and self.state.roles.get(p) is Role.TRAITOR
            )
        elif action.action is ActionType.PUBLIC_MESSAGE:
            channel = Channel.PUBLIC
            recipients = []
        else:
            channel = Channel.PRIVATE
            recipients = [action.target or ""]
        message = Message(
            message_id=f"{self.state.game_id}-msg-{self.sink.next_sequence:05d}",
            sender_id=action.actor_id,
            recipients=recipients,
            channel=channel,
            content=action.content or "",
            round_number=self.state.round_number,
            phase=self.state.phase.value,
        )
        self._emit(
            EventType.PUBLIC_MESSAGE
            if channel is Channel.PUBLIC
            else EventType.PRIVATE_MESSAGE,
            actor=action.actor_id,
            targets=recipients,
            payload={
                "content": message.content,
                "message_id": message.message_id,
                "confidence": action.confidence,
                "channel": channel.value,
            },
        )
        if self.router is not None:
            self.router.deliver(message)

    # ------------------------------------------------------------------
    # Seasonal cadence (quiet rounds)
    # ------------------------------------------------------------------
    def murder_is_quiet(self) -> bool:
        """True when this round is configured to skip the traitor night."""
        return self.state.round_number in self.config.game.quiet_murder_rounds

    def banishment_is_quiet(self) -> bool:
        """True when this round is configured to skip the round-table vote."""
        return self.state.round_number in self.config.game.quiet_banishment_rounds

    def skip_night_murder(self) -> None:
        """Record that the traitor night did nothing this round."""
        self._emit(EventType.MURDER_SKIPPED, payload={"reason": "quiet_round"})

    def skip_banishment(self) -> None:
        """Record that the round table voted on nobody this round."""
        self._emit(EventType.BANISHMENT_SKIPPED, payload={"reason": "quiet_round"})

    # ------------------------------------------------------------------
    # Endgame end-or-banish vote (finale, phase 25)
    # ------------------------------------------------------------------
    def end_vote_choices(self) -> dict[str, str]:
        """This round's end vote: player_id -> "end" or "banish"."""
        return dict(self._end_votes)

    def end_vote_unanimous(self) -> bool:
        """True when every living player answered `end`.

        A skipped turn counts as a missing answer, not as consent, so a
        partial vote can never finish the game by accident.
        """
        if len(self._end_votes) < len(self.state.alive_players):
            return False
        return all(choice == "end" for choice in self._end_votes.values())

    def end_vote_requests_banishment(self) -> bool:
        """True when at least one player answered `banish`."""
        return any(choice == "banish" for choice in self._end_votes.values())

    def resolve_end_vote(self) -> bool:
        """Finish the game on a unanimous `end`; True when it did.

        The traitors take the win if any of them survived (solo when
        exactly one did); otherwise the faithful do. Any `banish` sends
        the game on to the normal voting and elimination phases, after
        which the finale loop runs another end vote.
        """
        if not self.end_vote_unanimous():
            return False
        survivors = [
            p
            for p in self.state.alive_players
            if self.state.roles[p] is Role.TRAITOR
        ]
        self._declare_winner(
            Role.TRAITOR if survivors else Role.FAITHFUL, reason="endgame"
        )
        return True

    # ------------------------------------------------------------------
    # Elimination and win conditions
    # ------------------------------------------------------------------
    def tally_votes(self) -> TallyResult:
        counts: dict[str, int] = {}
        for voter, target in self.state.votes.items():
            # A spent dagger makes its holder's vote count twice.
            counts[target] = counts.get(target, 0) + self._vote_weights.get(voter, 1)
        if not counts:
            return TallyResult(counts={}, top=[], tie=False)
        highest = max(counts.values())
        top = sorted(t for t, c in counts.items() if c == highest)
        return TallyResult(counts=counts, top=top, tie=len(top) > 1)

    def recruitment_opportunity(self, player_id: str) -> bool:
        """Could this player recruit right now if the round table banishes them?

        Asked before the elimination resolves, while the banished player
        is still alive, so the phase knows whether to request a RECRUIT
        action from them.
        """
        game = self.config.game
        if game.recruit_choice:
            # The choice path owns recruitment whenever it is on, so the
            # automatic banishment conversion never fires as well.
            return False
        if not game.recruit_on_banish:
            return False
        if self.state.finale:
            return False  # rapid-fire voting is pure voting, no conversions
        if game.max_recruits and self.recruits_used >= game.max_recruits:
            return False
        if self.state.roles.get(player_id) is not Role.TRAITOR:
            return False
        return any(
            p != player_id and self.state.roles.get(p) is Role.FAITHFUL
            for p in self.state.alive_players
        )

    # ------------------------------------------------------------------
    # Recruitment as a choice (phase 26)
    # ------------------------------------------------------------------
    def recruit_window_open(self) -> bool:
        """May the traitors choose to recruit instead of murder tonight?

        The show's rule: the window opens only on the night after a
        traitor is banished at the round table (see `eliminate`). It also
        needs a living faithful to offer to, a living traitor to make the
        offer, and an unspent `max_recruits` budget.
        """
        game = self.config.game
        if not game.recruit_choice or self.state.finale:
            return False
        if not self._recruit_window:
            return False
        if game.max_recruits and self.recruits_used >= game.max_recruits:
            return False
        roles = self.state.roles
        alive = self.state.alive_players
        if not any(roles.get(p) is Role.TRAITOR for p in alive):
            return False
        return any(roles.get(p) is Role.FAITHFUL for p in alive)

    def spend_recruit_window(self) -> None:
        """Close tonight's recruitment window, whichever way it went."""
        self._recruit_window = False

    @property
    def recruit_offered(self) -> Optional[str]:
        """The player currently weighing a recruitment offer, if any."""
        return self._recruit_offered

    def resolve_recruit_choice(self) -> str:
        """Resolve the traitors' recruit-or-murder vote for tonight.

        Majority wins; a tie (including an unanswered council) falls to
        murder, so a split team keeps the night's kill. The choice and
        every traitor's answer are recorded in one event.
        """
        counts = {"recruit": 0, "murder": 0}
        for choice in self._recruit_votes.values():
            counts[choice] = counts.get(choice, 0) + 1
        choice = "recruit" if counts["recruit"] > counts["murder"] else "murder"
        self._emit(
            EventType.RECRUIT_CHOICE_MADE,
            payload={"choice": choice, "votes": dict(self._recruit_votes)},
        )
        return choice

    def resolve_recruit_offer(self) -> Optional[str]:
        """Pick the traitors' recruitment target and record the offer.

        Every living traitor gets one RECRUIT action; the majority target
        wins and the earliest submission breaks a tie, exactly like the
        night kill. Returns None when nobody offered.
        """
        if not self._recruit_offers:
            return None
        counts: dict[str, int] = {}
        for offer in self._recruit_offers:
            assert offer.target is not None
            counts[offer.target] = counts.get(offer.target, 0) + 1
        highest = max(counts.values())
        tied = {target for target, count in counts.items() if count == highest}
        chosen = next(o for o in self._recruit_offers if o.target in tied)
        self._recruit_offered = chosen.target
        self._recruit_offerer = chosen.actor_id
        self._emit(
            EventType.RECRUIT_OFFERED,
            actor=chosen.actor_id,
            targets=[chosen.target],
            payload={"target": chosen.target, "by": chosen.actor_id},
        )
        return chosen.target

    def _resolve_recruit_response(self, action: Action) -> None:
        """Apply the offered player's answer to tonight's recruitment.

        Accepting uses the same conversion as the banishment path; a
        decline wastes the night unless the offer came from a lone
        traitor, whose ultimatum murders the player instead.
        """
        offered = self._recruit_offered
        offerer = self._recruit_offerer
        # The validator guarantees the actor is the offered player and an
        # offer is open, so this is an engine invariant, not user input.
        assert offered is not None and offerer is not None
        if (action.content or "").strip().lower() == "accept":
            self._apply_recruit(offered, by=offerer)
            self._emit(
                EventType.RECRUIT_ACCEPTED,
                actor=offered,
                targets=[offerer],
                payload={"by": offerer},
            )
            self.check_win()
        else:
            self._emit(
                EventType.RECRUIT_DECLINED,
                actor=offered,
                targets=[offerer],
                payload={"by": offerer},
            )
            alive_traitors = [
                p
                for p in self.state.alive_players
                if self.state.roles.get(p) is Role.TRAITOR
            ]
            if len(alive_traitors) == 1:
                # A lone traitor's offer is an ultimatum: a refusal is fatal.
                self._emit(
                    EventType.ULTIMATUM_ISSUED,
                    actor=offerer,
                    targets=[offered],
                    payload={"target": offered, "by": offerer},
                )
                self._night_elimination(offered)
        # The answer is spent: a second response has nothing to answer.
        self._recruit_offered = None
        self._recruit_offerer = None

    def resolve_votes(self) -> TallyResult:
        tally = self.tally_votes()
        if not tally.counts:
            return tally
        if self._votes_repo is not None:
            self._votes_repo.replace_round(
                self.state.game_id,
                self.state.round_number,
                self.state.votes,
                weights=self._vote_weights,
            )
        if tally.tie:
            self._pending_recruit = None
            self._emit(EventType.VOTE_TIE, targets=tally.top, payload={"counts": tally.counts})
            return tally
        recruit, self._pending_recruit = self._pending_recruit, None
        self.eliminate(tally.top[0], method="vote", votes=tally.counts, recruit=recruit)
        return tally

    def resolve_night(self) -> Optional[str]:
        """Majority traitor choice; ties fall to the earliest submitted choice."""
        if not self._night_choices:
            return None
        counts: dict[str, int] = {}
        for choice in self._night_choices:
            assert choice.target is not None
            counts[choice.target] = counts.get(choice.target, 0) + 1
        highest = max(counts.values())
        tied = {t for t, c in counts.items() if c == highest}
        victim = next(
            c.target for c in self._night_choices if c.target in tied
        )
        assert victim is not None
        self._emit(
            EventType.TRAITOR_KILL,
            targets=[victim],
            payload={
                "choices": [c.target for c in self._night_choices],
                "counts": counts,
            },
        )
        return self._night_elimination(victim)

    def _night_elimination(self, victim: str) -> Optional[str]:
        """Apply one night kill and return who died (None if shielded).

        Shared by the traitors' majority murder and a lone traitor's
        ultimatum so both go through the same shield rule and the same
        `PLAYER_ELIMINATED` event.
        """
        if "shield" in self.state.items.get(victim, []):
            # The shield eats the murder: nobody dies, the shield is
            # spent, and the traitors' choice stays used (no second pick).
            self._remove_item(victim, "shield")
            self._emit(
                EventType.SHIELD_BLOCKED,
                actor=victim,
                targets=[victim],
                payload={"victim": victim},
            )
            return None
        self.eliminate(victim, method="night")
        return victim

    def resolve_nominations(self) -> list[str]:
        """Freeze the murder shortlist from this round's nominations.

        Runs between the nominations and the kill request so that
        `legal_targets` only offers shortlist members and the validator
        can reject a kill outside it.
        """
        self.murder_shortlist = sorted(
            {a.target for a in self._nominations if a.target}
        )
        self._emit(
            EventType.MURDER_SHORTLIST,
            targets=self.murder_shortlist,
            payload={
                "shortlist": self.murder_shortlist,
                "nominations": {
                    a.actor_id: a.target for a in self._nominations if a.target
                },
            },
        )
        return self.murder_shortlist

    def eliminate(
        self,
        player_id: str,
        method: str,
        votes: Optional[dict[str, int]] = None,
        recruit: Optional[str] = None,
    ) -> None:
        if player_id not in self.state.alive_players:
            raise ValueError(f"player '{player_id}' is not alive")
        self.state.players[player_id].alive = False
        self.state.alive_players.discard(player_id)
        self.state.eliminated_players.add(player_id)
        if self.state.finale:
            # Tracked for the blind finale: a player eliminated after the
            # finale started keeps their role hidden until the game ends.
            self.state.finale_eliminated.add(player_id)
        self._emit(
            EventType.PLAYER_ELIMINATED,
            actor=player_id,
            payload={"method": method, "votes": votes or {}},
        )
        if self._agents is not None:
            self._agents.set_alive(self.state.game_id, player_id, False)
        if self._eliminations is not None:
            self._eliminations.add(
                self.state.game_id, self.state.round_number, player_id, method
            )
        # A banished traitor recruits before the win check, so a last
        # traitor taken off the board by vote can still hand over.
        if recruit is not None:
            if self.state.roles.get(player_id) is not Role.TRAITOR:
                raise ValueError(
                    f"recruitment '{recruit}' requested for non-traitor '{player_id}'"
                )
            self._apply_recruit(recruit, by=player_id)
        # The show's rule: a round-table banishment of a traitor opens
        # tonight's recruitment window (see `recruit_window_open`).
        if method == "vote" and self.state.roles.get(player_id) is Role.TRAITOR:
            self._recruit_window = True
        self.check_win()

    def _apply_recruit(self, player_id: str, by: str) -> None:
        """Convert one living faithful player into a traitor."""
        if player_id not in self.state.alive_players:
            raise ValueError(f"cannot recruit '{player_id}': not alive")
        if self.state.roles.get(player_id) is not Role.FAITHFUL:
            raise ValueError(f"cannot recruit '{player_id}': not a faithful player")
        self.state.roles[player_id] = Role.TRAITOR
        self.recruits_used += 1
        self._emit(
            EventType.ROLE_RECRUITED,
            actor=player_id,
            targets=[by],
            payload={"by": by, "recruits_used": self.recruits_used},
        )
        if self._agents is not None:
            self._agents.set_role(self.state.game_id, player_id, Role.TRAITOR.value)

    def check_win(self) -> bool:
        """Return True if the game reached a terminal state."""
        if self.state.winner is not None:
            return True
        if self._check_finale_trigger():
            return False
        alive_traitors = [
            p for p in self.state.alive_players if self.state.roles[p] is Role.TRAITOR
        ]
        alive_faithful = [
            p for p in self.state.alive_players if self.state.roles[p] is Role.FAITHFUL
        ]
        endgame_vote = self.state.finale and self.config.game.endgame_vote
        winner: Optional[Role] = None
        if not alive_traitors:
            winner = Role.FAITHFUL
        elif not alive_faithful:
            winner = Role.TRAITOR
        elif endgame_vote and len(self.state.alive_players) <= 2:
            # The show stops at the final two: the surviving traitor, if
            # any, takes the prize.
            winner = Role.TRAITOR
        elif not endgame_vote and len(alive_traitors) >= len(alive_faithful):
            # Plain parity win. With the endgame vote on, parity no
            # longer ends the game; only extinction does.
            winner = Role.TRAITOR
        if winner is None:
            return False
        self._declare_winner(
            winner, reason="endgame" if endgame_vote else "elimination"
        )
        return True

    def _check_finale_trigger(self) -> bool:
        """Start the finale when the configured split is reached.

        Normal play stops there instead of a parity win: the rapid-fire
        vote decides from 3 against 3. Called on every win check and
        once at game start, so a game configured at the finale counts
        opens straight into rapid fire.
        """
        game = self.config.game
        if not game.finale_traitors or self.state.finale:
            return False
        alive_traitors = [
            p for p in self.state.alive_players if self.state.roles[p] is Role.TRAITOR
        ]
        alive_faithful = [
            p for p in self.state.alive_players if self.state.roles[p] is Role.FAITHFUL
        ]
        if (
            len(alive_traitors) == game.finale_traitors
            and len(alive_faithful) == game.finale_faithful
        ):
            self._start_finale(alive_traitors, alive_faithful)
            return True
        return False

    def _start_finale(self, traitors: list[str], faithful: list[str]) -> None:
        self.state.finale = True
        self._emit(
            EventType.FINALE_STARTED,
            payload={
                "traitors": sorted(traitors),
                "faithful": sorted(faithful),
            },
        )

    def apply_round_limit(self, reason: str = "round_limit") -> None:
        if self.state.winner is not None:
            return
        self._declare_winner(
            Role(self.config.game.round_limit_winner), reason=reason
        )

    def _declare_winner(self, team: Role, reason: str) -> None:
        self.state.winning_team = team
        self.state.winner = team.value
        survivors = sorted(
            p
            for p in self.state.alive_players
            if self.state.roles[p] is Role.TRAITOR
        )
        self._emit(
            EventType.GAME_WON,
            payload={
                "team": team.value,
                "reason": reason,
                "finale": self.state.finale,
                "surviving_traitors": survivors,
                # One traitor left when the faction wins: they took their
                # rivals out with the faithful and win alone.
                "solo": team is Role.TRAITOR and len(survivors) == 1,
            },
        )

    # ------------------------------------------------------------------
    # Mission abstraction (basic)
    # ------------------------------------------------------------------
    def start_mission(self) -> None:
        self._emit(EventType.MISSION_STARTED)
        self.state.missions.append(
            MissionState(round_number=self.state.round_number)
        )

    def complete_mission(self, success: bool) -> None:
        mission = next(
            (m for m in reversed(self.state.missions) if not m.completed),
            None,
        )
        if mission is None:
            raise ValueError("no mission in progress")
        mission.completed = True
        mission.outcome = success
        self._emit(EventType.MISSION_COMPLETED, payload={"success": success})
        if self._awarded_round != self.state.round_number:
            # One item per round, right after the round's first
            # completed mission (a round may run several missions).
            self._awarded_round = self.state.round_number
            self._award_item()

    # ------------------------------------------------------------------
    # Items (shield, dagger, seer)
    # ------------------------------------------------------------------
    def _award_item(self) -> None:
        """Hand out this round's item with the seeded RNG.

        The mission always succeeds, so the item is the mission's
        reward. The cycle advances one step per award through
        `ITEM_ORDER`, skipping any item somebody already holds, so
        shield, dagger and seer come round in turn (a spent shield or
        dagger becomes awardable again on a later lap). With no item
        flag on, or nobody eligible, no draw happens, which keeps
        default-off games on their original random sequence.
        """
        game = self.config.game
        flags = {"shield": game.shield, "dagger": game.dagger, "seer": game.seer}
        held = {
            item for items in self.state.items.values() for item in items
        }
        item: Optional[str] = None
        index = self._award_index
        for offset in range(len(ITEM_ORDER)):
            candidate = ITEM_ORDER[(index + offset) % len(ITEM_ORDER)]
            if flags[candidate] and candidate not in held:
                item = candidate
                index = (index + offset) % len(ITEM_ORDER)
                break
        if item is None:
            return  # every enabled item is already out there
        eligible = sorted(
            p
            for p in self.state.alive_players
            if item not in self.state.items.get(p, [])
        )
        if not eligible:
            return  # nobody left to receive it this round
        recipient = self.rng.choice(eligible)
        self._award_index = (index + 1) % len(ITEM_ORDER)
        self.state.items.setdefault(recipient, []).append(item)
        self._emit(EventType.ITEM_AWARDED, actor=recipient, payload={"item": item})

    def _remove_item(self, player_id: str, item: str) -> None:
        held = self.state.items.get(player_id, [])
        if item in held:
            held.remove(item)

    def _deliver_seer_answer(self, holder: str, target: str) -> None:
        """Answer a seer check as a host message only the holder reads.

        The answer rides on a role-private message from `host`, so the
        router's structural filter hides it from every other player, and
        the SEER_CHECK event records who asked about whom without
        restating the answer in its payload.
        """
        answer = f"{target} is a {self.state.roles[target].value}"
        self._emit(
            EventType.SEER_CHECK,
            actor=holder,
            targets=[target],
            payload={"holder": holder, "target": target},
        )
        message = Message(
            message_id=f"{self.state.game_id}-msg-{self.sink.next_sequence:05d}",
            sender_id="host",
            recipients=[holder],
            channel=Channel.ROLE_PRIVATE,
            content=answer,
            round_number=self.state.round_number,
            phase=self.state.phase.value,
        )
        self._emit(
            EventType.PRIVATE_MESSAGE,
            actor="host",
            targets=[holder],
            payload={
                "content": message.content,
                "message_id": message.message_id,
                "confidence": None,
                "channel": Channel.ROLE_PRIVATE.value,
            },
        )
        if self.router is not None:
            self.router.deliver(message)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @property
    def is_over(self) -> bool:
        return self.state.winner is not None

    def _emit(
        self,
        type_: EventType,
        actor: Optional[str] = None,
        targets: Optional[list[str]] = None,
        payload: Optional[dict] = None,
    ) -> None:
        self.sink.emit(
            type_,
            round_number=self.state.round_number,
            phase=self.state.phase.value,
            actor=actor,
            targets=targets,
            payload=payload,
        )
