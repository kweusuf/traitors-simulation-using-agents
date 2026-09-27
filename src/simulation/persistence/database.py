"""SQLite database setup and schema (spec section 24).

Repositories live in `simulation.persistence.repositories`; this module
only owns connection handling and DDL. Domain logic never talks to SQL
directly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id   TEXT PRIMARY KEY,
    random_seed     INTEGER,
    model           TEXT,
    model_parameters TEXT,          -- JSON
    prompt_version  TEXT,
    persona_version TEXT,
    game_rules_version TEXT,
    memory_strategy TEXT
);

CREATE TABLE IF NOT EXISTS games (
    game_id        TEXT PRIMARY KEY,
    experiment_id  TEXT REFERENCES experiments(experiment_id),
    config         TEXT,            -- JSON
    seed           INTEGER,
    status         TEXT NOT NULL DEFAULT 'created',
    winner         TEXT,
    winning_team   TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agents (
    game_id    TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    name       TEXT NOT NULL,
    role       TEXT,
    persona    TEXT,                -- JSON personality traits
    model      TEXT,
    alive      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (game_id, agent_id)
);

CREATE TABLE IF NOT EXISTS agent_memories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id   TEXT NOT NULL,
    agent_id  TEXT NOT NULL,
    round     INTEGER NOT NULL DEFAULT 0,
    kind      TEXT NOT NULL,
    content   TEXT NOT NULL,
    sequence  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    message_id  TEXT PRIMARY KEY,
    game_id     TEXT NOT NULL,
    round       INTEGER NOT NULL DEFAULT 0,
    phase       TEXT NOT NULL DEFAULT '',
    sender_id   TEXT NOT NULL,
    recipients  TEXT NOT NULL,      -- JSON list
    channel     TEXT NOT NULL,
    content     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    game_id   TEXT NOT NULL,
    event_id  TEXT NOT NULL,
    sequence  INTEGER NOT NULL,
    round     INTEGER NOT NULL DEFAULT 0,
    phase     TEXT NOT NULL DEFAULT '',
    type      TEXT NOT NULL,
    actor     TEXT,
    targets   TEXT NOT NULL DEFAULT '[]',  -- JSON list
    payload   TEXT NOT NULL DEFAULT '{}',  -- JSON object
    PRIMARY KEY (game_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_events_game_sequence ON events (game_id, sequence);

CREATE TABLE IF NOT EXISTS votes (
    game_id   TEXT NOT NULL,
    round     INTEGER NOT NULL,
    voter_id  TEXT NOT NULL,
    target_id TEXT NOT NULL,
    weight    INTEGER NOT NULL DEFAULT 1,  -- 2 while a dagger doubles it
    PRIMARY KEY (game_id, round, voter_id)
);

CREATE TABLE IF NOT EXISTS eliminations (
    game_id   TEXT NOT NULL,
    round     INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    method    TEXT NOT NULL,        -- 'vote' | 'night'
    PRIMARY KEY (game_id, round, player_id)
);

CREATE TABLE IF NOT EXISTS relationships (
    game_id   TEXT NOT NULL,
    agent_id  TEXT NOT NULL,
    target_id TEXT NOT NULL,
    trust     REAL NOT NULL DEFAULT 0.0,
    suspicion REAL NOT NULL DEFAULT 0.0,
    threat    REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (game_id, agent_id, target_id)
);

CREATE TABLE IF NOT EXISTS beliefs (
    game_id       TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    suspected_role TEXT,
    confidence    REAL NOT NULL DEFAULT 0.0,
    updated_round INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, agent_id, target_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    game_id TEXT NOT NULL,
    round   INTEGER NOT NULL,
    phase   TEXT NOT NULL,
    state   TEXT NOT NULL,          -- JSON GameState
    PRIMARY KEY (game_id, round, phase)
);
"""


class Database:
    """Thin wrapper around one sqlite3 connection."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Bring a database created before this schema version up to date.

        `CREATE TABLE IF NOT EXISTS` never alters an existing table, and
        `runs/simulation.db` may predate the vote weight column, so add
        it here once instead of failing on the first doubled vote.
        """
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(votes)")}
        if columns and "weight" not in columns:
            self._conn.execute(
                "ALTER TABLE votes ADD COLUMN weight INTEGER NOT NULL DEFAULT 1"
            )

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
