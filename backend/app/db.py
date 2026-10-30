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


# 10: SBP payment profile with an audit trail, payout registry (ТЗ модули 9–10)
MIGRATIONS.append("""
CREATE TABLE payment_profiles (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id),
    phone       TEXT NOT NULL,
    bank        TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  INTEGER NOT NULL
);
CREATE TABLE payment_profile_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    old_value   TEXT,
    new_value   TEXT NOT NULL,
    actor       INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE payout_batches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    period_from         TEXT NOT NULL,
    period_to           TEXT NOT NULL,
    network_id          INTEGER,
    rates               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'processed' CHECK (status IN ('processed')),
    proof_media         TEXT NOT NULL DEFAULT '[]',
    deduction_media     TEXT NOT NULL DEFAULT '[]',
    total               INTEGER NOT NULL,
    created_by          INTEGER NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE TABLE payout_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id         INTEGER NOT NULL REFERENCES payout_batches(id),
    user_id          INTEGER NOT NULL REFERENCES users(id),
    role             TEXT NOT NULL,
    network          TEXT,
    phone            TEXT,
    bank             TEXT,
    recipient        TEXT,
    personal_volume  INTEGER NOT NULL,
    team_volume      INTEGER NOT NULL,
    base_calc        INTEGER NOT NULL,
    base             INTEGER NOT NULL,
    bonuses          INTEGER NOT NULL,
    fines            INTEGER NOT NULL,
    deductions       INTEGER NOT NULL,
    total            INTEGER NOT NULL,
    carried_debt     INTEGER NOT NULL
);
CREATE INDEX ix_payout_items_batch ON payout_items (batch_id);
""")


# 11: broadcasts with media, audiences by network / people, read receipts (ТЗ модуль 6)
MIGRATIONS.append("""
ALTER TABLE broadcasts ADD COLUMN media_file_id TEXT;
ALTER TABLE broadcasts ADD COLUMN require_ack INTEGER NOT NULL DEFAULT 0;
ALTER TABLE broadcasts ADD COLUMN network_id INTEGER;
ALTER TABLE broadcasts ADD COLUMN people TEXT;
CREATE TABLE broadcast_deliveries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_id   INTEGER NOT NULL REFERENCES broadcasts(id),
    user_id        INTEGER NOT NULL REFERENCES users(id),
    message_id     INTEGER,
    delivered      INTEGER NOT NULL DEFAULT 0,
    sent_at        TEXT NOT NULL,
    read_at        TEXT,
    reminded_at    TEXT,
    reminders      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (broadcast_id, user_id)
);
""")


# 12: goals (ТЗ модуль 5)
MIGRATIONS.append("""
CREATE TABLE goals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    description  TEXT,
    kind         TEXT NOT NULL CHECK (kind IN ('personal','team','network')),
    mode         TEXT NOT NULL DEFAULT 'shared' CHECK (mode IN ('shared','each')),
    target       INTEGER NOT NULL CHECK (target > 0),
    date_from    TEXT NOT NULL,
    date_to      TEXT NOT NULL,
    user_id      INTEGER REFERENCES users(id),
    network_id   INTEGER REFERENCES networks(id),
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','archived')),
    created_by   INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE goal_participants (
    goal_id      INTEGER NOT NULL REFERENCES goals(id),
    user_id      INTEGER NOT NULL REFERENCES users(id),
    joined_at    TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (goal_id, user_id)
);
""")


# 13: live events with a leaderboard (ТЗ модуль 7)
MIGRATIONS.append("""
CREATE TABLE events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT,
    prize         TEXT,
    mode          TEXT NOT NULL CHECK (mode IN ('first_to_reach','accumulative')),
    target        INTEGER,
    max_winners   INTEGER NOT NULL DEFAULT 1 CHECK (max_winners > 0),
    max_attempts  INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    starts_at     TEXT NOT NULL,
    ends_at       TEXT NOT NULL,
    buffer_min    INTEGER NOT NULL DEFAULT 15,
    network_id    INTEGER REFERENCES networks(id),
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','finished','cancelled')),
    created_by    INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE TABLE event_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       INTEGER NOT NULL REFERENCES events(id),
    user_id        INTEGER NOT NULL REFERENCES users(id),
    attempt        INTEGER NOT NULL,
    value          INTEGER NOT NULL CHECK (value > 0),
    photo_file_id  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending_review' CHECK (status IN ('pending_review','confirmed','rejected')),
    reason         TEXT,
    submitted_at   TEXT NOT NULL,
    reviewed_by    INTEGER,
    reviewed_at    TEXT
);
CREATE INDEX ix_event_entries_event ON event_entries (event_id, status);
""")


def _attendance(conn: sqlite3.Connection) -> None:
    """14: schedule violations (ТЗ 12.4). The ledger is rebuilt only to allow source = 'attendance'
    (SQLite can't change a CHECK in place); ids, data and the id counter stay as they were."""
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'financial_transactions'").fetchone()
    _run_script(conn, """
    CREATE TABLE financial_transactions_new (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL REFERENCES users(id),
        type            TEXT NOT NULL CHECK (type IN ('fine','bonus','deduction','payout_offset')),
        amount          INTEGER CHECK (amount IS NULL OR amount > 0),
        status          TEXT NOT NULL CHECK (status IN (
                            'draft','pending_admin_approval','active','paid','cancelled','offset','rejected')),
        reason          TEXT NOT NULL,
        media_file_id   TEXT,
        source          TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual','final_cancel','report','no_show','payout','attendance')),
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
    INSERT INTO financial_transactions_new SELECT id, user_id, type, amount, status, reason, media_file_id, source,
        shift_id, payout_id, event_date, created_by, created_at, decided_by, decided_at, decision_comment
        FROM financial_transactions;
    DROP TABLE financial_transactions;
    ALTER TABLE financial_transactions_new RENAME TO financial_transactions;
    CREATE INDEX ix_fin_user_status ON financial_transactions (user_id, status);
    CREATE INDEX ix_fin_event_date ON financial_transactions (event_date);
    CREATE UNIQUE INDEX ux_fin_shift_source ON financial_transactions (shift_id, source)
        WHERE shift_id IS NOT NULL AND status NOT IN ('cancelled','rejected');
    CREATE TABLE attendance_violations (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id                     INTEGER NOT NULL REFERENCES users(id),
        week_start_date             TEXT NOT NULL,
        week_end_date               TEXT NOT NULL,
        required_days               INTEGER NOT NULL CHECK (required_days > 0),
        actual_days                 INTEGER NOT NULL CHECK (actual_days >= 0),
        absent_dates                TEXT NOT NULL DEFAULT '[]',
        status                      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','penalized','waived')),
        resolution_admin_id         INTEGER REFERENCES users(id),
        resolution_comment          TEXT,
        resolution_media_ids        TEXT,
        linked_fine_transaction_id  INTEGER REFERENCES financial_transactions(id),
        resolved_at                 TEXT,
        created_at                  TEXT NOT NULL,
        UNIQUE (user_id, week_start_date),
        CHECK (status = 'pending' OR (resolution_admin_id IS NOT NULL AND resolution_comment IS NOT NULL
                                      AND resolved_at IS NOT NULL)),
        CHECK ((status = 'penalized') = (linked_fine_transaction_id IS NOT NULL))
    );
    CREATE INDEX ix_violations_status ON attendance_violations (status, week_start_date);
    """)
    if seq:
        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'financial_transactions'", (seq[0],))


MIGRATIONS.append(_attendance)


# 15: a personal norm of work days a week (ТЗ 12.4); NULL = the common norm, 0 = no norm for this person
MIGRATIONS.append("""
ALTER TABLE users ADD COLUMN min_weekly_shifts INTEGER CHECK (min_weekly_shifts IS NULL OR min_weekly_shifts BETWEEN 0 AND 7);
""")


# 16: flexible rates (customer round B): personal rate and «no lazy tax» per person, a base rate and the
# trainer's ladder per network, deductions in pieces (with the trainer's part linked), the owner's
# earnings kept with each payout. An owner may lead several networks: one-trainer-one-network moves to code.
MIGRATIONS.append("""
ALTER TABLE users ADD COLUMN personal_rate INTEGER CHECK (personal_rate IS NULL OR personal_rate >= 0);
ALTER TABLE users ADD COLUMN lazy_exempt INTEGER NOT NULL DEFAULT 0;
ALTER TABLE networks ADD COLUMN rate_base INTEGER CHECK (rate_base IS NULL OR rate_base >= 0);
ALTER TABLE networks ADD COLUMN ladder TEXT;
ALTER TABLE networks ADD COLUMN ladder_period TEXT NOT NULL DEFAULT 'day' CHECK (ladder_period IN ('day','week'));
ALTER TABLE networks ADD COLUMN ladder_counts_trainer INTEGER NOT NULL DEFAULT 0;
ALTER TABLE financial_transactions ADD COLUMN pieces INTEGER CHECK (pieces IS NULL OR pieces > 0);
ALTER TABLE financial_transactions ADD COLUMN rate INTEGER;
ALTER TABLE financial_transactions ADD COLUMN linked_tx_id INTEGER REFERENCES financial_transactions(id);
ALTER TABLE payout_batches ADD COLUMN summary TEXT;
ALTER TABLE payout_items ADD COLUMN pieces_pay INTEGER NOT NULL DEFAULT 0;
ALTER TABLE payout_items ADD COLUMN commission INTEGER NOT NULL DEFAULT 0;
DROP INDEX IF EXISTS ux_networks_trainer;
""")

# ТЗ №20 + №22: live editing (optimistic version), prize type + photo, visibility in the feed
# (auto — hidden N days after the end; shown/hidden — set by hand), soft delete; annulled results.
# Old «archived» goals become hidden ones: they stay out of the feed and show up in «Архив».
MIGRATIONS.append("""
ALTER TABLE events ADD COLUMN prize_type TEXT;
ALTER TABLE events ADD COLUMN prize_photo TEXT;
ALTER TABLE events ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE events ADD COLUMN visibility TEXT NOT NULL DEFAULT 'auto' CHECK (visibility IN ('auto','shown','hidden'));
ALTER TABLE events ADD COLUMN hidden_at TEXT;
ALTER TABLE events ADD COLUMN deleted_at TEXT;
ALTER TABLE events ADD COLUMN updated_at TEXT;
ALTER TABLE goals ADD COLUMN prize TEXT;
ALTER TABLE goals ADD COLUMN prize_type TEXT;
ALTER TABLE goals ADD COLUMN prize_photo TEXT;
ALTER TABLE goals ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE goals ADD COLUMN visibility TEXT NOT NULL DEFAULT 'auto' CHECK (visibility IN ('auto','shown','hidden'));
ALTER TABLE goals ADD COLUMN hidden_at TEXT;
ALTER TABLE goals ADD COLUMN deleted_at TEXT;
ALTER TABLE goals ADD COLUMN updated_at TEXT;
ALTER TABLE goal_participants ADD COLUMN annulled_at TEXT;
ALTER TABLE goal_participants ADD COLUMN annul_reason TEXT;
ALTER TABLE event_entries ADD COLUMN annulled_at TEXT;
UPDATE goals SET visibility = 'hidden', hidden_at = COALESCE(completed_at, created_at),
                 status = CASE WHEN completed_at IS NOT NULL THEN 'completed' ELSE 'active' END
 WHERE status = 'archived';
""")

# the customer: «Не работаю» = no reminders; how long a person has had their status is shown everywhere
MIGRATIONS.append("""
UPDATE users SET notify_reminders = 0 WHERE work_status = 'not_working';
UPDATE users SET work_status_at = COALESCE(decided_at, created_at) WHERE work_status_at IS NULL;
""")

# broadcasts: a video instead of a photo, «Ответить» (replies go to the author), saved templates
MIGRATIONS.append("""
ALTER TABLE broadcasts ADD COLUMN media_kind TEXT NOT NULL DEFAULT 'photo' CHECK (media_kind IN ('photo','video'));
ALTER TABLE broadcasts ADD COLUMN allow_reply INTEGER NOT NULL DEFAULT 0;
CREATE TABLE broadcast_replies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_id  INTEGER NOT NULL REFERENCES broadcasts(id),
    user_id       INTEGER NOT NULL REFERENCES users(id),
    text          TEXT,
    media         TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX ix_broadcast_replies ON broadcast_replies (broadcast_id, user_id);
CREATE TABLE broadcast_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    html        TEXT NOT NULL,
    created_by  INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT,
    deleted_at  TEXT
);
""")


def _unmarked_days(conn: sqlite3.Connection) -> None:
    """The customer's module 1: «не отметил график на завтра» is a violation too. The table gets a kind
    (weekly — the norm of work days, unmarked — one day nobody marked); a rebuild, since SQLite can't change
    a UNIQUE in place. Ids and the counter stay."""
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'attendance_violations'").fetchone()
    _run_script(conn, """
    CREATE TABLE attendance_violations_new (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id                     INTEGER NOT NULL REFERENCES users(id),
        kind                        TEXT NOT NULL DEFAULT 'weekly' CHECK (kind IN ('weekly','unmarked')),
        week_start_date             TEXT NOT NULL,
        week_end_date               TEXT NOT NULL,
        required_days               INTEGER NOT NULL CHECK (required_days > 0),
        actual_days                 INTEGER NOT NULL CHECK (actual_days >= 0),
        absent_dates                TEXT NOT NULL DEFAULT '[]',
        status                      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','penalized','waived')),
        resolution_admin_id         INTEGER REFERENCES users(id),
        resolution_comment          TEXT,
        resolution_media_ids        TEXT,
        linked_fine_transaction_id  INTEGER REFERENCES financial_transactions(id),
        resolved_at                 TEXT,
        created_at                  TEXT NOT NULL,
        UNIQUE (user_id, kind, week_start_date),
        CHECK (status = 'pending' OR (resolution_admin_id IS NOT NULL AND resolution_comment IS NOT NULL
                                      AND resolved_at IS NOT NULL)),
        CHECK ((status = 'penalized') = (linked_fine_transaction_id IS NOT NULL))
    );
    INSERT INTO attendance_violations_new (id, user_id, week_start_date, week_end_date, required_days, actual_days,
        absent_dates, status, resolution_admin_id, resolution_comment, resolution_media_ids, linked_fine_transaction_id,
        resolved_at, created_at)
    SELECT id, user_id, week_start_date, week_end_date, required_days, actual_days, absent_dates, status,
           resolution_admin_id, resolution_comment, resolution_media_ids, linked_fine_transaction_id, resolved_at, created_at
      FROM attendance_violations;
    DROP TABLE attendance_violations;
    ALTER TABLE attendance_violations_new RENAME TO attendance_violations;
    CREATE INDEX ix_violations_status ON attendance_violations (status, week_start_date);
    """)
    if seq:
        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'attendance_violations'", (seq[0],))


MIGRATIONS.append(_unmarked_days)


def _goal_kinds(conn: sqlite3.Connection) -> None:
    """Goals for a hand-picked group (`group`) and a point's daily norm (`point`: the location, when the day
    resets). A rebuild — SQLite can't change a CHECK in place; ids and the counter stay."""
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'goals'").fetchone()
    cols = ("id, title, description, kind, mode, target, date_from, date_to, user_id, network_id, status, created_by, "
            "created_at, completed_at, prize, prize_type, prize_photo, version, visibility, hidden_at, deleted_at, updated_at")
    _run_script(conn, f"""
    CREATE TABLE goals_new (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        title        TEXT NOT NULL,
        description  TEXT,
        kind         TEXT NOT NULL CHECK (kind IN ('personal','team','network','group','point')),
        mode         TEXT NOT NULL DEFAULT 'shared' CHECK (mode IN ('shared','each')),
        target       INTEGER NOT NULL CHECK (target > 0),
        date_from    TEXT NOT NULL,
        date_to      TEXT NOT NULL,
        user_id      INTEGER REFERENCES users(id),
        network_id   INTEGER REFERENCES networks(id),
        status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','archived')),
        created_by   INTEGER NOT NULL,
        created_at   TEXT NOT NULL,
        completed_at TEXT,
        prize        TEXT,
        prize_type   TEXT,
        prize_photo  TEXT,
        version      INTEGER NOT NULL DEFAULT 1,
        visibility   TEXT NOT NULL DEFAULT 'auto' CHECK (visibility IN ('auto','shown','hidden')),
        hidden_at    TEXT,
        deleted_at   TEXT,
        updated_at   TEXT,
        location     TEXT,
        reset_time   TEXT,
        CHECK ((kind = 'point') = (location IS NOT NULL))
    );
    INSERT INTO goals_new ({cols}) SELECT {cols} FROM goals;
    DROP TABLE goals;
    ALTER TABLE goals_new RENAME TO goals;
    -- a point goal is met day by day: one row per goal-day it was reached
    CREATE TABLE goal_days (
        goal_id      INTEGER NOT NULL REFERENCES goals(id),
        day          TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        PRIMARY KEY (goal_id, day)
    );
    """)
    if seq:
        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'goals'", (seq[0],))


MIGRATIONS.append(_goal_kinds)

# the customer's module 3 — recruiting: referral links, the questionnaire in the Mini App, onboarding
# (training → internship of N unpaid units → the referrer's bonuses), training shifts kept out of pay
MIGRATIONS.append("""
ALTER TABLE users ADD COLUMN ref_code TEXT;
CREATE UNIQUE INDEX ux_users_ref_code ON users (ref_code) WHERE ref_code IS NOT NULL;
ALTER TABLE users ADD COLUMN referrer_id INTEGER REFERENCES users(id);
ALTER TABLE users ADD COLUMN trainee_since TEXT;
ALTER TABLE users ADD COLUMN trainee_done_at TEXT;
ALTER TABLE shifts ADD COLUMN training INTEGER NOT NULL DEFAULT 0;
CREATE TABLE referral_hits (
    telegram_id  INTEGER PRIMARY KEY,
    referrer_id  INTEGER NOT NULL REFERENCES users(id),
    created_at   TEXT NOT NULL
);
CREATE TABLE applications (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER NOT NULL REFERENCES users(id),
    status                 TEXT NOT NULL DEFAULT 'new'
                           CHECK (status IN ('new','scheduled','internship','done','rejected','returned')),
    full_name              TEXT NOT NULL,
    age                    INTEGER NOT NULL,
    metro                  TEXT NOT NULL,
    min_days               INTEGER NOT NULL,
    training_days          TEXT NOT NULL,
    communication          INTEGER NOT NULL,
    referrer_id            INTEGER REFERENCES users(id),
    consent_at             TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    reject_reason          TEXT,
    decided_by             INTEGER,
    decided_at             TEXT,
    training_date          TEXT,
    training_time          TEXT,
    training_location      TEXT,
    trainer_id             INTEGER REFERENCES users(id),
    training_set_by        INTEGER,
    training_set_at        TEXT,
    training_confirmed_at  TEXT,
    reminded_eve           TEXT,
    reminded_morning       TEXT,
    contact_manager        TEXT,
    contacted_at           TEXT,
    contact_answer         TEXT CHECK (contact_answer IS NULL OR contact_answer IN ('wrote','cant_write')),
    contact_answer_at      TEXT,
    stage1_counted         INTEGER,
    stage1_bonus_tx        INTEGER REFERENCES financial_transactions(id),
    stage1_at              TEXT,
    stage2_counted         INTEGER,
    stage2_bonus_tx        INTEGER REFERENCES financial_transactions(id),
    stage2_at              TEXT
);
CREATE INDEX ix_applications_status ON applications (status, created_at);
CREATE INDEX ix_applications_user ON applications (user_id);
""")


# 23: an event's description with formatting (Telegram HTML; `description` keeps the plain text) and a video prize
MIGRATIONS.append("""
ALTER TABLE events ADD COLUMN description_html TEXT;
ALTER TABLE events ADD COLUMN prize_media_kind TEXT NOT NULL DEFAULT 'photo' CHECK (prize_media_kind IN ('photo','video'));
""")


# 24: change requests for a shift (replaced on 27.10 by the 22:00 deadline; the table stays as history)
MIGRATIONS.append("""
CREATE TABLE shift_change_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER NOT NULL REFERENCES shifts(id),
    user_id         INTEGER NOT NULL REFERENCES users(id),
    start_time      TEXT NOT NULL,
    end_time        TEXT NOT NULL,
    location        TEXT NOT NULL,
    old_start_time  TEXT,
    old_end_time    TEXT,
    old_location    TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','withdrawn','expired')),
    created_at      TEXT NOT NULL,
    decided_by      INTEGER,
    decided_at      TEXT,
    comment         TEXT
);
CREATE UNIQUE INDEX ux_change_requests_pending ON shift_change_requests (shift_id) WHERE status = 'pending';
-- an admin changed the shift by hand: the employee's free editing window is over
ALTER TABLE shifts ADD COLUMN edit_window_closed INTEGER NOT NULL DEFAULT 0;
""")


# 25: «Налог на лень» as a pool (ТЗ 27.10 §20–21): what each payout took from it.
# lazy_before IS NULL — a payout from before the pool: those days were already paid at the lazy rate.
MIGRATIONS.append("""
ALTER TABLE payout_items ADD COLUMN lazy_units INTEGER NOT NULL DEFAULT 0;
ALTER TABLE payout_items ADD COLUMN lazy_before INTEGER;
ALTER TABLE payout_items ADD COLUMN rate_base INTEGER;
ALTER TABLE payout_items ADD COLUMN rate_lazy INTEGER;
""")


# 26: access (ТЗ 27.10 §5–9): managed invitation links that expire and rotate, their uses, attempts of strangers
MIGRATIONS.append("""
CREATE TABLE invite_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('ref','owner')),  -- an employee's referral link / an owner's invitation
    token       TEXT NOT NULL UNIQUE,
    user_id     INTEGER REFERENCES users(id),                   -- ref: the referrer, owner: who created it
    label       TEXT,
    max_uses    INTEGER,                                        -- NULL — any number of people
    uses        INTEGER NOT NULL DEFAULT 0,
    ttl_minutes INTEGER NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT,
    revoked_by  INTEGER,
    replaced_by INTEGER REFERENCES invite_links(id),
    created_at  TEXT NOT NULL
);
CREATE INDEX ix_invite_links_owner ON invite_links (kind, user_id, id);
CREATE TABLE invite_uses (
    link_id     INTEGER NOT NULL REFERENCES invite_links(id),
    telegram_id INTEGER NOT NULL,
    used_at     TEXT NOT NULL,
    user_id     INTEGER REFERENCES users(id),
    PRIMARY KEY (link_id, telegram_id)
);
CREATE TABLE access_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    kind        TEXT NOT NULL,          -- no_link / bad_link / app_no_link
    param       TEXT NOT NULL DEFAULT '',
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    first_at    TEXT NOT NULL,
    last_at     TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    answered    INTEGER NOT NULL DEFAULT 0,   -- «ссылка неактуальна» was sent (only once)
    UNIQUE (telegram_id, kind, param)
);
CREATE INDEX ix_access_attempts_last ON access_attempts (last_at);
ALTER TABLE referral_hits ADD COLUMN link_id INTEGER REFERENCES invite_links(id);
""")


def _instructors(conn: sqlite3.Connection) -> None:
    """Migration 27 (ТЗ 27.10 §22–25): the «instructor» role, people assigned to an instructor, an individual rate
    for trainers and instructors, the instructors' ladder of a network.

    The role's CHECK can't change in place: users is rebuilt from its own current definition (create-copy-drop-rename),
    every row and index kept."""
    import re

    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()[0]
    new = sql.replace("CHECK (role IN ('employee','trainer','admin'))",
                      "CHECK (role IN ('employee','trainer','instructor','admin'))")
    if new == sql:
        raise RuntimeError("migration 27: the users.role check was not found")
    new = re.sub(r'^CREATE TABLE\s+"?users"?', "CREATE TABLE users_new", new, count=1)
    indexes = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'users' AND sql IS NOT NULL")]
    cols = ", ".join(r[1] for r in conn.execute("PRAGMA table_info(users)"))
    conn.execute(new)
    conn.execute(f"INSERT INTO users_new ({cols}) SELECT {cols} FROM users")
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_new RENAME TO users")
    for index in indexes:
        conn.execute(index)
    _run_script(conn, """
    ALTER TABLE users ADD COLUMN instructor_id INTEGER REFERENCES users(id);
    ALTER TABLE users ADD COLUMN personal_commission INTEGER;
    ALTER TABLE networks ADD COLUMN instructor_ladder TEXT;
    ALTER TABLE networks ADD COLUMN instructor_ladder_period TEXT NOT NULL DEFAULT 'day'
        CHECK (instructor_ladder_period IN ('day','week'));
    CREATE INDEX ix_users_instructor ON users (instructor_id) WHERE instructor_id IS NOT NULL;
    """)


MIGRATIONS.append(_instructors)


# 28: groups by work days (ТЗ 27.10 §26–27): what Telegram said about membership, and personal invitations
MIGRATIONS.append("""
CREATE TABLE group_members (
    user_id     INTEGER NOT NULL REFERENCES users(id),
    kind        TEXT NOT NULL CHECK (kind IN ('pre','main')),
    state       TEXT NOT NULL CHECK (state IN ('member','out','unknown')),
    checked_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, kind)
);
CREATE TABLE group_invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    kind        TEXT NOT NULL CHECK (kind IN ('pre','main')),
    link        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    revoked_at  TEXT,
    auto        INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER
);
CREATE INDEX ix_group_invites_user ON group_invites (user_id, kind);
CREATE INDEX ix_group_invites_link ON group_invites (link);
""")


# 29: «Работа» and «Яндекс Алиса» (ТЗ 27.10 §28–44): access to products, QR codes, executions with three photo
# stages kept in a Telegram forum group, moderation, the payout by Yandex's report; goals and events per product
MIGRATIONS.append("""
CREATE TABLE product_access (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    product     TEXT NOT NULL,
    granted_at  TEXT NOT NULL,
    granted_by  INTEGER NOT NULL,
    revoked_at  TEXT,
    revoked_by  INTEGER
);
CREATE UNIQUE INDEX ux_product_access_live ON product_access (user_id, product) WHERE revoked_at IS NULL;
CREATE TABLE alice_qr (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    number      TEXT,
    image       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  INTEGER NOT NULL,
    archived_at TEXT
);
CREATE TABLE alice_qr_assign (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    qr_id       INTEGER NOT NULL REFERENCES alice_qr(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    assigned_at TEXT NOT NULL,
    assigned_by INTEGER NOT NULL,
    removed_at  TEXT,
    removed_by  INTEGER
);
CREATE UNIQUE INDEX ux_alice_qr_assign_live ON alice_qr_assign (qr_id, user_id) WHERE removed_at IS NULL;
CREATE TABLE alice_queries (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    text    TEXT NOT NULL UNIQUE,
    active  INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE alice_tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    qr_id          INTEGER NOT NULL REFERENCES alice_qr(id),
    qr_number      TEXT,
    query          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'in_progress'
                   CHECK (status IN ('in_progress','sending','send_failed','moderation','rejected','accepted','paid','abandoned')),
    step           INTEGER NOT NULL DEFAULT 0,          -- 0 the QR on screen, 1..3 the photo stage, 4 all three fixed
    started_at     TEXT NOT NULL,
    scanned_at     TEXT,
    stage1_at      TEXT,
    stage2_at      TEXT,
    stage3_at      TEXT,
    completed_at   TEXT,
    send_error     TEXT,
    send_attempts  INTEGER NOT NULL DEFAULT 0,
    chat_id        INTEGER,
    thread_id      INTEGER,
    message_ids    TEXT,
    moderated_by   INTEGER,
    moderated_at   TEXT,
    reject_reason  TEXT,
    payout_id      INTEGER REFERENCES payout_batches(id),
    paid_at        TEXT
);
CREATE UNIQUE INDEX ux_alice_tasks_active ON alice_tasks (user_id) WHERE status = 'in_progress';
CREATE INDEX ix_alice_tasks_status ON alice_tasks (status, completed_at);
CREATE INDEX ix_alice_tasks_qr ON alice_tasks (qr_id, status, completed_at);
CREATE TABLE alice_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES alice_tasks(id),
    stage       INTEGER NOT NULL CHECK (stage IN (1,2,3)),
    temp_path   TEXT,                 -- until it is in Telegram, removed after a successful send
    file_id     TEXT,                 -- the photo in the forum group
    message_id  INTEGER,
    sha256      TEXT NOT NULL,
    size        INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL,
    replaced_at TEXT
);
CREATE UNIQUE INDEX ux_alice_photos_live ON alice_photos (task_id, stage) WHERE replaced_at IS NULL;
CREATE INDEX ix_alice_photos_hash ON alice_photos (sha256);
CREATE TABLE alice_topics (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id),
    chat_id     INTEGER NOT NULL,
    thread_id   INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
ALTER TABLE payout_batches ADD COLUMN product TEXT;
ALTER TABLE goals ADD COLUMN product TEXT;
ALTER TABLE events ADD COLUMN product TEXT;
""")

# 30 — ТЗ 28.10 §13: the broadcast header is decided on the server by the author's real role and kept as sent
# («Сообщение от тренера» + a link to the trainer), so reminders repeat it word for word
MIGRATIONS.append("""
ALTER TABLE broadcasts ADD COLUMN signature TEXT;
""")


def _templates(conn: sqlite3.Connection) -> None:
    """Migration 31 — ТЗ 28.10 §2 (В1, В2): editable reason templates (seeded with the presets that lived in the
    code) and editable bot texts (a row only for a text someone changed)."""
    conn.execute("""CREATE TABLE reason_templates (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        kind        TEXT NOT NULL,
        text        TEXT NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        created_by  INTEGER,
        deleted_at  TEXT)""")
    conn.execute("CREATE INDEX ix_reason_templates_kind ON reason_templates (kind, position)")
    conn.execute("""CREATE TABLE message_texts (
        key         TEXT PRIMARY KEY,
        text        TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        updated_by  INTEGER)""")
    from .templates import seed_reasons

    seed_reasons(conn)


MIGRATIONS.append(_templates)


def _training(conn: sqlite3.Connection) -> None:
    """Migration 32 — ТЗ 28.10 §2–§6: the trainer of a candidate as a step of its own, the 48-hour timer, the
    trainer's confirmation, «Обучение начато», requests that wait for an admin, cancellations counted, personal
    access to «Обучение». The application statuses stay as they were (no table rebuild): the finer stage is
    derived from these columns."""
    import json

    for column in ("trainer_assigned_at TEXT", "contact_timer_at TEXT", "trainer_confirmed_at TEXT",
                   "training_started_at TEXT", "finish_requested_at TEXT", "cancel_ack INTEGER NOT NULL DEFAULT 0",
                   "version INTEGER NOT NULL DEFAULT 1", "pre_invited_at TEXT", "team_invited_at TEXT"):
        conn.execute(f"ALTER TABLE applications ADD COLUMN {column}")
    # trainings set before: the admin set the trainer and the date together — the trainer's part counts as agreed
    conn.execute("UPDATE applications SET trainer_assigned_at = training_set_at WHERE trainer_id IS NOT NULL")
    conn.execute("""UPDATE applications SET trainer_confirmed_at = training_set_at
                    WHERE trainer_id IS NOT NULL AND training_date IS NOT NULL""")
    conn.execute("""CREATE TABLE training_requests (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id  INTEGER NOT NULL REFERENCES applications(id),
        kind            TEXT NOT NULL CHECK (kind IN ('start','refuse','cancel','change','internship')),
        initiator_id    INTEGER REFERENCES users(id),
        initiator_role  TEXT NOT NULL CHECK (initiator_role IN ('trainer','candidate')),
        reason          TEXT,
        payload         TEXT,
        old             TEXT,
        status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','stale')),
        app_version     INTEGER NOT NULL,
        created_at      TEXT NOT NULL,
        decided_by      INTEGER,
        decided_at      TEXT,
        decision_reason TEXT)""")
    conn.execute("""CREATE UNIQUE INDEX ux_training_requests_pending ON training_requests (application_id, kind)
                    WHERE status = 'pending'""")
    conn.execute("""CREATE TABLE request_messages (
        request_id  INTEGER NOT NULL REFERENCES training_requests(id),
        chat_id     INTEGER NOT NULL,
        message_id  INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE training_cancellations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id  INTEGER NOT NULL REFERENCES applications(id),
        kind            TEXT NOT NULL CHECK (kind IN ('cancel','no_show')),
        at              TEXT NOT NULL,
        initiator_id    INTEGER,
        initiator_role  TEXT,
        reason          TEXT,
        legacy          INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE candidate_messages (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id  INTEGER NOT NULL REFERENCES applications(id),
        kind            TEXT NOT NULL,
        sent_at         TEXT NOT NULL,
        sent_by         INTEGER,
        delivered       INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("""CREATE TABLE training_access (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id),
        granted_at  TEXT NOT NULL,
        granted_by  INTEGER,
        revoked_at  TEXT,
        revoked_by  INTEGER,
        auto        INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("CREATE UNIQUE INDEX ux_training_access_live ON training_access (user_id) WHERE revoked_at IS NULL")
    # cancellations made before: counted too, marked «до 28.10» (В8)
    for row in conn.execute("SELECT * FROM event_logs WHERE event_type = 'TRAINING_CANCELLED' ORDER BY id").fetchall():
        try:
            app_id = json.loads(row["new_value"] or "{}").get("application")
        except ValueError:
            app_id = None
        if app_id and conn.execute("SELECT 1 FROM applications WHERE id = ?", (app_id,)).fetchone():
            conn.execute("""INSERT INTO training_cancellations (application_id, kind, at, initiator_id, initiator_role, legacy)
                            VALUES (?, 'cancel', ?, ?, 'admin', 1)""", (app_id, row["created_at"], row["actor_id"]))
    # trainers already assigned get «Обучение» automatically
    for (user_id,) in conn.execute("""SELECT DISTINCT trainer_id FROM applications WHERE trainer_id IS NOT NULL
                                      AND status IN ('scheduled', 'internship')""").fetchall():
        conn.execute("INSERT INTO training_access (user_id, granted_at, auto) VALUES (?, datetime('now'), 1)", (user_id,))


MIGRATIONS.append(_training)

# 33 — ТЗ 28.10 §15: who must be in which group is up to the admin per person (both groups are fine for some),
# and «Не новичок — подтвердить» counts a person as experienced without touching their role
MIGRATIONS.append("""
ALTER TABLE users ADD COLUMN group_mode TEXT;
ALTER TABLE users ADD COLUMN not_newbie_at TEXT;
ALTER TABLE users ADD COLUMN not_newbie_by INTEGER;
""")


def _profiles(conn: sqlite3.Connection) -> None:
    """Migration 34 — ТЗ 28.10 §7: the employee's profile (surname, name, patronymic, age, metro, work days) goes
    through the admin's review; payment details changes too (the old ones are used until approved). The
    patronymic, which used to be glued to the first name, is split off for people who came with a questionnaire."""
    for column in ("middle_name TEXT", "age INTEGER", "metro TEXT", "min_days INTEGER", "work_days TEXT",
                   "profile_approved_at TEXT"):
        conn.execute(f"ALTER TABLE users ADD COLUMN {column}")
    conn.execute("ALTER TABLE payment_profiles ADD COLUMN bank_name TEXT")
    conn.execute("""CREATE TABLE profile_requests (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id),
        kind        TEXT NOT NULL CHECK (kind IN ('profile','payment')),
        data        TEXT NOT NULL,
        old         TEXT,
        status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','stale')),
        created_at  TEXT NOT NULL,
        decided_by  INTEGER,
        decided_at  TEXT,
        reason      TEXT)""")
    conn.execute("CREATE UNIQUE INDEX ux_profile_requests_pending ON profile_requests (user_id, kind) WHERE status = 'pending'")
    rows = conn.execute("""SELECT u.id, u.first_name, u.last_name, a.full_name FROM users u JOIN applications a ON a.id = (
                               SELECT MAX(b.id) FROM applications b WHERE b.user_id = u.id)""").fetchall()
    for user_id, first, last, full in rows:
        parts = (full or "").split()
        if len(parts) >= 3 and last == parts[0] and first == " ".join(parts[1:]):
            conn.execute("UPDATE users SET first_name = ?, middle_name = ? WHERE id = ?",
                         (parts[1], " ".join(parts[2:]), user_id))


MIGRATIONS.append(_profiles)

# 35 — ТЗ 28.10 §12: manuals in «Обучение» — sections, versions, pictures kept on the server, per-person access
# and per-section reading progress (a section edited since — unread again)
MIGRATIONS.append("""
CREATE TABLE manuals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    created_by  INTEGER NOT NULL,
    deleted_at  TEXT
);
CREATE TABLE manual_sections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    manual_id   INTEGER NOT NULL REFERENCES manuals(id),
    position    INTEGER NOT NULL,
    title       TEXT NOT NULL,
    html        TEXT NOT NULL,
    hash        TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE TABLE manual_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    manual_id   INTEGER NOT NULL REFERENCES manuals(id),
    version     INTEGER NOT NULL,
    snapshot    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  INTEGER,
    note        TEXT
);
CREATE TABLE manual_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    manual_id   INTEGER NOT NULL REFERENCES manuals(id),
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE manual_access (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    manual_id   INTEGER NOT NULL REFERENCES manuals(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    granted_at  TEXT NOT NULL,
    granted_by  INTEGER,
    revoked_at  TEXT,
    revoked_by  INTEGER
);
CREATE UNIQUE INDEX ux_manual_access_live ON manual_access (manual_id, user_id) WHERE revoked_at IS NULL;
CREATE TABLE manual_progress (
    user_id     INTEGER NOT NULL REFERENCES users(id),
    section_id  INTEGER NOT NULL REFERENCES manual_sections(id),
    hash        TEXT NOT NULL,
    read_at     TEXT NOT NULL,
    PRIMARY KEY (user_id, section_id)
);
""")

# 36 — ТЗ 28.10 §19: one sender per «Алиса» album at a time (no double send by «Завершить» and the retry job)
MIGRATIONS.append("""
ALTER TABLE alice_tasks ADD COLUMN send_claimed_at TEXT;
""")


def _widen_check(conn: sqlite3.Connection, table: str, old: str, new: str) -> None:
    """Rebuild a table whose CHECK must allow more values: its own SQL with the list widened, the rows copied,
    the indexes recreated (SQLite can't change a CHECK in place). Foreign keys are off during migrations."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()[0]
    if old not in sql:
        raise RuntimeError(f"{table}: CHECK {old} not found")
    indexes = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (table,))]
    columns = ", ".join(r[1] for r in conn.execute(f"PRAGMA table_info({table})"))
    conn.execute(sql.replace(old, new, 1).replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}__new", 1)
                 .replace(f'CREATE TABLE "{table}"', f"CREATE TABLE {table}__new", 1))
    conn.execute(f"INSERT INTO {table}__new ({columns}) SELECT {columns} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
    for index in indexes:
        conn.execute(index)


def _alice_money(conn: sqlite3.Connection) -> None:
    """Migration 37 — ТЗ 28.10 §23–§25: «Алиса» tasks may be «Отклонено Яндексом» or deleted (softly, with the
    archive and the group cleanup tracked); payouts may be edited (versions) and annulled (kept, voided);
    a fine may be tied to «Алиса» and a task; settling a fine no longer overwrites who approved it."""
    _widen_check(conn, "alice_tasks", "'abandoned'", "'abandoned','yandex_rejected','deleted'")
    for column in ("deleted_at TEXT", "deleted_by INTEGER", "delete_reason TEXT", "archive_state TEXT",
                   "archive_message_ids TEXT", "unpublish_state TEXT", "status_message_id INTEGER"):
        conn.execute(f"ALTER TABLE alice_tasks ADD COLUMN {column}")
    _widen_check(conn, "payout_batches", "IN ('processed')", "IN ('processed','annulled')")
    for column in ("annulled_at TEXT", "annulled_by INTEGER", "annul_reason TEXT", "money_sent INTEGER",
                   "version INTEGER NOT NULL DEFAULT 1", "updated_at TEXT", "updated_by INTEGER"):
        conn.execute(f"ALTER TABLE payout_batches ADD COLUMN {column}")
    conn.execute("ALTER TABLE payout_items ADD COLUMN voided INTEGER NOT NULL DEFAULT 0")  # 1 — an older version, 2 — annulled
    conn.execute("""CREATE TABLE payout_versions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id    INTEGER NOT NULL REFERENCES payout_batches(id),
        version     INTEGER NOT NULL,
        snapshot    TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        created_by  INTEGER,
        note        TEXT)""")
    for column in ("product TEXT", "alice_task_id INTEGER", "settled_at TEXT", "settled_by INTEGER"):
        conn.execute(f"ALTER TABLE financial_transactions ADD COLUMN {column}")


MIGRATIONS.append(_alice_money)

# ТЗ 29.10 §3: a manual keeps the look of its page — its cleaned style sheet and the body's class (versions keep the
# sheet too)
MIGRATIONS.append("""
ALTER TABLE manuals ADD COLUMN css TEXT;
ALTER TABLE manuals ADD COLUMN body_class TEXT;
ALTER TABLE manual_versions ADD COLUMN css TEXT
""")


def _yandex_plus(conn: sqlite3.Connection) -> None:
    """Migration 39 — ТЗ «Яндекс Плюс»: the second product of «Работа» lives in the «Алиса» tables with its own
    `product`: its own QR codes, executions (one photo), topics in its own photo-report group. Old rows are «Алиса».
    One execution going per person and product; a topic per person and product (the groups are separate)."""
    conn.execute("ALTER TABLE alice_qr ADD COLUMN product TEXT NOT NULL DEFAULT 'alice'")
    conn.execute("ALTER TABLE alice_tasks ADD COLUMN product TEXT NOT NULL DEFAULT 'alice'")
    conn.execute("DROP INDEX IF EXISTS ux_alice_tasks_active")
    conn.execute("CREATE UNIQUE INDEX ux_alice_tasks_active ON alice_tasks (user_id, product) WHERE status = 'in_progress'")
    conn.execute("CREATE INDEX ix_alice_tasks_product ON alice_tasks (product, status, completed_at)")
    conn.execute("CREATE INDEX ix_alice_qr_product ON alice_qr (product, archived_at)")
    conn.execute("""CREATE TABLE alice_topics__new (
        user_id     INTEGER NOT NULL REFERENCES users(id),
        product     TEXT NOT NULL DEFAULT 'alice',
        chat_id     INTEGER NOT NULL,
        thread_id   INTEGER NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (user_id, product))""")
    conn.execute("""INSERT INTO alice_topics__new (user_id, product, chat_id, thread_id, created_at)
                    SELECT user_id, 'alice', chat_id, thread_id, created_at FROM alice_topics""")
    conn.execute("DROP TABLE alice_topics")
    conn.execute("ALTER TABLE alice_topics__new RENAME TO alice_topics")
    from .templates import REASON_KINDS

    if conn.execute("SELECT 1 FROM reason_templates WHERE kind = 'plus_reject'").fetchone():
        return  # a fresh database got them from migration 31 (it seeds every kind of today's code)
    for i, text in enumerate(REASON_KINDS["plus_reject"][1]):
        conn.execute("INSERT INTO reason_templates (kind, text, position, created_at) VALUES ('plus_reject', ?, ?, datetime('now'))",
                     (text, i + 1))


MIGRATIONS.append(_yandex_plus)

# 40 — «Яндекс Плюс» gets its own rate (plus_rate). It starts equal to the «Алиса» rate already set, so nothing
# changes until the owner sets it apart
MIGRATIONS.append("""
INSERT OR IGNORE INTO app_settings (key, value) SELECT 'plus_rate', value FROM app_settings WHERE key = 'alice_rate'
""")


def _tz3010(conn: sqlite3.Connection) -> None:
    """Migration 41 — ТЗ 30.10.

    §5: a cancelled training is a finished attempt. training_cancellations (one row per cancellation, already the
    counter) keeps what the attempt was: the trainer, the date, time and place, both confirmations, when the trainer
    was assigned and the training started. The candidate goes back to «Ожидают рассмотрения» (status `new`, no
    trainer); the next assignment is a new attempt, the old one stays in this row. Rows made before are filled
    from the event log where it has the data. Candidates waiting for a new date after a cancellation (1/3 and more)
    go back to «Ожидают рассмотрения» right away; refused ones stay where they are.

    §3: «Разрешить доступ» from «Попытки доступа» — who allowed it, when and with what result."""
    import json

    for column in ("trainer_id INTEGER", "training_date TEXT", "training_time TEXT", "training_location TEXT",
                   "candidate_confirmed_at TEXT", "trainer_confirmed_at TEXT", "attempt_started_at TEXT",
                   "training_started_at TEXT"):
        conn.execute(f"ALTER TABLE training_cancellations ADD COLUMN {column}")
    for column in ("restored_at TEXT", "restored_by INTEGER", "restore_result TEXT"):
        conn.execute(f"ALTER TABLE access_attempts ADD COLUMN {column}")
    # the attempt behind each earlier cancellation: the event written in the same transaction has the old values
    for row in conn.execute("SELECT id, application_id, at FROM training_cancellations").fetchall():
        for event in conn.execute("""SELECT old_value, new_value FROM event_logs WHERE event_type = 'TRAINING_CANCELLED'
                                     AND created_at = ? ORDER BY id""", (row["at"],)).fetchall():
            try:
                new, old = json.loads(event["new_value"] or "{}"), json.loads(event["old_value"] or "{}")
            except ValueError:
                continue
            if new.get("application") == row["application_id"]:
                conn.execute("""UPDATE training_cancellations SET trainer_id = ?, training_date = ?, training_time = ?,
                                training_location = ? WHERE id = ?""",
                             (old.get("trainer_id"), old.get("training_date"), old.get("training_time"),
                              old.get("training_location"), row["id"]))
                break
    # waiting for a new date after a cancellation → «Ожидают рассмотрения»; the trainer's part of the attempt ends
    from .timeutil import now_iso

    stamp = now_iso()
    waiting = conn.execute("""SELECT * FROM applications a WHERE status = 'scheduled' AND training_date IS NULL
                              AND training_started_at IS NULL
                              AND EXISTS (SELECT 1 FROM training_cancellations t WHERE t.application_id = a.id)""").fetchall()
    for app in waiting:
        conn.execute("""UPDATE training_cancellations SET trainer_id = COALESCE(trainer_id, ?),
                        attempt_started_at = COALESCE(attempt_started_at, ?)
                        WHERE id = (SELECT MAX(id) FROM training_cancellations WHERE application_id = ?)""",
                     (app["trainer_id"], app["trainer_assigned_at"], app["id"]))
        conn.execute("""UPDATE applications SET status = 'new', trainer_id = NULL, trainer_assigned_at = NULL,
                        trainer_confirmed_at = NULL, training_confirmed_at = NULL, training_set_by = NULL,
                        training_set_at = NULL, contact_timer_at = NULL, contact_answer = NULL, contact_answer_at = NULL,
                        reminded_eve = NULL, reminded_morning = NULL, version = version + 1 WHERE id = ?""", (app["id"],))
        conn.execute("""UPDATE training_requests SET status = 'stale', decided_at = ? WHERE application_id = ?
                        AND status = 'pending'""", (stamp, app["id"]))
        conn.execute("""INSERT INTO event_logs (user_id, actor_id, event_type, old_value, new_value, idempotency_key,
                        description, created_at) VALUES (?, 0, 'TRAINING_BACK_TO_REVIEW', ?, ?, ?, ?, ?)""",
                     (app["user_id"], json.dumps({"trainer_id": app["trainer_id"]}),
                      json.dumps({"application": app["id"]}), f"migration41:{app['id']}",
                      "ТЗ 30.10 §5: после отмены — в «Ожидают рассмотрения»", stamp))


MIGRATIONS.append(_tz3010)


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
