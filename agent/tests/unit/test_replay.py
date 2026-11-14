"""Unit tests for replay, inspection, and transcript rendering (spec 25, 44)."""

from __future__ import annotations

import json
from pathlib import Path

from simulation.engine.state import Role
from simulation.experiments.config import load_config
from simulation.experiments.replay import (
    ReplayState,
    build_transcript,
    load_events,
    render_transcript,
)
from simulation.experiments.runner import GameRunner
from simulation.persistence.database import Database


def play(tmp_path: Path, seed: int = 42, game_id: str = "game-001"):
    config = load_config("configs/traitors/basic.yaml")
    config.llm.provider = "fake"
    runner = GameRunner(
        config,
        runs_dir=tmp_path,
        db=Database(),
        personas_dir=Path("configs/personas"),
    )
    return runner.run(game_id=game_id, seed=seed)


def test_replay_reconstructs_final_state(tmp_path) -> None:
    result = play(tmp_path)
    state = ReplayState.from_events(result.events)

    saved = json.loads((result.run_dir / "game.json").read_text())
    assert state.game_id == "game-001"
    assert sorted(state.players) == sorted(saved["players"])
    assert {pid: role.value for pid, role in state.roles.items()} == saved["roles"]
    assert sorted(state.alive) == sorted(saved["alive_players"])
    assert state.winner == saved["winner"]
    assert state.rounds == saved["round_number"]
    assert state.rejected_actions == 0
    # Public + private message events match the saved transcript.
    transcript = json.loads((result.run_dir / "transcript.json").read_text())
    assert len(state.messages) == len(transcript["messages"])


def test_replay_is_deterministic(tmp_path) -> None:
    first = play(tmp_path / "a", seed=9)
    second = play(tmp_path / "b", seed=9)
    assert ReplayState.from_events(first.events) == ReplayState.from_events(
        second.events
    )


def test_load_events_reads_run_directory(tmp_path) -> None:
    result = play(tmp_path)
    events = load_events(result.run_dir / "events.jsonl")
    assert events == result.events


# ----------------------------------------------------------------------
# Visibility on reconstructed views (spec sections 10 and 25)
# ----------------------------------------------------------------------


def mid_game_view(tmp_path: Path) -> ReplayState:
    """Replay only up to the point before the winner is revealed."""
    result = play(tmp_path)
    cut = next(
        i
        for i, e in enumerate(result.events)
        if e.type.value == "GAME_WON"
    )
    return ReplayState.from_events(result.events[:cut])


def test_mid_game_projection_hides_living_traitor_roles(tmp_path) -> None:
    state = mid_game_view(tmp_path)
    traitors = [pid for pid, role in state.roles.items() if role is Role.TRAITOR]

    faithful = next(
        pid for pid, role in state.roles.items() if role is Role.FAITHFUL
    )
    faithful_view = state.project(faithful)
    for traitor in traitors:
        if traitor in faithful_view.alive_players:
            assert traitor not in faithful_view.known_roles, (
                f"{faithful} learned a living traitor's role"
            )

    traitor_view = state.project(traitors[0])
    for teammate in traitors:
        assert traitor_view.known_roles[teammate] is Role.TRAITOR


def test_projection_reveals_eliminated_roles(tmp_path) -> None:
    state = mid_game_view(tmp_path)
    assert state.eliminated, "test needs at least one elimination"
    observer = state.players[0]
    view = state.project(observer)
    for record in state.eliminated:
        assert record.player in view.known_roles
        assert sorted(view.eliminated_players) == sorted(
            {e.player for e in state.eliminated}
        )


def test_projection_keeps_private_messages_private(tmp_path) -> None:
    state = mid_game_view(tmp_path)
    private = [m for m in state.messages if m.channel.value == "private"]
    assert private, "test needs at least one private message"

    for message in private:
        viewers = {message.sender_id, *message.recipients}
        for pid in state.players:
            if pid in viewers:
                continue
            assert message not in state.project(pid).private_conversations

    sender = private[0].sender_id
    assert private[0] in state.project(sender).private_conversations
    assert state.project(sender).private_conversations


def test_projection_rejects_unknown_agent(tmp_path) -> None:
    state = mid_game_view(tmp_path)
    try:
        state.project("mallory")
    except ValueError as exc:
        assert "unknown agent" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown agent")


# ----------------------------------------------------------------------
# Transcripts
# ----------------------------------------------------------------------


def test_transcript_artifacts_match_events(tmp_path) -> None:
    result = play(tmp_path)
    structured = build_transcript(result.events)
    assert structured["winner"] == result.winner
    assert structured["messages"]
    assert len(structured["eliminations"]) >= 1

    narrative = render_transcript(result.events)
    assert "Round 1" in narrative
    assert "Game over. Winner:" in narrative
    assert "[public]" in narrative
    assert "[private]" in narrative

    saved = (result.run_dir / "transcript.txt").read_text()
    assert saved == narrative
