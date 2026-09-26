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
from simulation.persistence.repositories import AgentRepository, SnapshotRepository
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

        names = config.game.player_names or DEFAULT_PLAYER_NAMES
        if config.game.players > len(names):
            names = names + [f"player{i + 1}" for i in range(len(names), config.game.players)]
        self.player_ids: list[str] = names[: config.game.players]
        self._started = False

        self.state = GameState(game_id=sink.game_id)
        self._usage: dict[tuple[str, ActionType], int] = {}
        self._night_choices: list[Action] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            raise RuntimeError("game already started")
        self._started = True
        rng = random.Random(self.seed)
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

        traitor_ids = set(rng.sample(self.player_ids, self.config.game.traitors))
        for pid in self.player_ids:
            role = Role.TRAITOR if pid in traitor_ids else Role.FAITHFUL
            self.state.roles[pid] = role
            self._emit(
                EventType.ROLE_ASSIGNED, actor=pid, payload={"role": role.value}
            )
            if self._agents is not None:
                self._agents.upsert(
                    self.state.game_id, pid, pid.capitalize(), role.value, persona={}
                )

    def start_round(self) -> None:
        self.state.round_number += 1
        self.state.votes.clear()
        self._night_choices.clear()
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
        result = self.validator.validate(action, self.state, self._usage)
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

        if action.action in (ActionType.PUBLIC_MESSAGE, ActionType.PRIVATE_MESSAGE):
            self._record_message(action)
        elif action.action is ActionType.VOTE:
            assert action.target is not None
            self.state.votes[action.actor_id] = action.target
            self._emit(
                EventType.VOTE_CAST,
                actor=action.actor_id,
                targets=[action.target],
                payload={"confidence": action.confidence},
            )
        elif action.action is ActionType.TRAITOR_KILL:
            self._night_choices.append(action)

        return ValidationResult.accepted()

    def _record_message(self, action: Action) -> None:
        channel = (
            Channel.PUBLIC
            if action.action is ActionType.PUBLIC_MESSAGE
            else Channel.PRIVATE
        )
        recipients = (
            [] if channel is Channel.PUBLIC else [action.target or ""]
        )
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
            },
        )
        if self.router is not None:
            self.router.deliver(message)

    # ------------------------------------------------------------------
    # Elimination and win conditions
    # ------------------------------------------------------------------
    def tally_votes(self) -> TallyResult:
        counts: dict[str, int] = {}
        for target in self.state.votes.values():
            counts[target] = counts.get(target, 0) + 1
        if not counts:
            return TallyResult(counts={}, top=[], tie=False)
        highest = max(counts.values())
        top = sorted(t for t, c in counts.items() if c == highest)
        return TallyResult(counts=counts, top=top, tie=len(top) > 1)

    def resolve_votes(self) -> TallyResult:
        tally = self.tally_votes()
        if not tally.counts:
            return tally
        if tally.tie:
            self._emit(EventType.VOTE_TIE, targets=tally.top, payload={"counts": tally.counts})
            return tally
        self.eliminate(tally.top[0], method="vote", votes=tally.counts)
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
        self.eliminate(victim, method="night")
        return victim

    def eliminate(
        self,
        player_id: str,
        method: str,
        votes: Optional[dict[str, int]] = None,
    ) -> None:
        if player_id not in self.state.alive_players:
            raise ValueError(f"player '{player_id}' is not alive")
        self.state.players[player_id].alive = False
        self.state.alive_players.discard(player_id)
        self.state.eliminated_players.add(player_id)
        self._emit(
            EventType.PLAYER_ELIMINATED,
            actor=player_id,
            payload={"method": method, "votes": votes or {}},
        )
        if self._agents is not None:
            self._agents.set_alive(self.state.game_id, player_id, False)
        self.check_win()

    def check_win(self) -> bool:
        """Return True if the game reached a terminal state."""
        if self.state.winner is not None:
            return True
        alive_traitors = [
            p for p in self.state.alive_players if self.state.roles[p] is Role.TRAITOR
        ]
        alive_faithful = [
            p for p in self.state.alive_players if self.state.roles[p] is Role.FAITHFUL
        ]
        winner: Optional[Role] = None
        if not alive_traitors:
            winner = Role.FAITHFUL
        elif len(alive_traitors) >= len(alive_faithful):
            winner = Role.TRAITOR
        if winner is None:
            return False
        self._declare_winner(winner, reason="elimination")
        return True

    def apply_round_limit(self) -> None:
        if self.state.winner is not None:
            return
        self._declare_winner(
            Role(self.config.game.round_limit_winner), reason="round_limit"
        )

    def _declare_winner(self, team: Role, reason: str) -> None:
        self.state.winning_team = team
        self.state.winner = team.value
        self._emit(
            EventType.GAME_WON,
            payload={"team": team.value, "reason": reason},
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
