"""Information projection (spec section 10).

Transforms the global GameState into an agent-specific view. Hidden
information is never included: role knowledge is computed from an
allow-list, and message history comes from the router's structural
visibility filter.
"""

from __future__ import annotations

from simulation.communication.channels import Channel, Message
from simulation.communication.router import MessageRouter
from simulation.engine.state import GamePhase, GameState, Role
from simulation.models.base import StrictModel


class AgentView(StrictModel):
    """Everything one agent is allowed to observe at one moment."""

    agent_id: str
    game_id: str
    round_number: int
    phase: GamePhase
    own_role: Role
    known_roles: dict[str, Role]
    alive_players: list[str]
    eliminated_players: list[str]
    public_transcript: list[Message]
    private_conversations: list[Message]
    # Items this player holds, and only theirs: other players' items
    # are hidden information, like living roles.
    items: list[str] = []
    winner: str | None = None

    def render(self) -> str:
        """Plain-text observation for prompt construction."""
        lines = [
            f"Game: {self.game_id}",
            f"Round: {self.round_number}  Phase: {self.phase.value}",
            f"Your role: {self.own_role.value} (secret, never reveal it "
            "publicly)",
        ]
        others = {
            pid: role.value
            for pid, role in self.known_roles.items()
            if pid != self.agent_id
        }
        if others:
            lines.append(
                "Private knowledge of roles (never public): "
                + ", ".join(f"{pid}={r}" for pid, r in sorted(others.items()))
            )
        lines.append("Alive players: " + ", ".join(sorted(self.alive_players)))
        if self.eliminated_players:
            lines.append(
                "Eliminated: " + ", ".join(sorted(self.eliminated_players))
            )
        if self.items:
            lines.append("Items: " + ", ".join(sorted(self.items)))
        if self.winner:
            lines.append(f"Game over. Winner: {self.winner}")
        return "\n".join(lines)

    def pair_threads(self) -> dict[str, list[Message]]:
        """This player's private conversations, grouped by counterpart.

        One thread per person, oldest first. A private message has exactly
        two people in it, so the counterpart is unambiguous: the recipient
        when this player wrote it, the sender when they received it. The
        traitor channel is a group rather than a pair and is left out,
        since a per-pair thread is what a one-to-one chat needs.
        """
        threads: dict[str, list[Message]] = {}
        for msg in self.private_conversations:
            if msg.channel is not Channel.PRIVATE:
                continue
            if msg.sender_id == self.agent_id:
                peers = [r for r in msg.recipients if r != self.agent_id]
            else:
                peers = [msg.sender_id]
            if len(peers) != 1:
                continue
            threads.setdefault(peers[0], []).append(msg)
        return threads


class InformationProjector:
    def __init__(
        self,
        router: MessageRouter,
        reveal_on_elimination: bool = True,
        reveal_on_end: bool = True,
        blind_finale_banishments: bool = False,
    ) -> None:
        self._router = router
        self._reveal_on_elimination = reveal_on_elimination
        self._reveal_on_end = reveal_on_end
        # With the blind finale on, a player banished during the finale
        # keeps their role hidden while the game has no winner.
        self._blind_finale = blind_finale_banishments

    def project(self, state: GameState, agent_id: str) -> AgentView:
        if agent_id not in state.players:
            raise ValueError(f"unknown agent '{agent_id}'")

        return AgentView(
            agent_id=agent_id,
            game_id=state.game_id,
            round_number=state.round_number,
            phase=state.phase,
            own_role=state.roles[agent_id],
            known_roles=self._known_roles(state, agent_id),
            alive_players=sorted(state.alive_players),
            eliminated_players=sorted(state.eliminated_players),
            public_transcript=[
                m for m in self._router.visible_to(agent_id)
                if m.channel is Channel.PUBLIC
            ],
            private_conversations=[
                m
                for m in self._router.visible_to(agent_id)
                if m.channel in (Channel.PRIVATE, Channel.ROLE_PRIVATE)
            ],
            items=list(state.items.get(agent_id, [])),
            winner=state.winner,
        )

    def _known_roles(self, state: GameState, agent_id: str) -> dict[str, Role]:
        known: dict[str, Role] = {agent_id: state.roles[agent_id]}

        # Traitors know their own team (spec section 10 example).
        if state.roles[agent_id] is Role.TRAITOR:
            for pid, role in state.roles.items():
                if role is Role.TRAITOR:
                    known[pid] = role

        # Publicly eliminated players reveal their roles by default.
        if self._reveal_on_elimination:
            for pid in state.eliminated_players:
                if (
                    self._blind_finale
                    and state.winner is None
                    and pid in state.finale_eliminated
                ):
                    continue  # blind finale: role stays hidden for now
                known[pid] = state.roles[pid]

        if self._reveal_on_end and state.winner is not None:
            known.update(state.roles)

        return known
