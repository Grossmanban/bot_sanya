"""Live events with a leaderboard (ТЗ модуль 7).

    first_to_reach — a race to `target`: a participant sends their number + screenshot; staff confirm
                     winners in order (👑) until `max_winners`, then the event is finished.
    accumulative   — whoever makes the most: every confirmed entry adds up; the board ranks by
                     confirmed + pending (soft score) for drama, prizes go by confirmed score;
                     ties are broken by who reached the score first. After the timer there are
                     `buffer_min` minutes to review the last entries, then the top `max_winners` win.

An entry is pending_review → confirmed | rejected (reason required). A participant has one entry
under review at a time and is out of the event after `max_attempts` rejections.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import networks
from . import permissions as perms
from . import services as svc
from .config import settings
from .db import transaction
from .media import photo_url
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, now_local

MAX_TITLE = 80


@dataclass
class Review:
    entry: dict
    event: dict
    changed: bool
    finished: bool = False


def _dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=settings.tz)


def get(conn: sqlite3.Connection, event_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


def get_entry(conn: sqlite3.Connection, entry_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM event_entries WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def eligible(event: dict, user: dict) -> bool:
    return user["status"] == "approved" and user["account_state"] == "active" and (
        event["network_id"] is None or user.get("network_id") == event["network_id"])


def entries(conn: sqlite3.Connection, event_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM event_entries WHERE event_id = ? ORDER BY id", (event_id,))]


def leaderboard(conn: sqlite3.Connection, event: dict) -> list[dict]:
    """Rows: confirmed, pending (soft), winner flag; sorted for display."""
    rows: dict[int, dict] = {}
    for e in entries(conn, event["id"]):
        r = rows.setdefault(e["user_id"], {"user_id": e["user_id"], "confirmed": 0, "pending": 0, "rejected": 0,
                                           "reached_at": None, "winner_at": None})
        if e["status"] == "confirmed":
            if event["mode"] == "accumulative":
                r["confirmed"] += e["value"]
                r["reached_at"] = e["reviewed_at"]
            else:
                r["confirmed"] = max(r["confirmed"], e["value"])
                r["winner_at"] = r["winner_at"] or e["reviewed_at"]
        elif e["status"] == "pending_review":
            r["pending"] += e["value"] if event["mode"] == "accumulative" else 0
            if event["mode"] == "first_to_reach":
                r["pending"] = max(r["pending"], e["value"])
        else:
            r["rejected"] += 1
    out = []
    for r in rows.values():
        user = svc.get_user(conn, r["user_id"])
        score = r["confirmed"] + r["pending"] if event["mode"] == "accumulative" else max(r["confirmed"], r["pending"])
        out.append({**r, "name": svc.full_name(user), "score": score, "out": r["rejected"] >= event["max_attempts"]})
    if event["mode"] == "first_to_reach":
        out.sort(key=lambda r: (r["winner_at"] is None, r["winner_at"] or "", -r["score"], r["name"]))
    else:
        out.sort(key=lambda r: (-r["score"], r["reached_at"] or "9", r["name"]))
    winners = winner_ids(event, out)
    for i, r in enumerate(out, start=1):
        r["place"] = i
        r["winner"] = r["user_id"] in winners
    return out


def winner_ids(event: dict, board: list[dict]) -> set[int]:
    if event["mode"] == "first_to_reach":
        return {r["user_id"] for r in board if r["winner_at"]}
    if event["status"] != "finished":
        return set()
    ranked = sorted((r for r in board if r["confirmed"] > 0), key=lambda r: (-r["confirmed"], r["reached_at"] or "9"))
    return {r["user_id"] for r in ranked[: event["max_winners"]]}


def _phase(event: dict, now: datetime) -> str:
    if event["status"] != "active":
        return event["status"]
    if now < _dt(event["starts_at"]):
        return "upcoming"
    if now < _dt(event["ends_at"]):
        return "live"
    return "review"  # the buffer after the timer


def view(conn: sqlite3.Connection, event: dict, viewer: dict | None = None, now: datetime | None = None) -> dict:
    now = now or now_local()
    board = leaderboard(conn, event)
    mine = [e for e in entries(conn, event["id"]) if viewer and e["user_id"] == viewer["id"]]
    rejected = sum(e["status"] == "rejected" for e in mine)
    pending = any(e["status"] == "pending_review" for e in mine)
    me = next((r for r in board if viewer and r["user_id"] == viewer["id"]), None)
    won = bool(me and me["winner"])
    phase = _phase(event, now)
    can_submit = (viewer is not None and eligible(event, viewer) and phase == "live" and not pending
                  and rejected < event["max_attempts"] and not (event["mode"] == "first_to_reach" and won))
    return {
        "id": event["id"], "title": event["title"], "description": event["description"], "prize": event["prize"],
        "mode": event["mode"], "target": event["target"], "max_winners": event["max_winners"],
        "max_attempts": event["max_attempts"], "starts_at": event["starts_at"], "ends_at": event["ends_at"],
        "buffer_min": event["buffer_min"], "status": event["status"], "phase": phase,
        "network": (networks.get(conn, event["network_id"]) or {}).get("name") if event["network_id"] else None,
        "leaderboard": [{k: r[k] for k in ("user_id", "name", "score", "confirmed", "pending", "place", "winner", "out")}
                        for r in board],
        "winners_count": sum(r["winner"] for r in board),
        "my": {"score": me["score"] if me else 0, "confirmed": me["confirmed"] if me else 0, "winner": won,
               "attempts_left": max(0, event["max_attempts"] - rejected), "pending": pending, "can_submit": can_submit,
               "entries": [{"id": e["id"], "attempt": e["attempt"], "value": e["value"], "status": e["status"],
                            "reason": e["reason"], "submitted_at": e["submitted_at"]} for e in reversed(mine)]},
    }


def visible(conn: sqlite3.Connection, user: dict, days: int = 7) -> list[dict]:
    since = (now_local() - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE status != 'cancelled' AND (status = 'active' OR finished_at >= ?) ORDER BY ends_at",
        (since,))]
    return [r for r in rows if r["network_id"] is None or r["network_id"] == user.get("network_id") or perms.sees_everyone(user)]


def create(conn: sqlite3.Connection, actor: dict, *, title: str, mode: str, starts_at: datetime, ends_at: datetime,
           key: str, description: str | None = None, prize: str | None = None, target: int | None = None,
           max_winners: int = 1, max_attempts: int = 3, network_id: int | None = None) -> dict:
    title = " ".join((title or "").split())[:MAX_TITLE]
    if not title:
        raise ServiceError("Назовите событие", 422, "title_required")
    if mode not in ("first_to_reach", "accumulative"):
        raise ServiceError("Выберите тип события", 422, "bad_mode")
    if mode == "first_to_reach" and (isinstance(target, bool) or not isinstance(target, int) or target <= 0):
        raise ServiceError("Для гонки нужна цель в штуках", 422, "target_required")
    for v in (max_winners, max_attempts):
        if isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= 100:
            raise ServiceError("Победители и попытки — от 1 до 100", 422, "bad_limits")
    if ends_at <= starts_at or ends_at - starts_at > timedelta(days=31):
        raise ServiceError("Таймер — от минуты до месяца", 422, "bad_range")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, conn.execute("SELECT MAX(id) FROM events").fetchone()[0])
        actor = _fresh_actor(conn, actor)
        if not perms.is_staff(actor):
            raise ServiceError("Недостаточно прав", 403, "forbidden")
        if not perms.sees_everyone(actor):
            if network_id is None or network_id != actor.get("network_id"):
                raise ServiceError("Тренер проводит события только для своей сети", 403, "forbidden")
        if network_id is not None and (networks.get(conn, network_id) or {}).get("status") != "active":
            raise ServiceError("Сеть не найдена", 404, "not_found")
        cur = conn.execute(
            """INSERT INTO events (title, description, prize, mode, target, max_winners, max_attempts, starts_at, ends_at,
                                   network_id, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, (description or "").strip()[:500] or None, (prize or "").strip()[:200] or None, mode,
             target if mode == "first_to_reach" else None, max_winners, max_attempts,
             starts_at.isoformat(timespec="minutes"), ends_at.isoformat(timespec="minutes"), network_id,
             actor["telegram_id"], now_iso()),
        )
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="EVENT_CREATED", key=key,
                  new={"event": cur.lastrowid, "mode": mode}, description=title)
    return get(conn, cur.lastrowid)


def check_entry(conn, event: dict | None, user: dict, now: datetime) -> dict:
    if event is None or not eligible(event, user):
        raise ServiceError("Событие не найдено", 404, "not_found")
    v = view(conn, event, user, now)
    if v["phase"] != "live":
        raise ServiceError("Приём заявок закрыт" if v["phase"] != "upcoming" else "Событие ещё не началось", 409, "closed")
    if v["my"]["pending"]:
        raise ServiceError("Предыдущая заявка ещё на проверке", 409, "pending")
    if v["my"]["attempts_left"] == 0:
        raise ServiceError("Попытки закончились — участие в событии закрыто", 409, "no_attempts")
    if event["mode"] == "first_to_reach" and v["my"]["winner"]:
        raise ServiceError("Вы уже среди победителей 👑", 409, "already_won")
    return event


def submit(conn: sqlite3.Connection, user: dict, event_id: int, value: int, photo_file_id: str, key: str,
           now: datetime | None = None) -> tuple[dict, bool]:
    now = now or now_local()
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 1_000_000:
        raise ServiceError("Число — целое и больше нуля", 422, "bad_value")
    if not photo_file_id:
        raise ServiceError("Прикрепите скриншот", 422, "photo_required")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            row = conn.execute("SELECT * FROM event_entries WHERE event_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
                               (event_id, user["id"])).fetchone()
            return dict(row), False
        event = check_entry(conn, get(conn, event_id), svc.get_user(conn, user["id"]), now)
        attempt = conn.execute("SELECT COUNT(*) FROM event_entries WHERE event_id = ? AND user_id = ?",
                               (event_id, user["id"])).fetchone()[0] + 1
        if event["mode"] == "first_to_reach" and value < event["target"]:
            raise ServiceError(f"Цель гонки — {event['target']} шт. Сдайте заявку, когда дойдёте", 409, "below_target")
        cur = conn.execute(
            """INSERT INTO event_entries (event_id, user_id, attempt, value, photo_file_id, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?)""", (event_id, user["id"], attempt, value, photo_file_id, now_iso()))
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="EVENT_ENTRY", key=key,
                  new={"event": event_id, "value": value, "attempt": attempt})
    return get_entry(conn, cur.lastrowid), True


def review(conn: sqlite3.Connection, actor: dict, entry_id: int, approve: bool, reason: str | None, key: str) -> Review:
    reason = (reason or "").strip()[:300] or None
    if not approve and not reason:
        raise ServiceError("Укажите причину отклонения", 422, "reason_required")
    _check_key(key)
    with transaction(conn):
        entry = get_entry(conn, entry_id)
        if entry is None:
            raise ServiceError("Заявка не найдена", 404, "not_found")
        event = get(conn, entry["event_id"])
        if _seen(conn, key):
            return Review(entry, event, False)
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "review", svc.get_user(conn, entry["user_id"])):
            raise ServiceError("Недостаточно прав", 403, "forbidden")
        if entry["status"] != "pending_review" or event["status"] != "active":
            return Review(entry, event, False)
        # microseconds: the order of confirmations decides the winners and breaks ties
        conn.execute("UPDATE event_entries SET status = ?, reason = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                     ("confirmed" if approve else "rejected", reason, actor["telegram_id"], now_local().isoformat(), entry_id))
        finished = False
        if approve and event["mode"] == "first_to_reach":
            winners = conn.execute("SELECT COUNT(DISTINCT user_id) FROM event_entries WHERE event_id = ? AND status = 'confirmed'",
                                   (event["id"],)).fetchone()[0]
            if winners >= event["max_winners"]:
                conn.execute("UPDATE events SET status = 'finished', finished_at = ? WHERE id = ?", (now_iso(), event["id"]))
                finished = True
        log_event(conn, user_id=entry["user_id"], actor_id=actor["telegram_id"],
                  event_type="EVENT_ENTRY_CONFIRMED" if approve else "EVENT_ENTRY_REJECTED", key=key,
                  new={"entry": entry_id}, description=reason)
    return Review(get_entry(conn, entry_id), get(conn, event["id"]), True, finished)


def cancel(conn: sqlite3.Connection, actor: dict, event_id: int, key: str) -> dict:
    _check_key(key)
    with transaction(conn):
        event = get(conn, event_id)
        if event is None:
            raise ServiceError("Событие не найдено", 404, "not_found")
        if _seen(conn, key):
            return event
        actor = _fresh_actor(conn, actor)
        if not (perms.sees_everyone(actor) or event["created_by"] == actor["telegram_id"]):
            raise ServiceError("Недостаточно прав", 403, "forbidden")
        conn.execute("UPDATE events SET status = 'cancelled', finished_at = ? WHERE id = ? AND status = 'active'",
                     (now_iso(), event_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="EVENT_CANCELLED", key=key,
                  new={"event": event_id})
    return get(conn, event_id)


def finish_due(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """Timer + buffer passed: the event is finished (scheduler). Returns the finished events."""
    now = now or now_local()
    done = []
    for e in [dict(r) for r in conn.execute("SELECT * FROM events WHERE status = 'active'")]:
        if now >= _dt(e["ends_at"]) + timedelta(minutes=e["buffer_min"]):
            with transaction(conn):
                conn.execute("UPDATE events SET status = 'finished', finished_at = ? WHERE id = ? AND status = 'active'",
                             (now_iso(), e["id"]))
            done.append(get(conn, e["id"]))
    return done


def pending_entries(conn: sqlite3.Connection, viewer: dict) -> list[dict]:
    out = []
    for r in conn.execute(
            """SELECT x.*, e.title, e.mode, e.target FROM event_entries x JOIN events e ON e.id = x.event_id
               WHERE x.status = 'pending_review' AND e.status = 'active' ORDER BY x.id"""):
        r = dict(r)
        user = svc.get_user(conn, r["user_id"])
        if not perms.can(viewer, "review", user):
            continue
        out.append({"id": r["id"], "event_id": r["event_id"], "event": r["title"], "mode": r["mode"], "target": r["target"],
                    "user_id": r["user_id"], "name": svc.full_name(user), "value": r["value"], "attempt": r["attempt"],
                    "submitted_at": r["submitted_at"], "photo_url": entry_photo_url(r["id"])})
    return out


def entry_photo_url(entry_id: int) -> str:
    return photo_url(entry_id, kind="event")
