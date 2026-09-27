"""Event replay, inspection, and transcripts (spec sections 25, 44).

Everything here is derived from the immutable JSONL event log: no
database and no model are needed to rebuild what happened, or what one
agent was allowed to see.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from simulation.communication.channels import Channel, Message
from simulation.communication.visibility import AgentView
from simulation.engine.state import GamePhase, Role
from simulation.persistence.event_log import Event, EventType


@dataclass(frozen=True)
class Elimination:
    round: int
    player: str
    method: str  # "vote" | "night"


@dataclass
class ReplayState:
    """Game state reconstructed from events alone."""

    game_id: str = ""
    players: list[str] = field(default_factory=list)
    roles: dict[str, Role] = field(default_factory=dict)
    alive: set[str] = field(default_factory=set)
    eliminated: list[Elimination] = field(default_factory=list)
    votes: dict[int, dict[str, str]] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    phases: list[tuple[int, str]] = field(default_factory=list)  # (round, phase)
    missions: list[tuple[int, bool]] = field(default_factory=list)
    items: dict[str, list[str]] = field(default_factory=dict)
    winner: str | None = None
    winning_team: str | None = None
    rounds: int = 0
    rejected_actions: int = 0
    event_count: int = 0
    finale: bool = False

    @classmethod
    def from_events(cls, events: list[Event]) -> "ReplayState":
        """Fold an ordered event log back into game state (spec section 25)."""
        state = cls(event_count=len(events))
        for event in events:
            if event.type is EventType.GAME_STARTED:
                state.game_id = event.game_id
                state.players = list(event.payload.get("players", []))
                state.alive = set(state.players)
            elif event.type is EventType.ROLE_ASSIGNED and event.actor:
                role = event.payload.get("role")
                if role is not None:
                    state.roles[event.actor] = Role(role)
            elif event.type is EventType.ROLE_RECRUITED and event.actor:
                state.roles[event.actor] = Role.TRAITOR
            elif event.type is EventType.FINALE_STARTED:
                state.finale = True
            elif event.type is EventType.ROUND_STARTED:
                state.rounds = max(state.rounds, event.round)
            elif event.type is EventType.PHASE_STARTED:
                state.phases.append((event.round, event.payload.get("phase", "")))
            elif event.type in (EventType.PUBLIC_MESSAGE, EventType.PRIVATE_MESSAGE):
                state.messages.append(
                    Message(
                        message_id=str(event.payload.get("message_id", event.event_id)),
                        sender_id=event.actor or "",
                        recipients=list(event.targets),
                        channel=(
                            Channel.PUBLIC
                            if event.type is EventType.PUBLIC_MESSAGE
                            else Channel.PRIVATE
                        ),
                        content=str(event.payload.get("content", "")),
                        round_number=event.round,
                        phase=event.phase,
                    )
                )
            elif event.type is EventType.VOTE_CAST and event.actor:
                state.votes.setdefault(event.round, {})[event.actor] = (
                    event.targets[0] if event.targets else ""
                )
            elif event.type is EventType.PLAYER_ELIMINATED and event.actor:
                state.eliminated.append(
                    Elimination(
                        round=event.round,
                        player=event.actor,
                        method=str(event.payload.get("method", "unknown")),
                    )
                )
                state.alive.discard(event.actor)
            elif event.type is EventType.MISSION_COMPLETED:
                state.missions.append(
                    (event.round, bool(event.payload.get("success")))
                )
            elif event.type is EventType.ITEM_AWARDED and event.actor:
                item = str(event.payload.get("item", ""))
                if item:
                    state.items.setdefault(event.actor, []).append(item)
            elif (
                event.type in (EventType.SHIELD_BLOCKED, EventType.DAGGER_USED)
                and event.actor
            ):
                # Spent items leave the holder's hand in the same order
                # they were recorded, so the fold matches the live state.
                spent = (
                    "shield"
                    if event.type is EventType.SHIELD_BLOCKED
                    else "dagger"
                )
                held = state.items.get(event.actor, [])
                if spent in held:
                    held.remove(spent)
            elif event.type is EventType.ACTION_REJECTED:
                state.rejected_actions += 1
            elif event.type is EventType.GAME_WON:
                state.winning_team = str(event.payload.get("team", ""))
                state.winner = str(event.payload.get("team", ""))
            elif event.type is EventType.GAME_ENDED:
                winner = event.payload.get("winner")
                if winner is not None:
                    state.winner = str(winner)
                rounds = event.payload.get("rounds")
                if isinstance(rounds, int):
                    state.rounds = rounds
        return state

    @property
    def last_phase(self) -> GamePhase:
        if self.winner is not None:
            return GamePhase.GAME_END
        if not self.phases:
            return GamePhase.SETUP
        return GamePhase(self.phases[-1][1])

    def project(self, agent_id: str) -> AgentView:
        """Rebuild one agent's visible slice with the same allow-list the
        live projector uses (spec sections 10 and 25)."""
        if agent_id not in self.roles:
            raise ValueError(f"unknown agent '{agent_id}'")
        known: dict[str, Role] = {agent_id: self.roles[agent_id]}
        if self.roles[agent_id] is Role.TRAITOR:
            for pid, role in self.roles.items():
                if role is Role.TRAITOR:
                    known[pid] = role
        for record in self.eliminated:
            known[record.player] = self.roles[record.player]
        if self.winner is not None:
            known.update(self.roles)

        return AgentView(
            agent_id=agent_id,
            game_id=self.game_id,
            round_number=self.rounds,
            phase=self.last_phase,
            own_role=self.roles[agent_id],
            known_roles=known,
            alive_players=sorted(self.alive),
            eliminated_players=sorted({e.player for e in self.eliminated}),
            public_transcript=[m for m in self.messages if m.channel is Channel.PUBLIC],
            private_conversations=[
                m
                for m in self.messages
                if m.channel is Channel.PRIVATE
                and (agent_id == m.sender_id or agent_id in m.recipients)
            ],
            items=list(self.items.get(agent_id, [])),
            winner=self.winner,
        )


# ----------------------------------------------------------------------
# Transcripts (spec sections 34 and 43)
# ----------------------------------------------------------------------


def build_transcript(events: list[Event]) -> dict:
    """Structured transcript artifact for a run directory."""
    messages = [
        {
            "round": event.round,
            "phase": event.phase,
            "channel": "public"
            if event.type is EventType.PUBLIC_MESSAGE
            else "private",
            "sender": event.actor,
            "recipients": list(event.targets),
            "content": event.payload.get("content", ""),
        }
        for event in events
        if event.type in (EventType.PUBLIC_MESSAGE, EventType.PRIVATE_MESSAGE)
    ]
    eliminations = [
        {
            "round": event.round,
            "player": event.actor,
            "method": event.payload.get("method", "unknown"),
        }
        for event in events
        if event.type is EventType.PLAYER_ELIMINATED
    ]
    recruitments = [
        {
            "round": event.round,
            "player": event.actor,
            "by": event.payload.get("by"),
        }
        for event in events
        if event.type is EventType.ROLE_RECRUITED
    ]
    winner = next(
        (
            str(event.payload.get("team"))
            for event in reversed(events)
            if event.type is EventType.GAME_WON
        ),
        None,
    )
    finale = any(event.type is EventType.FINALE_STARTED for event in events)
    solo = next(
        (
            bool(event.payload.get("solo"))
            for event in reversed(events)
            if event.type is EventType.GAME_WON
        ),
        False,
    )
    return {
        "messages": messages,
        "eliminations": eliminations,
        "recruitments": recruitments,
        "finale": finale,
        "solo_traitor_win": solo,
        "winner": winner,
    }


def render_transcript(events: list[Event]) -> str:
    """Human-readable narrative of a game, grouped by round and phase."""
    lines: list[str] = []
    current_round: int | None = None
    for event in events:
        if event.type is EventType.ROUND_STARTED:
            current_round = event.round
            lines.append(f"Round {event.round}")
        elif event.type is EventType.PHASE_STARTED:
            phase = event.payload.get("phase", "")
            lines.append(f"  {phase.replace('_', ' ').title()}")
        elif event.type is EventType.PUBLIC_MESSAGE:
            lines.append(f"    [public] {event.actor}: {event.payload.get('content', '')}")
        elif event.type is EventType.PRIVATE_MESSAGE:
            to = ", ".join(event.targets)
            lines.append(
                f"    [private] {event.actor} -> {to}: {event.payload.get('content', '')}"
            )
        elif event.type is EventType.PLAYER_ELIMINATED:
            method = event.payload.get("method", "")
            suffix = " (night)" if method == "night" else ""
            lines.append(f"    Eliminated: {event.actor}{suffix}")
        elif event.type is EventType.ITEM_AWARDED:
            item = event.payload.get("item", "?")
            lines.append(f"    Item awarded: {event.actor} receives the {item}")
        elif event.type is EventType.SHIELD_BLOCKED:
            victim = event.payload.get("victim", event.actor)
            lines.append(f"    Shield blocked the murder: {victim} survives")
        elif event.type is EventType.ROLE_RECRUITED:
            lines.append(
                f"    Recruited: {event.actor} (by {event.payload.get('by', '?')})"
            )
        elif event.type is EventType.FINALE_STARTED:
            lines.append(
                "Finale: rapid fire voting "
                f"({len(event.payload.get('traitors', []))} traitors, "
                f"{len(event.payload.get('faithful', []))} faithful)"
            )
        elif event.type is EventType.VOTE_TIE:
            lines.append("    Vote tied; nobody eliminated")
        elif event.type is EventType.GAME_WON:
            team = event.payload.get("team", "")
            reason = event.payload.get("reason", "")
            lines.append(f"Game over. Winner: {team} ({reason})")
        elif event.type is EventType.GAME_ENDED:
            lines.append(f"Rounds played: {event.payload.get('rounds', current_round)}")
    return "\n".join(lines) + ("\n" if lines else "")


def load_events(path) -> list[Event]:
    """Read a run's JSONL event log."""
    from simulation.persistence.jsonl import EventLog

    return EventLog(path).read_all()
