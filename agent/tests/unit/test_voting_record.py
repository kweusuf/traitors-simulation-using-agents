"""Tests for the public voting record used by the faithful enhancements."""

from __future__ import annotations

from simulation.agents.voting_record import voting_record_lines
from simulation.engine.state import GameState, PlayerState, Role, VoteRecord


def _state() -> GameState:
    state = GameState(game_id="g")
    for pid in ("a", "b", "c"):
        state.players[pid] = PlayerState(player_id=pid, name=pid)
        state.alive_players.add(pid)
    # x was banished (revealed) as a traitor, so the room knows the role.
    state.players["x"] = PlayerState(
        player_id="x", name="x", alive=False, elimination_method="vote"
    )
    state.eliminated_players.add("x")
    state.roles = {
        "a": Role.FAITHFUL,
        "b": Role.FAITHFUL,
        "c": Role.TRAITOR,
        "x": Role.TRAITOR,
    }
    state.vote_history = [
        VoteRecord(round_number=1, voter_id="a", target_id="x"),
        VoteRecord(round_number=1, voter_id="b", target_id="c"),
        VoteRecord(round_number=1, voter_id="x", target_id="a"),
    ]
    return state


def test_empty_history_is_empty() -> None:
    state = _state()
    state.vote_history = []
    assert voting_record_lines(state, {}) == []


def test_vote_history_and_revealed_traitor_cross_reference() -> None:
    state = _state()
    lines = voting_record_lines(state, {"c": Role.TRAITOR, "x": Role.TRAITOR})
    text = "\n".join(lines)
    assert "a voted: r1->x" in text
    assert "b voted: r1->c" in text
    assert "x was revealed as a traitor" in text
    assert "players who voted for them: a" in text
    assert "x's own votes were: r1->a" in text


def test_living_traitors_are_not_revealed() -> None:
    state = _state()
    lines = voting_record_lines(state, {"c": Role.TRAITOR, "x": Role.TRAITOR})
    assert not any("c was revealed" in line for line in lines)
