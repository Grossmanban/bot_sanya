"""ТЗ 28.10 §2–§6: обучение кандидата — тренер, согласование, запросы на одобрение, отмены, раздел «Обучение».

Built on the existing applications (recruiting.py): the statuses stay (new → scheduled → internship → done,
rejected, returned); the finer stage is derived from the columns of migration 32:

    scheduled, no date                    «Тренер назначен — согласование даты» (after a cancellation: «Ожидает новой даты»)
    scheduled, date, not confirmed        «Ожидает подтверждения» (by the candidate and/or the trainer)
    scheduled, both confirmed             «Обучение согласовано» — the personal invitation to «Предзакреп» goes out
    scheduled, training_started_at        «Обучение начато»
    internship                            «Стажировка»; norm reached or the trainer said it's over —
                                          «Завершение ожидает проверки администратора»
    done                                  «Принят в команду»

«Тренер кандидата» (in the app — «Обучающий») is any employee, not the network trainer role. A trainer asks,
an admin decides: start, refusal, cancellation, a change, the internship. A request is one row: a second tap
returns the same one; a change made meanwhile makes a change request stale. An admin may act directly.
Cancellations are counted (a reschedule is not a cancellation, a no-show is); 3 of them (a setting) — a warning,
never an automatic refusal. A refusal blocks the account (restorable by «Вернуть в резерв»).

ТЗ 30.10: the trainer sets and changes the date and time of their own candidate directly (the same `_apply_schedule`
as an admin; the trainer who set it has confirmed it). «Отменить обучение» — by an admin or by the candidate's own
trainer, with a reason — ends the attempt: it is kept in training_cancellations with everything it had (the
counter), the candidate goes back to «Ожидают рассмотрения» without a trainer and may be given to the same or
another trainer — a new attempt. «Обучение» is sorted by the agreement: both confirmed first (green), then the
candidate confirmed, then a date waiting for the candidate, then no date."""

import json
import sqlite3
from datetime import date, time

from . import app_settings, recruiting, templates
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, now_local

ACTIVE = ("scheduled", "internship")
KINDS = {"start": "Начать обучение", "refuse": "Отказать в обучении", "cancel": "Отменить обучение",
         "change": "Изменить обучение", "internship": "Начать стажировку"}
NEEDS_REASON = ("refuse", "cancel", "change")
LEGACY_NOTE = "до 28.10"


# ---------------------------------------------------------------- reading


def get(conn: sqlite3.Connection, app_id: int) -> dict | None:
    return recruiting.get(conn, app_id)


def cancellations(conn: sqlite3.Connection, app_id: int) -> list[dict]:
    names = {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT telegram_id, first_name, last_name FROM users")}
    ids = {r["id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT id, first_name, last_name FROM users")}
    out = []
    for r in conn.execute("SELECT * FROM training_cancellations WHERE application_id = ? ORDER BY id", (app_id,)):
        r = dict(r)
        who = ids.get(r["initiator_id"]) if r["initiator_role"] in ("trainer", "candidate") else names.get(r["initiator_id"])
        out.append({"at": r["at"], "kind": r["kind"], "reason": r["reason"] or (LEGACY_NOTE if r["legacy"] else None),
                    "initiator": who, "role": r["initiator_role"], "legacy": bool(r["legacy"]),
                    # ТЗ 30.10 §5: what the cancelled attempt was
                    "trainer": ids.get(r["trainer_id"]) if r.get("trainer_id") else None,
                    "training_date": r.get("training_date"), "training_time": r.get("training_time"),
                    "training_location": r.get("training_location"),
                    "candidate_confirmed_at": r.get("candidate_confirmed_at"),
                    "trainer_confirmed_at": r.get("trainer_confirmed_at"),
                    "attempt_started_at": r.get("attempt_started_at"), "training_started_at": r.get("training_started_at")})
    return out


# ТЗ 30.10 §5: the history of statuses inside each attempt, read from the event log
EVENT_TITLES = {
    "TRAINER_ASSIGNED": "Назначен тренер", "TRAINING_ASSIGNED": "Назначены дата и время",
    "TRAINING_CONFIRMED": "Кандидат подтвердил дату", "TRAINING_TRAINER_CONFIRMED": "Тренер подтвердил дату",
    "TRAINING_STARTED": "Обучение начато", "TRAINING_REQUESTED": "Запрос тренера",
    "TRAINING_REQUEST_APPROVED": "Запрос одобрен", "TRAINING_REQUEST_REJECTED": "Запрос отклонён",
    "TRAINING_CANCELLED": "Обучение отменено", "TRAINING_BACK_TO_REVIEW": "Возвращён в «Ожидают рассмотрения»",
    "INTERNSHIP_STARTED": "Стажировка начата", "APPLICATION_REJECTED": "Отказано",
}


def _event_title(conn, row: dict, names: dict[int, str]) -> str:
    title = EVENT_TITLES[row["event_type"]]
    try:
        new = json.loads(row["new_value"] or "{}")
    except ValueError:
        new = {}
    if row["event_type"] == "TRAINER_ASSIGNED" and new.get("trainer"):
        title += f": {names.get(new['trainer'], '—')}"
    elif row["event_type"] == "TRAINING_ASSIGNED" and new.get("date"):
        d = new["date"]
        title += f": {d[8:10]}.{d[5:7]} в {new.get('time') or '—'}" + (" (тренер)" if new.get("by") == "trainer" else "")
    elif row["event_type"] == "TRAINING_REQUESTED" and new.get("kind") in KINDS:
        title += f": {KINDS[new['kind']]}"
    elif row["event_type"] == "TRAINING_CANCELLED" and new.get("kind") == "no_show":
        title = "Неявка кандидата"
    return title


def attempts(conn: sqlite3.Connection, app: dict, cancels: list[dict] | None = None) -> list[dict]:
    """ТЗ 30.10 §5: every attempt of training — the cancelled ones (kept in training_cancellations) and the current
    one — each with its own history of statuses. A new assignment after a cancellation is a new attempt."""
    cancels = cancellations(conn, app["id"]) if cancels is None else cancels
    current = app["status"] == "scheduled" or (app["status"] in ("internship", "done") and app.get("trainer_id"))
    if not cancels and not current:
        return []
    names = {r["id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT id, first_name, last_name FROM users")}
    types = tuple(EVENT_TITLES)
    events = [dict(r) for r in conn.execute(
        f"""SELECT event_type, new_value, created_at FROM event_logs WHERE user_id = ? AND created_at >= ?
            AND event_type IN ({','.join('?' * len(types))}) ORDER BY created_at, id""",
        (app["user_id"], app["created_at"], *types))]
    # each «Обучение отменено» closes an attempt (the migration's «back to review» belongs to the one it closed)
    buckets: list[list[dict]] = [[]]
    for e in events:
        if e["event_type"] == "TRAINING_BACK_TO_REVIEW" and len(buckets) > 1:
            buckets[-2].append(e)
            continue
        buckets[-1].append(e)
        if e["event_type"] == "TRAINING_CANCELLED":
            buckets.append([])
    buckets += [[] for _ in range(len(cancels) + 1 - len(buckets))]

    def history(n: int) -> list[dict]:
        return [{"at": e["created_at"], "title": _event_title(conn, e, names)} for e in buckets[n]]

    out = []
    for n, c in enumerate(cancels):
        out.append({"n": n + 1, "status": "cancelled", "status_title": "Неявка — отменено" if c["kind"] == "no_show" else "Отменено",
                    **{k: c[k] for k in ("trainer", "training_date", "training_time", "training_location",
                                         "candidate_confirmed_at", "trainer_confirmed_at", "attempt_started_at",
                                         "training_started_at", "reason", "initiator", "role", "legacy")},
                    "cancelled_at": c["at"], "events": history(n)})
    if current:
        trainer = names.get(app["trainer_id"]) if app.get("trainer_id") else None
        out.append({"n": len(cancels) + 1, "status": "active", "status_title": stage(conn, app)[1], "trainer": trainer,
                    "training_date": app.get("training_date"), "training_time": app.get("training_time"),
                    "training_location": app.get("training_location"),
                    "candidate_confirmed_at": app.get("training_confirmed_at"),
                    "trainer_confirmed_at": app.get("trainer_confirmed_at"),
                    "attempt_started_at": app.get("trainer_assigned_at"), "training_started_at": app.get("training_started_at"),
                    "reason": None, "initiator": None, "role": None, "legacy": False, "cancelled_at": None,
                    "events": history(len(cancels)) if len(buckets) > len(cancels) else []})
    return out


# ТЗ 30.10 §4: the agreement of the date — mutual confirmation first
AGREEMENT = {
    "agreed": (0, "Обе стороны подтвердили дату и время"),
    "candidate_confirmed": (1, "Кандидат подтвердил, тренер — ещё нет"),
    "waiting_candidate": (2, "Дата назначена, кандидат ещё не подтвердил"),
    "no_date": (3, "Дата и время ещё не назначены"),
    "cancelled": (4, "Обучение отменено"),
}


def agreement(app: dict) -> str | None:
    """None outside training. «agreed» — only when both confirmed the current date (never a cancelled one)."""
    if app["status"] == "new" and app.get("_cancelled"):
        return "cancelled"
    if app["status"] != "scheduled":
        return None
    if not app.get("training_date"):
        return "no_date"
    if app.get("training_confirmed_at") and app.get("trainer_confirmed_at"):
        return "agreed"
    if app.get("training_confirmed_at"):
        return "candidate_confirmed"
    return "waiting_candidate"


def sort_by_agreement(rows: list[dict]) -> list[dict]:
    """«Обучение» (§4): by the agreement, inside one state by the questionnaire — newer first, stable by id."""
    newest = sorted(rows, key=lambda a: (a.get("created_at") or "", a["id"]), reverse=True)
    return sorted(newest, key=lambda a: AGREEMENT[agreement(a)][0] if agreement(a) else 9)


def limit(conn: sqlite3.Connection) -> int:
    return app_settings.get_all(conn)["training_cancel_limit"]


def requests_of(conn: sqlite3.Connection, app_id: int, status: str = "pending") -> list[dict]:
    return [request_view(conn, dict(r)) for r in conn.execute(
        "SELECT * FROM training_requests WHERE application_id = ? AND status = ? ORDER BY id", (app_id, status))]


def request_view(conn: sqlite3.Connection, r: dict) -> dict:
    who = svc.get_user(conn, r["initiator_id"]) if r["initiator_id"] else None
    payload = json.loads(r["payload"] or "{}")
    new_trainer = svc.get_user(conn, payload["trainer_id"]) if payload.get("trainer_id") else None
    app = get(conn, r["application_id"]) or {}
    return {"id": r["id"], "application_id": r["application_id"], "kind": r["kind"], "title": KINDS[r["kind"]],
            "status": r["status"], "reason": r["reason"], "created_at": r["created_at"],
            "initiator": svc.full_name(who) if who else None, "initiator_role": r["initiator_role"],
            "initiator_tg": {"telegram_id": who["telegram_id"], "username": who["username"]} if who else None,
            "candidate": app.get("full_name"), "payload": payload, "old": json.loads(r["old"] or "{}"),
            "new_trainer": svc.full_name(new_trainer) if new_trainer else None,
            "decision_reason": r["decision_reason"], "decided_at": r["decided_at"]}


def pending_requests(conn: sqlite3.Connection) -> list[dict]:
    """«Ожидает согласования» — one list for the bot and the app."""
    return [request_view(conn, dict(r)) for r in conn.execute(
        "SELECT * FROM training_requests WHERE status = 'pending' ORDER BY id")]


def stage(conn: sqlite3.Connection, app: dict) -> tuple[str, str]:
    s = app["status"]
    if s == "rejected":
        return "rejected", "Отказано"
    if s == "returned":
        return "returned", "Возвращён в резерв"
    if s == "new":
        if conn.execute("SELECT 1 FROM training_cancellations WHERE application_id = ?", (app["id"],)).fetchone():
            return "cancelled", "Обучение отменено — ожидает рассмотрения"
        return "new", "Ожидает рассмотрения"
    if s == "done":
        return "done", "Принят в команду"
    if s == "internship":
        p = recruiting.progress(conn, app)
        if app.get("finish_requested_at") or (p and p["review"]):
            return "finish_review", "Завершение ожидает проверки администратора"
        return "internship", "Стажировка"
    if app.get("training_started_at"):
        return "started", "Обучение начато"
    if not app.get("training_date"):
        cancelled = conn.execute("SELECT 1 FROM training_cancellations WHERE application_id = ?", (app["id"],)).fetchone()
        return ("new_date", "Ожидает новой даты") if cancelled else ("trainer", "Тренер назначен — согласование даты")
    waiting = [who for who, at in (("кандидата", app.get("training_confirmed_at")), ("тренера", app.get("trainer_confirmed_at")))
               if not at]
    if waiting:
        return "confirm", "Ожидает подтверждения " + " и ".join(waiting)
    return "agreed", "Обучение согласовано"


def extra(conn: sqlite3.Connection, app: dict) -> dict:
    """What the onboarding card and «Обучение» add to recruiting.view."""
    code, title = stage(conn, app)
    cancels = cancellations(conn, app["id"])
    n, top = len(cancels), limit(conn)
    agree = agreement({**app, "_cancelled": bool(cancels)})
    sent = [dict(r) for r in conn.execute(
        "SELECT kind, sent_at, delivered FROM candidate_messages WHERE application_id = ? ORDER BY id", (app["id"],))]
    return {"stage": code, "stage_title": title, "cancellations": cancels, "cancel_count": n, "cancel_limit": top,
            "cancel_warning": n >= top > 0 and app.get("cancel_ack", 0) < n,
            "requests": requests_of(conn, app["id"]), "version": app.get("version", 1),
            "trainer_confirmed_at": app.get("trainer_confirmed_at"), "training_started_at": app.get("training_started_at"),
            "finish_requested_at": app.get("finish_requested_at"), "trainer_assigned_at": app.get("trainer_assigned_at"),
            # §2: the 48-hour timer from the last «свяжитесь с тренером» (or a manual reset); shown until agreed
            "contact_timer_at": app.get("contact_timer_at") if code in ("trainer", "new_date", "confirm") else None,
            "messages": sent, "referrer_id": app.get("referrer_id"),
            # ТЗ 30.10 §4: the agreement of the date (green only when both confirmed), §5: the attempts with history
            "agreement": agree, "agreement_title": AGREEMENT[agree][1] if agree else None,
            "agreement_rank": AGREEMENT[agree][0] if agree else None, "attempt": n + 1,
            "attempts": attempts(conn, app, cancels),
            "pre_invited_at": app.get("pre_invited_at"), "team_invited_at": app.get("team_invited_at")}


# ---------------------------------------------------------------- access to «Обучение»


def assigned(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM applications WHERE trainer_id = ? AND status IN {ACTIVE}",
                        (user_id,)).fetchone()[0]


def has_access(conn: sqlite3.Connection, user: dict | None) -> bool:
    """«Обучение»: a trainer of a candidate (automatically) or a grant by an admin; never a blocked account."""
    if user is None or user["status"] != "approved" or user["account_state"] == "blocked":
        return False
    if assigned(conn, user["id"]):
        return True
    return conn.execute("SELECT 1 FROM training_access WHERE user_id = ? AND revoked_at IS NULL", (user["id"],)).fetchone() is not None


def granted(conn: sqlite3.Connection, user_id: int) -> bool:
    return conn.execute("SELECT 1 FROM training_access WHERE user_id = ? AND revoked_at IS NULL", (user_id,)).fetchone() is not None


def _grant(conn, user_id: int, actor_tg: int | None, auto: bool) -> None:
    if not granted(conn, user_id):
        conn.execute("INSERT INTO training_access (user_id, granted_at, granted_by, auto) VALUES (?, ?, ?, ?)",
                     (user_id, now_iso(), actor_tg, int(auto)))


def set_access(conn: sqlite3.Connection, actor: dict, user_id: int, allow: bool, key: str) -> bool:
    """§3: an admin gives or takes «Обучение»; not while candidates are still assigned to the person."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return False
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "registrations"):
            raise ServiceError("Доступ к «Обучению» выдают владелец и администраторы", 403, "forbidden")
        user = svc.get_user(conn, user_id)
        if user is None or user["status"] != "approved":
            raise ServiceError("Сотрудник не найден", 404, "not_found")
        if allow == granted(conn, user_id):
            return False
        if allow:
            _grant(conn, user_id, actor["telegram_id"], False)
        else:
            left = assigned(conn, user_id)
            if left:
                raise ServiceError(f"За сотрудником ещё {left} кандидат(ов) — сначала передайте их другому тренеру",
                                   409, "has_candidates")
            conn.execute("UPDATE training_access SET revoked_at = ?, revoked_by = ? WHERE user_id = ? AND revoked_at IS NULL",
                         (now_iso(), actor["telegram_id"], user_id))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], key=key,
                  event_type="TRAINING_ACCESS_GRANTED" if allow else "TRAINING_ACCESS_REVOKED")
    return True


def my_candidates(conn: sqlite3.Connection, user: dict) -> list[dict]:
    """«Обучение»: the candidates this person trains now (a refusal or the team takes them off the list)."""
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM applications WHERE trainer_id = ? AND status IN {ACTIVE} ORDER BY id DESC", (user["id"],))]
    return sort_by_agreement(rows)  # ТЗ 30.10 §4: both confirmed first


# ---------------------------------------------------------------- the admin's direct actions


def _load(conn, actor: dict, app_id: int) -> tuple[dict, dict]:
    return recruiting._load(conn, actor, app_id)


def _bump(conn, app_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE applications SET {sets}{', ' if sets else ''}version = version + 1 WHERE id = ?",
                 (*fields.values(), app_id))


def _stale_changes(conn, app_id: int, keep: int | None = None) -> None:
    """A change applied by an admin makes other pending change requests stale."""
    conn.execute("""UPDATE training_requests SET status = 'stale', decided_at = ? WHERE application_id = ?
                    AND kind = 'change' AND status = 'pending' AND id != ?""", (now_iso(), app_id, keep or 0))


def _trainer_ok(conn, trainer_id: int, app: dict) -> dict:
    trainer = svc.get_user(conn, trainer_id)
    if (trainer is None or trainer["status"] != "approved" or trainer["account_state"] != "active"
            or trainer["id"] == app["user_id"]):
        raise ServiceError("Выберите тренера из действующих сотрудников", 422, "trainer_required")
    return trainer


def assign_trainer(conn: sqlite3.Connection, actor: dict, app_id: int, trainer_id: int, key: str) -> tuple[dict, bool, int | None]:
    """§2: the candidate's trainer — any active employee; the date comes later. Returns (app, changed, previous)."""
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key):
            return app, False, None
        if app["status"] not in recruiting.OPEN:
            raise ServiceError("Тренера назначают кандидату до стажировки", 409, "bad_status")
        _trainer_ok(conn, trainer_id, app)
        previous = app["trainer_id"]
        if previous == trainer_id and app["status"] == "scheduled":
            return app, False, None
        _bump(conn, app_id, status="scheduled", trainer_id=trainer_id, trainer_assigned_at=now_iso(),
              trainer_confirmed_at=None)
        _grant(conn, trainer_id, actor["telegram_id"], True)
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="TRAINER_ASSIGNED", key=key,
                  old={"trainer": previous}, new={"trainer": trainer_id, "application": app_id})
    return get(conn, app_id), True, previous


def message_sent(conn: sqlite3.Connection, app_id: int, kind: str, actor_tg: int | None, delivered: bool,
                 restart_timer: bool) -> None:
    """History of «свяжитесь с тренером» and other messages; a new one restarts the 48-hour count (§2)."""
    with transaction(conn):
        conn.execute("INSERT INTO candidate_messages (application_id, kind, sent_at, sent_by, delivered) VALUES (?, ?, ?, ?, ?)",
                     (app_id, kind, now_iso(), actor_tg, int(delivered)))
        if restart_timer:
            conn.execute("UPDATE applications SET contact_timer_at = ? WHERE id = ?", (now_iso(), app_id))


def reset_timer(conn: sqlite3.Connection, actor: dict, app_id: int, key: str) -> dict:
    """§2: start the 48 hours again without touching the status (the history stays)."""
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key):
            return app
        if app["status"] != "scheduled":
            raise ServiceError("Таймер — пока обучение не согласовано", 409, "bad_status")
        conn.execute("UPDATE applications SET contact_timer_at = ? WHERE id = ?", (now_iso(), app_id))
        conn.execute("INSERT INTO candidate_messages (application_id, kind, sent_at, sent_by, delivered) VALUES (?, 'timer_reset', ?, ?, 0)",
                     (app_id, now_iso(), actor["telegram_id"]))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="TRAINING_TIMER_RESET", key=key)
    return get(conn, app_id)


def schedule(conn: sqlite3.Connection, actor: dict, app_id: int, day: date, at: str, location: str,
             trainer_id: int | None, key: str) -> tuple[dict, bool, int | None]:
    """§4: an admin sets or changes the date, time, place (and the trainer). Both the candidate and the trainer
    confirm again (В9). Not a cancellation. Returns (app, changed, previous trainer if replaced)."""
    try:
        at = time.fromisoformat(at).strftime("%H:%M")
    except (TypeError, ValueError) as exc:
        raise ServiceError("Время — в формате ЧЧ:ММ", 422, "bad_time") from exc
    location = recruiting._text(location, "Место")
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key):
            return app, False, None
        if app["status"] not in recruiting.OPEN:
            raise ServiceError("Обучение назначают кандидату до стажировки", 409, "bad_status")
        if day < now_local().date():
            raise ServiceError("Дата обучения уже прошла", 422, "past_date")
        trainer_id = trainer_id or app["trainer_id"]
        if not trainer_id:
            raise ServiceError("Сначала назначьте тренера", 422, "trainer_required")
        _trainer_ok(conn, trainer_id, app)
        previous = app["trainer_id"] if app["trainer_id"] and app["trainer_id"] != trainer_id else None
        _apply_schedule(conn, app, day.isoformat(), at, location, trainer_id, actor["telegram_id"])
        _stale_changes(conn, app_id)
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="TRAINING_ASSIGNED", key=key,
                  old={k: app[k] for k in ("training_date", "training_time", "training_location", "trainer_id")},
                  new={"date": day.isoformat(), "time": at, "location": location, "trainer": trainer_id})
    return get(conn, app_id), True, previous


def schedule_by_trainer(conn: sqlite3.Connection, user: dict, app_id: int, day: date, at: str, location: str,
                        key: str) -> tuple[dict, bool, dict]:
    """ТЗ 30.10 §2: «Назначить дату» / «Изменить дату» in the trainer's «Обучение» — their own candidate only (checked
    here, not only by the button), the same mechanism as an admin's (`_apply_schedule`): the candidate confirms again,
    the reminders start over. The trainer who set the date has confirmed it. Returns (app, changed, the old values)."""
    try:
        at = time.fromisoformat(at).strftime("%H:%M")
    except (TypeError, ValueError) as exc:
        raise ServiceError("Время — в формате ЧЧ:ММ", 422, "bad_time") from exc
    location = recruiting._text(location, "Место")
    _check_key(key)
    with transaction(conn):
        app = get(conn, app_id)
        old = {k: (app or {}).get(k) for k in ("training_date", "training_time", "training_location")}
        if _seen(conn, key):
            return app, False, old
        user = svc.get_user(conn, user["id"])
        if app is None or app["trainer_id"] != user["id"] or not has_access(conn, user):
            raise ServiceError("Это не ваш кандидат", 403, "forbidden")
        if app["status"] != "scheduled":
            raise ServiceError("Дату назначают до стажировки — обновите экран", 409, "bad_status")
        if day < now_local().date():
            raise ServiceError("Дата обучения уже прошла", 422, "past_date")
        if (app["training_date"], app["training_time"], app["training_location"]) == (day.isoformat(), at, location):
            return app, False, old  # nothing changed: the confirmations stay
        _apply_schedule(conn, app, day.isoformat(), at, location, app["trainer_id"], user["telegram_id"])
        conn.execute("UPDATE applications SET trainer_confirmed_at = ? WHERE id = ?", (now_iso(), app_id))
        _stale_changes(conn, app_id)
        log_event(conn, user_id=app["user_id"], actor_id=user["telegram_id"], event_type="TRAINING_ASSIGNED", key=key,
                  old={**old, "trainer_id": app["trainer_id"]},
                  new={"date": day.isoformat(), "time": at, "location": location, "trainer": app["trainer_id"], "by": "trainer"})
    return get(conn, app_id), True, old


def _apply_schedule(conn, app: dict, day: str | None, at: str | None, location: str | None, trainer_id: int,
                    actor_tg: int | None) -> None:
    trainer_changed = trainer_id != app["trainer_id"]
    _bump(conn, app["id"], status="scheduled", training_date=day, training_time=at, training_location=location,
          trainer_id=trainer_id, training_set_by=actor_tg, training_set_at=now_iso(), training_confirmed_at=None,
          trainer_confirmed_at=None, reminded_eve=None, reminded_morning=None,
          **({"trainer_assigned_at": now_iso()} if trainer_changed else {}))
    if trainer_changed:
        _grant(conn, trainer_id, actor_tg, True)


def confirm_by_trainer(conn: sqlite3.Connection, user: dict, app_id: int, version: int | None = None) -> tuple[dict, bool]:
    """The trainer confirms the agreed date, time and place (В9). An old button for an older version — refused."""
    with transaction(conn):
        app = get(conn, app_id)
        if app is None or app["trainer_id"] != user["id"] or app["status"] != "scheduled" or not app["training_date"]:
            raise ServiceError("Обучение не найдено или изменилось", 404, "not_found")
        if version is not None and version != app["version"]:
            raise ServiceError("Параметры обучения уже изменились — подтвердите новые в приложении", 409, "stale")
        if app["trainer_confirmed_at"]:
            return app, False
        conn.execute("UPDATE applications SET trainer_confirmed_at = ? WHERE id = ?", (now_iso(), app_id))
        log_event(conn, user_id=app["user_id"], actor_id=user["telegram_id"], event_type="TRAINING_TRAINER_CONFIRMED",
                  key=svc.new_key(), new={"application": app_id})
    return get(conn, app_id), True


def agreed(app: dict) -> bool:
    return bool(app["status"] == "scheduled" and app["training_date"] and app["training_confirmed_at"]
                and app["trainer_confirmed_at"])


def cancel(conn: sqlite3.Connection, actor: dict, app_id: int, reason: str | None, key: str, kind: str = "cancel",
           initiator: dict | None = None, role: str = "admin") -> tuple[dict, bool]:
    """«Отменить обучение» (or a no-show of the candidate) by an owner or an admin — directly, or a trainer's request
    approved (initiator, role). A reason is required. ТЗ 30.10 §5: the attempt ends (see _cancel)."""
    if kind not in ("cancel", "no_show"):
        raise ServiceError("Неизвестный вид отмены", 422, "bad_kind")
    reason = templates.require_reason(reason, "Укажите причину отмены")
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key):
            return app, False
        before = _cancel(conn, app, actor["telegram_id"],
                         (initiator or {}).get("id") if role != "admin" else actor["telegram_id"], role, reason, kind, key)
    return _cancelled_view(conn, app_id, before, reason, kind), True


def cancel_by_trainer(conn: sqlite3.Connection, user: dict, app_id: int, reason: str | None, key: str) -> tuple[dict, bool]:
    """ТЗ 30.10 §5: the candidate's own trainer cancels the training — a reason and a confirmation in the app, no
    admin's approval. Only their own candidate, checked here (not only by the button)."""
    reason = templates.require_reason(reason, "Укажите причину отмены")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, app_id), False
        user = svc.get_user(conn, user["id"])
        app = get(conn, app_id)
        if app is None or app["trainer_id"] != user["id"] or not has_access(conn, user):
            raise ServiceError("Это не ваш кандидат", 403, "forbidden")
        before = _cancel(conn, app, user["telegram_id"], user["id"], "trainer", reason, "cancel", key)
    return _cancelled_view(conn, app_id, before, reason, "cancel"), True


def _cancel(conn, app: dict, actor_tg: int, initiator_id: int | None, role: str, reason: str, kind: str, key: str) -> dict:
    """Inside the caller's transaction. ТЗ 30.10 §5: the current attempt gets «Отменено» — kept with its trainer,
    date, time, place and confirmations (the row is also the counter, it never resets); the candidate leaves the
    trainer's «Обучение» and goes back to «Ожидают рассмотрения»; their pending requests are closed. A no-show is a
    cancellation too."""
    if app["status"] != "scheduled":
        raise ServiceError("Отменить можно только назначенное обучение — обновите экран", 409, "bad_status")
    if kind == "no_show" and not app["training_date"]:
        raise ServiceError("Дата обучения ещё не назначена", 409, "no_date")
    conn.execute("""INSERT INTO training_cancellations (application_id, kind, at, initiator_id, initiator_role, reason,
                    trainer_id, training_date, training_time, training_location, candidate_confirmed_at,
                    trainer_confirmed_at, attempt_started_at, training_started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (app["id"], kind, now_iso(), initiator_id, role, reason, app["trainer_id"], app["training_date"],
                  app["training_time"], app["training_location"], app["training_confirmed_at"],
                  app["trainer_confirmed_at"], app["trainer_assigned_at"], app["training_started_at"]))
    _bump(conn, app["id"], status="new", trainer_id=None, trainer_assigned_at=None, training_date=None, training_time=None,
          training_location=None, training_set_by=None, training_set_at=None, training_confirmed_at=None,
          trainer_confirmed_at=None, training_started_at=None, contact_timer_at=None, contact_answer=None,
          contact_answer_at=None, reminded_eve=None, reminded_morning=None)
    conn.execute("""UPDATE training_requests SET status = 'stale', decided_at = ? WHERE application_id = ?
                    AND status = 'pending'""", (now_iso(), app["id"]))
    log_event(conn, user_id=app["user_id"], actor_id=actor_tg, event_type="TRAINING_CANCELLED", key=key,
              old={k: app[k] for k in ("training_date", "training_time", "training_location", "trainer_id")},
              new={"application": app["id"], "kind": kind, "by": role, "status": "new"}, description=reason)
    return app


def _cancelled_view(conn, app_id: int, before: dict, reason: str, kind: str) -> dict:
    return {**get(conn, app_id), "cancelled_trainer_id": before["trainer_id"], "cancelled_date": before["training_date"],
            "cancelled_time": before["training_time"], "cancel_reason": reason, "cancel_kind": kind}


def ack_cancellations(conn: sqlite3.Connection, actor: dict, app_id: int, key: str) -> dict:
    """§5: at 3/3 the admin decides «Продолжить обучение» — the warning goes until the next cancellation."""
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key):
            return app
        n = conn.execute("SELECT COUNT(*) FROM training_cancellations WHERE application_id = ?", (app_id,)).fetchone()[0]
        conn.execute("UPDATE applications SET cancel_ack = ? WHERE id = ?", (n, app_id))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="TRAINING_CONTINUED", key=key,
                  new={"cancellations": n})
    return get(conn, app_id)


def start(conn: sqlite3.Connection, actor: dict, app_id: int, key: str) -> tuple[dict, bool]:
    """«Обучение начато» (an admin, or a trainer's request approved)."""
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key) or app["training_started_at"]:
            return app, False
        if app["status"] != "scheduled" or not app["trainer_id"]:
            raise ServiceError("Сначала назначьте тренера", 409, "bad_status")
        _bump(conn, app_id, training_started_at=now_iso())
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="TRAINING_STARTED", key=key)
    return get(conn, app_id), True


def reject(conn: sqlite3.Connection, actor: dict, app_id: int, reason: str | None, key: str) -> tuple[dict, bool]:
    """§6: a refusal — «Отказано», the account is blocked through the usual block (account_state), out of
    «Предзакреп» (the caller removes them from the group). «Вернуть в резерв» restores everything."""
    reason = templates.require_reason(reason, "Укажите причину отказа")
    _check_key(key)
    with transaction(conn):
        actor, app = _load(conn, actor, app_id)
        if _seen(conn, key) or app["status"] == "rejected":
            return app, False
        if app["status"] not in ("new", "scheduled", "internship"):
            raise ServiceError("Отказать можно кандидату до принятия в команду", 409, "bad_status")
        conn.execute("""UPDATE applications SET status = 'rejected', reject_reason = ?, decided_by = ?, decided_at = ?,
                        version = version + 1 WHERE id = ?""", (reason, actor["telegram_id"], now_iso(), app_id))
        conn.execute("""UPDATE users SET status = 'rejected', decided_at = ?, decided_by = ?, account_state = 'blocked',
                        state_reason = ?, state_changed_at = ?, state_changed_by = ?, work_status = 'not_working',
                        notify_reminders = 0 WHERE id = ?""",
                     (now_iso(), actor["telegram_id"], reason, now_iso(), actor["telegram_id"], app["user_id"]))
        conn.execute("""UPDATE training_requests SET status = 'stale', decided_at = ? WHERE application_id = ?
                        AND status = 'pending'""", (now_iso(), app_id))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], event_type="APPLICATION_REJECTED", key=key,
                  new={"application": app_id, "blocked": True}, description=reason)
    return get(conn, app_id), True


def return_to_reserve(conn: sqlite3.Connection, actor: dict, app_id: int, key: str) -> tuple[dict, bool]:
    """«Вернуть в резерв» — a refusal by mistake is undone: the block is lifted, the questionnaire can be filled again."""
    app, changed = recruiting.return_to_reserve(conn, actor, app_id, key)
    if changed:
        with transaction(conn):
            conn.execute("""UPDATE users SET account_state = 'active', state_reason = NULL, state_changed_at = ?
                            WHERE id = ? AND account_state = 'blocked'""", (now_iso(), app["user_id"]))
    return app, changed


def finish(conn: sqlite3.Connection, user: dict, app_id: int) -> tuple[dict, bool]:
    """§3: the trainer says the internship is over — «Ожидает проверки администратора». Not an acceptance."""
    with transaction(conn):
        app = get(conn, app_id)
        if app is None or app["trainer_id"] != user["id"]:
            raise ServiceError("Кандидат не найден", 404, "not_found")
        if app["status"] != "internship":
            raise ServiceError("Завершить можно стажировку", 409, "bad_status")
        if app["finish_requested_at"]:
            return app, False
        conn.execute("UPDATE applications SET finish_requested_at = ? WHERE id = ?", (now_iso(), app_id))
        log_event(conn, user_id=app["user_id"], actor_id=user["telegram_id"], event_type="INTERNSHIP_FINISH_REQUESTED",
                  key=svc.new_key())
    return get(conn, app_id), True


def mark(conn: sqlite3.Connection, app_id: int, column: str) -> None:
    if column not in ("pre_invited_at", "team_invited_at"):
        raise ValueError(column)
    with transaction(conn):
        conn.execute(f"UPDATE applications SET {column} = ? WHERE id = ?", (now_iso(), app_id))


# ---------------------------------------------------------------- requests (trainer / candidate → admin)


def _payload(conn, app: dict, data: dict) -> dict:
    out = {}
    if data.get("date"):
        day = svc.parse_date(data["date"])
        if day < now_local().date():
            raise ServiceError("Дата уже прошла", 422, "past_date")
        out["date"] = day.isoformat()
    if data.get("time"):
        try:
            out["time"] = time.fromisoformat(data["time"]).strftime("%H:%M")
        except ValueError as exc:
            raise ServiceError("Время — в формате ЧЧ:ММ", 422, "bad_time") from exc
    if data.get("location"):
        out["location"] = recruiting._text(data["location"], "Место")
    if data.get("trainer_id") and data["trainer_id"] != app["trainer_id"]:
        _trainer_ok(conn, data["trainer_id"], app)
        out["trainer_id"] = data["trainer_id"]
    if data.get("note"):
        out["note"] = " ".join(str(data["note"]).split())[:300]
    return out


def create_request(conn: sqlite3.Connection, user: dict, app_id: int, kind: str, reason: str | None, data: dict | None,
                   key: str) -> tuple[dict, bool]:
    """A trainer (or, for a change, the candidate) asks; an admin decides. A repeated tap returns the same request."""
    if kind not in KINDS:
        raise ServiceError("Неизвестный запрос", 422, "bad_kind")
    if kind in NEEDS_REASON:
        reason = templates.require_reason(reason, "Укажите причину")
    reason = (reason or "").strip()[:500] or None
    _check_key(key)
    with transaction(conn):
        app = get(conn, app_id)
        if app is None:
            raise ServiceError("Кандидат не найден", 404, "not_found")
        if _seen(conn, key):
            row = conn.execute("SELECT * FROM training_requests WHERE application_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
                               (app_id, kind)).fetchone()
            return request_view(conn, dict(row)), False
        user = svc.get_user(conn, user["id"])
        if app["trainer_id"] == user["id"] and has_access(conn, user):
            role = "trainer"
        elif app["user_id"] == user["id"] and kind == "change":
            role = "candidate"
        else:
            raise ServiceError("Это не ваш кандидат", 403, "forbidden")
        allowed = {"start": ("scheduled",), "refuse": ("scheduled", "internship"), "cancel": ("scheduled",),
                   "change": ("scheduled",), "internship": ("scheduled",)}[kind]
        if app["status"] not in allowed:
            raise ServiceError("Сейчас этот запрос недоступен — обновите экран", 409, "bad_status")
        if kind == "start" and app["training_started_at"]:
            raise ServiceError("Обучение уже начато", 409, "already")
        if kind == "cancel" and not app["training_date"]:
            raise ServiceError("Дата обучения ещё не назначена", 409, "no_date")
        existing = conn.execute("SELECT * FROM training_requests WHERE application_id = ? AND kind = ? AND status = 'pending'",
                                (app_id, kind)).fetchone()
        if existing:
            return request_view(conn, dict(existing)), False
        payload = _payload(conn, app, data or {}) if kind == "change" else {}
        if kind == "change" and role == "trainer" and not any(k in payload for k in ("date", "time", "location", "trainer_id")):
            raise ServiceError("Укажите, что изменить: дату, время, место или тренера", 422, "nothing_to_change")
        old = {k: app[k] for k in ("training_date", "training_time", "training_location", "trainer_id")}
        cur = conn.execute("""INSERT INTO training_requests (application_id, kind, initiator_id, initiator_role, reason,
                              payload, old, app_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (app_id, kind, user["id"], role, reason, json.dumps(payload, ensure_ascii=False),
                            json.dumps(old), app["version"], now_iso()))
        log_event(conn, user_id=app["user_id"], actor_id=user["telegram_id"], event_type="TRAINING_REQUESTED", key=key,
                  new={"request": cur.lastrowid, "kind": kind, **payload}, description=reason)
    return request_view(conn, dict(conn.execute("SELECT * FROM training_requests WHERE id = ?", (cur.lastrowid,)).fetchone())), True


def decide(conn: sqlite3.Connection, actor: dict, request_id: int, approve: bool, reason: str | None, key: str,
           payload: dict | None = None) -> tuple[dict, bool, dict | None]:
    """An admin approves or rejects (a reason is required). Until then nothing about the training changes.
    A change can be completed by the admin (payload) — e.g. the candidate only wrote a wish.
    Returns (request, changed, effect) — effect is what the caller announces."""
    if not approve:
        reason = templates.require_reason(reason, "Укажите причину отклонения")
    with transaction(conn):
        row = conn.execute("SELECT * FROM training_requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            raise ServiceError("Запрос не найден", 404, "not_found")
        r = dict(row)
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "registrations"):
            raise ServiceError("Решают владелец и администраторы", 403, "forbidden")
        if r["status"] != "pending":
            return request_view(conn, r), False, None
        app = get(conn, r["application_id"])
        if approve and r["kind"] == "change" and r["app_version"] != app["version"]:
            conn.execute("UPDATE training_requests SET status = 'stale', decided_at = ? WHERE id = ?", (now_iso(), request_id))
            raise ServiceError("Обучение уже изменили после этого запроса — запрос устарел", 409, "stale")
        conn.execute("""UPDATE training_requests SET status = ?, decided_by = ?, decided_at = ?, decision_reason = ?
                        WHERE id = ? AND status = 'pending'""",
                     ("approved" if approve else "rejected", actor["telegram_id"], now_iso(), reason, request_id))
        log_event(conn, user_id=app["user_id"], actor_id=actor["telegram_id"], key=svc.new_key(),
                  event_type="TRAINING_REQUEST_APPROVED" if approve else "TRAINING_REQUEST_REJECTED",
                  new={"request": request_id, "kind": r["kind"]}, description=reason)
    effect = None
    if approve:
        try:
            effect = _apply_request(conn, actor, r, app, payload)
        except ServiceError:
            with transaction(conn):  # nothing happened: the request waits for a decision again
                conn.execute("""UPDATE training_requests SET status = 'pending', decided_by = NULL, decided_at = NULL,
                                decision_reason = NULL WHERE id = ? AND status = 'approved'""", (request_id,))
            raise
    final = dict(conn.execute("SELECT * FROM training_requests WHERE id = ?", (request_id,)).fetchone())
    return request_view(conn, final), True, effect


def _apply_request(conn, actor: dict, r: dict, app: dict, payload: dict | None) -> dict:
    initiator = svc.get_user(conn, r["initiator_id"]) if r["initiator_id"] else None
    k = svc.new_key()
    if r["kind"] == "start":
        return {"kind": "start", "app": start(conn, actor, app["id"], k)[0]}
    if r["kind"] == "refuse":
        return {"kind": "refuse", "app": reject(conn, actor, app["id"], r["reason"], k)[0]}
    if r["kind"] == "cancel":
        return {"kind": "cancel", "app": cancel(conn, actor, app["id"], r["reason"], k, "cancel", initiator, r["initiator_role"])[0]}
    if r["kind"] == "internship":
        return {"kind": "internship", "app": recruiting.start_internship(conn, actor, app["id"], k)[0]}
    data = {**json.loads(r["payload"] or "{}"), **_payload(conn, app, payload or {})}
    day = data.get("date") or app["training_date"]
    at = data.get("time") or app["training_time"]
    place = data.get("location") or app["training_location"]
    trainer_id = data.get("trainer_id") or app["trainer_id"]
    if not (day and at and place):
        raise ServiceError("Укажите новую дату, время и место", 422, "schedule_required")
    with transaction(conn):
        _trainer_ok(conn, trainer_id, app)
        _apply_schedule(conn, app, day, at, place, trainer_id, actor["telegram_id"])
        _stale_changes(conn, app["id"], keep=r["id"])
    return {"kind": "change", "app": get(conn, app["id"]),
            "previous_trainer": app["trainer_id"] if trainer_id != app["trainer_id"] else None}


def candidate_answer(conn: sqlite3.Connection, telegram_id: int, app_id: int, answer: str) -> tuple[dict, bool]:
    """The candidate's buttons under «свяжитесь с тренером»: «Я связался» / «Не могу написать первым»."""
    if answer not in ("wrote", "cant_write"):
        raise ServiceError("Неизвестный ответ", 422, "bad_answer")
    with transaction(conn):
        app = get(conn, app_id)
        user = svc.get_user_by_tg(conn, telegram_id)
        if app is None or user is None or app["user_id"] != user["id"] or not app["trainer_id"] or app["status"] != "scheduled":
            raise ServiceError("Сообщение устарело", 404, "not_found")
        if app["contact_answer"] == answer:
            return app, False
        conn.execute("UPDATE applications SET contact_answer = ?, contact_answer_at = ? WHERE id = ?", (answer, now_iso(), app_id))
        log_event(conn, user_id=user["id"], actor_id=telegram_id, event_type="CANDIDATE_CONTACT_ANSWER", key=svc.new_key(),
                  new={"answer": answer})
    return get(conn, app_id), True
