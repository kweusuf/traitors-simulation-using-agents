"""Unit tests for persistence: JSONL event log, SQLite repositories, snapshots."""

from __future__ import annotations

import pytest

from simulation.communication.channels import Channel, Message
from simulation.engine.state import GamePhase, GameState, PlayerState, Role
from simulation.persistence.database import Database
from simulation.persistence.event_log import Event, EventType
from simulation.persistence.jsonl import EventLog
from simulation.persistence.repositories import (
    AgentRepository,
    BeliefRepository,
    EliminationRepository,
    EventRepository,
    ExperimentRepository,
    GameRepository,
    MemoryRepository,
    MessageRepository,
    RelationshipRepository,
    SnapshotRepository,
    VoteRepository,
)


def _event(seq: int, type_: EventType = EventType.PHASE_STARTED) -> Event:
    return Event(
        event_id=f"evt-{seq}",
        game_id="game-001",
        sequence=seq,
        round=1,
        phase="voting",
        type=type_,
        actor="alice",
        targets=["bob"],
        payload={"n": seq},
    )


def test_jsonl_is_append_only_and_round_trips(tmp_path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.append(_event(0))
    log.append_many([_event(1, EventType.VOTE_CAST), _event(2, EventType.PLAYER_ELIMINATED)])
    log.append(_event(3, EventType.GAME_WON))

    events = log.read_all()
    assert [e.sequence for e in events] == [0, 1, 2, 3]
    assert events[2].type is EventType.PLAYER_ELIMINATED

    # Re-opening and appending must not truncate prior content.
    log2 = EventLog(tmp_path / "events.jsonl")
    log2.append(_event(4))
    assert [e.sequence for e in log2.read_all()] == [0, 1, 2, 3, 4]


def test_jsonl_corrupt_line_raises(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(_event(0))
    with path.open("a") as fh:
        fh.write("{not json\n")
    with pytest.raises(ValueError, match="corrupt event log"):
        log.read_all()


def test_event_repository_orders_by_sequence() -> None:
    with Database() as db:
        repo = EventRepository(db)
        repo.append("game-001", _event(2))
        repo.append("game-001", _event(0, EventType.GAME_STARTED))
        repo.append("game-001", _event(1, EventType.ROLE_ASSIGNED))
        repo.append("game-002", _event(0))
        events = repo.get_all("game-001")
        assert [e.sequence for e in events] == [0, 1, 2]
        assert events[0].type is EventType.GAME_STARTED
        assert repo.get_all("game-002")[0].game_id == "game-002"


def test_snapshot_round_trip() -> None:
    state = GameState(game_id="game-001", round_number=3, phase=GamePhase.VOTING)
    state.players["alice"] = PlayerState(player_id="alice", name="Alice")
    state.alive_players = {"alice"}
    state.roles = {"alice": Role.FAITHFUL}
    with Database() as db:
        repo = SnapshotRepository(db)
        repo.create(state)
        restored = repo.get("game-001", 3, "voting")
        assert restored is not None
        assert restored == state
        assert repo.list_rounds("game-001") == [(3, "voting")]
        assert repo.get("game-001", 9, "voting") is None


def test_message_reads_respect_recipients() -> None:
    private = Message(
        message_id="msg-1",
        sender_id="eve",
        recipients=["alice"],
        channel=Channel.PRIVATE,
        content="meet me at night",
    )
    public = Message(
        message_id="msg-2",
        sender_id="eve",
        recipients=[],
        channel=Channel.PUBLIC,
        content="I trust bob",
    )
    with Database() as db:
        repo = MessageRepository(db)
        repo.insert("game-001", private)
        repo.insert("game-001", public)

        alice_visible = repo.get_visible_to("game-001", "alice")
        assert {m.message_id for m in alice_visible} == {"msg-1", "msg-2"}

        # bob is not a recipient: he must never see the private message.
        bob_visible = repo.get_visible_to("game-001", "bob")
        assert {m.message_id for m in bob_visible} == {"msg-2"}

        # sender sees own message
        eve_visible = repo.get_visible_to("game-001", "eve")
        assert {m.message_id for m in eve_visible} == {"msg-1", "msg-2"}


def test_game_experiment_agent_repositories() -> None:
    with Database() as db:
        ExperimentRepository(db).create(
            "exp-1",
            random_seed=42,
            model="ollama/gpt-oss:20b",
            model_parameters={"temperature": 0.7},
            prompt_version="v1",
            memory_strategy="recent_buffer",
        )
        exp = ExperimentRepository(db).get("exp-1")
        assert exp["model_parameters"] == {"temperature": 0.7}

        GameRepository(db).create("game-001", "exp-1", {"players": 6}, seed=42)
        GameRepository(db).update_status("game-001", "finished", winner="faithful", winning_team="faithful")
        game = GameRepository(db).get("game-001")
        assert game["status"] == "finished"
        assert game["config"] == {"players": 6}
        assert GameRepository(db).list_ids() == ["game-001"]

        agents = AgentRepository(db)
        agents.upsert("game-001", "alice", "Alice", "faithful", {"analytical": 0.9})
        agents.upsert("game-001", "bob", "Bob", "traitor", {"trust": 0.2}, alive=True)
        agents.set_alive("game-001", "bob", False)
        rows = agents.get("game-001")
        assert rows[0]["persona"] == {"analytical": 0.9}
        assert rows[1]["alive"] == 0


def test_votes_eliminations_beliefs_relationships_memories() -> None:
    with Database() as db:
        votes = VoteRepository(db)
        votes.replace_round("game-001", 1, {"alice": "bob", "bob": "alice"})
        assert votes.get_round("game-001", 1) == {"alice": "bob", "bob": "alice"}
        # Replacement must overwrite, not duplicate.
        votes.replace_round("game-001", 1, {"alice": "charlie"})
        assert votes.get_round("game-001", 1) == {"alice": "charlie"}

        elims = EliminationRepository(db)
        elims.add("game-001", 1, "charlie", "vote")
        elims.add("game-001", 2, "david", "night")
        assert [e["player_id"] for e in elims.get("game-001")] == ["charlie", "david"]

        beliefs = BeliefRepository(db)
        beliefs.upsert("game-001", "alice", "bob", "traitor", 0.7, updated_round=2)
        assert beliefs.get("game-001", "alice")["bob"]["confidence"] == 0.7

        rels = RelationshipRepository(db)
        rels.upsert("game-001", "alice", "bob", trust=0.72, suspicion=0.1, threat=0.55)
        assert rels.get("game-001", "alice")["bob"]["trust"] == 0.72

        memories = MemoryRepository(db)
        memories.append("game-001", "alice", 2, "event", "bob was accused", sequence=5)
        memories.append("game-001", "alice", 1, "event", "game started", sequence=1)
        items = memories.get("game-001", "alice")
        assert [m["content"] for m in items] == ["game started", "bob was accused"]
