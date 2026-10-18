"""Shift reports: fact + screenshot, review, no-shows (ТЗ 3.2–3.4).

One shift has a chain of report attempts; the shift row carries the summary:

    report_state  NULL ── submit, fact ≥ plan ──> approved          (auto)
                  NULL ── submit, fact < plan ──> pending_review
        pending_review ── approve ──> approved
        pending_review ── return (comment) ──> needs_fix ── submit ──> …  (next attempt, no deadline)
        pending_review ── fine (amount, reason) ──> rejected
                  NULL ── deadline passed ──> no_show ── fine | excuse | manual result
    any state ── manual result by staff ──> approved

Deadline: max(00:00 after the shift day, shift end + 2h). Fines for reports reuse the
shortfall_* columns of the shift, so stats, the penalty log and the Excel export keep working.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from . import media
from . import permissions as perms
from . import services as svc
from .config import settings
from .db import transaction
from .services import ACTIVE, MAX_PENALTY, MAX_VOLUME, ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, now_local

GRACE = timedelta(hours=2)
REVIEWABLE = ("pending_review", "approved")
MAX_COMMENT = 500


@dataclass
class ReportResult:
    shift: dict
    report: dict | None
    changed: bool
    event: str | None = None


# ---------------------------------------------------------------- time


def shift_start(shift: dict) -> datetime:
    return datetime.combine(date.fromisoformat(shift["shift_date"]), time.fromisoformat(shift["start_time"]), tzinfo=settings.tz)


def shift_end(shift: dict) -> datetime:
    end = datetime.combine(date.fromisoformat(shift["shift_date"]), time.fromisoformat(shift["end_time"]), tzinfo=settings.tz)
    return end if end > shift_start(shift) else end + timedelta(days=1)


def deadline(shift: dict) -> datetime:
    midnight = datetime.combine(date.fromisoformat(shift["shift_date"]) + timedelta(days=1), time(0), tzinfo=settings.tz)
    return max(midnight, shift_end(shift) + GRACE)


# ---------------------------------------------------------------- reads


def get_report(conn: sqlite3.Connection, report_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row else None


def latest(conn: sqlite3.Connection, shift_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM reports WHERE shift_id = ? ORDER BY attempt DESC LIMIT 1", (shift_id,)).fetchone()
    return dict(row) if row else None


def attempts(conn: sqlite3.Connection, shift_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM reports WHERE shift_id = ? ORDER BY attempt", (shift_id,))]


def public_report(report: dict | None) -> dict | None:
    if report is None:
        return None
    return {
        "id": report["id"],
        "attempt": report["attempt"],
        "fact_volume": report["fact_volume"],
        "status": report["status"],
        "source": report["source"],
        "submitted_at": report["submitted_at"],
        "review_comment": report["review_comment"],
        "penalty_amount": report["penalty_amount"],
        "photo_url": media.photo_url(report["id"]) if report["photo_file_id"] else None,
    }


def can_submit(shift: dict, now: datetime) -> bool:
    if shift["status"] not in ACTIVE or not shift["start_time"]:
        return False
    state = shift.get("report_state")
    if state == "needs_fix":
        return True
    if state not in (None, "pending_review"):
        return False
    return shift_start(shift) <= now < deadline(shift)


def view(conn: sqlite3.Connection, shift: dict, now: datetime | None = None) -> dict | None:
    """Report block for a shift card (None for days off and cancelled shifts)."""
    if shift["status"] not in ACTIVE or not shift["start_time"]:
        return None
    now = now or now_local()
    return {
        "state": shift.get("report_state"),
        "deadline": deadline(shift).isoformat(timespec="minutes"),
        "can_submit": can_submit(shift, now),
        "started": shift_start(shift) <= now,
        "note": shift.get("report_note"),
        "fine": shift.get("shortfall_penalty") if shift.get("shortfall_status") == "fined" else None,
        "decided": shift.get("shortfall_status"),
        "last": public_report(latest(conn, shift["id"])),
    }


def _queue_row(conn, s: dict) -> dict:
    return {
        "shift_id": s["id"],
        "date": s["shift_date"],
        "user_id": s["user_id"],
        "telegram_id": s["telegram_id"],
        "name": svc.full_name(s),
        "username": s["username"],
        "location": s["location"],
        "start_time": s["start_time"],
        "end_time": s["end_time"],
        "volume": s["volume"],
        "fact_volume": s["fact_volume"],
        "state": s["report_state"],
        "decided": s["shortfall_status"],
        "fine": s["shortfall_penalty"],
        "note": s["report_note"],
        "deadline": deadline(s).isoformat(timespec="minutes"),
        "attempts": [public_report(r) for r in attempts(conn, s["id"])],
    }


def queue(conn: sqlite3.Connection, kind: str, day: date | None = None, limit: int = 200) -> list[dict]:
    """review: waiting for a decision; no_show: unresolved no-shows; missing: started shifts of `day` without a report."""
    base = """SELECT s.*, u.first_name, u.last_name, u.username, u.telegram_id FROM shifts s JOIN users u ON u.id = s.user_id
              WHERE s.status IN ('active','restored') AND s.start_time IS NOT NULL"""
    if kind == "review":
        sql, params = base + " AND s.report_state = 'pending_review' ORDER BY s.shift_date, s.start_time LIMIT ?", (limit,)
    elif kind == "no_show":
        sql = base + " AND s.report_state = 'no_show' AND s.shortfall_status IS NULL ORDER BY s.shift_date, s.start_time LIMIT ?"
        params = (limit,)
    elif kind == "missing":
        sql = base + " AND s.shift_date = ? AND (s.report_state IS NULL OR s.report_state = 'needs_fix') ORDER BY s.start_time"
        params = ((day or now_local().date()).isoformat(),)
    else:
        raise ServiceError("Неизвестный список", 422, "bad_kind")
    rows = [dict(r) for r in conn.execute(sql, params)]
    if kind == "missing":
        now = now_local()
        rows = [s for s in rows if shift_start(s) <= now]
    return [_queue_row(conn, s) for s in rows]


def counts(conn: sqlite3.Connection, only: set[int] | None = None) -> dict:
    rows = conn.execute(
        """SELECT user_id, report_state FROM shifts WHERE status IN ('active','restored')
           AND (report_state = 'pending_review' OR (report_state = 'no_show' AND shortfall_status IS NULL))"""
    ).fetchall()
    rows = [r for r in rows if only is None or r["user_id"] in only]
    return {"review": sum(r["report_state"] == "pending_review" for r in rows),
            "no_show": sum(r["report_state"] == "no_show" for r in rows)}


# ---------------------------------------------------------------- employee


def _check_value(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_VOLUME:
        raise ServiceError("Факт — целое число от 0", 422, "bad_fact")
    return value


def check_submittable(shift: dict | None, user: dict, now: datetime) -> dict:
    if shift is None or shift["user_id"] != user["id"]:
        raise ServiceError("Смена не найдена", 404, "not_found")
    if shift["status"] not in ACTIVE or not shift["start_time"]:
        raise ServiceError("Отчёт сдаётся только по состоявшейся смене", 409, "bad_status")
    state = shift["report_state"]
    if state in ("approved", "rejected"):
        raise ServiceError("По этой смене отчёт уже проверен", 409, "report_closed")
    if state == "no_show":
        raise ServiceError("Срок сдачи прошёл — решение за администратором", 409, "deadline_passed")
    if state != "needs_fix":
        if now < shift_start(shift):
            raise ServiceError("Отчёт можно сдать после начала смены", 409, "too_early")
        if now >= deadline(shift):
            raise ServiceError("Срок сдачи прошёл — решение за администратором", 409, "deadline_passed")
    return shift


def submit(conn: sqlite3.Connection, user: dict, shift_id: int, value: int, photo_file_id: str, source: str, key: str,
           now: datetime | None = None) -> ReportResult:
    """Fact + screenshot. Fact ≥ plan is approved at once; below plan waits for a review.
    While a report waits for review the employee may replace it (same attempt)."""
    now = now or now_local()
    _check_key(key)
    _check_value(value)
    if not photo_file_id:
        raise ServiceError("Прикрепите скриншот результата", 422, "photo_required")
    with transaction(conn):
        if _seen(conn, key):
            shift = svc.get_shift(conn, shift_id)
            return ReportResult(shift, latest(conn, shift_id), False)
        shift = check_submittable(svc.get_shift(conn, shift_id), user, now)
        auto = bool(shift["volume"]) and value >= shift["volume"]
        status = "auto_approved" if auto else "pending_review"
        last = latest(conn, shift_id)
        if last and last["status"] == "pending_review":
            conn.execute(
                """UPDATE reports SET fact_volume = ?, photo_file_id = ?, source = ?, status = ?, submitted_by = ?,
                                      submitted_at = ? WHERE id = ?""",
                (value, photo_file_id, source, status, user["telegram_id"], now_iso(), last["id"]),
            )
            report_id = last["id"]
        else:
            cur = conn.execute(
                """INSERT INTO reports (shift_id, user_id, attempt, fact_volume, photo_file_id, source, status,
                                        submitted_by, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (shift_id, shift["user_id"], (last["attempt"] + 1) if last else 1, value, photo_file_id, source, status,
                 user["telegram_id"], now_iso()),
            )
            report_id = cur.lastrowid
        conn.execute(
            """UPDATE shifts SET fact_volume = ?, fact_set_at = ?, fact_set_by = ?, report_state = ?, report_note = NULL,
                                 shortfall_status = NULL, shortfall_penalty = NULL, shortfall_decided_by = NULL,
                                 shortfall_decided_at = NULL, updated_at = ? WHERE id = ?""",
            (value, now_iso(), user["telegram_id"], "approved" if auto else "pending_review", now_iso(), shift_id),
        )
        event = "REPORT_AUTO_APPROVED" if auto else "REPORT_SUBMITTED"
        log_event(conn, user_id=shift["user_id"], actor_id=user["telegram_id"], event_type=event, key=key,
                  shift_id=shift_id, old={"fact_volume": shift["fact_volume"], "report_state": shift["report_state"]},
                  new={"fact_volume": value, "report_id": report_id})
    return ReportResult(svc.get_shift(conn, shift_id), get_report(conn, report_id), True, event)


# ---------------------------------------------------------------- staff


def _comment(text: str | None, required: bool, what: str) -> str | None:
    text = (text or "").strip()[:MAX_COMMENT]
    if required and not text:
        raise ServiceError(f"Укажите {what}", 422, "comment_required")
    return text or None


def _amount(amount) -> int:
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= MAX_PENALTY:
        raise ServiceError("Некорректная сумма штрафа", 422, "bad_penalty")
    return amount


def _staff_shift(conn, actor: dict, shift_id: int, capability: str) -> tuple[dict, dict]:
    actor = _fresh_actor(conn, actor)
    shift = svc.get_shift(conn, shift_id)
    if shift is None:
        raise ServiceError("Смена не найдена", 404, "not_found")
    if not perms.can(actor, capability, svc.get_user(conn, shift["user_id"])):
        raise ServiceError("Недостаточно прав", 403, "forbidden")
    if shift["user_id"] == actor["id"] and not perms.is_owner(actor):
        raise ServiceError("Свой отчёт проверяет другой администратор", 403, "own_report")
    if shift["status"] not in ACTIVE or not shift["start_time"]:
        raise ServiceError("Смена не состоялась", 409, "bad_status")
    return actor, shift


def review(conn: sqlite3.Connection, actor: dict, shift_id: int, action: str, key: str, amount: int | None = None,
           comment: str | None = None) -> ReportResult:
    """approve | return (comment) | fine (amount + reason). Staff may also overturn an approved report."""
    if action not in ("approve", "return", "fine"):
        raise ServiceError("Неизвестное действие", 422, "bad_action")
    _check_key(key)
    comment = _comment(comment, action != "approve", "комментарий" if action == "return" else "причину штрафа")
    if action == "fine":
        amount = _amount(amount)
    with transaction(conn):
        if _seen(conn, key):
            shift = svc.get_shift(conn, shift_id)
            return ReportResult(shift, latest(conn, shift_id), False)
        actor, shift = _staff_shift(conn, actor, shift_id, "fine_direct" if action == "fine" else "review")
        report = latest(conn, shift_id)
        if report is None or shift["report_state"] not in REVIEWABLE:
            raise ServiceError("Нет отчёта для проверки", 409, "no_report")
        if action == "approve" and shift["report_state"] == "approved":
            return ReportResult(shift, report, False)
        status = {"approve": "approved", "return": "rejected_retry", "fine": "rejected_penalty"}[action]
        conn.execute(
            """UPDATE reports SET status = ?, reviewed_by = ?, reviewed_at = ?, review_comment = ?, penalty_amount = ?
               WHERE id = ?""",
            (status, actor["telegram_id"], now_iso(), comment, amount if action == "fine" else None, report["id"]),
        )
        under = bool(shift["volume"]) and report["fact_volume"] < shift["volume"]
        decided = {"approve": "waived" if under else None, "return": None, "fine": "fined"}[action]
        state = {"approve": "approved", "return": "needs_fix", "fine": "rejected"}[action]
        conn.execute(
            """UPDATE shifts SET report_state = ?, report_note = ?, shortfall_status = ?, shortfall_penalty = ?,
                                 shortfall_decided_by = ?, shortfall_decided_at = ?, updated_at = ? WHERE id = ?""",
            (state, comment, decided, amount if action == "fine" else None,
             actor["telegram_id"] if decided else None, now_iso() if decided else None, now_iso(), shift_id),
        )
        event = {"approve": "REPORT_APPROVED", "return": "REPORT_RETURNED", "fine": "REPORT_FINED"}[action]
        log_event(conn, user_id=shift["user_id"], actor_id=actor["telegram_id"], event_type=event, key=key,
                  shift_id=shift_id, old={"report_state": shift["report_state"]},
                  new={"report_state": state, "penalty": amount if action == "fine" else None}, description=comment)
    return ReportResult(svc.get_shift(conn, shift_id), get_report(conn, report["id"]), True, event)


def resolve_no_show(conn: sqlite3.Connection, actor: dict, shift_id: int, action: str, key: str,
                    amount: int | None = None, comment: str | None = None) -> ReportResult:
    """ТЗ 3.3 path A: fine (amount + reason), or excuse without a fine (reason)."""
    if action not in ("fine", "excuse"):
        raise ServiceError("Неизвестное действие", 422, "bad_action")
    _check_key(key)
    comment = _comment(comment, True, "причину")
    if action == "fine":
        amount = _amount(amount)
    with transaction(conn):
        if _seen(conn, key):
            return ReportResult(svc.get_shift(conn, shift_id), None, False)
        actor, shift = _staff_shift(conn, actor, shift_id, "fine_direct" if action == "fine" else "review")
        if shift["report_state"] != "no_show":
            raise ServiceError("Смена не отмечена как неявка", 409, "not_no_show")
        if shift["shortfall_status"]:
            return ReportResult(shift, None, False)
        conn.execute(
            """UPDATE shifts SET shortfall_status = ?, shortfall_penalty = ?, shortfall_decided_by = ?,
                                 shortfall_decided_at = ?, report_note = ?, updated_at = ? WHERE id = ?""",
            ("fined" if action == "fine" else "waived", amount if action == "fine" else None, actor["telegram_id"],
             now_iso(), comment, now_iso(), shift_id),
        )
        event = "NO_SHOW_FINED" if action == "fine" else "NO_SHOW_EXCUSED"
        log_event(conn, user_id=shift["user_id"], actor_id=actor["telegram_id"], event_type=event, key=key,
                  shift_id=shift_id, new={"penalty": amount if action == "fine" else None}, description=comment)
    return ReportResult(svc.get_shift(conn, shift_id), None, True, event)


def manual_result(conn: sqlite3.Connection, actor: dict, shift_id: int, value: int, key: str,
                  photo_file_id: str | None = None, comment: str | None = None) -> ReportResult:
    """ТЗ 3.3 path B: staff enter the result themselves (optionally with their own proof) and approve the shift.
    Also used to correct an approved fact. Clears any fine decided on the report."""
    _check_key(key)
    _check_value(value)
    comment = _comment(comment, False, "")
    with transaction(conn):
        if _seen(conn, key):
            shift = svc.get_shift(conn, shift_id)
            return ReportResult(shift, latest(conn, shift_id), False)
        actor, shift = _staff_shift(conn, actor, shift_id, "review")
        if shift_start(shift) > now_local():
            raise ServiceError("Смена ещё не началась", 409, "too_early")
        last = latest(conn, shift_id)
        if last and last["status"] == "pending_review":
            conn.execute("UPDATE reports SET status = 'rejected_retry', reviewed_by = ?, reviewed_at = ?, "
                         "review_comment = 'Заменён результатом от администратора' WHERE id = ?",
                         (actor["telegram_id"], now_iso(), last["id"]))
        cur = conn.execute(
            """INSERT INTO reports (shift_id, user_id, attempt, fact_volume, photo_file_id, source, status, submitted_by,
                                    submitted_at, reviewed_by, reviewed_at, review_comment)
               VALUES (?, ?, ?, ?, ?, 'admin', 'approved', ?, ?, ?, ?, ?)""",
            (shift_id, shift["user_id"], (last["attempt"] + 1) if last else 1, value, photo_file_id, actor["telegram_id"],
             now_iso(), actor["telegram_id"], now_iso(), comment),
        )
        under = bool(shift["volume"]) and value < shift["volume"]
        conn.execute(
            """UPDATE shifts SET fact_volume = ?, fact_set_at = ?, fact_set_by = ?, report_state = 'approved',
                                 report_note = ?, shortfall_status = ?, shortfall_penalty = NULL,
                                 shortfall_decided_by = ?, shortfall_decided_at = ?, updated_at = ? WHERE id = ?""",
            (value, now_iso(), actor["telegram_id"], comment, "waived" if under else None,
             actor["telegram_id"] if under else None, now_iso() if under else None, now_iso(), shift_id),
        )
        log_event(conn, user_id=shift["user_id"], actor_id=actor["telegram_id"], event_type="REPORT_MANUAL", key=key,
                  shift_id=shift_id, old={"fact_volume": shift["fact_volume"], "report_state": shift["report_state"]},
                  new={"fact_volume": value, "report_id": cur.lastrowid}, description=comment)
    return ReportResult(svc.get_shift(conn, shift_id), get_report(conn, cur.lastrowid), True, "REPORT_MANUAL")


# ---------------------------------------------------------------- scheduler helpers


def mark_no_show(conn: sqlite3.Connection, shift_id: int, now: datetime) -> dict | None:
    """Deadline passed without a report: the shift becomes a no-show (returned once)."""
    with transaction(conn):
        shift = svc.get_shift(conn, shift_id)
        if (shift is None or shift["status"] not in ACTIVE or not shift["start_time"]
                or shift["report_state"] is not None or now < deadline(shift)):
            return None
        conn.execute("UPDATE shifts SET report_state = 'no_show', no_show_at = ?, updated_at = ? WHERE id = ?",
                     (now_iso(), now_iso(), shift_id))
        log_event(conn, user_id=shift["user_id"], actor_id=0, event_type="REPORT_NO_SHOW", key=svc.new_key(),
                  shift_id=shift_id, description=f"дедлайн {deadline(shift):%d.%m %H:%M}")
    return svc.get_shift(conn, shift_id)


def reviewers(conn: sqlite3.Connection, user: dict) -> list[int]:
    """Telegram ids of staff who decide on this employee's reports: owners, admins, the network's trainer."""
    ids = set(svc.admin_telegram_ids(conn))
    if user.get("network_id") is not None:
        ids |= {r[0] for r in conn.execute(
            """SELECT telegram_id FROM users WHERE role = 'trainer' AND status = 'approved'
               AND account_state != 'blocked' AND network_id = ?""", (user["network_id"],))}
    ids.discard(user["telegram_id"])
    return sorted(ids)
