"""Schedule violations (ТЗ 12.4): fewer work days in a week than `weekly_attendance_quota`.

Every Monday the past week is checked; each person below the norm gets one attendance_violation
waiting for a decision. Staff resolve it exactly once:

    pending ── fine (amount + comment + up to 5 photos) ──> penalized   (a fine in the ledger)
            └─ waive (comment + up to 5 photos) ─────────> waived      (no money)

A trainer's fine waits for an owner like any trainer's fine (ТЗ 5.3). If the owner declines it,
the violation goes back to pending so somebody decides again.

A work day is a day with a live shift (active/restored) that didn't end as a no-show.
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
    return f"Неделя {start.isocalendar()[1]} ({ddmm(start)} – {ddmm(end)})"


def default_comment(v: dict) -> str:
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


def public(conn: sqlite3.Connection, v: dict, nets: dict[int, str] | None = None) -> dict:
    user = svc.get_user(conn, v["user_id"])
    resolver = svc.get_user(conn, v["resolution_admin_id"]) if v["resolution_admin_id"] else None
    tx = finance.get(conn, v["linked_fine_transaction_id"]) if v["linked_fine_transaction_id"] else None
    nets = nets if nets is not None else {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM networks")}
    return {
        "id": v["id"],
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
        "resolution_comment": v["resolution_comment"],
        "resolved_by": svc.full_name(resolver) if resolver else None,
        "resolved_at": v["resolved_at"],
        "media_count": len(media_ids(v)),
        "fine": {"id": tx["id"], "amount": tx["amount"], "status": tx["status"]} if tx else None,
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


def scan(conn: sqlite3.Connection, quota: int, today: date | None = None) -> list[dict]:
    """Create violations for the last finished week (Mon–Sun before `today`). Safe to run again:
    one violation per person and week. People paused, blocked or approved during that week are skipped."""
    if quota <= 0:
        return []
    today = today or today_local()
    start = week_start(today) - timedelta(days=7)
    end = start + timedelta(days=6)
    created = []
    with transaction(conn):
        for u in conn.execute(
            """SELECT * FROM users WHERE status = 'approved' AND account_state = 'active'
               AND (decided_at IS NULL OR substr(decided_at, 1, 10) < ?)""", (start.isoformat(),)).fetchall():
            u = dict(u)
            if perms.role_of(u) not in (perms.EMPLOYEE, perms.TRAINER):
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


# ---------------------------------------------------------------- decisions


def _comment(text: str | None) -> str:
    text = (text or "").strip()[:MAX_COMMENT]
    if not text:
        raise ServiceError("Комментарий обязателен", 422, "comment_required")
    return text


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


def fine(conn: sqlite3.Connection, actor: dict, violation_id: int, amount: int, comment: str,
         files: list[str] | None, key: str) -> Resolution:
    amount, comment, files = finance._amount(amount), _comment(comment), _media(files)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            v = get(conn, violation_id)
            return Resolution(v, False, finance.get(conn, v["linked_fine_transaction_id"]) if v else None)
        v, actor, target = _open(conn, actor, violation_id)
        status = finance.fine_status(actor, target)
        tx = finance.record(conn, user_id=v["user_id"], type="fine", amount=amount, reason=comment,
                            actor_tg=actor["telegram_id"], status=status, source="attendance",
                            event_date=v["week_end_date"], media=files[0] if files else None)
        conn.execute(
            """UPDATE attendance_violations SET status = 'penalized', resolution_admin_id = ?, resolution_comment = ?,
                   resolution_media_ids = ?, linked_fine_transaction_id = ?, resolved_at = ? WHERE id = ?""",
            (actor["id"], comment, json.dumps(files), tx["id"], now_iso(), violation_id))
        log_event(conn, user_id=v["user_id"], actor_id=actor["telegram_id"], event_type="VIOLATION_PENALIZED", key=key,
                  new={"violation": violation_id, "tx": tx["id"], "amount": amount, "status": status}, description=comment)
    return Resolution(get(conn, violation_id), True, tx)


def waive(conn: sqlite3.Connection, actor: dict, violation_id: int, comment: str, files: list[str] | None,
          key: str) -> Resolution:
    comment, files = _comment(comment), _media(files)
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
