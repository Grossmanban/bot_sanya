"""Schedule violations (ТЗ 12.4): fewer work days in a week than the person's norm.

The norm is personal (users.min_weekly_shifts, set by owners, admins or the person's trainer) or,
when not set, the common `weekly_attendance_quota` — which can be switched off; personal norms
are checked either way. A personal norm of 0 means «no norm» for that person.

Every Monday the past week is checked; each person below the norm gets one attendance_violation
waiting for a decision. Staff resolve it exactly once:

    pending ── fine (amount + comment + up to 5 photos) ──> penalized   (a fine in the ledger)
            └─ waive (comment + up to 5 photos) ─────────> waived      (no money)

A trainer's fine waits for an owner like any trainer's fine (ТЗ 5.3). If the owner declines it,
the violation goes back to pending so somebody decides again.

A work day is a day with a live shift (active/restored) that didn't end as a no-show.

The customer's module 1 adds a second kind, `unmarked`: every evening (`daily_mark_*` settings) people with
«Работаю» who haven't marked tomorrow — no shift and no day off — get a reminder, and at the deadline
(22:00 by default) those still silent get a violation for that day. «Не работаю» isn't checked. Decided the
same way: a fine or «простить».
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from . import finance
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import ddmm, now_iso, today_local, week_start

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

STATUSES = ("pending", "penalized", "waived")
MAX_MEDIA = 5
MAX_COMMENT = 500


@dataclass
class Resolution:
    violation: dict
    changed: bool
    tx: dict | None = None


def days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def period(v: dict) -> str:
    start, end = date.fromisoformat(v["week_start_date"]), date.fromisoformat(v["week_end_date"])
    if v.get("kind") == "unmarked":
        return f"{ddmm(start)} ({WEEKDAYS[start.weekday()]})"
    return f"Неделя {start.isocalendar()[1]} ({ddmm(start)} – {ddmm(end)})"


def what(v: dict) -> str:
    """The reason in one line, for messages and the archive."""
    if v.get("kind") == "unmarked":
        return f"не отмечен график на {ddmm(date.fromisoformat(v['week_start_date']))} — ни смены, ни выходного"
    return f"невыполнение нормы выходов ({v['actual_days']}/{v['required_days']}, {period(v)})"


def default_comment(v: dict) -> str:
    if v.get("kind") == "unmarked":
        return f"Не отмечен график на {ddmm(date.fromisoformat(v['week_start_date']))}: ни смены, ни выходного к сроку."
    start, end = date.fromisoformat(v["week_start_date"]), date.fromisoformat(v["week_end_date"])
    return (f"Невыполнение плана выходов за период {ddmm(start)}–{ddmm(end)}. "
            f"Вышли {v['actual_days']} из {v['required_days']} {'дня' if v['required_days'] % 10 == 1 and v['required_days'] % 100 != 11 else 'дней'}.")


# ---------------------------------------------------------------- reads


def get(conn: sqlite3.Connection, violation_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM attendance_violations WHERE id = ?", (violation_id,)).fetchone()
    return dict(row) if row else None


def by_tx(conn: sqlite3.Connection, tx_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM attendance_violations WHERE linked_fine_transaction_id = ?", (tx_id,)).fetchone()
    return dict(row) if row else None


def media_ids(v: dict) -> list[str]:
    return json.loads(v["resolution_media_ids"] or "[]")


def _units(conn: sqlite3.Connection) -> int:
    from . import app_settings

    return app_settings.get_all(conn)["unmarked_fine_units"]


def _rate(conn: sqlite3.Connection, user_id: int) -> int:
    from . import rates

    return rates.for_user(conn, user_id)["rate_base"]


def public(conn: sqlite3.Connection, v: dict, nets: dict[int, str] | None = None) -> dict:
    user = svc.get_user(conn, v["user_id"])
    resolver = svc.get_user(conn, v["resolution_admin_id"]) if v["resolution_admin_id"] else None
    tx = finance.get(conn, v["linked_fine_transaction_id"]) if v["linked_fine_transaction_id"] else None
    nets = nets if nets is not None else {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM networks")}
    return {
        "id": v["id"],
        "kind": v["kind"],
        "user_id": v["user_id"],
        "name": svc.full_name(user),
        "username": user["username"],
        "telegram_id": user["telegram_id"],
        "network": nets.get(user["network_id"]),
        "week_start": v["week_start_date"],
        "week_end": v["week_end_date"],
        "week_number": date.fromisoformat(v["week_start_date"]).isocalendar()[1],
        "required_days": v["required_days"],
        "actual_days": v["actual_days"],
        "absent_dates": json.loads(v["absent_dates"] or "[]"),
        "status": v["status"],
        "default_comment": default_comment(v),
        # ТЗ 27.10 §17: an unmarked day — N fine units at the person's own rate (the settings say N)
        "suggested_units": _units(conn) if v["kind"] == "unmarked" else None,
        "suggested_amount": _units(conn) * _rate(conn, v["user_id"]) if v["kind"] == "unmarked" else None,
        "unit_rate": _rate(conn, v["user_id"]),  # one fine unit in rubles, for the staff's form
        "resolution_comment": v["resolution_comment"],
        "resolved_by": svc.full_name(resolver) if resolver else None,
        "resolved_at": v["resolved_at"],
        "media_count": len(media_ids(v)),
        "fine": {"id": tx["id"], "amount": tx["amount"], "units": tx["pieces"], "status": tx["status"]} if tx else None,
        "created_at": v["created_at"],
    }


def listing(conn: sqlite3.Connection, viewer: dict, status: str = "pending", limit: int = 200) -> list[dict]:
    """Pending ones oldest first; resolved ones newest first. Trainers see their network only."""
    if status not in STATUSES and status != "resolved":
        raise ServiceError("Неизвестный статус", 422, "bad_status")
    if status == "resolved":
        sql = "SELECT * FROM attendance_violations WHERE status != 'pending' ORDER BY resolved_at DESC LIMIT ?"
    else:
        sql = f"SELECT * FROM attendance_violations WHERE status = '{status}' ORDER BY week_start_date, id LIMIT ?"
    nets = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM networks")}
    out = []
    for v in conn.execute(sql, (limit,)):
        v = dict(v)
        if perms.can(viewer, "review", svc.get_user(conn, v["user_id"])):
            out.append(public(conn, v, nets))
    return out


def pending_count(conn: sqlite3.Connection, viewer: dict) -> int:
    rows = conn.execute("SELECT user_id FROM attendance_violations WHERE status = 'pending'").fetchall()
    if perms.sees_everyone(viewer):
        return len(rows)
    return sum(perms.can(viewer, "review", svc.get_user(conn, r["user_id"])) for r in rows)


# ---------------------------------------------------------------- detection


def work_days(conn: sqlite3.Connection, user_id: int, start: date, end: date) -> set[str]:
    return {r[0] for r in conn.execute(
        """SELECT DISTINCT shift_date FROM shifts WHERE user_id = ? AND shift_date BETWEEN ? AND ?
           AND status IN ('active','restored') AND (report_state IS NULL OR report_state != 'no_show')""",
        (user_id, start.isoformat(), end.isoformat()))}


def norm_of(user: dict, common: int) -> int:
    personal = user.get("min_weekly_shifts")
    return common if personal is None else personal


def has_personal_norms(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE min_weekly_shifts > 0 LIMIT 1").fetchone() is not None


def scan(conn: sqlite3.Connection, common: int, today: date | None = None) -> list[dict]:
    """Create violations for the last finished week (Mon–Sun before `today`). `common` is the common
    norm (0 = switched off). Safe to run again: one violation per person and week. People paused,
    blocked or approved during that week are skipped."""
    today = today or today_local()
    start = week_start(today) - timedelta(days=7)
    end = start + timedelta(days=6)
    created = []
    with transaction(conn):
        for u in conn.execute(
            """SELECT * FROM users WHERE status = 'approved' AND account_state = 'active'
               AND (decided_at IS NULL OR substr(decided_at, 1, 10) < ?)""", (start.isoformat(),)).fetchall():
            u = dict(u)
            quota = norm_of(u, common)
            if quota <= 0 or perms.role_of(u) not in (perms.EMPLOYEE, perms.TRAINER):
                continue
            worked = work_days(conn, u["id"], start, end)
            if len(worked) >= quota:
                continue
            absent = [(start + timedelta(days=i)).isoformat() for i in range(7)
                      if (start + timedelta(days=i)).isoformat() not in worked]
            cur = conn.execute(
                """INSERT OR IGNORE INTO attendance_violations (user_id, week_start_date, week_end_date, required_days,
                       actual_days, absent_dates, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (u["id"], start.isoformat(), end.isoformat(), quota, len(worked), json.dumps(absent), now_iso()))
            if cur.rowcount:
                created.append(get(conn, cur.lastrowid))
                log_event(conn, user_id=u["id"], actor_id=0, event_type="ATTENDANCE_VIOLATION", key=svc.new_key(),
                          new={"violation": cur.lastrowid, "required": quota, "actual": len(worked)},
                          description=f"{start.isoformat()}…{end.isoformat()}")
    return created


def unmarked(conn: sqlite3.Connection, day: date) -> list[dict]:
    """People with «Работаю» who have neither a shift nor a day off on `day` (paused/blocked — skipped)."""
    marked = svc.marked_on(conn, day)
    return [dict(u) for u in conn.execute(
        """SELECT * FROM users WHERE status = 'approved' AND account_state = 'active' AND work_status = 'working'
           ORDER BY first_name, last_name""")
        if u["id"] not in marked and perms.role_of(dict(u)) in (perms.EMPLOYEE, perms.TRAINER)]


def scan_unmarked(conn: sqlite3.Connection, day: date) -> list[dict]:
    """At the deadline: one violation per person who still hasn't marked `day`. Safe to run again."""
    created = []
    with transaction(conn):
        for u in unmarked(conn, day):
            cur = conn.execute(
                """INSERT OR IGNORE INTO attendance_violations (user_id, kind, week_start_date, week_end_date, required_days,
                       actual_days, absent_dates, status, created_at) VALUES (?, 'unmarked', ?, ?, 1, 0, ?, 'pending', ?)""",
                (u["id"], day.isoformat(), day.isoformat(), json.dumps([day.isoformat()]), now_iso()))
            if cur.rowcount:
                created.append(get(conn, cur.lastrowid))
                log_event(conn, user_id=u["id"], actor_id=0, event_type="UNMARKED_DAY_VIOLATION", key=svc.new_key(),
                          new={"violation": cur.lastrowid, "day": day.isoformat()})
    return created


def set_norm(conn: sqlite3.Connection, actor: dict, user_id: int, norm: int | None, key: str) -> dict:
    """A personal norm: owners and admins for anyone, a trainer for people of their network."""
    if norm is not None and (isinstance(norm, bool) or not isinstance(norm, int) or not 0 <= norm <= 7):
        raise ServiceError("Норма — от 0 до 7 дней в неделю", 422, "bad_norm")
    _check_key(key)
    with transaction(conn):
        target = svc.get_user(conn, user_id)
        if target is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        if _seen(conn, key):
            return target
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "review", target):
            raise ServiceError("Недостаточно прав", 403, "forbidden")
        if target["min_weekly_shifts"] == norm:
            return target
        conn.execute("UPDATE users SET min_weekly_shifts = ? WHERE id = ?", (norm, user_id))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type="WEEKLY_NORM_SET", key=key,
                  old={"norm": target["min_weekly_shifts"]}, new={"norm": norm})
    return svc.get_user(conn, user_id)


# ---------------------------------------------------------------- decisions


def _comment(text: str | None, required: bool = True) -> str | None:
    text = (text or "").strip()[:MAX_COMMENT]
    if not text and required:
        raise ServiceError("Комментарий обязателен", 422, "comment_required")
    return text or None


def _media(files: list[str] | None) -> list[str]:
    files = [f for f in (files or []) if isinstance(f, str) and f.strip()]
    if len(files) > MAX_MEDIA:
        raise ServiceError(f"Не больше {MAX_MEDIA} файлов", 422, "too_many_files")
    if any(len(f) > 300 for f in files):
        raise ServiceError("Некорректный файл", 422, "bad_media")
    return files


def _open(conn, actor: dict, violation_id: int) -> tuple[dict, dict, dict]:
    """Inside the transaction: the pending violation, the fresh actor and the employee. A second decision is a 409."""
    v = get(conn, violation_id)
    if v is None:
        raise ServiceError("Заявка не найдена", 404, "not_found")
    actor = _fresh_actor(conn, actor)
    target = svc.get_user(conn, v["user_id"])
    if not perms.can(actor, "review", target):
        raise ServiceError("Недостаточно прав", 403, "forbidden")
    if v["status"] != "pending":
        raise ServiceError("Заявка уже обработана", 409, "already_resolved")
    return v, actor, target


def fine(conn: sqlite3.Connection, actor: dict, violation_id: int, units: int, comment: str,
         files: list[str] | None, key: str) -> Resolution:
    """A fine in units at the person's own rate (ТЗ 27.10 §17, §19)."""
    units, comment, files = finance.units_of(units), _comment(comment), _media(files)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            v = get(conn, violation_id)
            return Resolution(v, False, finance.get(conn, v["linked_fine_transaction_id"]) if v else None)
        v, actor, target = _open(conn, actor, violation_id)
        status = finance.fine_status(actor, target)
        tx = finance.record_fine(conn, user_id=v["user_id"], units=units, reason=comment,
                                 actor_tg=actor["telegram_id"], status=status, source="attendance",
                                 event_date=v["week_end_date"], media=files[0] if files else None)
        conn.execute(
            """UPDATE attendance_violations SET status = 'penalized', resolution_admin_id = ?, resolution_comment = ?,
                   resolution_media_ids = ?, linked_fine_transaction_id = ?, resolved_at = ? WHERE id = ?""",
            (actor["id"], comment, json.dumps(files), tx["id"], now_iso(), violation_id))
        log_event(conn, user_id=v["user_id"], actor_id=actor["telegram_id"], event_type="VIOLATION_PENALIZED", key=key,
                  new={"violation": violation_id, "tx": tx["id"], "units": units, "amount": tx["amount"], "status": status},
                  description=comment)
    return Resolution(get(conn, violation_id), True, tx)


def waive(conn: sqlite3.Connection, actor: dict, violation_id: int, comment: str, files: list[str] | None,
          key: str) -> Resolution:
    # «Простить»: the reason is optional (kept as "" — the table wants a comment on every decision)
    comment, files = _comment(comment, required=False) or "", _media(files)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return Resolution(get(conn, violation_id), False)
        v, actor, _ = _open(conn, actor, violation_id)
        conn.execute(
            """UPDATE attendance_violations SET status = 'waived', resolution_admin_id = ?, resolution_comment = ?,
                   resolution_media_ids = ?, resolved_at = ? WHERE id = ?""",
            (actor["id"], comment, json.dumps(files), now_iso(), violation_id))
        log_event(conn, user_id=v["user_id"], actor_id=actor["telegram_id"], event_type="VIOLATION_WAIVED", key=key,
                  new={"violation": violation_id}, description=comment)
    return Resolution(get(conn, violation_id), True)


def reopen_for_rejected_fine(conn: sqlite3.Connection, tx: dict) -> dict | None:
    """An owner declined a trainer's violation fine: the violation waits for a decision again.
    Called inside finance.decide's transaction."""
    v = by_tx(conn, tx["id"])
    if v is None or v["status"] != "penalized":
        return None
    conn.execute(
        """UPDATE attendance_violations SET status = 'pending', resolution_admin_id = NULL, resolution_comment = NULL,
               resolution_media_ids = NULL, linked_fine_transaction_id = NULL, resolved_at = NULL WHERE id = ?""",
        (v["id"],))
    return get(conn, v["id"])
