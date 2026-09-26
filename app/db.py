"""SQLite storage. The audit trail's guarantees are enforced here, not just in Python."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .stages import TRANSITIONS

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "pipeline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
    email       TEXT UNIQUE,
    created_at  TEXT NOT NULL
);

-- The state machine as data. The insert trigger below refuses anything not listed here.
CREATE TABLE IF NOT EXISTS allowed_transitions (
    from_stage  TEXT,
    action      TEXT NOT NULL,
    to_stage    TEXT NOT NULL
);

-- The audit trail. Current stage is derived from the latest row; nothing is ever overwritten.
CREATE TABLE IF NOT EXISTS stage_events (
    id            INTEGER PRIMARY KEY,
    candidate_id  INTEGER NOT NULL REFERENCES candidates(id),
    seq           INTEGER NOT NULL,
    action        TEXT NOT NULL,
    from_stage    TEXT,
    to_stage      TEXT NOT NULL,
    at            TEXT NOT NULL,
    note          TEXT CHECK (note IS NULL OR length(note) <= 500),
    UNIQUE (candidate_id, seq)
);
CREATE INDEX IF NOT EXISTS stage_events_by_stage ON stage_events (to_stage, at);

-- Append-only: history can't be edited or removed, by the app or by a stray SQL statement.
CREATE TRIGGER IF NOT EXISTS stage_events_no_update BEFORE UPDATE ON stage_events
BEGIN SELECT RAISE(ABORT, 'audit trail is append-only: events cannot be changed'); END;

CREATE TRIGGER IF NOT EXISTS stage_events_no_delete BEFORE DELETE ON stage_events
BEGIN SELECT RAISE(ABORT, 'audit trail is append-only: events cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS candidates_no_update BEFORE UPDATE ON candidates
BEGIN SELECT RAISE(ABORT, 'candidate records are immutable'); END;

CREATE TRIGGER IF NOT EXISTS candidates_no_delete BEFORE DELETE ON candidates
BEGIN SELECT RAISE(ABORT, 'candidates cannot be deleted: their history is permanent'); END;

CREATE TRIGGER IF NOT EXISTS allowed_transitions_no_update BEFORE UPDATE ON allowed_transitions
BEGIN SELECT RAISE(ABORT, 'the pipeline rules are fixed'); END;

CREATE TRIGGER IF NOT EXISTS allowed_transitions_no_delete BEFORE DELETE ON allowed_transitions
BEGIN SELECT RAISE(ABORT, 'the pipeline rules are fixed'); END;

-- Every new event must continue the candidate's chain: next seq, starting from the current
-- stage, via a legal transition, and not dated earlier than the event before it.
CREATE TRIGGER IF NOT EXISTS stage_events_valid_transition BEFORE INSERT ON stage_events
BEGIN
    SELECT RAISE(ABORT, 'event seq must directly follow the previous event')
    WHERE NEW.seq != COALESCE(
        (SELECT MAX(seq) FROM stage_events WHERE candidate_id = NEW.candidate_id), 0) + 1;

    SELECT RAISE(ABORT, 'from_stage must be the candidate''s current stage')
    WHERE NEW.from_stage IS NOT (
        SELECT to_stage FROM stage_events
        WHERE candidate_id = NEW.candidate_id AND seq = NEW.seq - 1);

    SELECT RAISE(ABORT, 'transition is not allowed by the pipeline')
    WHERE NOT EXISTS (
        SELECT 1 FROM allowed_transitions
        WHERE from_stage IS NEW.from_stage AND action = NEW.action AND to_stage = NEW.to_stage);

    SELECT RAISE(ABORT, 'an event cannot be dated before the previous event')
    WHERE NEW.at < COALESCE(
        (SELECT at FROM stage_events WHERE candidate_id = NEW.candidate_id AND seq = NEW.seq - 1), '');
END;
"""


def resolve_path(path: str | os.PathLike | None = None) -> Path:
    return Path(path or os.environ.get("HIRING_DB") or DEFAULT_PATH)


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    db_path = resolve_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM allowed_transitions").fetchone()[0] == 0:
        conn.executemany("INSERT INTO allowed_transitions VALUES (?, ?, ?)", TRANSITIONS)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """BEGIN IMMEDIATE takes the write lock up front, so read-check-write is serialised."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
