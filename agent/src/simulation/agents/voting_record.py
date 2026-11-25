"""The public voting record, rendered for the agents.

Votes are public at the round table, but the simulation never showed them
back to the players. The real-world evidence is strong: almost every vote
for a traitor is cast by a faithful, traitors do not vote each other until
the night one is caught, and a revealed traitor's past votes clear the
people they targeted. A faithful who can cross-reference the record beats
chance, especially after the first traitor is revealed.

This module turns `GameState.vote_history` plus the viewer's revealed roles
into a few compact lines for the prompt. It only uses public facts (votes
and roles that have already been revealed), so it is safe for every player.
"""

from __future__ import annotations

from typing import Mapping

from simulation.engine.state import GameState, Role


def voting_record_lines(
    state: GameState, known_roles: Mapping[str, Role], limit: int = 10
) -> list[str]:
    """Compact public vote history, with revealed-traitor cross-references."""
    if not state.vote_history:
        return []

    by_voter: dict[str, list[tuple[int, str]]] = {}
    for record in state.vote_history:
        by_voter.setdefault(record.voter_id, []).append(
            (record.round_number, record.target_id)
        )

    lines: list[str] = []
    for player in sorted(state.alive_players):
        votes = by_voter.get(player, [])
        if votes:
            shown = ", ".join(f"r{round}->{target}" for round, target in votes[-limit:])
            lines.append(f"{player} voted: {shown}")

    for player, role in sorted(known_roles.items()):
        if role is not Role.TRAITOR or player not in state.eliminated_players:
            continue
        voters = sorted(
            {record.voter_id for record in state.vote_history if record.target_id == player}
        )
        if voters:
            lines.append(
                f"{player} was revealed as a traitor; players who voted for them: "
                + ", ".join(voters)
                + " (that vote is a clearing signal for them)."
            )
        own = by_voter.get(player, [])
        if own:
            shown = ", ".join(f"r{round}->{target}" for round, target in own[-limit:])
            lines.append(
                f"{player}'s own votes were: {shown} (players a revealed traitor "
                "targeted are usually cleared)."
            )
    return lines
