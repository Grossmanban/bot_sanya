"""ТЗ 28.10 §7: профиль сотрудника и реквизиты — через рассмотрение администратором.

Profile: surname, name, patronymic, age, metro, the minimum work days a week and the convenient days (exactly as
many as the minimum). The employee sends a request («Ожидает рассмотрения администратора»); while it's pending a new
one can't be sent; an admin approves (the data become current, the name changes everywhere) or rejects with a reason
(the employee fixes and sends again). An admin may edit at any moment. Every change is in event_logs
(PROFILE_CHANGED: who, when, old → new, how).

Payment details (СБП): the employee's change is a request too — the old details stay in use until the owner approves
(В20: only the owner sees and decides financial data). The first fill as well (В21): payouts need approved ones.
"Мин. дней" is information for the admin, not the attendance norm (В17)."""

import json
import re
import sqlite3

from . import payouts, templates
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso

FIELDS = ("last_name", "first_name", "middle_name", "age", "metro", "min_days", "work_days")
TITLES = {"last_name": "Фамилия", "first_name": "Имя", "middle_name": "Отчество", "age": "Возраст",
          "metro": "Станция метро", "min_days": "Минимум рабочих дней в неделю", "work_days": "Удобные дни работы"}
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё' -]{0,49}$")
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


# ---------------------------------------------------------------- profile


def current(user: dict) -> dict:
    return {"last_name": user["last_name"], "first_name": user["first_name"], "middle_name": user.get("middle_name"),
            "age": user.get("age"), "metro": user.get("metro"), "min_days": user.get("min_days"),
            "work_days": json.loads(user["work_days"]) if user.get("work_days") else []}


def missing(data: dict) -> list[str]:
    return [TITLES[k] for k in FIELDS if data.get(k) in (None, "", [])]


def clean(form: dict) -> dict:
    """The form, checked: names letters only, age 14–80, days chosen = the minimum (§7)."""
    out = {}
    for k in ("last_name", "first_name", "middle_name"):
        v = " ".join(str(form.get(k) or "").split())
        if not v:
            raise ServiceError(f"Заполните поле «{TITLES[k]}»", 422, "required")
        if not NAME_RE.match(v):
            raise ServiceError(f"«{TITLES[k]}» — только буквы", 422, "bad_name")
        out[k] = v
    age = form.get("age")
    if isinstance(age, bool) or not isinstance(age, int) or not 14 <= age <= 80:
        raise ServiceError("Возраст — от 14 до 80 лет", 422, "bad_age")
    out["age"] = age
    metro = " ".join(str(form.get("metro") or "").split())[:60]
    if not metro:
        raise ServiceError("Укажите станцию метро", 422, "required")
    out["metro"] = metro
    n = form.get("min_days")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 7:
        raise ServiceError("Минимум рабочих дней — от 1 до 7", 422, "bad_days")
    out["min_days"] = n
    days = form.get("work_days") or []
    if not isinstance(days, list) or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days):
        raise ServiceError("Выберите удобные дни", 422, "bad_days")
    days = sorted(set(days))
    if len(days) != n:
        raise ServiceError(f"Удобных дней выбрано {len(days)}, а минимум — {n}: их должно быть столько же", 422, "days_mismatch")
    out["work_days"] = days
    return out


def draft(conn: sqlite3.Connection, user: dict) -> dict:
    """What the form starts with: the current data, gaps filled from the person's questionnaire (В16)."""
    data = current(user)
    app = conn.execute("SELECT * FROM applications WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
    if app is not None:
        data["age"] = data["age"] or app["age"]
        data["metro"] = data["metro"] or app["metro"]
        data["min_days"] = data["min_days"] or app["min_days"]
    return data


def request_of(conn, user_id: int, kind: str) -> dict | None:
    row = conn.execute("SELECT * FROM profile_requests WHERE user_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
                       (user_id, kind)).fetchone()
    return dict(row) if row else None


def state(conn: sqlite3.Connection, user: dict) -> dict:
    """§7: «Команда» shows complete / incomplete / waiting / rejected for the profile and the payment details."""
    out = {}
    for kind in ("profile", "payment"):
        last = request_of(conn, user["id"], kind)
        if kind == "profile":
            filled = not missing(current(user))
        else:
            filled = payouts.get_profile(conn, user["id"]) is not None
        if last and last["status"] == "pending":
            status = "pending"
        elif last and last["status"] == "rejected":
            status = "rejected"
        else:
            status = "complete" if filled else "incomplete"
        out[kind] = {"status": status, "reason": last["reason"] if last and last["status"] == "rejected" else None}
    return out


def needs_reminder(conn: sqlite3.Connection, user: dict) -> bool:
    s = state(conn, user)
    return any(v["status"] in ("incomplete", "rejected") for v in s.values())


def view(conn: sqlite3.Connection, user: dict) -> dict:
    last = request_of(conn, user["id"], "profile")
    return {"profile": current(user), "draft": draft(conn, user), "missing": missing(current(user)),
            "state": state(conn, user)["profile"],
            "pending": public_request(conn, last) if last and last["status"] == "pending" else None,
            "rejected": public_request(conn, last) if last and last["status"] == "rejected" else None,
            "weekdays": WEEKDAYS, "titles": TITLES}


def public_request(conn, r: dict, full: bool = True) -> dict:
    user = svc.get_user(conn, r["user_id"])
    data, old = json.loads(r["data"]), json.loads(r["old"] or "null")
    if r["kind"] == "payment" and not full:
        data = {**data, "phone": payouts.mask_phone(data.get("phone")), "recipient": None}
        old = old and {**old, "phone": payouts.mask_phone(old.get("phone")), "recipient": None}
    elif r["kind"] == "payment":
        data = {**data, "phone": payouts.pretty_phone(data["phone"])}
        old = old and {**old, "phone": payouts.pretty_phone(old["phone"])}
    return {"id": r["id"], "kind": r["kind"], "user_id": r["user_id"], "name": svc.full_name(user),
            "username": user["username"], "telegram_id": user["telegram_id"], "data": data, "old": old,
            "status": r["status"], "created_at": r["created_at"], "decided_at": r["decided_at"], "reason": r["reason"]}


def _request(conn, user: dict, kind: str, data: dict, old: dict | None, key: str) -> tuple[dict, bool]:
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return request_of(conn, user["id"], kind), False
        if conn.execute("SELECT 1 FROM profile_requests WHERE user_id = ? AND kind = ? AND status = 'pending'",
                        (user["id"], kind)).fetchone():
            raise ServiceError("Предыдущие изменения ещё на рассмотрении — дождитесь решения", 409, "pending")
        cur = conn.execute("INSERT INTO profile_requests (user_id, kind, data, old, created_at) VALUES (?, ?, ?, ?, ?)",
                           (user["id"], kind, json.dumps(data, ensure_ascii=False),
                            json.dumps(old, ensure_ascii=False) if old else None, now_iso()))
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], key=key,
                  event_type="PROFILE_REQUESTED" if kind == "profile" else "PAYMENT_REQUESTED",
                  new={"request": cur.lastrowid})
    return dict(conn.execute("SELECT * FROM profile_requests WHERE id = ?", (cur.lastrowid,)).fetchone()), True


def submit(conn: sqlite3.Connection, user: dict, form: dict, key: str) -> tuple[dict, bool]:
    data = clean(form)
    old = current(user)
    if data == old and user.get("profile_approved_at"):
        raise ServiceError("Изменений нет", 422, "no_changes")
    return _request(conn, user, "profile", data, old, key)


def _apply_profile(conn, user_id: int, data: dict, actor_tg: int, method: str, key: str) -> None:
    user = svc.get_user(conn, user_id)
    old = current(user)
    conn.execute("""UPDATE users SET last_name = ?, first_name = ?, middle_name = ?, age = ?, metro = ?, min_days = ?,
                    work_days = ?, profile_approved_at = ? WHERE id = ?""",
                 (data["last_name"], data["first_name"], data["middle_name"], data["age"], data["metro"], data["min_days"],
                  json.dumps(data["work_days"]), now_iso(), user_id))
    log_event(conn, user_id=user_id, actor_id=actor_tg, event_type="PROFILE_CHANGED", key=key,
              old=old, new=data, description=method)


def decide(conn: sqlite3.Connection, actor: dict, request_id: int, approve: bool, reason: str | None, key: str) -> tuple[dict, bool]:
    """Profiles — owners and admins; payment details — owners only (В20). A refusal needs a reason."""
    if not approve:
        reason = templates.require_reason(reason, "Укажите причину отклонения")
    _check_key(key)
    with transaction(conn):
        row = conn.execute("SELECT * FROM profile_requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            raise ServiceError("Заявка не найдена", 404, "not_found")
        r = dict(row)
        if _seen(conn, key) or r["status"] != "pending":
            return r, False
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money" if r["kind"] == "payment" else "profiles"):
            raise ServiceError("Реквизиты одобряет владелец" if r["kind"] == "payment" else "Недостаточно прав", 403, "forbidden")
        conn.execute("UPDATE profile_requests SET status = ?, decided_by = ?, decided_at = ?, reason = ? WHERE id = ?",
                     ("approved" if approve else "rejected", actor["telegram_id"], now_iso(), reason, request_id))
        if approve and r["kind"] == "profile":
            _apply_profile(conn, r["user_id"], json.loads(r["data"]), actor["telegram_id"], "одобрена заявка сотрудника", key)
        elif not approve:
            log_event(conn, user_id=r["user_id"], actor_id=actor["telegram_id"], key=key,
                      event_type="PROFILE_REJECTED" if r["kind"] == "profile" else "PAYMENT_REJECTED",
                      new={"request": request_id}, description=reason)
    if approve and r["kind"] == "payment":
        d = json.loads(r["data"])
        payouts.save_profile(conn, actor, r["user_id"], d["phone"], d["bank"], d["recipient"], svc.new_key(),
                             d.get("bank_name"))
    return dict(conn.execute("SELECT * FROM profile_requests WHERE id = ?", (request_id,)).fetchone()), True


def admin_edit(conn: sqlite3.Connection, actor: dict, user_id: int, form: dict, key: str) -> dict:
    """An admin saves the profile at once (§7); a waiting request of the employee becomes outdated."""
    data = clean(form)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return svc.get_user(conn, user_id)
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "profiles"):
            raise ServiceError("Профили правят владелец и администраторы", 403, "forbidden")
        if svc.get_user(conn, user_id) is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        conn.execute("""UPDATE profile_requests SET status = 'stale', decided_at = ? WHERE user_id = ? AND kind = 'profile'
                        AND status = 'pending'""", (now_iso(), user_id))
        _apply_profile(conn, user_id, data, actor["telegram_id"], "изменено администратором", key)
    return svc.get_user(conn, user_id)


def pending_list(conn: sqlite3.Connection, viewer: dict) -> list[dict]:
    kinds = [k for k, cap in (("profile", "profiles"), ("payment", "money")) if perms.can(viewer, cap)]
    if not kinds:
        return []
    rows = conn.execute(f"""SELECT * FROM profile_requests WHERE status = 'pending' AND kind IN ({','.join('?' * len(kinds))})
                            ORDER BY id""", kinds)
    return [public_request(conn, dict(r)) for r in rows]


def history(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """The log of profile changes: who, when, old → new, how."""
    names = {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT telegram_id, first_name, last_name FROM users")}
    return [{"at": r["created_at"], "by": names.get(r["actor_id"], str(r["actor_id"])), "how": r["description"],
             "old": json.loads(r["old_value"] or "null"), "new": json.loads(r["new_value"] or "null")}
            for r in conn.execute("""SELECT * FROM event_logs WHERE user_id = ? AND event_type = 'PROFILE_CHANGED'
                                     ORDER BY id DESC LIMIT 30""", (user_id,))]


# ---------------------------------------------------------------- payment details


def payment_form(form: dict) -> dict:
    phone = payouts.normalize_phone(form.get("phone") or "")
    bank = form.get("bank")
    if bank not in payouts.BANKS:
        raise ServiceError("Выберите банк из списка", 422, "bad_bank")
    bank_name = " ".join(str(form.get("bank_name") or "").split())[:60] or None
    if bank == "Другой банк" and not bank_name:
        raise ServiceError("Напишите название банка", 422, "bank_name_required")  # В22
    recipient = " ".join(str(form.get("recipient") or "").split())[:120]
    if len(recipient.split()) < 2:
        raise ServiceError("ФИО получателя — как в банке, минимум имя и фамилия", 422, "bad_recipient")
    return {"phone": phone, "bank": bank, "bank_name": bank_name if bank == "Другой банк" else None, "recipient": recipient}


def submit_payment(conn: sqlite3.Connection, user: dict, form: dict, key: str) -> tuple[dict, bool]:
    data = payment_form(form)
    old = payouts.get_profile(conn, user["id"])
    old = old and {k: old.get(k) for k in ("phone", "bank", "bank_name", "recipient")}
    if old == data:
        raise ServiceError("Изменений нет", 422, "no_changes")
    return _request(conn, user, "payment", data, old, key)


def payment_view(conn: sqlite3.Connection, user: dict) -> dict:
    last = request_of(conn, user["id"], "payment")
    return {"profile": payouts.public_profile(payouts.get_profile(conn, user["id"]), True), "banks": payouts.BANKS,
            "pending": public_request(conn, last) if last and last["status"] == "pending" else None,
            "rejected": public_request(conn, last) if last and last["status"] == "rejected" else None}
