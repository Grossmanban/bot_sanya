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


# Incremental migrations, applied in order and tracked with PRAGMA user_version.
MIGRATIONS = [
    # 1: fact volume, reminders, points, settings, scheduled jobs, broadcasts
    """
    ALTER TABLE shifts ADD COLUMN fact_volume INTEGER CHECK (fact_volume IS NULL OR fact_volume >= 0);
    ALTER TABLE shifts ADD COLUMN fact_set_at TEXT;
    ALTER TABLE shifts ADD COLUMN fact_set_by INTEGER;
    ALTER TABLE shifts ADD COLUMN reminded_at TEXT;
    ALTER TABLE shifts ADD COLUMN fact_asked_at TEXT;
    ALTER TABLE users ADD COLUMN notify_reminders INTEGER NOT NULL DEFAULT 1;

    CREATE TABLE IF NOT EXISTS points (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
        capacity   INTEGER CHECK (capacity IS NULL OR capacity > 0),
        active     INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS app_settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS job_runs (
        job      TEXT NOT NULL,
        run_date TEXT NOT NULL,
        ran_at   TEXT NOT NULL,
        PRIMARY KEY (job, run_date)
    );

    CREATE TABLE IF NOT EXISTS broadcasts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        author_id  INTEGER NOT NULL,
        audience   TEXT NOT NULL,
        text       TEXT NOT NULL,
        recipients INTEGER NOT NULL DEFAULT 0,
        delivered  INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """,
    # 2: formatted broadcasts
    """
    ALTER TABLE broadcasts ADD COLUMN html TEXT;
    ALTER TABLE broadcasts ADD COLUMN signed INTEGER NOT NULL DEFAULT 1;
    """,
]


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in filter(None, (s.strip() for s in script.split(";"))):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


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
    migrate(conn)
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
