"""SQLite storage.

The whole app (bot + API) runs in one asyncio process. All DB calls are
synchronous and never await inside a transaction, so each service function
runs atomically with respect to other coroutines. `transaction()` adds
BEGIN IMMEDIATE so the file stays consistent even if a second process
(e.g. a backup script) touches it.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL UNIQUE,
    username        TEXT,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected')),
    work_status     TEXT NOT NULL DEFAULT 'not_working'
                    CHECK (work_status IN ('working','not_working')),
    role            TEXT NOT NULL DEFAULT 'employee'
                    CHECK (role IN ('employee','admin')),
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    decided_by      INTEGER,
    welcome_sent    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shifts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(id),
    shift_date           TEXT NOT NULL,
    start_time           TEXT,
    end_time             TEXT,
    volume               INTEGER CHECK (volume IS NULL OR volume > 0),
    location             TEXT,
    status               TEXT NOT NULL CHECK (status IN (
                            'active','cancelled','restored','finally_cancelled',
                            'awaiting_approval','rejected_by_admin','pre_declared_off')),
    edit_count           INTEGER NOT NULL DEFAULT 0,
    admin_locked         INTEGER NOT NULL DEFAULT 0,
    penalty_amount       INTEGER,
    penalty_set_by       INTEGER,
    penalty_set_at       TEXT,
    is_urgent            INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    cancelled_at         TEXT,
    restored_at          TEXT,
    finally_cancelled_at TEXT,
    UNIQUE (user_id, shift_date)
);
CREATE INDEX IF NOT EXISTS ix_shifts_date_status ON shifts (shift_date, status);

CREATE TABLE IF NOT EXISTS event_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id         INTEGER REFERENCES shifts(id) ON DELETE SET NULL,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    actor_id         INTEGER NOT NULL,
    event_type       TEXT NOT NULL,
    old_value        TEXT,
    new_value        TEXT,
    idempotency_key  TEXT NOT NULL UNIQUE,
    description      TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_event_logs_user ON event_logs (user_id, created_at);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
