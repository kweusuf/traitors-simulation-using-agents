"""Repositories: the only place SQL lives (spec section 24).

Each repository takes a `Database` and plain domain values. Domain
logic never imports sqlite3.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from simulation.communication.channels import Message
from simulation.engine.state import GameState
from simulation.persistence.database import Database
from simulation.persistence.event_log import Event


class ExperimentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        experiment_id: str,
        random_seed: int,
        model: str,
        model_parameters: dict[str, Any],
        prompt_version: str = "",
        persona_version: str = "",
        game_rules_version: str = "",
        memory_strategy: str = "",
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO experiments VALUES (?,?,?,?,?,?,?,?)",
            (
                experiment_id,
                random_seed,
                model,
                json.dumps(model_parameters, sort_keys=True),
                prompt_version,
                persona_version,
                game_rules_version,
                memory_strategy,
            ),
        )
        self._db.commit()

    def get(self, experiment_id: str) -> Optional[dict[str, Any]]:
        row = self._db.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["model_parameters"] = json.loads(result["model_parameters"])
        return result


class GameRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        game_id: str,
        experiment_id: str,
        config: dict[str, Any],
        seed: int,
        status: str = "created",
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO games (game_id, experiment_id, config, seed, status) "
            "VALUES (?,?,?,?,?)",
            (game_id, experiment_id, json.dumps(config, sort_keys=True), seed, status),
        )
        self._db.commit()

    def update_status(
        self,
        game_id: str,
        status: str,
        winner: Optional[str] = None,
        winning_team: Optional[str] = None,
    ) -> None:
        self._db.execute(
            "UPDATE games SET status = ?, winner = ?, winning_team = ? WHERE game_id = ?",
            (status, winner, winning_team, game_id),
        )
        self._db.commit()

    def get(self, game_id: str) -> Optional[dict[str, Any]]:
        row = self._db.execute(
            "SELECT * FROM games WHERE game_id = ?", (game_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["config"] = json.loads(result["config"])
        return result

    def list_ids(self) -> list[str]:
        rows = self._db.execute(
            "SELECT game_id FROM games ORDER BY created_at, game_id"
        ).fetchall()
        return [r["game_id"] for r in rows]


class AgentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(
        self,
        game_id: str,
        agent_id: str,
        name: str,
        role: str,
        persona: dict[str, Any],
        model: str = "",
        alive: bool = True,
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO agents VALUES (?,?,?,?,?,?,?)",
            (game_id, agent_id, name, role, json.dumps(persona, sort_keys=True), model, int(alive)),
        )
        self._db.commit()

    def set_alive(self, game_id: str, agent_id: str, alive: bool) -> None:
        self._db.execute(
            "UPDATE agents SET alive = ? WHERE game_id = ? AND agent_id = ?",
            (int(alive), game_id, agent_id),
        )
        self._db.commit()

    def get(self, game_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM agents WHERE game_id = ? ORDER BY agent_id", (game_id,)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["persona"] = json.loads(item["persona"])
            result.append(item)
        return result


class MemoryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(
        self, game_id: str, agent_id: str, round_number: int, kind: str, content: str, sequence: int
    ) -> None:
        self._db.execute(
            "INSERT INTO agent_memories (game_id, agent_id, round, kind, content, sequence) "
            "VALUES (?,?,?,?,?,?)",
            (game_id, agent_id, round_number, kind, content, sequence),
        )
        self._db.commit()

    def get(self, game_id: str, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM agent_memories WHERE game_id = ? AND agent_id = ? "
            "ORDER BY sequence DESC, id DESC LIMIT ?",
            (game_id, agent_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


class MessageRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, game_id: str, message: Message) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?)",
            (
                message.message_id,
                game_id,
                message.round_number,
                message.phase,
                message.sender_id,
                json.dumps(message.recipients),
                message.channel.value,
                message.content,
            ),
        )
        self._db.commit()

    def get_public(self, game_id: str) -> list[Message]:
        rows = self._db.execute(
            "SELECT * FROM messages WHERE game_id = ? AND channel = 'public' "
            "ORDER BY rowid",
            (game_id,),
        ).fetchall()
        return [self._to_message(r) for r in rows]

    def get_visible_to(self, game_id: str, agent_id: str) -> list[Message]:
        """Only messages the agent sent, receives, or that are public/system.

        Structural privacy at the storage-read layer: a non-recipient
        never gets another agent's private message out of the database.
        """
        rows = self._db.execute(
            "SELECT * FROM messages WHERE game_id = ? AND "
            "(channel IN ('public','system') OR sender_id = ? OR recipients LIKE ?) "
            "ORDER BY rowid",
            (game_id, agent_id, f'%"{agent_id}"%'),
        ).fetchall()
        return [self._to_message(r) for r in rows]

    @staticmethod
    def _to_message(row: Any) -> Message:
        return Message(
            message_id=row["message_id"],
            sender_id=row["sender_id"],
            recipients=json.loads(row["recipients"]),
            channel=row["channel"],
            content=row["content"],
            round_number=row["round"],
            phase=row["phase"],
        )


class EventRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(self, game_id: str, event: Event) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?)",
            (
                game_id,
                event.event_id,
                event.sequence,
                event.round,
                event.phase,
                event.type.value,
                event.actor,
                json.dumps(event.targets),
                json.dumps(event.payload, sort_keys=True),
            ),
        )
        self._db.commit()

    def get_all(self, game_id: str) -> list[Event]:
        rows = self._db.execute(
            "SELECT * FROM events WHERE game_id = ? ORDER BY sequence", (game_id,)
        ).fetchall()
        return [
            Event(
                event_id=r["event_id"],
                game_id=r["game_id"],
                sequence=r["sequence"],
                round=r["round"],
                phase=r["phase"],
                type=r["type"],
                actor=r["actor"],
                targets=json.loads(r["targets"]),
                payload=json.loads(r["payload"]),
            )
            for r in rows
        ]


class VoteRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def replace_round(self, game_id: str, round_number: int, votes: dict[str, str]) -> None:
        self._db.execute(
            "DELETE FROM votes WHERE game_id = ? AND round = ?", (game_id, round_number)
        )
        for voter, target in votes.items():
            self._db.execute(
                "INSERT INTO votes VALUES (?,?,?,?)", (game_id, round_number, voter, target)
            )
        self._db.commit()

    def get_round(self, game_id: str, round_number: int) -> dict[str, str]:
        rows = self._db.execute(
            "SELECT voter_id, target_id FROM votes WHERE game_id = ? AND round = ?",
            (game_id, round_number),
        ).fetchall()
        return {r["voter_id"]: r["target_id"] for r in rows}


class EliminationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, game_id: str, round_number: int, player_id: str, method: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO eliminations VALUES (?,?,?,?)",
            (game_id, round_number, player_id, method),
        )
        self._db.commit()

    def get(self, game_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM eliminations WHERE game_id = ? ORDER BY round, player_id",
            (game_id,),
        ).fetchall()
        return [dict(r) for r in rows]


class RelationshipRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(
        self,
        game_id: str,
        agent_id: str,
        target_id: str,
        trust: float,
        suspicion: float,
        threat: float,
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO relationships VALUES (?,?,?,?,?,?)",
            (game_id, agent_id, target_id, trust, suspicion, threat),
        )
        self._db.commit()

    def get(self, game_id: str, agent_id: str) -> dict[str, dict[str, float]]:
        rows = self._db.execute(
            "SELECT target_id, trust, suspicion, threat FROM relationships "
            "WHERE game_id = ? AND agent_id = ?",
            (game_id, agent_id),
        ).fetchall()
        return {
            r["target_id"]: {
                "trust": r["trust"],
                "suspicion": r["suspicion"],
                "threat": r["threat"],
            }
            for r in rows
        }


class BeliefRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(
        self,
        game_id: str,
        agent_id: str,
        target_id: str,
        suspected_role: Optional[str],
        confidence: float,
        updated_round: int,
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO beliefs VALUES (?,?,?,?,?,?)",
            (game_id, agent_id, target_id, suspected_role, confidence, updated_round),
        )
        self._db.commit()

    def get(self, game_id: str, agent_id: str) -> dict[str, dict[str, Any]]:
        rows = self._db.execute(
            "SELECT target_id, suspected_role, confidence, updated_round "
            "FROM beliefs WHERE game_id = ? AND agent_id = ?",
            (game_id, agent_id),
        ).fetchall()
        return {
            r["target_id"]: {
                "suspected_role": r["suspected_role"],
                "confidence": r["confidence"],
                "updated_round": r["updated_round"],
            }
            for r in rows
        }


class SnapshotRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, state: GameState) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)",
            (state.game_id, state.round_number, state.phase.value, state.model_dump_json()),
        )
        self._db.commit()

    def get(self, game_id: str, round_number: int, phase: str) -> Optional[GameState]:
        row = self._db.execute(
            "SELECT state FROM snapshots WHERE game_id = ? AND round = ? AND phase = ?",
            (game_id, round_number, phase),
        ).fetchone()
        if row is None:
            return None
        return GameState.model_validate_json(row["state"])

    def list_rounds(self, game_id: str) -> list[tuple[int, str]]:
        rows = self._db.execute(
            "SELECT round, phase FROM snapshots WHERE game_id = ? ORDER BY round",
            (game_id,),
        ).fetchall()
        return [(r["round"], r["phase"]) for r in rows]
