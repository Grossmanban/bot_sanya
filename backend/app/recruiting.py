"""Recruiting and onboarding (the customer's module 3).

    referral link (t.me/<bot>?start=ref_<token>, see invites.py — it changes every hour; the chat with the bot
    opens first, so the bot can write; startapp=ref_<token> straight into the Mini App still counts)
      → the questionnaire in the Mini App (an application, status `new`)
      → staff assign a training: date, time, place, trainer (`scheduled`); the candidate confirms with a button;
        reminders the evening before and in the morning
      → «Начать стажировку»: access to the app as a trainee (`internship`); their shifts are training ones —
        in the schedule and the raspred as «стажёр»; only the first `internship_units` pieces of theirs are
        the internship, everything above pays as usual
      → the goal counts what was PAID to the trainee by the payouts (18.11): a payout's units advance the
        goal and reaching it closes the internship by itself (the trainee is a regular employee from now on);
        staff may still check it by hand (may count fewer, e.g. 15 of 20) and give the referrer a bonus
      → `second_stage_units` work units after the internship → the second check and bonus.
    Any step: «Отказать» (optional reason; the account is closed — the bot stays silent), «Связаться
    напрямую» (a manager's @username; the candidate answers «Я написал» / «Не могу написать первым»),
    and from «Отклонено» — «Вернуть в резерв»: the candidate fills the questionnaire again.
Nothing is deleted: a returned application stays in the history (`returned`), the new one is a new row.
"""

import json
import sqlite3
from datetime import date, datetime, time, timedelta

from . import app_settings, finance, invites
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ACTIVE, ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import msk_datetime, now_iso, now_local

OPEN = ("new", "scheduled")              # waiting for staff
# «Ожидают рассмотрения», «На обучении», «Стажировка», «Завершённые» (the second stage is tracked there), «Отклонённые»
TABS = {"new": ("new",), "scheduled": ("scheduled",), "internship": ("internship",), "done": ("done",),
        "rejected": ("rejected",)}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MAX_TEXT = 100


# ---------------------------------------------------------------- referral links
# Links are managed by invites.py (ТЗ 27.10 §8–9): a token that changes every hour; users.ref_code is history.


# ---------------------------------------------------------------- the questionnaire


def get(conn: sqlite3.Connection, app_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return dict(row) if row else None


def current(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """The person's application in work (not one returned to the reserve)."""
    row = conn.execute("SELECT * FROM applications WHERE user_id = ? AND status != 'returned' ORDER BY id DESC LIMIT 1",
                       (user_id,)).fetchone()
    return dict(row) if row else None


def open_internship(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """The person's application while it is an internship (18.11: payouts advance its goal)."""
    row = conn.execute("SELECT * FROM applications WHERE user_id = ? AND status = 'internship' ORDER BY id DESC LIMIT 1",
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
    priority = form.get("priority") is True  # the questionnaire's last toggle: «назначат в первую очередь»
    if form.get("consent") is not True or form.get("truthful") is not True:
        raise ServiceError("Нужны согласие на обработку данных и подтверждение достоверности", 422, "consent_required")
    if start_param:  # startapp=ref_<token> straight into the Mini App: the same server check as /start
        link, st = invites.resolve(conn, start_param)
        if link is not None and link["kind"] == "ref" and st == "active":
            invites.use(conn, tg["id"], link)
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
        ref = invites.referred(conn, tg["id"])
        if ref is not None and ref["telegram_id"] == tg["id"]:
            ref = None
        if user is None and ref is None:  # ТЗ 27.10 §5: the questionnaire only by an employee's live link
            raise ServiceError(invites.STALE, 403, "no_invite")
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
        invites.mark_registered(conn, tg["id"], user_id)
        referrer_id = conn.execute("SELECT referrer_id FROM users WHERE id = ?", (user_id,)).fetchone()[0]
        cur = conn.execute(
            """INSERT INTO applications (user_id, full_name, age, metro, min_days, training_days, communication,
                                         referrer_id, consent_at, created_at, priority, priority_at, priority_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, full_name, age, metro, min_days, json.dumps(sorted(set(days))), communication, referrer_id,
             # svc's clock: the waiting indicator compares against it (tests patch both together)
             svc.now_iso(), svc.now_iso(), int(priority),
             svc.now_iso() if priority else None, "candidate" if priority else None))
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
    """What the trainee, the referrer and staff see: the internship, then the work units for the second bonus.
    18.11: the internship's goal counts what was PAID (payout_items.internship_units + the owner's manual
    correction); before the first payout it shows the approved fact, like it always did."""
    if app["status"] not in ("internship", "done"):
        return None
    user = user or svc.get_user(conn, app["user_id"])
    cfg = app_settings.get_all(conn)
    if app["status"] == "internship":
        fact, pending = units(conn, user["id"], True)
        target = cfg["internship_units"]
        paid = paid_total(conn, app)
        done = paid if paid else min(fact, target)
        return {"stage": 1, "units": done, "paid": paid, "fact": fact, "pending": pending, "target": target,
                "review": fact >= target}
    done, pending = units(conn, user["id"], False, user.get("trainee_done_at"))
    target = cfg["second_stage_units"]
    return {"stage": 2, "units": done, "pending": pending, "target": target,
            "review": done >= target and not app["stage2_at"], "finished": bool(app["stage2_at"])}


def paid_total(conn: sqlite3.Connection, app: dict) -> float:
    """How much of the internship's goal is already paid: the live payout items' contributions plus the
    owner's manual correction (applications.internship_manual_units)."""
    items = conn.execute(
        """SELECT COALESCE(SUM(i.internship_units), 0) FROM payout_items i WHERE i.user_id = ? AND i.voided = 0
           AND i.internship_units IS NOT NULL""", (app["user_id"],)).fetchone()[0]
    return round(items + (app.get("internship_manual_units") or 0), 2)


def internship_target(conn: sqlite3.Connection) -> int:
    return app_settings.get_all(conn)["internship_units"]


def internship_take(conn: sqlite3.Connection, app: dict, units: float) -> float:
    """How much of this payout's units goes to the goal: as much as is still missing (never above the target)."""
    if units <= 0:
        return 0.0
    left = max(0.0, internship_target(conn) - paid_total(conn, app))
    return round(min(units, left), 2)


def shift_is_training(conn: sqlite3.Connection, user: dict) -> bool:
    """18.11: a trainee's shift is a training one while their approved training pieces are still below the
    goal — everything above the first `internship_units` pieces pays as usual."""
    if not is_trainee(user):
        return False
    done = conn.execute(
        f"""SELECT COALESCE(SUM(fact_volume), 0) FROM shifts WHERE user_id = ? AND training = 1
            AND fact_volume IS NOT NULL AND status IN {ACTIVE}""", (user["id"],)).fetchone()[0]
    return done < internship_target(conn)


def close_internship(conn: sqlite3.Connection, actor: dict, app_id: int, batch_id: int | None = None) -> dict:
    """Plain writes, the caller owns the transaction: «Принять в команду» by itself — the goal is paid up
    (a payout reached it) or the owner counted it by hand. The referrer's bonus stays staff's decision."""
    app = get(conn, app_id)
    user = svc.get_user(conn, app["user_id"])
    counted = paid_total(conn, {**app, "internship_manual_units": app.get("internship_manual_units") or 0})
    conn.execute("UPDATE applications SET status = 'done', stage1_counted = ?, stage1_at = ?, version = version + 1 "
                 "WHERE id = ?", (round(counted), now_iso(), app_id))
    conn.execute("UPDATE users SET trainee_done_at = ? WHERE id = ?", (now_iso(), user["id"]))
    log_event(conn, user_id=user["id"], actor_id=actor["telegram_id"], event_type="INTERNSHIP_AUTO_CLOSED",
              key=svc.new_key(), new={"application": app_id, "counted": round(counted)},
              description=f"закрыта выплатой №{batch_id}" if batch_id else "закрыта вручную по выплатам")
    return get(conn, app_id)


def set_internship_units(conn: sqlite3.Connection, actor: dict, app_id: int, value: int, key: str) -> tuple[dict, bool]:
    """18.11: the owner corrects the goal's paid counter by hand (the card of the internship). Reaching the
    target closes the internship; an auto-closed one returns to «Стажировка» if the value drops below."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, app_id), False
        actor, app = _load(conn, actor, app_id)
        user = svc.get_user(conn, app["user_id"])
        if app["status"] not in ("internship", "done") or app["stage2_at"] or user is None:
            raise ServiceError("Корректировать можно идущую стажировку", 409, "bad_status")
        target = internship_target(conn)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= target:
            raise ServiceError(f"Значение — от 0 до {target}", 422, "bad_units")
        items = conn.execute(
            "SELECT COALESCE(SUM(internship_units), 0) FROM payout_items WHERE user_id = ? AND voided = 0",
            (user["id"],)).fetchone()[0]
        old_paid = paid_total(conn, app)
        conn.execute("UPDATE applications SET internship_manual_units = ? WHERE id = ?",
                     (round(value - items, 2), app_id))
        closed = False
        if value >= target and app["status"] == "internship":
            close_internship(conn, actor, app_id)
            closed = True
        elif value < target and app["status"] == "done" and app["stage1_at"]:
            last = conn.execute(
                """SELECT event_type FROM event_logs WHERE user_id = ? AND user_id != 0
                   AND event_type IN ('INTERNSHIP_AUTO_CLOSED', 'REFERRAL_STAGE1_CONFIRMED') ORDER BY id DESC LIMIT 1""",
                (user["id"],)).fetchone()
            if last and last["event_type"] == "INTERNSHIP_AUTO_CLOSED":  # a staff-checked one stays done
                conn.execute("UPDATE applications SET status = 'internship', stage1_counted = NULL, stage1_at = NULL, "
                             "version = version + 1 WHERE id = ?", (app_id,))
                conn.execute("UPDATE users SET trainee_done_at = NULL WHERE id = ?", (user["id"],))
        log_event(conn, user_id=user["id"], actor_id=actor["telegram_id"], event_type="INTERNSHIP_UNITS_SET", key=key,
                  old={"paid": old_paid}, new={"paid": value})
    return get(conn, app_id), closed


# ---------------------------------------------------------------- views


def set_hidden(conn: sqlite3.Connection, actor: dict, app_id: int, on: bool, key: str) -> dict | None:
    """19.11: «Скрыть» on a finished card — it leaves «Завершённые» (and the tab count) and sits under «Скрытые»,
    «Вернуть» brings it back. Nothing else changes: the person stays in the team as they are."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, app_id)
        actor, app = _load(conn, actor, app_id)
        if app["status"] != "done":
            raise ServiceError("Скрывать можно только завершённые анкеты", 409, "bad_status")
        conn.execute("UPDATE applications SET hidden_at = ?, hidden_by = ? WHERE id = ?",
                     (now_iso() if on else None, actor["id"] if on else None, app_id))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="APPLICATION_HIDDEN",
                  key=key, old={"hidden": bool(app["hidden_at"])}, new={"hidden": bool(on)})
    return get(conn, app_id)


def _days(app: dict) -> list[int]:
    return json.loads(app["training_days"] or "[]")


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def wait_text(hours: float) -> str:
    """How long the candidate has been waiting: «40 мин», «12 часов», «1 день 12 часов», «7 дней» (exactly
    days — without the zero hours); the staff card colours it red after 3 days, yellow after a day."""
    minutes = max(0, int(hours * 60))
    if minutes < 60:
        return f"{minutes} мин"
    days, hr = divmod(minutes // 60, 24)
    parts = [f"{days} {_plural(days, 'день', 'дня', 'дней')}" if days else "",
             f"{hr} {_plural(hr, 'час', 'часа', 'часов')}" if hr else ""]
    return " ".join(p for p in parts if p)


def view(conn: sqlite3.Connection, app: dict) -> dict:
    user = svc.get_user(conn, app["user_id"])
    ref = svc.get_user(conn, app["referrer_id"]) if app["referrer_id"] else None
    trainer = svc.get_user(conn, app["trainer_id"]) if app["trainer_id"] else None
    history = conn.execute("SELECT COUNT(*) FROM applications WHERE user_id = ? AND id != ?", (app["user_id"], app["id"])).fetchone()[0]
    # «Ожидает рассмотрения»: how long the questionnaire has been waiting for the staff (MSK, the server's clock)
    waiting = None
    if app["status"] == "new":
        waited = (svc.now_local() - datetime.fromisoformat(app["created_at"])).total_seconds() / 3600
        waiting = {"hours": round(max(0.0, waited), 2), "text": wait_text(waited)}
    return {
        **{k: app[k] for k in ("id", "status", "full_name", "age", "metro", "min_days", "communication", "created_at",
                               "reject_reason", "decided_at", "training_date", "training_time", "training_location",
                               "training_confirmed_at", "contact_manager", "contacted_at", "contact_answer",
                               "contact_answer_at", "stage1_counted", "stage1_at", "stage2_counted", "stage2_at")},
        "training_days": _days(app),
        "priority": bool(app["priority"]),
        "hidden": bool(app["hidden_at"]),
        "waiting": waiting,
        # ТЗ 30.10 §7: the moment the candidate sent the questionnaire, in Moscow time
        "submitted_text": msk_datetime(app["created_at"]),
        "user_id": user["id"], "telegram_id": user["telegram_id"], "username": user["username"],
        "referrer": svc.full_name(ref) if ref else None,
        "referrer_tg": {"telegram_id": ref["telegram_id"], "username": ref["username"]} if ref else None,
        "trainer": {"id": trainer["id"], "name": svc.full_name(trainer), "username": trainer["username"]} if trainer else None,
        "earlier": history,  # earlier applications of the same person (kept in history)
        "progress": progress(conn, app, user),
        "trainer_tg": {"telegram_id": trainer["telegram_id"], "username": trainer["username"]} if trainer else None,
        **_training_extra(conn, app),
    }


def _training_extra(conn: sqlite3.Connection, app: dict) -> dict:
    from . import training  # training builds on this module

    return training.extra(conn, app)


def listing(conn: sqlite3.Connection, tab: str) -> list[dict]:
    if tab not in TABS:
        raise ServiceError("Неизвестная вкладка", 422, "bad_tab")
    statuses = TABS[tab]
    # 19.11: hidden finished cards come back with the `hidden` flag — the UI tucks them under «Скрытые»; a blocked
    # person isn't shown in «Завершённые» at all — «принят в команду» for someone the team blocked is noise
    blocked = "" if tab != "done" else " AND COALESCE(u.account_state, '') != 'blocked'"
    rows = [dict(r) for r in conn.execute(
        f"""SELECT a.* FROM applications a LEFT JOIN users u ON u.id = a.user_id
            WHERE a.status IN ({','.join('?' * len(statuses))}){blocked}
            ORDER BY a.id DESC LIMIT 300""", statuses)]
    if tab == "scheduled":  # ТЗ 30.10 §4: both confirmed first
        from . import training

        rows = training.sort_by_agreement(rows)
    return rows


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """The tab pills — the same rule as the listing: without the hidden ones, without blocked people in «Завершённые»."""
    out = {tab: 0 for tab in TABS}
    rows = conn.execute(
        """SELECT a.status, COUNT(*) FROM applications a LEFT JOIN users u ON u.id = a.user_id
           WHERE a.hidden_at IS NULL AND NOT (a.status = 'done' AND u.account_state = 'blocked')
           GROUP BY a.status""")
    for status, n in rows:
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
        person = svc.get_user(conn, app["user_id"]) or {}
        out.append({"id": app["id"], "name": app["full_name"], "status": app["status"], "created_at": app["created_at"],
                    # ТЗ 27.10 §11: the profile by the Telegram ID
                    "telegram_id": person.get("telegram_id"), "username": person.get("username"),
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


def confirm_training(conn: sqlite3.Connection, telegram_id: int, app_id: int, version: int | None = None) -> tuple[dict, bool]:
    """The candidate's «Подтвердить запись». ТЗ 30.10 §4: the button carries the version of the date — one from an
    earlier date or a cancelled attempt doesn't confirm the current one (buttons sent before had no version)."""
    with transaction(conn):
        app = get(conn, app_id)
        user = svc.get_user_by_tg(conn, telegram_id)
        if (app is None or user is None or app["user_id"] != user["id"] or app["status"] != "scheduled"
                or not app["training_date"]):
            raise ServiceError("Запись не найдена или изменилась", 404, "not_found")
        if version is not None and version != app["version"]:
            raise ServiceError("Дата обучения уже изменилась — подтвердите новую по последнему сообщению", 409, "stale")
        if app["training_confirmed_at"]:
            return app, False
        conn.execute("UPDATE applications SET training_confirmed_at = ? WHERE id = ?", (now_iso(), app_id))
        log_event(conn, user_id=user["id"], actor_id=telegram_id, event_type="TRAINING_CONFIRMED", key=svc.new_key(),
                  new={"application": app_id, "date": app["training_date"], "time": app["training_time"]})
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


def set_priority(conn: sqlite3.Connection, actor: dict, app_id: int, on: bool, key: str) -> tuple[dict, bool]:
    """The staff's switch of the training priority on any questionnaire (the candidate's own toggle marks
    `priority_by = "candidate"`). Returns (application, changed)."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, app_id), False
        actor, app = _load(conn, actor, app_id)
        if bool(app["priority"]) == bool(on):
            return app, False
        conn.execute("UPDATE applications SET priority = ?, priority_at = ?, priority_by = ? WHERE id = ?",
                     (int(on), now_iso() if on else None, str(actor["telegram_id"]) if on else None, app_id))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"],
                  event_type="APPLICATION_PRIORITY_SET" if on else "APPLICATION_PRIORITY_CLEARED", key=key,
                  new={"application": app_id},
                  description="приоритет обучения" if on else "приоритет обучения снят")
    return get(conn, app_id), True


def cancel_training(conn: sqlite3.Connection, actor: dict, app_id: int, key: str) -> tuple[dict, bool]:
    """«Отменить обучение»: back to «Ожидают рассмотрения», the training details are cleared. Not a refusal."""
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key) or app["status"] == "new":
            return app, False
        if app["status"] != "scheduled":
            raise ServiceError("Отменить можно только назначенное обучение", 409, "bad_status")
        conn.execute("""UPDATE applications SET status = 'new', training_date = NULL, training_time = NULL,
                               training_location = NULL, trainer_id = NULL, training_set_by = NULL, training_set_at = NULL,
                               training_confirmed_at = NULL, reminded_eve = NULL, reminded_morning = NULL WHERE id = ?""",
                     (app_id,))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="TRAINING_CANCELLED", key=key,
                  old={k: app[k] for k in ("training_date", "training_time", "training_location", "trainer_id")},
                  new={"application": app_id})
    return {**get(conn, app_id), "cancelled_trainer_id": app["trainer_id"], "cancelled_date": app["training_date"]}, True


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
        if app["status"] != "scheduled" or not app["trainer_id"]:
            raise ServiceError("Сначала назначьте тренера и обучение", 409, "bad_status")
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
