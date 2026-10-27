"""Business rules. Every mutation runs in one transaction and is idempotent by key.

Shift lifecycle (one row per user per date):

    (none) --create--> active                       (future date)
    (none) --create--> awaiting_approval            (today: urgent, admin decides)
    awaiting_approval --admin--> active | rejected_by_admin
    (none) --pre-declare--> pre_declared_off --clear--> (none)
    active --cancel--> cancelled --restore--> restored --final cancel--> finally_cancelled

A cancelled shift is a day off («Беру выходной»): the day counts as marked and colleagues see it as off.

Changing a shift yourself (ТЗ 27.10, §15–18), Moscow time, for each shift's own date D:
    before 22:00 on D−1 (the settings' daily mark deadline) — anything: «Работаю»/«Выходной», the volume, the time,
        the point, «Беру выходной» and back, as often as needed;
    22:00 – 00:00 — only the time and the point (as often as needed); the volume and the status are fixed;
    from 00:00 of D — nothing (except an urgent booking for today); «Связаться с администратором» instead.
"""

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from . import permissions as perms
from . import pricing
from .config import settings
from .db import transaction
from .timeutil import now_iso, now_local, today_local, week_dates

ACTIVE = ("active", "restored")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_VOLUME = 10_000
MAX_LOCATION = 100
MAX_PENALTY = 1_000_000
BOOKING_WEEKS_AHEAD = 1
BUSY = ("active", "restored", "awaiting_approval")
# a day off: declared ahead, or the shift given up («Беру выходной»)
DAY_OFF = ("pre_declared_off", "cancelled", "finally_cancelled")
# soft-deleted rows: kept as history, invisible everywhere else
DEAD = ("withdrawn", "removed")
LIVE = f"status NOT IN {DEAD}"  # statuses that take a place at a point


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


@dataclass
class Result:
    shift: dict | None
    changed: bool
    event: str | None = None


# ---------------------------------------------------------------- helpers


def new_key() -> str:
    return str(uuid.uuid4())


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def full_name(user: dict) -> str:
    return f"{user['first_name']} {user['last_name']}".strip()


def _check_key(key: str) -> str:
    if not isinstance(key, str) or not (8 <= len(key) <= 64):
        raise ServiceError("Некорректный ключ запроса", 422, "bad_idempotency_key")
    return key


def _seen(conn: sqlite3.Connection, key: str) -> bool:
    return conn.execute("SELECT 1 FROM event_logs WHERE idempotency_key = ?", (key,)).fetchone() is not None


def log_event(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    actor_id: int,
    event_type: str,
    key: str,
    shift_id: int | None = None,
    old: dict | None = None,
    new: dict | None = None,
    description: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO event_logs
           (shift_id, user_id, actor_id, event_type, old_value, new_value, idempotency_key, description, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            shift_id,
            user_id,
            actor_id,
            event_type,
            json.dumps(old, ensure_ascii=False) if old is not None else None,
            json.dumps(new, ensure_ascii=False) if new is not None else None,
            key,
            description,
            now_iso(),
        ),
    )


def shortfall(shift: dict) -> int:
    """How many units short of the plan (0 if the plan was met or there is no fact yet)."""
    if shift.get("status") not in ACTIVE or shift.get("fact_volume") is None or not shift.get("volume"):
        return 0
    return max(0, shift["volume"] - shift["fact_volume"])


def shortfall_percent(shift: dict) -> int:
    return round(100 * shift["fact_volume"] / shift["volume"]) if shift.get("volume") and shift.get("fact_volume") is not None else 0


def _snapshot(shift: dict) -> dict:
    return {k: shift.get(k) for k in ("status", "start_time", "end_time", "volume", "location", "penalty_amount", "fact_volume")}


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ServiceError("Некорректная дата", 422, "bad_date") from exc


def validate_payload(start_time, end_time, volume, location) -> dict:
    if not isinstance(start_time, str) or not TIME_RE.match(start_time):
        raise ServiceError("Укажите время начала в формате ЧЧ:ММ", 422, "bad_start_time")
    if not isinstance(end_time, str) or not TIME_RE.match(end_time):
        raise ServiceError("Укажите время окончания в формате ЧЧ:ММ", 422, "bad_end_time")
    if end_time <= start_time:
        raise ServiceError("Время «До» должно быть позже «С»", 422, "bad_time_range")
    if isinstance(volume, bool) or not isinstance(volume, int) or volume <= 0:
        raise ServiceError("Объём — целое число больше нуля", 422, "bad_volume")
    if volume > MAX_VOLUME:
        raise ServiceError(f"Объём не может быть больше {MAX_VOLUME}", 422, "bad_volume")
    location = (location or "").strip() if isinstance(location, str) else ""
    if not location:
        raise ServiceError("Укажите точку", 422, "bad_location")
    if len(location) > MAX_LOCATION:
        raise ServiceError("Слишком длинное название точки", 422, "bad_location")
    return {"start_time": start_time, "end_time": end_time, "volume": volume, "location": location}


# ---------------------------------------------------------------- users


def get_user_by_tg(conn: sqlite3.Connection, telegram_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone())


def get_user(conn: sqlite3.Connection, user_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def touch_username(conn: sqlite3.Connection, user: dict, username: str | None) -> dict:
    if user["username"] != username:
        conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, user["id"]))
        user = {**user, "username": username}
    return user


def ensure_admin(conn, telegram_id: int, first_name: str, last_name: str, username: str | None) -> dict | None:
    """Admins from ADMIN_IDS get an approved admin account without a registration request."""
    if telegram_id not in settings.admin_ids:
        return get_user_by_tg(conn, telegram_id)
    with transaction(conn):
        user = get_user_by_tg(conn, telegram_id)
        if user is None:
            conn.execute(
                """INSERT INTO users (telegram_id, username, first_name, last_name, status, role,
                                      created_at, decided_at, welcome_sent)
                   VALUES (?, ?, ?, ?, 'approved', 'admin', ?, ?, 1)""",
                (telegram_id, username, first_name or "Админ", last_name or "", now_iso(), now_iso()),
            )
        elif user["role"] != "admin" or user["status"] != "approved":
            conn.execute(
                "UPDATE users SET role = 'admin', status = 'approved', welcome_sent = 1 WHERE id = ?", (user["id"],)
            )
    return get_user_by_tg(conn, telegram_id)


def admin_telegram_ids(conn: sqlite3.Connection) -> list[int]:
    """Owners and admins (trainers get notifications only about their network)."""
    ids = {
        r[0]
        for r in conn.execute(
            "SELECT telegram_id FROM users WHERE role = 'admin' AND status = 'approved' AND account_state != 'blocked'"
        )
    }
    return sorted(ids | set(settings.admin_ids))


def owner_telegram_ids() -> list[int]:
    return sorted(settings.admin_ids)


def create_registration(conn, telegram_id: int, username: str | None, first_name: str, last_name: str) -> tuple[dict, bool]:
    first_name, last_name = first_name.strip()[:100], last_name.strip()[:100]
    if not first_name or not last_name:
        raise ServiceError("Нужны имя и фамилия", 422, "bad_name")
    with transaction(conn):
        existing = get_user_by_tg(conn, telegram_id)
        if existing is not None:
            return existing, False
        cur = conn.execute(
            """INSERT INTO users (telegram_id, username, first_name, last_name, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (telegram_id, username, first_name, last_name, now_iso()),
        )
        log_event(conn, user_id=cur.lastrowid, actor_id=telegram_id, event_type="REGISTRATION_REQUESTED", key=new_key())
    return get_user(conn, cur.lastrowid), True


def decide_registration(conn, user_id: int, approve: bool, admin_tg: int) -> tuple[dict, bool]:
    with transaction(conn):
        user = get_user(conn, user_id)
        if user is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        if user["status"] != "pending":
            return user, False
        conn.execute(
            """UPDATE users SET status = ?, decided_at = ?, decided_by = ?, work_status = 'not_working',
                                work_status_at = decided_at, notify_reminders = 0 WHERE id = ?""",
            ("approved" if approve else "rejected", now_iso(), admin_tg, user_id),
        )
        log_event(
            conn,
            user_id=user_id,
            actor_id=admin_tg,
            event_type="REGISTRATION_APPROVED" if approve else "REGISTRATION_REJECTED",
            key=new_key(),
        )
    return get_user(conn, user_id), True


def claim_welcome(conn, user_id: int) -> bool:
    """True exactly once per user: whoever claims it sends the welcome message."""
    with transaction(conn):
        cur = conn.execute("UPDATE users SET welcome_sent = 1 WHERE id = ? AND welcome_sent = 0", (user_id,))
    return cur.rowcount == 1


def set_work_status(conn, user: dict, work_status: str, key: str) -> dict:
    if work_status not in ("working", "not_working"):
        raise ServiceError("Некорректный статус", 422, "bad_work_status")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get_user(conn, user["id"])
        current = get_user(conn, user["id"])
        if work_status == "working" and current["account_state"] == "paused":
            raise ServiceError("Аккаунт на паузе: статус «Работаю» включит администратор, сняв паузу", 403, "paused")
        if current["work_status"] != work_status:
            # the customer: «Не работаю» switches the bot's reminders off, «Работаю» — on
            conn.execute(
                "UPDATE users SET work_status = ?, work_status_at = ?, notify_reminders = ? WHERE id = ?",
                (work_status, now_iso(), int(work_status == "working"), user["id"]),
            )
            log_event(
                conn,
                user_id=user["id"],
                actor_id=user["telegram_id"],
                event_type="WORK_STATUS_CHANGED",
                key=key,
                old={"work_status": current["work_status"]},
                new={"work_status": work_status},
            )
    return get_user(conn, user["id"])


def status_days(user: dict, today: date | None = None) -> int | None:
    """How many days the person has had their current work status (since work_status_at)."""
    since = user.get("work_status_at") or user.get("decided_at") or user.get("created_at")
    if not since:
        return None
    return max(0, ((today or today_local()) - date.fromisoformat(since[:10])).days)


def is_owner(user: dict) -> bool:
    """Owners are the ADMIN_IDS from config (Super Admin)."""
    return perms.is_owner(user)


def team(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM users
           ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                    role = 'employee', first_name, last_name"""
    ).fetchall()
    return [dict(r) for r in rows]


def set_role(conn, actor: dict, user_id: int, role: str, key: str) -> tuple[dict, bool]:
    if role not in ("admin", "trainer", "instructor", "employee"):
        raise ServiceError("Некорректная роль", 422, "bad_role")
    if not is_owner(actor):
        raise ServiceError("Назначать роли может только владелец", 403, "owner_only")
    _check_key(key)
    with transaction(conn):
        actor = _fresh_actor(conn, actor)
        user = get_user(conn, user_id)
        if user is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        if _seen(conn, key):
            return user, False
        if is_owner(user):
            raise ServiceError("Владельцу роль не меняется", 409, "owner")
        if user["status"] != "approved":
            raise ServiceError("Сначала откройте человеку доступ", 409, "not_approved")
        if user["role"] == role:
            return user, False
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if role != "trainer":  # no longer leads a network
            conn.execute("UPDATE networks SET trainer_id = NULL WHERE trainer_id = ?", (user_id,))
        if role != "instructor":  # their people go back to the trainer — nobody is left without a lead (ТЗ 27.10 §23)
            conn.execute("UPDATE users SET instructor_id = NULL WHERE instructor_id = ?", (user_id,))
        if role != "employee":  # staff aren't assigned to an instructor
            conn.execute("UPDATE users SET instructor_id = NULL WHERE id = ?", (user_id,))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type="ROLE_CHANGED", key=key,
                  old={"role": user["role"]}, new={"role": role})
    return get_user(conn, user_id), True


def set_access(conn, actor: dict, user_id: int, allow: bool, key: str) -> tuple[dict, bool]:
    """Decide a registration request (pending), or reopen a declined one. Closing access is block/pause."""
    _check_key(key)
    with transaction(conn):
        user = get_user(conn, user_id)
        if user is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        if _seen(conn, key):
            return user, False
        if user["status"] == "approved":
            if allow:
                return user, False
            raise ServiceError("У сотрудника уже есть доступ: используйте паузу или блокировку", 409, "use_block")
        target = "approved" if allow else "rejected"
        if user["status"] == target:
            return user, False
        conn.execute(
            """UPDATE users SET status = ?, decided_at = ?, decided_by = ?, work_status = 'not_working',
                                work_status_at = decided_at, notify_reminders = 0 WHERE id = ?""",
            (target, now_iso(), actor["telegram_id"], user_id),
        )
        event = {
            ("pending", True): "REGISTRATION_APPROVED",
            ("pending", False): "REGISTRATION_REJECTED",
            ("rejected", True): "ACCESS_REOPENED",
        }[(user["status"], allow)]
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type=event, key=key,
                  old={"status": user["status"]}, new={"status": target})
    return get_user(conn, user_id), True


def _fresh_actor(conn, actor: dict) -> dict:
    """Permissions are checked against the current DB row, never a stale copy."""
    fresh = get_user(conn, actor["id"]) if actor.get("id") else None
    if fresh is None or fresh["status"] != "approved" or fresh["account_state"] == "blocked":
        raise ServiceError("Нет доступа", 403, "forbidden")
    return fresh


def set_account_state(conn, actor: dict, user_id: int, state: str, reason: str | None, key: str) -> tuple[dict, bool]:
    """active / paused (owner, admin, own trainer; reason required) / blocked (owner only)."""
    if state not in ("active", "paused", "blocked"):
        raise ServiceError("Некорректное состояние", 422, "bad_state")
    reason = (reason or "").strip() or None
    _check_key(key)
    with transaction(conn):
        actor = _fresh_actor(conn, actor)
        user = get_user(conn, user_id)
        if user is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        if _seen(conn, key):
            return user, False
        if user["status"] != "approved":
            raise ServiceError("Сначала примите заявку", 409, "not_approved")
        if is_owner(user):
            raise ServiceError("Владельца нельзя поставить на паузу или заблокировать", 409, "owner")
        if user["id"] == actor["id"]:
            raise ServiceError("Нельзя менять состояние своего аккаунта", 409, "self")
        current = user["account_state"]
        if "blocked" in (state, current) and not perms.can(actor, "block"):
            raise ServiceError("Блокировать и разблокировать может только владелец", 403, "owner_only")
        if not perms.can(actor, "pause", user):
            raise ServiceError("Нет прав на этого сотрудника", 403, "forbidden")
        if state == "paused" and not reason:
            raise ServiceError("Укажите причину паузы", 422, "reason_required")
        if current == state:
            return user, False
        conn.execute(
            """UPDATE users SET account_state = ?, state_reason = ?, state_changed_at = ?, state_changed_by = ?,
                                work_status = CASE WHEN ? = 'active' THEN work_status ELSE 'not_working' END,
                                work_status_at = CASE WHEN ? != 'active' AND work_status = 'working' THEN ?
                                                      ELSE work_status_at END,
                                notify_reminders = CASE WHEN ? = 'active' THEN notify_reminders ELSE 0 END,
                                role = CASE WHEN ? = 'blocked' THEN 'employee' ELSE role END
               WHERE id = ?""",
            (state, reason if state != "active" else None, now_iso(), actor["telegram_id"], state, state, now_iso(), state,
             state, user_id),
        )
        if state == "blocked":
            conn.execute("UPDATE networks SET trainer_id = NULL WHERE trainer_id = ?", (user_id,))
        event = {"paused": "ACCOUNT_PAUSED", "blocked": "ACCOUNT_BLOCKED", "active": "ACCOUNT_ACTIVATED"}[state]
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type=event, key=key,
                  old={"account_state": current}, new={"account_state": state}, description=reason)
    return get_user(conn, user_id), True


def list_users(conn, statuses=("approved",)) -> list[dict]:
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT * FROM users WHERE status IN ({marks}) ORDER BY first_name, last_name", tuple(statuses)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- shifts: reads


def get_shift(conn, shift_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone())


def get_user_shift_on(conn, user_id: int, day: date) -> dict | None:
    return _row(
        conn.execute(f"SELECT * FROM shifts WHERE user_id = ? AND shift_date = ? AND {LIVE}", (user_id, day.isoformat())).fetchone()
    )


def day_totals(conn, day: date) -> tuple[int, int]:
    row = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(volume), 0) FROM shifts WHERE shift_date = ? AND status IN {ACTIVE}",
        (day.isoformat(),),
    ).fetchone()
    return row[0], row[1]


def active_shifts_on(conn, day: date) -> list[dict]:
    rows = conn.execute(
        f"""SELECT s.*, u.first_name, u.last_name, u.username, u.telegram_id, u.network_id, u.instructor_id
            FROM shifts s JOIN users u ON u.id = s.user_id
            WHERE s.shift_date = ? AND s.status IN {ACTIVE}
            ORDER BY s.start_time, u.first_name""",
        (day.isoformat(),),
    ).fetchall()
    return [dict(r) for r in rows]


def shifts_on(conn, day: date) -> list[dict]:
    rows = conn.execute(
        """SELECT s.*, u.first_name, u.last_name, u.username, u.telegram_id
           FROM shifts s JOIN users u ON u.id = s.user_id
           WHERE s.shift_date = ? AND s.status NOT IN ('withdrawn','removed')
           ORDER BY s.start_time IS NULL, s.start_time, u.first_name""",
        (day.isoformat(),),
    ).fetchall()
    return [dict(r) for r in rows]


SCHEDULE = {"deadline": "22:00"}  # daily_mark_deadline from the settings; app_settings keeps it in sync


def mark_deadline(day: date) -> datetime:
    """The deadline the day before the shift (22:00 by default): from then on the schedule for `day` is fixed."""
    hour, minute = map(int, SCHEDULE["deadline"].split(":"))
    return datetime.combine(day - timedelta(days=1), time(hour, minute), tzinfo=settings.tz)


def clock(today: date | None = None, now: datetime | None = None) -> datetime:
    """The moment rules are checked against: `now`, else the real time — moved to `today` noon when a caller
    passes another day (tests and back-dated jobs)."""
    if now is not None:
        return now.astimezone(settings.tz) if now.tzinfo else now.replace(tzinfo=settings.tz)
    real = now_local()
    return real if today is None or real.date() == today else datetime.combine(today, time(12), tzinfo=settings.tz)


def phase(day: date, now: datetime) -> str:
    """open — change anything; late — 22:00–00:00, only the time and the point; started — the day has come; past."""
    if now.date() > day:
        return "past"
    if now.date() == day:
        return "started"
    return "open" if now < mark_deadline(day) else "late"


def _after_deadline(what: str) -> ServiceError:
    return ServiceError(f"После {SCHEDULE['deadline']} накануне смены {what} нельзя — напишите администратору",
                        409, "after_deadline")


def serialize_shift(shift: dict | None, today: date, conn: sqlite3.Connection | None = None,
                    now: datetime | None = None, staff: bool = False) -> dict | None:
    """conn adds the report block (state, deadline, last attempt); `now` — see clock().
    Without `staff` a fine counted in units shows no rubles (ТЗ 27.10 §19); an old fine keeps them."""
    if shift is None:
        return None
    day = date.fromisoformat(shift["shift_date"])
    past = day < today
    status = shift["status"]
    volume = shift["volume"]
    reported = bool(shift.get("report_state"))
    when = phase(day, clock(today, now))
    changeable = not past and not reported and status in ACTIVE
    data = {
        "id": shift["id"],
        "date": shift["shift_date"],
        "status": status,
        "start_time": shift["start_time"],
        "end_time": shift["end_time"],
        "volume": volume,
        "location": shift["location"],
        "edit_count": shift["edit_count"],
        "is_urgent": bool(shift["is_urgent"]),
        "penalty_amount": shift["penalty_amount"],
        "admin_locked": bool(shift["admin_locked"]),
        # a report was sent: the shift stays as reported (changes — through an admin)
        # the schedule's phase for this date: open → late (22:00–00:00) → started → past
        "phase": when,
        "deadline": mark_deadline(day).isoformat(timespec="minutes"),
        "can_edit": changeable and when in ("open", "late"),
        "volume_open": changeable and when == "open",  # from the deadline only the time and the point change
        "can_cancel": changeable and when == "open",
        "can_restore": status == "cancelled" and not shift["admin_locked"] and when == "open",
        "can_clear_off": status == "pre_declared_off" and when == "open",
        # after the deadline: a message to the admins instead of a change
        "can_contact_admin": when in ("late", "started") and status in (*BUSY, *DAY_OFF),
        "fact_volume": shift.get("fact_volume"),
        "report_state": shift.get("report_state"),
        "shortfall": shortfall(shift),
        "shortfall_status": shift.get("shortfall_status"),
        "shortfall_penalty": shift.get("shortfall_penalty"),
    }
    # the person's own rates (personal / network / common) when the database is at hand
    own = None
    if conn is not None:
        from . import rates as rates_mod

        own = rates_mod.for_user(conn, shift["user_id"])
    if shift.get("training"):
        # a trainee's training shift is not paid
        data.update(training=True, rate=0, expected_pay=0, lazy=False)
        if shift.get("fact_volume") is not None:
            data["fact_pay"] = 0
    elif volume:
        data.update(
            rate=pricing.calculate_rate(volume, own), expected_pay=pricing.expected_pay(volume, own),
            lazy=pricing.is_lazy(volume, own)
        )
    if shift.get("fact_volume") is not None and not shift.get("training"):
        data["fact_pay"] = pricing.expected_pay(shift["fact_volume"], own)
    if conn is not None:
        from . import reports  # reports builds on this module

        data["report"] = reports.view(conn, shift)
        if shift.get("shortfall_status") == "fined":  # the employee sees units, not rubles (ТЗ 27.10 §19)
            fine = (data["report"] or {}).get("fine_units")
            data["shortfall_units"] = fine if fine is not None else next(
                (t["pieces"] for t in _shift_fines(conn, shift["id"])), None)
        if not staff:
            report = data["report"]
            if report and report.get("fine_units"):
                report["fine"] = None
            if report and report.get("fine_pending_units"):
                report["fine_pending"] = None
            if data.get("shortfall_units"):
                data["shortfall_penalty"] = None
    return data


def week_view(conn, user: dict, offset: int, today: date | None = None, hide_names: bool = False) -> dict:
    """hide_names: colleagues stay on the timeline as «Коллега» (the setting «show_colleague_names», ТЗ №9)."""
    today = today or today_local()
    days = week_dates(offset, today)
    first, last = days[0].isoformat(), days[-1].isoformat()
    mine = {
        r["shift_date"]: dict(r)
        for r in conn.execute(
            f"SELECT * FROM shifts WHERE user_id = ? AND shift_date BETWEEN ? AND ? AND {LIVE}", (user["id"], first, last)
        )
    }
    others: dict[str, list[dict]] = {}
    for r in conn.execute(
        f"""SELECT s.shift_date, s.status, s.start_time, s.end_time, s.location, u.first_name, u.last_name
            FROM shifts s JOIN users u ON u.id = s.user_id
            WHERE s.shift_date BETWEEN ? AND ? AND s.user_id != ?
              AND s.status IN {(*ACTIVE, *DAY_OFF)}
            ORDER BY s.start_time IS NULL, s.start_time, u.first_name""",
        (first, last, user["id"]),
    ):
        others.setdefault(r["shift_date"], []).append(dict(r))
    load: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        f"""SELECT shift_date, location, COUNT(*) AS n FROM shifts
            WHERE shift_date BETWEEN ? AND ? AND status IN {BUSY} GROUP BY shift_date, location""",
        (first, last),
    ):
        load.setdefault(r["shift_date"], {})[r["location"]] = r["n"]
    result = []
    for d in days:
        iso = d.isoformat()
        colleagues, off = [], []
        for c in others.get(iso, []):
            name = "Коллега" if hide_names else full_name(c)
            if hide_names:
                c = {**c, "first_name": ""}
            if c["status"] in DAY_OFF:
                off.append({"name": name, "first_name": c["first_name"]})
            else:
                colleagues.append(
                    {
                        "name": name,
                        "first_name": c["first_name"],
                        "start_time": c["start_time"],
                        "end_time": c["end_time"],
                        "location": c["location"],
                    }
                )
        result.append(
            {
                "date": iso,
                # ТЗ 27.10 §15–16: what can still be done for this date and until when
                "phase": phase(d, clock(today)),
                "deadline": mark_deadline(d).isoformat(timespec="minutes"),
                "weekday": d.weekday(),
                "is_past": d < today,
                "is_today": d == today,
                "my_shift": serialize_shift(mine.get(iso), today, conn),
                "colleagues": colleagues,
                "colleagues_off": off,
                "names_hidden": hide_names,
                "points_load": load.get(iso, {}),
            }
        )
    return {"offset": offset, "start": first, "end": last, "today": today.isoformat(), "days": result}


def history(conn, user: dict, weeks_back: int, today: date | None = None) -> list[dict]:
    today = today or today_local()
    weeks = []
    for offset in range(-1, -weeks_back - 1, -1):
        days = week_dates(offset, today)
        rows = conn.execute(
            f"SELECT * FROM shifts WHERE user_id = ? AND shift_date BETWEEN ? AND ? AND {LIVE} ORDER BY shift_date",
            (user["id"], days[0].isoformat(), days[-1].isoformat()),
        ).fetchall()
        if rows:
            weeks.append(
                {
                    "start": days[0].isoformat(),
                    "end": days[-1].isoformat(),
                    "shifts": [serialize_shift(dict(r), today) for r in rows],
                }
            )
    return weeks


def report_fine_reason(s: dict) -> str:
    """Why a report-related fine (shortfall_* columns) was given."""
    if s.get("report_state") == "no_show":
        head = "Неявка: отчёт не сдан"
    elif s.get("fact_volume") is not None and s.get("volume") and s["fact_volume"] < s["volume"]:
        head = f"Не выполнена норма: план {s['volume']}, факт {s['fact_volume']} шт."
    else:
        head = "Отчёт отклонён"
    return f"{head.rstrip('.')}. {s['report_note']}" if s.get("report_note") else head


def user_penalties(conn, user_id: int) -> list[dict]:
    """The employee's own fines (a trainer's fine is shown only once an owner approved it)."""
    from . import finance

    out = []
    for tx in finance.journal(conn, user_id=user_id, types=("fine",),
                              statuses=("draft", "active", "paid", "offset", "cancelled")):
        out.append({"id": tx["shift_id"] or -tx["id"], "tx_id": tx["id"], "kind": tx["source"], "shift_date": tx["event_date"],
                    "penalty_amount": tx["amount"], "reason": tx["reason"], "status": tx["status"]})
    return out


# ---------------------------------------------------------------- shifts: employee mutations


def _require_working(user: dict) -> None:
    if user.get("account_state") == "paused":
        reason = f": {user['state_reason']}" if user.get("state_reason") else ""
        raise ServiceError(f"Аккаунт на паузе{reason}. Запись в график недоступна", 403, "paused")
    if user["work_status"] != "working":
        raise ServiceError("Сначала включите статус «Работаю» в профиле", 403, "not_working")


def _require_bookable(day: date, today: date) -> None:
    if day < today:
        raise ServiceError("Нельзя записаться на прошедшую дату", 409, "past_date")
    if day > week_dates(BOOKING_WEEKS_AHEAD, today)[-1]:
        raise ServiceError("Запись открыта только на текущую и следующую неделю", 409, "too_far")


def _own_shift(conn, user: dict, shift_id: int, today: date) -> dict:
    shift = get_shift(conn, shift_id)
    if shift is None or shift["user_id"] != user["id"]:
        raise ServiceError("Смена не найдена", 404, "not_found")
    if date.fromisoformat(shift["shift_date"]) < today:
        raise ServiceError("Эта дата уже прошла", 409, "past_date")
    return shift


def _shift_fines(conn, shift_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT * FROM financial_transactions WHERE shift_id = ? AND type = 'fine' AND source IN ('report','no_show')
           AND status NOT IN ('cancelled','rejected') ORDER BY id DESC""", (shift_id,))]


def _require_no_report(shift: dict) -> None:
    """A report was sent: the plan it was checked against stays; changes go through an admin."""
    if shift.get("report_state"):
        raise ServiceError("По этой смене уже сдан отчёт — изменить или отменить её может администратор", 409, "has_report")


def create_shift(conn, user: dict, day: date, payload: dict, key: str, today: date | None = None,
                 now: datetime | None = None) -> Result:
    """Before the deadline — a shift; from 00:00 of the day itself — an urgent request the admins decide."""
    now = clock(today, now)
    today = now.date()
    _check_key(key)
    data = validate_payload(**payload)
    with transaction(conn):
        if _seen(conn, key):
            return Result(get_user_shift_on(conn, user["id"], day), False)
        user = get_user(conn, user["id"])
        _require_working(user)
        _require_bookable(day, today)
        if get_user_shift_on(conn, user["id"], day) is not None:
            raise ServiceError("На эту дату у вас уже есть запись", 409, "already_exists")
        if phase(day, now) == "late":
            raise ServiceError(f"После {SCHEDULE['deadline']} записаться на завтра нельзя. С 00:00 можно попроситься "
                               "на сегодня — срочной записью", 409, "after_deadline")
        data["location"] = check_point(conn, day, data["location"])
        urgent = day == today
        status = "awaiting_approval" if urgent else "active"
        now = now_iso()
        # a trainee's shifts are training ones: in the schedule as «стажёр», not paid (the customer's module 3)
        training = bool(user.get("trainee_since") and not user.get("trainee_done_at"))
        cur = conn.execute(
            """INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status,
                                   is_urgent, created_at, updated_at, training)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], day.isoformat(), data["start_time"], data["end_time"], data["volume"], data["location"],
             status, int(urgent), now, now, int(training)),
        )
        event = "SHIFT_URGENT_REQUESTED" if urgent else "SHIFT_CREATED"
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type=event, key=key,
                  shift_id=cur.lastrowid, new={**data, "status": status, "date": day.isoformat()})
    return Result(get_shift(conn, cur.lastrowid), True, event)


def edit_shift(conn, user: dict, shift_id: int, payload: dict, key: str, today: date | None = None,
               now: datetime | None = None) -> Result:
    """Before the deadline — anything; 22:00–00:00 — the time and the point only; from 00:00 — nothing."""
    now = clock(today, now)
    _check_key(key)
    data = validate_payload(**payload)
    with transaction(conn):
        if _seen(conn, key):
            return Result(get_shift(conn, shift_id), False)
        shift = _own_shift(conn, user, shift_id, now.date())
        if shift["status"] not in ACTIVE:
            raise ServiceError("Изменить можно только активную смену", 409, "bad_status")
        _require_no_report(shift)
        when = phase(date.fromisoformat(shift["shift_date"]), now)
        if when not in ("open", "late"):
            raise ServiceError("День смены уже наступил — изменения через администратора", 409, "day_started")
        if when == "late" and data["volume"] != shift["volume"]:
            raise ServiceError(f"После {SCHEDULE['deadline']} объём не меняется — только время и точка", 409, "volume_locked")
        data["location"] = check_point(conn, date.fromisoformat(shift["shift_date"]), data["location"], exclude=shift_id)
        conn.execute(
            """UPDATE shifts SET start_time = ?, end_time = ?, volume = ?, location = ?,
                                 edit_count = edit_count + 1, updated_at = ? WHERE id = ?""",
            (data["start_time"], data["end_time"], data["volume"], data["location"], now_iso(), shift_id),
        )
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="SHIFT_EDITED_BY_USER",
                  key=key, shift_id=shift_id, old=_snapshot(shift), new=data)
    return Result(get_shift(conn, shift_id), True, "SHIFT_EDITED_BY_USER")


def cancel_shift(conn, user: dict, shift_id: int, key: str, today: date | None = None,
                 now: datetime | None = None) -> Result:
    """«Беру выходной»: the shift is cancelled and the day is a day off. Only before the deadline."""
    now = clock(today, now)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return Result(get_shift(conn, shift_id), False)
        shift = _own_shift(conn, user, shift_id, now.date())
        _require_no_report(shift)
        if shift["status"] not in ACTIVE:
            raise ServiceError("Эту смену нельзя отменить", 409, "bad_status")
        if phase(date.fromisoformat(shift["shift_date"]), now) != "open":
            raise _after_deadline("взять выходной")
        stamp = now_iso()
        conn.execute("UPDATE shifts SET status = 'cancelled', cancelled_at = ?, updated_at = ? WHERE id = ?",
                     (stamp, stamp, shift_id))
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="SHIFT_CANCELLED", key=key,
                  shift_id=shift_id, old=_snapshot(shift), new={"status": "cancelled"})
    return Result(get_shift(conn, shift_id), True, "SHIFT_CANCELLED")


def restore_shift(conn, user: dict, shift_id: int, payload: dict, key: str, today: date | None = None,
                  now: datetime | None = None) -> Result:
    """«Передумал, выйду»: back to work on a day taken off — before the deadline, as often as needed."""
    now = clock(today, now)
    _check_key(key)
    data = validate_payload(**payload)
    with transaction(conn):
        if _seen(conn, key):
            return Result(get_shift(conn, shift_id), False)
        user = get_user(conn, user["id"])
        shift = _own_shift(conn, user, shift_id, now.date())
        _require_working(user)
        if shift["status"] != "cancelled" or shift["admin_locked"]:
            raise ServiceError("Эту смену нельзя восстановить", 409, "cannot_restore")
        if phase(date.fromisoformat(shift["shift_date"]), now) != "open":
            raise _after_deadline("вернуться в график")
        data["location"] = check_point(conn, date.fromisoformat(shift["shift_date"]), data["location"], exclude=shift_id)
        stamp = now_iso()
        conn.execute(
            """UPDATE shifts SET status = 'active', start_time = ?, end_time = ?, volume = ?, location = ?,
                                 restored_at = ?, updated_at = ? WHERE id = ?""",
            (data["start_time"], data["end_time"], data["volume"], data["location"], stamp, stamp, shift_id),
        )
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="SHIFT_RESTORED", key=key,
                  shift_id=shift_id, old=_snapshot(shift), new={**data, "status": "active"})
    return Result(get_shift(conn, shift_id), True, "SHIFT_RESTORED")


def pre_declare_off(conn, user: dict, day: date, key: str, today: date | None = None,
                    now: datetime | None = None) -> Result:
    now = clock(today, now)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return Result(get_user_shift_on(conn, user["id"], day), False)
        user = get_user(conn, user["id"])
        _require_working(user)
        _require_bookable(day, now.date())
        if get_user_shift_on(conn, user["id"], day) is not None:
            raise ServiceError("На эту дату у вас уже есть запись", 409, "already_exists")
        if phase(day, now) != "open":
            raise _after_deadline("отметить выходной")
        stamp = now_iso()
        cur = conn.execute(
            """INSERT INTO shifts (user_id, shift_date, status, created_at, updated_at)
               VALUES (?, ?, 'pre_declared_off', ?, ?)""",
            (user["id"], day.isoformat(), stamp, stamp),
        )
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="PRE_DECLARED_OFF_SET",
                  key=key, shift_id=cur.lastrowid, new={"date": day.isoformat()})
    return Result(get_shift(conn, cur.lastrowid), True, "PRE_DECLARED_OFF_SET")


def clear_pre_declare_off(conn, user: dict, shift_id: int, key: str, today: date | None = None,
                          now: datetime | None = None) -> Result:
    now = clock(today, now)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return Result(None, False)
        shift = _own_shift(conn, user, shift_id, now.date())
        if shift["status"] != "pre_declared_off":
            raise ServiceError("День не отмечен как нерабочий", 409, "bad_status")
        if phase(date.fromisoformat(shift["shift_date"]), now) != "open":
            raise _after_deadline("выйти вместо выходного")
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="PRE_DECLARED_OFF_CLEARED",
                  key=key, shift_id=shift_id, old={"date": shift["shift_date"], "status": "pre_declared_off"})
        conn.execute("UPDATE shifts SET status = 'withdrawn', updated_at = ? WHERE id = ?", (now_iso(), shift_id))
    return Result(None, True, "PRE_DECLARED_OFF_CLEARED")


MAX_CONTACT = 500


def contact_admin(conn, user: dict, shift_id: int, text: str, key: str, today: date | None = None,
                  now: datetime | None = None) -> dict:
    """«Связаться с администратором» after the deadline: the shift doesn't change — an admin decides."""
    now = clock(today, now)
    text = " ".join((text or "").split())[:MAX_CONTACT]
    if len(text) < 3:
        raise ServiceError("Напишите, что случилось", 422, "text_required")
    _check_key(key)
    with transaction(conn):
        shift = _own_shift(conn, user, shift_id, now.date())
        if _seen(conn, key):
            return shift
        if phase(date.fromisoformat(shift["shift_date"]), now) == "open":
            raise ServiceError("До дедлайна смену можно изменить самому", 409, "still_open")
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="SHIFT_ADMIN_CONTACTED", key=key,
                  shift_id=shift_id, description=text)
    return shift


# ---------------------------------------------------------------- shifts: admin


def decide_urgent(conn, shift_id: int, approve: bool, admin_tg: int) -> Result:
    with transaction(conn):
        shift = get_shift(conn, shift_id)
        if shift is None:
            raise ServiceError("Смена не найдена", 404, "not_found")
        if shift["status"] != "awaiting_approval":
            return Result(shift, False)
        status = "active" if approve else "rejected_by_admin"
        conn.execute("UPDATE shifts SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), shift_id))
        event = "SHIFT_URGENT_APPROVED" if approve else "SHIFT_URGENT_REJECTED"
        log_event(conn, user_id=shift["user_id"], actor_id=admin_tg, event_type=event, key=new_key(),
                  shift_id=shift_id, old={"status": shift["status"]}, new={"status": status})
    return Result(get_shift(conn, shift_id), True, event)


def set_penalty(conn, shift_id: int, amount: int, admin_tg: int, override: bool = False) -> Result:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0 or amount > MAX_PENALTY:
        raise ServiceError("Некорректная сумма штрафа", 422, "bad_penalty")
    with transaction(conn):
        shift = get_shift(conn, shift_id)
        if shift is None:
            raise ServiceError("Смена не найдена", 404, "not_found")
        if shift["status"] != "finally_cancelled":
            raise ServiceError("Штраф назначается только за окончательную отмену", 409, "bad_status")
        if shift["penalty_amount"] is not None and not override:
            return Result(shift, False)
        if shift["penalty_amount"] == amount:
            return Result(shift, False)
        conn.execute(
            "UPDATE shifts SET penalty_amount = ?, penalty_set_by = ?, penalty_set_at = ?, updated_at = ? WHERE id = ?",
            (amount, admin_tg, now_iso(), now_iso(), shift_id),
        )
        from . import finance

        finance.set_final_cancel_amount(conn, shift, amount, admin_tg)
        log_event(conn, user_id=shift["user_id"], actor_id=admin_tg, event_type="PENALTY_ASSIGNED", key=new_key(),
                  shift_id=shift_id, old={"penalty_amount": shift["penalty_amount"]},
                  new={"penalty_amount": amount})
    return Result(get_shift(conn, shift_id), True, "PENALTY_ASSIGNED")


def admin_edit_shift(conn, admin: dict, shift_id: int, changes: dict, key: str) -> Result:
    """changes: optional start_time/end_time/volume/location, action ('force_cancel'|'force_restore'),
    penalty_amount, and a required reason."""
    _check_key(key)
    reason = (changes.get("reason") or "").strip()
    if not reason:
        raise ServiceError("Укажите причину изменения", 422, "reason_required")
    with transaction(conn):
        if _seen(conn, key):
            return Result(get_shift(conn, shift_id), False)
        shift = get_shift(conn, shift_id)
        if shift is None:
            raise ServiceError("Смена не найдена", 404, "not_found")
        updates: dict = {}
        field_keys = ("start_time", "end_time", "volume", "location")
        if any(changes.get(k) is not None for k in field_keys):
            if shift["status"] == "pre_declared_off":
                raise ServiceError("У выходного дня нет времени и объёма", 409, "bad_status")
            merged = {k: changes.get(k) if changes.get(k) is not None else shift[k] for k in field_keys}
            data = validate_payload(**merged)
            updates.update({k: v for k, v in data.items() if v != shift[k]})
        action = changes.get("action")
        if action == "force_cancel":
            if shift["status"] not in (*ACTIVE, "awaiting_approval"):
                raise ServiceError("Отменить можно только активную смену", 409, "bad_status")
            updates.update(status="cancelled", cancelled_at=now_iso(), admin_locked=1)
        elif action == "force_restore":
            if shift["status"] not in ("cancelled", "finally_cancelled", "rejected_by_admin", "awaiting_approval"):
                raise ServiceError("Восстановить можно только отменённую смену", 409, "bad_status")
            if shift["start_time"] is None:
                raise ServiceError("У смены нет времени", 409, "bad_status")
            updates.update(status="active", admin_locked=0)
            if shift["status"] == "finally_cancelled":
                from . import finance

                finance.cancel_linked(conn, shift_id, ("final_cancel",), admin["telegram_id"], "Смена восстановлена")
                updates.update(penalty_amount=None)
        elif action not in (None, ""):
            raise ServiceError("Неизвестное действие", 422, "bad_action")
        penalty = changes.get("penalty_amount")
        if penalty is not None:
            if isinstance(penalty, bool) or not isinstance(penalty, int) or not 0 <= penalty <= MAX_PENALTY:
                raise ServiceError("Некорректная сумма штрафа", 422, "bad_penalty")
            if updates.get("status", shift["status"]) != "finally_cancelled":
                raise ServiceError("Штраф назначается только за окончательную отмену", 409, "bad_status")
            if penalty != shift["penalty_amount"]:
                updates.update(penalty_amount=penalty, penalty_set_by=admin["telegram_id"], penalty_set_at=now_iso())
        if not updates:
            raise ServiceError("Нет изменений", 422, "no_changes")
        if any(k in updates for k in field_keys):
            updates["edit_count"] = max(1, shift["edit_count"])
        updates["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE shifts SET {sets} WHERE id = ?", (*updates.values(), shift_id))
        new = {k: v for k, v in updates.items()
               if k in (*field_keys, "status", "penalty_amount", "fact_volume", "shortfall_status", "shortfall_penalty")}
        log_event(conn, user_id=shift["user_id"], actor_id=admin["telegram_id"], event_type="SHIFT_EDITED_BY_ADMIN",
                  key=key, shift_id=shift_id, old=_snapshot(shift), new=new, description=reason)
        if updates.get("penalty_amount") is not None:
            from . import finance

            finance.set_final_cancel_amount(conn, get_shift(conn, shift_id), updates["penalty_amount"], admin["telegram_id"])
        if "penalty_amount" in updates:
            log_event(conn, user_id=shift["user_id"], actor_id=admin["telegram_id"], event_type="PENALTY_ASSIGNED",
                      key=new_key(), shift_id=shift_id, old={"penalty_amount": shift["penalty_amount"]},
                      new={"penalty_amount": updates["penalty_amount"]}, description=reason)
    return Result(get_shift(conn, shift_id), True, "SHIFT_EDITED_BY_ADMIN")


def admin_delete_shift(conn, admin: dict, shift_id: int, reason: str, key: str) -> Result:
    _check_key(key)
    reason = (reason or "").strip()
    if not reason:
        raise ServiceError("Укажите причину", 422, "reason_required")
    with transaction(conn):
        if _seen(conn, key):
            return Result(None, False)
        shift = get_shift(conn, shift_id)
        if shift is None:
            raise ServiceError("Смена не найдена", 404, "not_found")
        if shift["penalty_amount"] or shift["shortfall_penalty"]:
            raise ServiceError("У смены назначен штраф — сначала обнулите его", 409, "has_penalty")
        log_event(conn, user_id=shift["user_id"], actor_id=admin["telegram_id"], event_type="SHIFT_REMOVED_BY_ADMIN",
                  key=key, shift_id=shift_id, old={**_snapshot(shift), "date": shift["shift_date"]},
                  description=reason)
        conn.execute("UPDATE shifts SET status = 'removed', updated_at = ? WHERE id = ?", (now_iso(), shift_id))
    return Result({**shift, "status": "removed"}, True, "SHIFT_REMOVED_BY_ADMIN")


# ---------------------------------------------------------------- admin reports


def raspred(conn, day: date, viewer: dict | None = None) -> dict:
    """The day's list for the work chat. A trainer (viewer) gets only their own network's people,
    an instructor only the people assigned to them."""
    shifts = active_shifts_on(conn, day)
    network = None
    if viewer is not None and not perms.sees_everyone(viewer):
        mine = viewer.get("network_id")
        shifts = [s for s in shifts if mine is not None and s["network_id"] == mine]
        if perms.role_of(viewer) == perms.INSTRUCTOR:
            shifts = [s for s in shifts if s["instructor_id"] == viewer["id"]]
        network = conn.execute("SELECT name FROM networks WHERE id = ?", (mine,)).fetchone() if mine else None
    lines = []
    for s in shifts:
        name = full_name(s)
        if s["username"]:
            name += f" (@{s['username']})"
        if s.get("training"):
            name += " — стажёр"
        lines.append(f"{s['start_time']}–{s['end_time']} — {name} — {s['volume']} шт. — {s['location']}")
    total_volume = sum(s["volume"] for s in shifts)
    title = f" · сеть «{network[0]}»" if network else ""
    if shifts:
        text = (
            f"Распред на {day.strftime('%d.%m.%y')}{title}\n\n" + "\n".join(lines)
            + f"\n\nВсего сотрудников: {len(shifts)}\nОбщий плановый объём: {total_volume} шт."
        )
    else:
        text = f"Распред на {day.strftime('%d.%m.%y')}{title}\n\nВ этот день нет сотрудников в графике."
    return {
        "date": day.isoformat(),
        "rows": [
            {
                "shift_id": s["id"],
                "name": full_name(s),
                "username": s["username"],
                "start_time": s["start_time"],
                "end_time": s["end_time"],
                "volume": s["volume"],
                "location": s["location"],
                "trainee": bool(s.get("training")),
            }
            for s in shifts
        ],
        "total_count": len(shifts),
        "total_volume": total_volume,
        "text": text,
    }


def stats(conn, user_id: int, date_from: date, date_to: date) -> dict:
    if date_to < date_from:
        raise ServiceError("Дата «по» раньше даты «с»", 422, "bad_range")
    if (date_to - date_from) > timedelta(days=370):
        raise ServiceError("Период не больше года", 422, "bad_range")
    user = get_user(conn, user_id)
    if user is None:
        raise ServiceError("Сотрудник не найден", 404, "not_found")
    rows = conn.execute(
        f"SELECT * FROM shifts WHERE user_id = ? AND shift_date BETWEEN ? AND ? AND {LIVE} ORDER BY shift_date",
        (user_id, date_from.isoformat(), date_to.isoformat()),
    ).fetchall()
    from . import rates as rates_mod

    own = rates_mod.for_user(conn, user_id)
    items, active_volume, cancelled_volume, penalties, pay = [], 0, 0, 0, 0
    fact_volume, fact_pay, fact_missing = 0, 0, 0
    short_shifts, short_volume, short_pending = 0, 0, 0
    for r in rows:
        s = dict(r)
        notes = []
        if s["training"]:
            notes.append("Учебная")
        elif s["volume"] and pricing.is_lazy(s["volume"], own):
            notes.append("Налог на лень")
        if s["is_urgent"]:
            notes.append("Срочная")
        if s["status"] == "finally_cancelled":
            notes.append(f"Штраф {s['penalty_amount']} ₽" if s["penalty_amount"] is not None else "Штраф не назначен")
        if s["status"] in ACTIVE:
            active_volume += s["volume"]
            if not s["training"]:
                pay += pricing.expected_pay(s["volume"], own)
            if s["fact_volume"] is not None:
                fact_volume += s["fact_volume"]
                if not s["training"]:
                    fact_pay += pricing.expected_pay(s["fact_volume"], own)
            elif s["shift_date"] < today_local().isoformat():
                fact_missing += 1
        elif s["status"] in ("cancelled", "finally_cancelled") and s["volume"]:
            cancelled_volume += s["volume"]
        short = shortfall(s)
        if short:
            short_shifts += 1
            short_volume += short
            short_pending += not s["shortfall_status"]
            notes.append(
                f"Не по норме −{short}" + (f", штраф {s['shortfall_penalty']} ₽" if s["shortfall_status"] == "fined"
                                            else ", без штрафа" if s["shortfall_status"] == "waived" else "")
            )
        items.append(
            {
                "id": s["id"],
                "date": s["shift_date"],
                "status": s["status"],
                "start_time": s["start_time"],
                "end_time": s["end_time"],
                "volume": s["volume"],
                "location": s["location"],
                "rate": pricing.calculate_rate(s["volume"], own) if s["volume"] else None,
                "expected_pay": pricing.expected_pay(s["volume"], own) if s["volume"] and s["status"] in ACTIVE else None,
                "penalty_amount": s["penalty_amount"],
                "fact_volume": s["fact_volume"],
                "shortfall": short,
                "shortfall_status": s["shortfall_status"],
                "shortfall_penalty": s["shortfall_penalty"],
                "notes": ", ".join(notes),
            }
        )
    from . import finance

    penalties = finance.fines_total(conn, user_id, date_from, date_to)
    return {
        "user": {"id": user["id"], "name": full_name(user), "username": user["username"]},
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "items": items,
        "totals": {
            "shifts": len(items),
            "active_shifts": sum(1 for i in items if i["status"] in ACTIVE),
            "active_volume": active_volume,
            "cancelled_volume": cancelled_volume,
            "penalties": penalties,
            "expected_pay": pay,
            "fact_volume": fact_volume,
            "fact_pay": fact_pay,
            "fact_missing": fact_missing,
            "shortfall_shifts": short_shifts,
            "shortfall_volume": short_volume,
            "shortfall_pending": short_pending,
        },
    }


def penalty_log(conn, user_id: int | None, date_from: date | None, date_to: date | None) -> list[dict]:
    """Fines from the money ledger (every status), newest first."""
    from . import finance

    out = []
    for tx in finance.journal(conn, user_id=user_id, types=("fine",), date_from=date_from, date_to=date_to):
        out.append({"shift_id": tx["shift_id"], "tx_id": tx["id"], "kind": tx["source"], "date": tx["event_date"],
                    "user_id": tx["user_id"], "name": tx["name"], "username": tx["username"], "reason": tx["reason"],
                    "amount": tx["amount"], "status": tx["status"], "set_by": tx["decided_by"] or tx["created_by"],
                    "set_at": tx["decided_at"] or tx["created_at"]})
    return out


# ---------------------------------------------------------------- points (справочник точек)


def list_points(conn, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM points" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY active DESC, name"
    return [dict(r) for r in conn.execute(sql)]


def _validate_point(name, capacity) -> tuple[str, int | None]:
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name or len(name) > MAX_LOCATION:
        raise ServiceError("Название точки: от 1 до 100 символов", 422, "bad_point")
    if capacity is not None and (isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 1000):
        raise ServiceError("Лимит людей — число от 1", 422, "bad_capacity")
    return name, capacity


def _point_named(conn, name: str) -> dict | None:
    # SQLite's NOCASE only folds ASCII, so compare Cyrillic names in Python
    return next((p for p in list_points(conn, include_inactive=True) if p["name"].casefold() == name.casefold()), None)


def create_point(conn, admin: dict, name: str, capacity: int | None) -> dict:
    name, capacity = _validate_point(name, capacity)
    with transaction(conn):
        if _point_named(conn, name):
            raise ServiceError("Такая точка уже есть", 409, "point_exists")
        cur = conn.execute("INSERT INTO points (name, capacity, created_at) VALUES (?, ?, ?)", (name, capacity, now_iso()))
        log_event(conn, user_id=admin["id"], actor_id=admin["telegram_id"], event_type="POINT_CREATED", key=new_key(),
                  new={"name": name, "capacity": capacity})
    return dict(conn.execute("SELECT * FROM points WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_point(conn, admin: dict, point_id: int, changes: dict, today: date | None = None) -> dict:
    with transaction(conn):
        point = conn.execute("SELECT * FROM points WHERE id = ?", (point_id,)).fetchone()
        if point is None:
            raise ServiceError("Точка не найдена", 404, "not_found")
        point = dict(point)
        name = changes["name"] if "name" in changes and changes["name"] is not None else point["name"]
        capacity = changes["capacity"] if "capacity" in changes else point["capacity"]
        name, capacity = _validate_point(name, capacity)
        active = int(bool(changes["active"])) if changes.get("active") is not None else point["active"]
        clash = _point_named(conn, name)
        if clash and clash["id"] != point_id:
            raise ServiceError("Такая точка уже есть", 409, "point_exists")
        conn.execute("UPDATE points SET name = ?, capacity = ?, active = ? WHERE id = ?", (name, capacity, active, point_id))
        if name != point["name"]:
            # keep upcoming shifts pointing at the renamed point
            upcoming = conn.execute("SELECT id, location FROM shifts WHERE shift_date >= ?", ((today or today_local()).isoformat(),))
            ids = [r["id"] for r in upcoming if (r["location"] or "").casefold() == point["name"].casefold()]
            conn.executemany("UPDATE shifts SET location = ? WHERE id = ?", [(name, i) for i in ids])
        log_event(conn, user_id=admin["id"], actor_id=admin["telegram_id"], event_type="POINT_UPDATED", key=new_key(),
                  old={k: point[k] for k in ("name", "capacity", "active")},
                  new={"name": name, "capacity": capacity, "active": active})
    return dict(conn.execute("SELECT * FROM points WHERE id = ?", (point_id,)).fetchone())


def check_point(conn, day: date, location: str, exclude: int | None = None) -> str:
    """With a points list configured, the location must be one of them and the point must have room.
    Returns the canonical point name. Without points, any location is accepted."""
    points = list_points(conn)
    if not points:
        return location
    point = next((p for p in points if p["name"].casefold() == location.casefold()), None)
    if point is None:
        raise ServiceError("Выберите точку из списка", 422, "unknown_point")
    if point["capacity"]:
        rows = conn.execute(
            f"SELECT location FROM shifts WHERE shift_date = ? AND status IN {BUSY} AND id != ?",
            (day.isoformat(), exclude or 0),
        )
        taken = sum(1 for r in rows if (r["location"] or "").casefold() == point["name"].casefold())
        if taken >= point["capacity"]:
            raise ServiceError(f"На точке «{point['name']}» уже {taken} из {point['capacity']}", 409, "point_full")
    return point["name"]


# ---------------------------------------------------------------- fact after the shift


# ---------------------------------------------------------------- notifications preferences and broadcasts


def set_notify(conn, user: dict, enabled: bool) -> dict:
    """The reminders follow the status: on with «Работаю» (ТЗ 2.1), off with «Не работаю» (the customer)."""
    with transaction(conn):
        current = get_user(conn, user["id"])
        if not enabled and current["work_status"] == "working":
            raise ServiceError('Переведите статус "Не работаю", чтобы выключить напоминания', 409, "reminders_locked")
        if enabled and current["work_status"] != "working":
            raise ServiceError('Напоминания приходят, пока статус «Работаю»', 409, "reminders_off")
        conn.execute("UPDATE users SET notify_reminders = ? WHERE id = ?", (int(enabled), user["id"]))
    return get_user(conn, user["id"])


AUDIENCES = {
    "working": "Работают",
    "not_working": "Не работают",
    "admins": "Администраторы",
}
# a filter on top of the chosen people, not a group: only those who haven't marked tomorrow
UNMARKED = "unmarked_tomorrow"
AUDIENCE_LABELS = {**AUDIENCES, UNMARKED: "Не отметили график на завтра"}
MARKED = (*BUSY, *DAY_OFF)  # going (incl. waiting for approval) or a day off (incl. a given-up shift)


def marked_on(conn, day: date) -> set[int]:
    """Who has marked the day in the schedule: a shift or a day off."""
    return {r[0] for r in conn.execute(
        f"SELECT user_id FROM shifts WHERE shift_date = ? AND status IN {MARKED}", (day.isoformat(),))}


def broadcast_recipients(conn, audiences: list[str] | str, network_id: int | None = None,
                         user_ids: list[int] | None = None, actor: dict | None = None,
                         only_unmarked: date | None = None) -> list[dict]:
    """Union of the chosen groups, a network and hand-picked people, among people with access; each once.
    The Mini App sends only hand-picked people (a checklist), so they get exactly that list.
    A trainer reaches only their own network (ТЗ 7.1). only_unmarked: keep those with nothing on that day."""
    if isinstance(audiences, str):
        audiences = ["working", "not_working"] if audiences == "all" else [audiences]
    audiences = audiences or []
    if any(a not in AUDIENCES for a in audiences):
        raise ServiceError("Выберите, кому отправить", 422, "bad_audience")
    if not audiences and network_id is None and not user_ids:
        raise ServiceError("Выберите, кому отправить", 422, "bad_audience")
    rules = {
        "working": "work_status = 'working'",
        "not_working": "work_status = 'not_working'",
        "admins": "role = 'admin'",
    }
    parts = [f"({rules[a]})" for a in dict.fromkeys(audiences)]
    params: list = []
    if network_id is not None:
        parts.append("(network_id = ?)")
        params.append(network_id)
    if user_ids:
        parts.append(f"(id IN ({','.join('?' * len(user_ids))}))")
        params += list(user_ids)
    people = [dict(r) for r in conn.execute(
        f"""SELECT * FROM users WHERE status = 'approved' AND account_state != 'blocked' AND ({' OR '.join(parts)})
            ORDER BY id""", params)]
    if actor is not None and not perms.sees_everyone(actor):
        people = [u for u in people if perms.manages(actor, u)]
    if only_unmarked is not None:
        marked = marked_on(conn, only_unmarked)
        people = [u for u in people if u["id"] not in marked]
    return people


def prepare_message(html: str) -> tuple[str, str]:
    from .richtext import MAX_TEXT, sanitize

    clean, text = sanitize(html)
    if not text.strip():
        raise ServiceError("Напишите текст сообщения", 422, "empty")
    if len(text) > MAX_TEXT:
        raise ServiceError(f"Сообщение длиннее {MAX_TEXT} символов", 422, "too_long")
    return clean, text


def create_broadcast(conn, admin: dict, audiences: list[str] | str, html: str, signed: bool = True,
                     network_id: int | None = None, user_ids: list[int] | None = None, require_ack: bool = False,
                     media: str | None = None, only_unmarked: date | None = None, media_kind: str = "photo",
                     allow_reply: bool = False) -> tuple[dict, list[dict]]:
    if media_kind not in ("photo", "video"):
        raise ServiceError("Вложение — фото или видео", 422, "bad_media")
    clean, text = prepare_message(html)
    if media and len(clean) > 4000:
        raise ServiceError("Слишком длинный текст", 422, "too_long")
    recipients = broadcast_recipients(conn, audiences, network_id, user_ids, admin, only_unmarked)
    if not recipients:
        raise ServiceError("Среди выбранных никого нет" if only_unmarked is None
                           else "Все выбранные уже отметили график на завтра", 422, "no_recipients")
    audience = ",".join(audiences) if isinstance(audiences, list) else audiences
    if only_unmarked is not None:
        audience = ",".join(x for x in (audience, UNMARKED) if x)
    with transaction(conn):
        cur = conn.execute(
            """INSERT INTO broadcasts (author_id, audience, text, html, signed, recipients, created_at, media_file_id,
                                       require_ack, network_id, people, media_kind, allow_reply)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (admin["telegram_id"], audience, text, clean, int(signed), len(recipients), now_iso(), media,
             int(require_ack), network_id, json.dumps(user_ids) if user_ids else None, media_kind if media else "photo",
             int(allow_reply)),
        )
    return dict(conn.execute("SELECT * FROM broadcasts WHERE id = ?", (cur.lastrowid,)).fetchone()), recipients


def list_broadcasts(conn, limit: int = 20, author_tg: int | None = None) -> list[dict]:
    """author_tg: only this person's broadcasts (trainers see their own)."""
    rows = conn.execute(
        """SELECT b.*, u.first_name, u.last_name,
                  (SELECT COUNT(*) FROM broadcast_deliveries d WHERE d.broadcast_id = b.id AND d.read_at IS NOT NULL) AS read,
                  (SELECT COUNT(DISTINCT r.user_id) FROM broadcast_replies r WHERE r.broadcast_id = b.id) AS replied
           FROM broadcasts b LEFT JOIN users u ON u.telegram_id = b.author_id
           WHERE (? IS NULL OR b.author_id = ?) ORDER BY b.id DESC LIMIT ?""",
        (author_tg, author_tg, limit),
    )
    return [dict(r) for r in rows]


def broadcast_deliveries(conn, broadcast_id: int) -> list[dict]:
    return [{**dict(r), "name": full_name(dict(r))} for r in conn.execute(
        """SELECT d.*, u.first_name, u.last_name, u.username, u.telegram_id FROM broadcast_deliveries d
           JOIN users u ON u.id = d.user_id WHERE d.broadcast_id = ? ORDER BY d.read_at IS NOT NULL, u.first_name""",
        (broadcast_id,))]


def acknowledge(conn, broadcast_id: int, telegram_id: int) -> bool:
    """«Я ознакомился»: returns True the first time."""
    user = get_user_by_tg(conn, telegram_id)
    if user is None:
        return False
    cur = conn.execute("UPDATE broadcast_deliveries SET read_at = ? WHERE broadcast_id = ? AND user_id = ? AND read_at IS NULL",
                       (now_iso(), broadcast_id, user["id"]))
    return cur.rowcount == 1
