"""Recruiting and onboarding (the customer's module 3).

    referral link (t.me/<bot>?startapp=ref_<code>, or /start ref_<code> in the bot)
      → the questionnaire in the Mini App (an application, status `new`)
      → staff assign a training: date, time, place, trainer (`scheduled`); the candidate confirms with a button;
        reminders the evening before and in the morning
      → «Начать стажировку»: access to the app as a trainee (`internship`); their shifts are training ones —
        in the schedule and the raspred as «стажёр», not paid
      → `internship_units` approved units reached → staff check it (may count fewer, e.g. 15 of 20),
        confirm and give the referrer a bonus (`done`; the trainee is a regular employee from now on)
      → `second_stage_units` work units after the internship → the second check and bonus.
    Any step: «Отказать» (optional reason; the account is closed — the bot stays silent), «Связаться
    напрямую» (a manager's @username; the candidate answers «Я написал» / «Не могу написать первым»),
    and from «Отклонено» — «Вернуть в резерв»: the candidate fills the questionnaire again.
Nothing is deleted: a returned application stays in the history (`returned`), the new one is a new row.
"""

import json
import secrets
import sqlite3
from datetime import date, datetime, time, timedelta

from . import app_settings, finance
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ACTIVE, ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, now_local

OPEN = ("new", "scheduled")              # waiting for staff
TABS = {"new": ("new",), "scheduled": ("scheduled",), "internship": ("internship", "done"), "rejected": ("rejected",)}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MAX_TEXT = 100


# ---------------------------------------------------------------- referral links


def ref_code(conn: sqlite3.Connection, user: dict) -> str:
    """The person's own referral code (made on first use)."""
    if user.get("ref_code"):
        return user["ref_code"]
    with transaction(conn):
        row = conn.execute("SELECT ref_code FROM users WHERE id = ?", (user["id"],)).fetchone()
        if row and row[0]:
            return row[0]
        while True:
            code = secrets.token_hex(4)
            if conn.execute("SELECT 1 FROM users WHERE ref_code = ?", (code,)).fetchone() is None:
                break
        conn.execute("UPDATE users SET ref_code = ? WHERE id = ?", (code, user["id"]))
    return code


def referrer_by_code(conn: sqlite3.Connection, code: str | None) -> dict | None:
    code = (code or "").removeprefix("ref_").strip()
    if not code:
        return None
    row = conn.execute("SELECT * FROM users WHERE ref_code = ? AND status = 'approved'", (code,)).fetchone()
    return dict(row) if row else None


def remember_referral(conn: sqlite3.Connection, telegram_id: int, code: str | None) -> bool:
    """/start ref_<code> in the bot: kept until the person sends the questionnaire."""
    ref = referrer_by_code(conn, code)
    if ref is None or ref["telegram_id"] == telegram_id:
        return False
    with transaction(conn):
        conn.execute("""INSERT INTO referral_hits (telegram_id, referrer_id, created_at) VALUES (?, ?, ?)
                        ON CONFLICT(telegram_id) DO UPDATE SET referrer_id = excluded.referrer_id""",
                     (telegram_id, ref["id"], now_iso()))
    return True


# ---------------------------------------------------------------- the questionnaire


def get(conn: sqlite3.Connection, app_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(row) if row else None


def current(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """The person's application in work (not one returned to the reserve)."""
    row = conn.execute("SELECT * FROM applications WHERE user_id = ? AND status != 'returned' ORDER BY id DESC LIMIT 1",
                       (user_id,)).fetchone()
    return dict(row) if row else None


def _text(value, what: str, limit: int = MAX_TEXT) -> str:
    value = " ".join(str(value or "").split())[:limit]
    if not value:
        raise ServiceError(f"Заполните поле «{what}»", 422, "required")
    return value


def _int(value, lo: int, hi: int, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ServiceError(message, 422, "bad_value")
    return value


def submit(conn: sqlite3.Connection, tg: dict, form: dict, start_param: str | None = None) -> tuple[dict, dict]:
    """The questionnaire. tg — the Telegram user from the signed initData. Returns (user, application)."""
    full_name = _text(form.get("full_name"), "ФИО")
    parts = full_name.split()
    if len(parts) < 2:
        raise ServiceError("Укажите фамилию и имя", 422, "bad_name")
    age = _int(form.get("age"), 14, 80, "Возраст — от 14 до 80 лет")
    metro = _text(form.get("metro"), "Ближайшая станция метро", 60)
    min_days = _int(form.get("min_days"), 1, 7, "Дней в неделю — от 1 до 7")
    days = form.get("training_days") or []
    if not isinstance(days, list) or not days or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days):
        raise ServiceError("Выберите удобные дни для обучения", 422, "days_required")
    communication = _int(form.get("communication"), 1, 10, "Оценка навыков — от 1 до 10")
    if form.get("consent") is not True or form.get("truthful") is not True:
        raise ServiceError("Нужны согласие на обработку данных и подтверждение достоверности", 422, "consent_required")
    with transaction(conn):
        user = svc.get_user_by_tg(conn, tg["id"])
        if user is not None:
            if user["status"] == "approved":
                raise ServiceError("Вы уже в команде — откройте график", 409, "already_member")
            if user["status"] == "rejected":
                raise ServiceError("Заявка отклонена", 403, "rejected")
            active = current(conn, user["id"])
            if active and active["status"] in OPEN:
                raise ServiceError("Анкета уже отправлена — ждите сообщения от бота", 409, "already_sent")
        ref = referrer_by_code(conn, start_param) if start_param else None
        if ref is None:
            hit = conn.execute("SELECT referrer_id FROM referral_hits WHERE telegram_id = ?", (tg["id"],)).fetchone()
            ref = svc.get_user(conn, hit[0]) if hit else None
        if ref is not None and ref["telegram_id"] == tg["id"]:
            ref = None
        last, first = parts[0], " ".join(parts[1:])
        if user is None:
            cur = conn.execute("""INSERT INTO users (telegram_id, username, first_name, last_name, created_at, referrer_id)
                                  VALUES (?, ?, ?, ?, ?, ?)""",
                               (tg["id"], tg.get("username"), first[:100], last[:100], now_iso(), ref["id"] if ref else None))
            user_id = cur.lastrowid
        else:
            user_id = user["id"]
            conn.execute("UPDATE users SET first_name = ?, last_name = ?, referrer_id = COALESCE(referrer_id, ?) WHERE id = ?",
                         (first[:100], last[:100], ref["id"] if ref else None, user_id))
        referrer_id = conn.execute("SELECT referrer_id FROM users WHERE id = ?", (user_id,)).fetchone()[0]
        cur = conn.execute(
            """INSERT INTO applications (user_id, full_name, age, metro, min_days, training_days, communication,
                                         referrer_id, consent_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, full_name, age, metro, min_days, json.dumps(sorted(set(days))), communication, referrer_id,
             now_iso(), now_iso()))
        log_event(conn, user_id=user_id, actor_id=tg["id"], event_type="APPLICATION_SUBMITTED", key=svc.new_key(),
                  new={"application": cur.lastrowid, "referrer": referrer_id})
    return svc.get_user(conn, user_id), get(conn, cur.lastrowid)


# ---------------------------------------------------------------- progress (internship and the second stage)


def units(conn: sqlite3.Connection, user_id: int, training: bool, since: str | None = None) -> tuple[int, int]:
    """(approved, under review) pieces: training shifts, or work shifts after `since`."""
    row = conn.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN report_state = 'approved' THEN fact_volume END), 0),
                   COALESCE(SUM(CASE WHEN report_state = 'pending_review' THEN fact_volume END), 0)
            FROM shifts WHERE user_id = ? AND status IN {ACTIVE} AND fact_volume IS NOT NULL AND training = ?
            AND (? IS NULL OR shift_date >= ?)""",
        (user_id, int(training), since[:10] if since else None, since[:10] if since else None)).fetchone()
    return row[0], row[1]


def progress(conn: sqlite3.Connection, app: dict, user: dict | None = None) -> dict | None:
    """What the trainee, the referrer and staff see: the internship, then the work units for the second bonus."""
    if app["status"] not in ("internship", "done"):
        return None
    user = user or svc.get_user(conn, app["user_id"])
    cfg = app_settings.get_all(conn)
    if app["status"] == "internship":
        done, pending = units(conn, user["id"], True)
        target = cfg["internship_units"]
        return {"stage": 1, "units": done, "pending": pending, "target": target, "review": done >= target}
    done, pending = units(conn, user["id"], False, user.get("trainee_done_at"))
    target = cfg["second_stage_units"]
    return {"stage": 2, "units": done, "pending": pending, "target": target,
            "review": done >= target and not app["stage2_at"], "finished": bool(app["stage2_at"])}


# ---------------------------------------------------------------- views


def _days(app: dict) -> list[int]:
    return json.loads(app["training_days"] or "[]")


def view(conn: sqlite3.Connection, app: dict) -> dict:
    user = svc.get_user(conn, app["user_id"])
    ref = svc.get_user(conn, app["referrer_id"]) if app["referrer_id"] else None
    trainer = svc.get_user(conn, app["trainer_id"]) if app["trainer_id"] else None
    history = conn.execute("SELECT COUNT(*) FROM applications WHERE user_id = ? AND id != ?", (app["user_id"], app["id"])).fetchone()[0]
    return {
        **{k: app[k] for k in ("id", "status", "full_name", "age", "metro", "min_days", "communication", "created_at",
                               "reject_reason", "decided_at", "training_date", "training_time", "training_location",
                               "training_confirmed_at", "contact_manager", "contacted_at", "contact_answer",
                               "contact_answer_at", "stage1_counted", "stage1_at", "stage2_counted", "stage2_at")},
        "training_days": _days(app),
        "user_id": user["id"], "telegram_id": user["telegram_id"], "username": user["username"],
        "referrer": svc.full_name(ref) if ref else None,
        "trainer": {"id": trainer["id"], "name": svc.full_name(trainer), "username": trainer["username"]} if trainer else None,
        "earlier": history,  # earlier applications of the same person (kept in history)
        "progress": progress(conn, app, user),
    }


def listing(conn: sqlite3.Connection, tab: str) -> list[dict]:
    if tab not in TABS:
        raise ServiceError("Неизвестная вкладка", 422, "bad_tab")
    statuses = TABS[tab]
    rows = conn.execute(f"SELECT * FROM applications WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY id DESC LIMIT 300",
                        statuses)
    return [dict(r) for r in rows]


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {tab: 0 for tab in TABS}
    for status, n in conn.execute("SELECT status, COUNT(*) FROM applications GROUP BY status"):
        for tab, statuses in TABS.items():
            if status in statuses:
                out[tab] += n
    return out


def referrals(conn: sqlite3.Connection, user: dict) -> list[dict]:
    """«Мои рефералы»: everyone this person invited, with where they are now."""
    rows = conn.execute("""SELECT a.* FROM applications a WHERE a.referrer_id = ? AND a.id = (
                               SELECT MAX(b.id) FROM applications b WHERE b.user_id = a.user_id) ORDER BY a.id DESC""",
                        (user["id"],))
    out = []
    for r in rows:
        app = dict(r)
        p = progress(conn, app)
        out.append({"id": app["id"], "name": app["full_name"], "status": app["status"], "created_at": app["created_at"],
                    "progress": p, "stage1_counted": app["stage1_counted"], "stage2_counted": app["stage2_counted"]})
    return out


# ---------------------------------------------------------------- staff actions


def _load(conn: sqlite3.Connection, actor: dict, app_id: int) -> tuple[dict, dict]:
    app = get(conn, app_id)
    if app is None:
        raise ServiceError("Анкета не найдена", 404, "not_found")
    actor = _fresh_actor(conn, actor)
    if not perms.can(actor, "registrations"):
        raise ServiceError("Недостаточно прав", 403, "forbidden")
    return actor, app


def assign_training(conn: sqlite3.Connection, actor: dict, app_id: int, day: date, at: str, location: str,
                    trainer_id: int, key: str) -> tuple[dict, bool]:
    """Set or change the training; a change resets the confirmation and the reminders. Returns (app, changed)."""
    try:
        at = time.fromisoformat(at).strftime("%H:%M")
    except (TypeError, ValueError) as exc:
        raise ServiceError("Время — в формате ЧЧ:ММ", 422, "bad_time") from exc
    location = _text(location, "Место")
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key):
            return app, False
        if app["status"] not in OPEN:
            raise ServiceError("Обучение назначают новым кандидатам", 409, "bad_status")
        if day < now_local().date():
            raise ServiceError("Дата обучения уже прошла", 422, "past_date")
        trainer = svc.get_user(conn, trainer_id)
        if trainer is None or trainer["status"] != "approved":
            raise ServiceError("Выберите тренера из команды", 422, "trainer_required")
        conn.execute(
            """UPDATE applications SET status = 'scheduled', training_date = ?, training_time = ?, training_location = ?,
                   trainer_id = ?, training_set_by = ?, training_set_at = ?, training_confirmed_at = NULL,
                   reminded_eve = NULL, reminded_morning = NULL WHERE id = ?""",
            (day.isoformat(), at, location, trainer_id, actor["telegram_id"], now_iso(), app_id))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="TRAINING_ASSIGNED", key=key,
                  old={k: app[k] for k in ("training_date", "training_time", "training_location", "trainer_id")},
                  new={"date": day.isoformat(), "time": at, "location": location, "trainer": trainer_id})
    return get(conn, app_id), True


def confirm_training(conn: sqlite3.Connection, telegram_id: int, app_id: int) -> tuple[dict, bool]:
    """The candidate's «Подтвердить запись»."""
    with transaction(conn):
        app = get(conn, app_id)
        user = svc.get_user_by_tg(conn, telegram_id)
        if app is None or user is None or app["user_id"] != user["id"] or app["status"] != "scheduled":
            raise ServiceError("Запись не найдена или изменилась", 404, "not_found")
        if app["training_confirmed_at"]:
            return app, False
        conn.execute("UPDATE applications SET training_confirmed_at = ? WHERE id = ?", (now_iso(), app_id))
    return get(conn, app_id), True


def reject(conn: sqlite3.Connection, actor: dict, app_id: int, reason: str | None, key: str) -> tuple[dict, bool]:
    """«Отказать»: the account is closed (the bot ignores the person) until «Вернуть в резерв»."""
    reason = " ".join((reason or "").split())[:300] or None
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key) or app["status"] == "rejected":
            return app, False
        if app["status"] not in OPEN:
            raise ServiceError("Кандидат уже на стажировке — закрыть доступ можно в «Команде»", 409, "bad_status")
        conn.execute("UPDATE applications SET status = 'rejected', reject_reason = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                     (reason, actor["telegram_id"], now_iso(), app_id))
        conn.execute("UPDATE users SET status = 'rejected', decided_at = ?, decided_by = ? WHERE id = ?",
                     (now_iso(), actor["telegram_id"], app["user_id"]))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="APPLICATION_REJECTED", key=key,
                  new={"application": app_id}, description=reason)
    return get(conn, app_id), True


def return_to_reserve(conn: sqlite3.Connection, actor: dict, app_id: int, key: str) -> tuple[dict, bool]:
    """«Вернуть в резерв»: the person may fill the questionnaire again; this one stays in the history."""
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key) or app["status"] == "returned":
            return app, False
        if app["status"] != "rejected":
            raise ServiceError("Вернуть можно только отклонённую анкету", 409, "bad_status")
        conn.execute("UPDATE applications SET status = 'returned' WHERE id = ?", (app_id,))
        conn.execute("UPDATE users SET status = 'pending', decided_at = NULL, decided_by = NULL WHERE id = ?", (app["user_id"],))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="APPLICATION_RETURNED", key=key,
                  new={"application": app_id})
    return get(conn, app_id), True


def contact(conn: sqlite3.Connection, actor: dict, app_id: int, manager: str | None) -> dict:
    """«Связаться напрямую»: the candidate gets the manager's @username and two answer buttons."""
    manager = (manager or actor.get("username") or "").strip().lstrip("@")[:64]
    if not manager or not all(c.isalnum() or c == "_" for c in manager):
        raise ServiceError("Укажите @username менеджера", 422, "manager_required")
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if app["status"] not in OPEN:
            raise ServiceError("Связаться можно с кандидатом, ещё не начавшим стажировку", 409, "bad_status")
        conn.execute("""UPDATE applications SET contact_manager = ?, contacted_at = ?, contact_answer = NULL,
                        contact_answer_at = NULL WHERE id = ?""", (manager, now_iso(), app_id))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="APPLICATION_CONTACTED",
                  key=svc.new_key(), new={"application": app_id, "manager": manager})
    return get(conn, app_id)


def contact_answer(conn: sqlite3.Connection, telegram_id: int, app_id: int, answer: str) -> tuple[dict, bool]:
    if answer not in ("wrote", "cant_write"):
        raise ServiceError("Неизвестный ответ", 422, "bad_answer")
    with transaction(conn):
        app = get(conn, app_id)
        user = svc.get_user_by_tg(conn, telegram_id)
        if app is None or user is None or app["user_id"] != user["id"] or not app["contacted_at"]:
            raise ServiceError("Сообщение устарело", 404, "not_found")
        if app["contact_answer"] == answer:
            return app, False
        conn.execute("UPDATE applications SET contact_answer = ?, contact_answer_at = ? WHERE id = ?", (answer, now_iso(), app_id))
    return get(conn, app_id), True


def start_internship(conn: sqlite3.Connection, actor: dict, app_id: int, key: str) -> tuple[dict, bool]:
    """After the training: the candidate gets the app as a trainee (status «Работаю»); their shifts are training."""
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key) or app["status"] == "internship":
            return app, False
        if app["status"] != "scheduled":
            raise ServiceError("Сначала назначьте обучение", 409, "bad_status")
        conn.execute("UPDATE applications SET status = 'internship' WHERE id = ?", (app_id,))
        conn.execute("""UPDATE users SET status = 'approved', decided_at = ?, decided_by = ?, trainee_since = ?,
                        trainee_done_at = NULL, work_status = 'working', work_status_at = ?, notify_reminders = 1
                        WHERE id = ?""", (now_iso(), actor["telegram_id"], now_iso(), now_iso(), app["user_id"]))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="INTERNSHIP_STARTED", key=key,
                  new={"application": app_id})
    return get(conn, app_id), True


def confirm_stage(conn: sqlite3.Connection, actor: dict, app_id: int, stage: int, counted: int, bonus: int,
                  key: str) -> tuple[dict, dict | None, bool]:
    """Staff check a stage: `counted` — what really counts (may be less than done), `bonus` — to the referrer
    (0 — none). Stage 1 ends the internship. Returns (app, bonus tx, changed)."""
    counted = _int(counted, 0, 100_000, "Засчитано — целое число штук")
    bonus = _int(bonus, 0, 1_000_000, "Бонус — целое число рублей")
    if stage not in (1, 2):
        raise ServiceError("Этап 1 или 2", 422, "bad_stage")
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key):
            return app, None, False
        if stage == 1 and app["status"] != "internship":
            raise ServiceError("Стажировка уже проверена", 409, "bad_status")
        if stage == 2 and (app["status"] != "done" or app["stage2_at"]):
            raise ServiceError("Второй этап уже проверен или ещё не начался", 409, "bad_status")
        user = svc.get_user(conn, app["user_id"])
        if stage == 1:
            conn.execute("UPDATE applications SET status = 'done', stage1_counted = ?, stage1_at = ? WHERE id = ?",
                         (counted, now_iso(), app_id))
            conn.execute("UPDATE users SET trainee_done_at = ? WHERE id = ?", (now_iso(), user["id"]))
        else:
            conn.execute("UPDATE applications SET stage2_counted = ?, stage2_at = ? WHERE id = ?", (counted, now_iso(), app_id))
        log_event(conn, user_id=user["id"], actor_id=actor["telegram_id"], event_type=f"REFERRAL_STAGE{stage}_CONFIRMED",
                  key=key, new={"application": app_id, "counted": counted, "bonus": bonus})
    tx = None
    if bonus and app["referrer_id"]:
        what = "прошёл стажировку" if stage == 1 else "отработал второй этап"
        result = finance.create(conn, actor, app["referrer_id"], "bonus", bonus,
                                f"Бонус за приглашённого: {app['full_name']} {what} ({counted} шт.)", svc.new_key())
        tx = result.tx
        with transaction(conn):
            conn.execute(f"UPDATE applications SET stage{stage}_bonus_tx = ? WHERE id = ?", (tx["id"], app_id))
    return get(conn, app_id), tx, True


# ---------------------------------------------------------------- scheduler


def training_reminders(conn: sqlite3.Connection, now: datetime | None = None) -> list[tuple[dict, str]]:
    """(application, «eve» | «morning») due now; marked as sent. Times from the settings."""
    now = now or now_local()
    cfg = app_settings.get_all(conn)
    out = []
    for kind, column, at, shift in (("eve", "reminded_eve", cfg["training_reminder_eve_time"], 1),
                                    ("morning", "reminded_morning", cfg["training_reminder_morning_time"], 0)):
        if now.time() < time.fromisoformat(at):
            continue
        day = (now.date() + timedelta(days=shift)).isoformat()
        with transaction(conn):
            # not right after the training was set — the candidate has just got the message
            rows = [dict(r) for r in conn.execute(
                f"""SELECT * FROM applications WHERE status = 'scheduled' AND training_date = ? AND {column} IS NULL
                    AND training_set_at < ?""", (day, (now - timedelta(hours=2)).isoformat(timespec="seconds")))]
            for app in rows:
                conn.execute(f"UPDATE applications SET {column} = ? WHERE id = ?", (now_iso(), app["id"]))
        out += [(app, kind) for app in rows]
    return out


def reached(conn: sqlite3.Connection) -> list[dict]:
    """Trainees who reached a stage and staff weren't told yet (told once per stage)."""
    out = []
    for r in conn.execute("SELECT * FROM applications WHERE status IN ('internship','done') AND stage2_at IS NULL"):
        app = dict(r)
        p = progress(conn, app)
        if not p or not p["review"]:
            continue
        mark = f"stage{p['stage']}:{app['id']}"
        with transaction(conn):
            seen = conn.execute("SELECT 1 FROM event_logs WHERE event_type = 'REFERRAL_STAGE_REACHED' AND description = ?",
                                (mark,)).fetchone()
            if seen:
                continue
            log_event(conn, user_id=app["user_id"], actor_id=0, event_type="REFERRAL_STAGE_REACHED", key=svc.new_key(),
                      description=mark)
        out.append({**app, "stage": p["stage"], "units": p["units"], "target": p["target"]})
    return out


def is_trainee(user: dict | None) -> bool:
    return bool(user and user.get("trainee_since") and not user.get("trainee_done_at"))


def days_text(app: dict) -> str:
    return ", ".join(WEEKDAYS[d] for d in _days(app))
