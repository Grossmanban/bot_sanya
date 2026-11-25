"""«Надбавка за точку» + «Распределение по точкам» (19.11).

Надбавка: the point's steps «от N оплаченных штук → R ₽ за день» acting on chosen weekdays (points.bonus,
migration 53). The thresholds count the paid pieces of the day — approved, non-training fact, the same base the
lazy tax counts. The reached step pays its sum in full, never stacked with the lower ones. When a report is
approved the day's step turns into a bonus transaction: one live bonus per shift (the partial unique index on
shift_id + source), annulled like any bonus. It is a bonus, NOT a rate — rates, ladders and the payout math
are untouched. When a manager corrects an approved fact by hand, the bonus follows the corrected figure.

Распределение: a manager puts an employee on a point for a day; no shift — one is created with the point alone
(«время уточняется»). Who may put whom (may_assign): owners and admins — every employee; the personal grant
«Распределение по всем точкам» — every employee, whatever the holder's own role and network; a trainer — the
employees of their network; an instructor — the people assigned to them. Managers are nobody's targets.
The schedule notice goes through the bot per person, from a template of the «Распределение» kind — by button only,
nothing is sent when a point is assigned.
"""

import json
import re
from datetime import date
from html import escape

from . import finance, permissions as perms, services as svc
from .db import transaction
from .services import ACTIVE, ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, today_local

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
SOURCE = "location_bonus"  # financial_transactions.source of the allowance
CORRECTED = "Факт исправлен — надбавка пересчитана"
# broadcasts.audience of a sent schedule notice: «assign:<date>:<all|point>»
ASSIGN_AUDIENCE = "assign"
ASSIGN_VARIABLES = ("имя", "дата", "точка", "коллеги", "надбавка", "отправитель")
MAX_MESSAGE = 4000  # Telegram's limit, as everywhere in the app


# ---------------------------------------------------------------- the allowance (auto-bonus)


def _point(conn, location: str | None) -> dict | None:
    if not location:
        return None
    return next((p for p in svc.list_points(conn) if p["name"].casefold() == location.casefold()), None)


def _refused_by_hand(conn, shift_id: int) -> bool:
    """The allowance of this shift was annulled by a person, not by a correction of the fact (those carry
    CORRECTED as their comment) — it stays annulled whatever happens to the fact later."""
    last = conn.execute(
        "SELECT status, decision_comment FROM financial_transactions WHERE shift_id = ? AND source = ? "
        "ORDER BY id DESC LIMIT 1", (shift_id, SOURCE)).fetchone()
    return last is not None and last["status"] == "cancelled" and last["decision_comment"] != CORRECTED


def award(conn, shift: dict, actor_tg: int, key: str, corrected: bool = False) -> dict | None:
    """The day's step → a bonus transaction. Runs inside the caller's transaction, on approval only (approved,
    non-training fact). One live bonus per shift: a repeated button or a re-approval never pays twice.
    `corrected` — the manager has just entered the fact by hand over an approved one: the bonus follows the new
    figure — re-made by the new step, annulled when the step is no longer reached, made when there was none to
    reach before. One thing never happens: a bonus a person annulled by hand doesn't come back.
    Returns the transaction made or None (no point, no steps, the weekday doesn't count, the step isn't reached)."""
    if shift.get("training"):  # a trainee's day is not paid — and earns no allowance
        return None
    point = _point(conn, shift.get("location"))
    bonus = svc.parse_point_bonus(point["bonus"]) if point else {"tiers": [], "days": []}
    tier = svc.bonus_tier(bonus, shift.get("fact_volume") or 0, date.fromisoformat(shift["shift_date"]).weekday())
    live = finance.linked(conn, shift["id"], (SOURCE,))
    if live:
        if not corrected or (tier is not None and tier["amount"] == live[0]["amount"]):
            return None  # already paid for this shift
        conn.execute(
            "UPDATE financial_transactions SET status = 'cancelled', decided_by = ?, decided_at = ?, decision_comment = ? "
            "WHERE id = ?", (actor_tg, now_iso(), CORRECTED, live[0]["id"]))
    elif corrected and _refused_by_hand(conn, shift["id"]):
        return None
    if tier is None:
        return None
    tx = finance.record(conn, user_id=shift["user_id"], type="bonus", amount=tier["amount"],
                        reason=f"Надбавка за точку «{point['name']}»", actor_tg=actor_tg, source=SOURCE,
                        shift_id=shift["id"], event_date=shift["shift_date"])
    log_event(conn, user_id=shift["user_id"], actor_id=actor_tg, event_type="LOCATION_BONUS", key=f"{key}:loc",
              shift_id=shift["id"],
              new={"tx": tx["id"], "amount": tier["amount"], "from": tier["from"], "point": point["name"]})
    return tx


# ---------------------------------------------------------------- who may distribute whom


def may_assign(conn, actor: dict | None, target: dict | None) -> bool:
    """Право распределения, на каждого человека: owners and admins — every employee; the personal grant — every
    employee from anywhere; a trainer — the employees of their network, an instructor — their own people.
    Blocked people and other managers are nobody's targets."""
    if not actor or not target or not perms.is_staff(actor):
        return False
    if target["status"] != "approved" or target["account_state"] == "blocked":
        return False
    if perms.role_of(target) != perms.EMPLOYEE:
        return False
    if perms.has_grant(actor, "assign_points"):
        return True
    role = perms.role_of(actor)
    if role in (perms.OWNER, perms.ADMIN):
        return True
    if role == perms.TRAINER:
        led = actor.get("led_network_id") or actor.get("network_id")
        return led is not None and target.get("network_id") == led
    if role == perms.INSTRUCTOR:
        return target.get("instructor_id") == actor["id"]
    return False


def _pool(conn, actor: dict) -> list[dict]:
    return sorted((u for u in svc.list_users(conn) if may_assign(conn, actor, u)), key=svc.full_name)


# ---------------------------------------------------------------- the day and the assignment


def _item(u: dict, shift: dict | None) -> dict:
    """A row of the day: the person and what they have on that date. `assignable` is false where a record of
    another kind is in the way (a day off, a cancelled shift) — those are changed in «День»."""
    status = shift["status"] if shift else None
    return {
        "user_id": u["id"], "name": svc.full_name(u), "username": u["username"],
        "shift_id": shift["id"] if shift else None,
        "status": status,
        "start_time": shift["start_time"] if shift else None,
        "end_time": shift["end_time"] if shift else None,
        "volume": shift["volume"] if shift else None,
        "location": shift["location"] if shift else None,
        "trainee": bool(shift.get("training")) if shift else False,
        "assignable": shift is None or shift["status"] in (*ACTIVE, "awaiting_approval"),
    }


def day(conn, actor: dict, day: date) -> dict:
    """Дата → люди (только те, кого этот руководитель вправе распределить) с их записями на дату + точки с надбавками."""
    actor = _fresh_actor(conn, actor)
    if not perms.can(actor, "assign_points"):
        raise ServiceError("Распределение — для руководителей или с личным разрешением", 403, "forbidden")
    items = [_item(u, svc.get_user_shift_on(conn, u["id"], day)) for u in _pool(conn, actor)]
    bonuses = {p["name"].casefold(): svc.parse_point_bonus(p["bonus"]) for p in svc.list_points(conn)}
    return {
        "date": day.isoformat(), "weekday": day.weekday(),
        "points": [{**svc.public_point(p),
                    # 19.11: whether THIS weekday counts for the point's allowance, and the load of the day
                    "bonus_today": (lambda b: {**b, "weekday_ok": day.weekday() in b["days"]})(bonuses[p["name"].casefold()])
                    if bonuses[p["name"].casefold()]["tiers"] else None,
                    "taken": sum(1 for i in items if (i["location"] or "").casefold() == p["name"].casefold())}
                   for p in svc.list_points(conn)],
        "items": items,
    }


def assign(conn, actor: dict, user_id: int, day: date, location: str | None, key: str) -> dict:
    """One person on one day. No record — a shift is created with the point alone (no time, no volume yet:
    «время уточняется», the person or a manager fills them in as usual); a shift without a point — the point
    is set; another point — replaced (the client asks first). The points' capacity is checked as always."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            shift = svc.get_user_shift_on(conn, user_id, day)
            return {"shift_id": shift["id"] if shift else None, "location": shift["location"] if shift else None,
                    "changed": False}
        actor = _fresh_actor(conn, actor)
        user = svc.get_user(conn, user_id)
        if user is None:
            raise ServiceError("Сотрудник не найден", 404, "not_found")
        if not may_assign(conn, actor, user):
            raise ServiceError("Нет прав распределять этого сотрудника", 403, "forbidden")
        if day < today_local():
            raise ServiceError("Прошедший день распределить нельзя — правка смены в «Дне»", 409, "past_date")
        shift = svc.get_user_shift_on(conn, user_id, day)
        if shift is not None and shift["status"] == "pre_declared_off":
            raise ServiceError("Этот день сотрудник взял выходным — сначала снимите выходной в «Дне»", 409, "day_off")
        if shift is not None and shift["status"] in ("cancelled", "finally_cancelled"):
            raise ServiceError("Смена на этот день отменена — сначала восстановите её в «Дне»", 409, "cancelled")
        location = svc.check_point(conn, day, location, exclude=shift["id"] if shift else None) if location else None
        if shift is None:
            from . import recruiting  # local: recruiting builds on services and finance

            stamp = now_iso()
            cur = conn.execute(
                """INSERT INTO shifts (user_id, shift_date, status, location, created_at, updated_at, training)
                   VALUES (?, ?, 'active', ?, ?, ?, ?)""",
                (user_id, day.isoformat(), location, stamp, stamp, int(recruiting.shift_is_training(conn, user))))
            shift_id, event = cur.lastrowid, "ASSIGN_SHIFT_CREATED"
            old = None
        elif (shift["location"] or "") == (location or ""):
            return {"shift_id": shift["id"], "location": shift["location"], "changed": False}
        else:
            conn.execute("UPDATE shifts SET location = ?, updated_at = ? WHERE id = ?", (location, now_iso(), shift["id"]))
            shift_id, event, old = shift["id"], "ASSIGN_POINT_SET", {"location": shift["location"]}
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type=event, key=key, shift_id=shift_id,
                  old=old, new={"date": day.isoformat(), "location": location})
    return {"shift_id": shift_id, "location": location, "changed": True}


# ---------------------------------------------------------------- the schedule notice


def _line(s: dict) -> str:
    name = svc.full_name(s)
    if s.get("username"):
        name += f" (@{s['username']})"
    if s.get("training"):
        name += " — стажёр"
    parts = [svc.shift_when_text(s), name]
    if s["volume"]:
        parts.append(f"{s['volume']} шт.")
    parts.append(s["location"] or "точка не назначена")
    return " — ".join(parts)


def _allowance_text(bonus: dict | None, weekday: int) -> str:
    """The tiers and their weekdays for the notice ({надбавка}); empty when the point has no allowance or it
    doesn't act on this weekday."""
    if not bonus or not bonus["tiers"] or weekday not in bonus["days"]:
        return ""
    steps = ", ".join(f"от {t['from']} оплач. → {t['amount']} ₽" for t in bonus["tiers"])
    days = ", ".join(WEEKDAYS[d] for d in bonus["days"])
    return f"💰 Надбавка на точке: {steps} ({days})."


def render(conn, actor: dict, day: date, html: str, mode: str) -> list[dict]:
    """The per-person texts of the notice: {имя}, {дата}, {точка}, {коллеги}, {надбавка}, {отправитель}.
    mode «all» — everyone's distribution; «point» — the person's point and the colleagues on the same point.
    Only people the sender may distribute who have a shift that day. The template is already sanitized;
    every value is escaped before it goes in."""
    if mode not in ("all", "point"):
        raise ServiceError("Режим: весь список или только своя точка", 422, "bad_mode")
    people = {u["id"] for u in _pool(conn, actor)}
    shifts = [s for s in svc.active_shifts_on(conn, day) if s["user_id"] in people]
    if not shifts:
        raise ServiceError("На эту дату из ваших людей никто не выходит", 422, "nobody")
    bonuses = {p["name"].casefold(): svc.parse_point_bonus(p["bonus"]) for p in svc.list_points(conn)}
    day_text = f"{day.strftime('%d.%m')} ({WEEKDAYS[day.weekday()]})"
    sender = escape(svc.full_name(actor))
    out = []
    for s in shifts:
        if mode == "all":
            colleagues = [x for x in shifts if x["id"] != s["id"]]
        else:
            colleagues = [x for x in shifts if x["id"] != s["id"]
                          and (x["location"] or "").casefold() == (s["location"] or "").casefold()]
        text = (html.replace("{имя}", escape(svc.given_name(s)))
                    .replace("{дата}", day_text)
                    .replace("{точка}", escape(s["location"] or "точка не назначена"))
                    .replace("{коллеги}", escape("\n".join(_line(x) for x in colleagues) or "—"))
                    .replace("{надбавка}", escape(_allowance_text(bonuses.get((s["location"] or "").casefold()),
                                                                day.weekday())))
                    .replace("{отправитель}", sender))
        # the substitutes may make the text longer than the template: a message over Telegram's limit would
        # silently not be delivered — refuse the whole sending with the person to look at instead
        if len(re.sub(r"<[^>]+>", "", text)) > MAX_MESSAGE:
            raise ServiceError(f"Сообщение для {svc.full_name(s)} после подстановок длиннее {MAX_MESSAGE} символов — "
                               "сократите текст или отправьте «только своя точка»", 422, "too_long")
        out.append({"user_id": s["user_id"], "telegram_id": s["telegram_id"], "html": text})
    return out


def send(conn, actor: dict, day: date, html: str, mode: str, signature: str | None, key: str
         ) -> tuple[dict, list[dict], dict[int, str], bool]:
    """«Отправить график»: one row in the broadcast registry (receipts, «доставлено N из M», «Отправленные»)
    and a personal text per person — the route hands them to the bot. A repeated request returns the row
    already made and sends nothing (`repeated`)."""
    _check_key(key)
    with transaction(conn):
        done = conn.execute("SELECT new_value FROM event_logs WHERE idempotency_key = ?", (key,)).fetchone()
        if done is not None:
            made = json.loads(done[0] or "{}").get("broadcast")
            row = conn.execute("SELECT * FROM broadcasts WHERE id = ?", (made,)).fetchone() if made else None
            if row is None:
                raise ServiceError("Отправка уже выполняется — обновите список", 409, "in_progress")
            return dict(row), [], {}, True
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "assign_points"):
            raise ServiceError("Распределение — для руководителей или с личным разрешением", 403, "forbidden")
        clean, plain = svc.prepare_message(html)
        if not plain.strip():
            raise ServiceError("Напишите текст сообщения", 422, "empty")
        messages = render(conn, actor, day, clean, mode)
        recipients = [svc.get_user(conn, m["user_id"]) for m in messages]
        cur = conn.execute(
            """INSERT INTO broadcasts (author_id, audience, text, html, signed, recipients, created_at, people,
                                       media_kind, allow_reply, signature)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'photo', 0, ?)""",
            (actor["telegram_id"], f"{ASSIGN_AUDIENCE}:{day.isoformat()}:{mode}", plain, clean,
             int(signature is not None), len(messages), now_iso(),
             json.dumps([m["user_id"] for m in messages]), signature))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="ASSIGN_NOTICE_SENT", key=key,
                  new={"broadcast": cur.lastrowid, "date": day.isoformat(), "mode": mode, "people": len(messages)})
    row = dict(conn.execute("SELECT * FROM broadcasts WHERE id = ?", (cur.lastrowid,)).fetchone())
    return row, recipients, {m["user_id"]: m["html"] for m in messages}, False
