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
    # 3: plan/fact shortfall decisions and fines
    """
    ALTER TABLE shifts ADD COLUMN shortfall_status TEXT CHECK (shortfall_status IS NULL OR shortfall_status IN ('fined','waived'));
    ALTER TABLE shifts ADD COLUMN shortfall_penalty INTEGER;
    ALTER TABLE shifts ADD COLUMN shortfall_decided_by INTEGER;
    ALTER TABLE shifts ADD COLUMN shortfall_decided_at TEXT;
    """,
]


def _run_script(conn: sqlite3.Connection, script: str) -> None:
    """Run statements one by one inside the caller's transaction (executescript would commit it)."""
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    for statement in filter(None, (s.strip() for s in "\n".join(lines).split(";"))):
        conn.execute(statement)


def _rebuild_users_and_shifts(conn: sqlite3.Connection) -> None:
    """Migration 4: four roles, account state (active/paused/blocked), network, and soft-deleted shifts.

    SQLite can't change CHECK/UNIQUE constraints in place, so both tables are rebuilt
    (the standard create-copy-drop-rename procedure) with every row kept.
    """
    _run_script(conn, """
    CREATE TABLE users_new (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id      INTEGER NOT NULL UNIQUE,
        username         TEXT,
        first_name       TEXT NOT NULL,
        last_name        TEXT NOT NULL,
        status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
        work_status      TEXT NOT NULL DEFAULT 'not_working' CHECK (work_status IN ('working','not_working')),
        role             TEXT NOT NULL DEFAULT 'employee' CHECK (role IN ('employee','trainer','admin')),
        created_at       TEXT NOT NULL,
        decided_at       TEXT,
        decided_by       INTEGER,
        welcome_sent     INTEGER NOT NULL DEFAULT 0,
        notify_reminders INTEGER NOT NULL DEFAULT 1,
        account_state    TEXT NOT NULL DEFAULT 'active' CHECK (account_state IN ('active','paused','blocked')),
        state_reason     TEXT,
        state_changed_at TEXT,
        state_changed_by INTEGER,
        network_id       INTEGER,
        work_status_at   TEXT
    );
    INSERT INTO users_new (id, telegram_id, username, first_name, last_name, status, work_status, role, created_at,
                           decided_at, decided_by, welcome_sent, notify_reminders)
        SELECT id, telegram_id, username, first_name, last_name, status, work_status, role, created_at,
               decided_at, decided_by, welcome_sent, notify_reminders FROM users;
    DROP TABLE users;
    ALTER TABLE users_new RENAME TO users;

    CREATE TABLE shifts_new (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id              INTEGER NOT NULL REFERENCES users(id),
        shift_date           TEXT NOT NULL,
        start_time           TEXT,
        end_time             TEXT,
        volume               INTEGER CHECK (volume IS NULL OR volume > 0),
        location             TEXT,
        status               TEXT NOT NULL CHECK (status IN (
                                'active','cancelled','restored','finally_cancelled','awaiting_approval',
                                'rejected_by_admin','pre_declared_off','withdrawn','removed')),
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
        fact_volume          INTEGER CHECK (fact_volume IS NULL OR fact_volume >= 0),
        fact_set_at          TEXT,
        fact_set_by          INTEGER,
        reminded_at          TEXT,
        fact_asked_at        TEXT,
        shortfall_status     TEXT CHECK (shortfall_status IS NULL OR shortfall_status IN ('fined','waived')),
        shortfall_penalty    INTEGER,
        shortfall_decided_by INTEGER,
        shortfall_decided_at TEXT
    );
    INSERT INTO shifts_new SELECT id, user_id, shift_date, start_time, end_time, volume, location, status, edit_count,
        admin_locked, penalty_amount, penalty_set_by, penalty_set_at, is_urgent, created_at, updated_at, cancelled_at,
        restored_at, finally_cancelled_at, fact_volume, fact_set_at, fact_set_by, reminded_at, fact_asked_at,
        shortfall_status, shortfall_penalty, shortfall_decided_by, shortfall_decided_at FROM shifts;
    DROP TABLE shifts;
    ALTER TABLE shifts_new RENAME TO shifts;
    CREATE INDEX ix_shifts_date_status ON shifts (shift_date, status);
    -- one live record per person per day; withdrawn/removed rows stay as history
    CREATE UNIQUE INDEX ux_shifts_live ON shifts (user_id, shift_date) WHERE status NOT IN ('withdrawn','removed');
    """)
    # people whose access was closed (not declined applications) become blocked accounts
    closed = conn.execute(
        """SELECT u.id FROM users u WHERE u.status = 'rejected' AND EXISTS (
               SELECT 1 FROM event_logs e WHERE e.user_id = u.id AND e.event_type = 'ACCESS_CLOSED')"""
    ).fetchall()
    for (uid,) in closed:
        conn.execute("UPDATE users SET status = 'approved', account_state = 'blocked' WHERE id = ?", (uid,))


MIGRATIONS.append(_rebuild_users_and_shifts)
# 5: archive group, Telegram files
MIGRATIONS.append("""
CREATE TABLE IF NOT EXISTS archive_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tags       TEXT NOT NULL,
    text       TEXT NOT NULL,
    sent       INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    created_at TEXT NOT NULL
);
""")



def _reports(conn: sqlite3.Connection) -> None:
    """6: shift reports (fact + screenshot), review and no-shows (ТЗ 3.2–3.4)."""
    _run_script(conn, """
    CREATE TABLE reports (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        shift_id       INTEGER NOT NULL REFERENCES shifts(id),
        user_id        INTEGER NOT NULL REFERENCES users(id),
        attempt        INTEGER NOT NULL,
        fact_volume    INTEGER NOT NULL CHECK (fact_volume >= 0),
        photo_file_id  TEXT,
        source         TEXT NOT NULL CHECK (source IN ('app','bot','admin','legacy')),
        status         TEXT NOT NULL CHECK (status IN (
                           'pending_review','approved','auto_approved','rejected_penalty','rejected_retry')),
        submitted_by   INTEGER NOT NULL,
        submitted_at   TEXT NOT NULL,
        reviewed_by    INTEGER,
        reviewed_at    TEXT,
        review_comment TEXT,
        penalty_amount INTEGER,
        UNIQUE (shift_id, attempt)
    );
    CREATE INDEX ix_reports_status ON reports (status, submitted_at);
    ALTER TABLE shifts ADD COLUMN report_state TEXT CHECK (report_state IS NULL OR report_state IN (
        'pending_review','approved','needs_fix','rejected','no_show'));
    ALTER TABLE shifts ADD COLUMN report_reminded_at TEXT;
    ALTER TABLE shifts ADD COLUMN no_show_at TEXT;
    ALTER TABLE shifts ADD COLUMN report_note TEXT;
    """)
    # facts entered before reports existed become legacy reports with the decision already made
    rows = conn.execute(
        """SELECT * FROM shifts WHERE fact_volume IS NOT NULL AND status IN ('active','restored')"""
    ).fetchall()
    for s in rows:
        under = s["volume"] is not None and s["fact_volume"] < s["volume"]
        if s["shortfall_status"] == "fined":
            status, state = "rejected_penalty", "rejected"
        elif under and s["shortfall_status"] is None:
            status, state = "pending_review", "pending_review"
        else:
            status, state = "approved", "approved"
        conn.execute(
            """INSERT INTO reports (shift_id, user_id, attempt, fact_volume, source, status, submitted_by, submitted_at,
                                    reviewed_by, reviewed_at, penalty_amount)
               VALUES (?, ?, 1, ?, 'legacy', ?, ?, ?, ?, ?, ?)""",
            (s["id"], s["user_id"], s["fact_volume"], status, s["fact_set_by"] or 0, s["fact_set_at"] or s["updated_at"],
             s["shortfall_decided_by"], s["shortfall_decided_at"], s["shortfall_penalty"]),
        )
        conn.execute("UPDATE shifts SET report_state = ? WHERE id = ?", (state, s["id"]))


MIGRATIONS.append(_reports)



def _day_actuals(conn: sqlite3.Connection) -> None:
    """7: the day's global actual output (ТЗ 3.5) and a report status for «approved: the team met the plan»."""
    _run_script(conn, """
    CREATE TABLE reports_new (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        shift_id       INTEGER NOT NULL REFERENCES shifts(id),
        user_id        INTEGER NOT NULL REFERENCES users(id),
        attempt        INTEGER NOT NULL,
        fact_volume    INTEGER NOT NULL CHECK (fact_volume >= 0),
        photo_file_id  TEXT,
        source         TEXT NOT NULL CHECK (source IN ('app','bot','admin','legacy')),
        status         TEXT NOT NULL CHECK (status IN (
                           'pending_review','approved','auto_approved','approved_team',
                           'rejected_penalty','rejected_retry')),
        submitted_by   INTEGER NOT NULL,
        submitted_at   TEXT NOT NULL,
        reviewed_by    INTEGER,
        reviewed_at    TEXT,
        review_comment TEXT,
        penalty_amount INTEGER,
        UNIQUE (shift_id, attempt)
    );
    INSERT INTO reports_new SELECT * FROM reports;
    DROP TABLE reports;
    ALTER TABLE reports_new RENAME TO reports;
    CREATE INDEX ix_reports_status ON reports (status, submitted_at);

    CREATE TABLE day_actuals (
        day     TEXT PRIMARY KEY,
        actual  INTEGER NOT NULL CHECK (actual >= 0),
        plan    INTEGER NOT NULL,
        met     INTEGER NOT NULL,
        set_by  INTEGER NOT NULL,
        set_at  TEXT NOT NULL
    );
    -- every change of the global actual (ТЗ 3.5 step 4, admin_actions_log)
    CREATE TABLE day_actual_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        day        TEXT NOT NULL,
        old_value  INTEGER,
        new_value  INTEGER NOT NULL,
        plan       INTEGER NOT NULL,
        actor      INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX ix_day_actual_log_day ON day_actual_log (day, id);
    """)


MIGRATIONS.append(_day_actuals)



def _finance(conn: sqlite3.Connection) -> None:
    """8: money ledger (ТЗ модуль 4). Existing fines move here and it becomes the source of truth."""
    _run_script(conn, """
    CREATE TABLE financial_transactions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL REFERENCES users(id),
        type            TEXT NOT NULL CHECK (type IN ('fine','bonus','deduction','payout_offset')),
        amount          INTEGER CHECK (amount IS NULL OR amount > 0),
        status          TEXT NOT NULL CHECK (status IN (
                            'draft','pending_admin_approval','active','paid','cancelled','offset','rejected')),
        reason          TEXT NOT NULL,
        media_file_id   TEXT,
        source          TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual','final_cancel','report','no_show','payout')),
        shift_id        INTEGER REFERENCES shifts(id),
        payout_id       INTEGER,
        event_date      TEXT NOT NULL,
        created_by      INTEGER NOT NULL,
        created_at      TEXT NOT NULL,
        decided_by      INTEGER,
        decided_at      TEXT,
        decision_comment TEXT,
        CHECK (amount IS NOT NULL OR status IN ('draft','cancelled'))
    );
    CREATE INDEX ix_fin_user_status ON financial_transactions (user_id, status);
    CREATE INDEX ix_fin_event_date ON financial_transactions (event_date);
    -- one live fine per shift and reason
    CREATE UNIQUE INDEX ux_fin_shift_source ON financial_transactions (shift_id, source)
        WHERE shift_id IS NOT NULL AND status NOT IN ('cancelled','rejected');
    """)
    for s in conn.execute("SELECT * FROM shifts WHERE status = 'finally_cancelled'").fetchall():
        amount = s["penalty_amount"] or None
        conn.execute(
            """INSERT INTO financial_transactions (user_id, type, amount, status, reason, source, shift_id, event_date,
                                                   created_by, created_at, decided_by, decided_at)
               VALUES (?, 'fine', ?, ?, 'Окончательная отмена смены', 'final_cancel', ?, ?, ?, ?, ?, ?)""",
            (s["user_id"], amount, "active" if amount else "draft", s["id"], s["shift_date"], s["penalty_set_by"] or 0,
             s["penalty_set_at"] or s["finally_cancelled_at"] or s["updated_at"], s["penalty_set_by"], s["penalty_set_at"]),
        )
    for s in conn.execute("SELECT * FROM shifts WHERE shortfall_status = 'fined' AND shortfall_penalty > 0").fetchall():
        no_show = s["report_state"] == "no_show"
        reason = "Неявка: отчёт не сдан" if no_show else f"Не выполнена норма: план {s['volume']}, факт {s['fact_volume']} шт."
        if s["report_note"]:
            reason += f". {s['report_note']}"
        conn.execute(
            """INSERT INTO financial_transactions (user_id, type, amount, status, reason, source, shift_id, event_date,
                                                   created_by, created_at, decided_by, decided_at)
               VALUES (?, 'fine', ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (s["user_id"], s["shortfall_penalty"], reason, "no_show" if no_show else "report", s["id"], s["shift_date"],
             s["shortfall_decided_by"] or 0, s["shortfall_decided_at"] or s["updated_at"], s["shortfall_decided_by"],
             s["shortfall_decided_at"]),
        )


MIGRATIONS.append(_finance)


def _networks(conn: sqlite3.Connection) -> None:
    """9: networks (ТЗ модуль 8). users.network_id already exists; one trainer per network.
    Network ids already given to people get a network row, and their trainer becomes its trainer."""
    _run_script(conn, """
    CREATE TABLE networks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        trainer_id  INTEGER REFERENCES users(id),
        status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
        created_by  INTEGER NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE UNIQUE INDEX ux_networks_trainer ON networks (trainer_id) WHERE trainer_id IS NOT NULL AND status = 'active';
    """)
    for (nid,) in conn.execute("SELECT DISTINCT network_id FROM users WHERE network_id IS NOT NULL").fetchall():
        trainer = conn.execute("SELECT id FROM users WHERE network_id = ? AND role = 'trainer' ORDER BY id LIMIT 1",
                               (nid,)).fetchone()
        conn.execute("INSERT INTO networks (id, name, trainer_id, created_by, created_at) VALUES (?, ?, ?, 0, datetime('now'))",
                     (nid, f"Сеть {nid}", trainer[0] if trainer else None))


MIGRATIONS.append(_networks)


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, step in enumerate(MIGRATIONS[version:], start=version + 1):
        # table rebuilds need foreign keys off; it can only be switched outside a transaction
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if callable(step):
                step(conn)
            else:
                _run_script(conn, step)
            broken = conn.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"migration {number} broke foreign keys: {broken[:3]}")
            conn.execute(f"PRAGMA user_version = {number}")
        except BaseException:
            conn.execute("ROLLBACK")
            conn.execute("PRAGMA foreign_keys = ON")
            raise
        conn.execute("COMMIT")
        conn.execute("PRAGMA foreign_keys = ON")


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
