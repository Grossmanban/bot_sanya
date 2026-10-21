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
