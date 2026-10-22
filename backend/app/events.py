"""Live events with a leaderboard (ТЗ модуль 7).

    first_to_reach — a race to `target`: a participant sends their number + screenshot; staff confirm
                     winners in order (👑) until `max_winners`, then the event is finished.
    accumulative   — whoever makes the most: every confirmed entry adds up; the board ranks by
                     confirmed + pending (soft score) for drama, prizes go by confirmed score;
                     ties are broken by who reached the score first. After the timer there are
                     `buffer_min` minutes to review the last entries, then the top `max_winners` win.

An entry is pending_review → confirmed | rejected (reason required). A participant has one entry
under review at a time and is out of the event after `max_attempts` rejections.

ТЗ №20: a running event can be edited (lifecycle.py: rights, version). The start moves only before it
starts; a race with winners keeps its target; a finish set in the past ends the event, a later finish on a
finished one restarts it. A confirmed result can be cancelled with a reason («отменить результат»): it no
longer counts and doesn't use up an attempt. ТЗ №22: hidden events take no entries; see lifecycle.py.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import lifecycle, networks, richtext
from . import permissions as perms
from . import services as svc
from .config import settings
from .db import transaction
from .media import photo_url
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, now_local

MAX_TITLE = 80
CRITICAL = ("starts_at", "ends_at", "target", "max_winners", "max_attempts", "prize", "prize_type")


MAX_DESCRIPTION = 1000  # visible characters; the formatting is kept separately


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def winners_text(n: int) -> str:
    return f"{n}\u00a0{_plural(n, 'победитель', 'победителя', 'победителей')}"  # the number never parts from its word


def rule(event: dict) -> str:
    """«Гонка до 30 шт., 1 победитель» / «Кто больше сделает, 3 победителя»."""
    if event["mode"] == "first_to_reach":
        return f"Гонка до {event['target']}\u00a0шт., {winners_text(event['max_winners'])}"
    return f"Кто больше сделает, {winners_text(event['max_winners'])}"


def how_to_win(event: dict) -> str:
    n = event["max_winners"]
    if event["mode"] == "first_to_reach":
        who = "Побеждает первый, кто" if n == 1 else f"Побеждают первые {n}, кто"
        return f"{who} сделает {event['target']}\u00a0шт. и подтвердит скриншотом"
    if n == 1:
        return "Побеждает тот, кто к финишу сделает больше всех"
    return f"Побеждают {n} лучших по подтверждённым штукам к финишу"


def clean_description(value: str | None) -> tuple[str | None, str | None]:
    """The editor sends Telegram HTML (bold, italic, links…); returns (html, plain), both None when empty."""
    html, plain = richtext.sanitize(value or "")
    if len(plain) > MAX_DESCRIPTION:
        raise ServiceError(f"Описание длиннее {MAX_DESCRIPTION} символов", 422, "too_long")
    return (html or None, plain or None) if plain else (None, None)


def clean_media_kind(value: str | None) -> str:
    if value not in (None, "photo", "video"):
        raise ServiceError("Приз — фото или видео", 422, "bad_media")
    return value or "photo"


@dataclass
class Review:
    entry: dict
    event: dict
    changed: bool
    finished: bool = False
    reopened: bool = False


@dataclass
class Update:
    event: dict
    changed: list[str]
    critical: list[str]
    finished: bool = False
    restarted: bool = False
    old: dict | None = None


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
        elif not e["annulled_at"]:
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
    rejected = sum(e["status"] == "rejected" and not e["annulled_at"] for e in mine)
    pending = any(e["status"] == "pending_review" for e in mine)
    me = next((r for r in board if viewer and r["user_id"] == viewer["id"]), None)
    won = bool(me and me["winner"])
    phase = _phase(event, now)
    can_submit = (viewer is not None and eligible(event, viewer) and phase == "live" and not pending
                  and event["visibility"] != "hidden"
                  and rejected < event["max_attempts"] and not (event["mode"] == "first_to_reach" and won))
    return {
        "id": event["id"], "title": event["title"], "description": event["description"],
        "description_html": event["description_html"] or (richtext.plain_to_html(event["description"])
                                                           if event["description"] else None),
        "rule": rule(event), "how_to_win": how_to_win(event), "prize": event["prize"],
        "prize_type": event["prize_type"], "prize_photo_url": lifecycle.prize_photo_url("event", event),
        "prize_media_kind": event["prize_media_kind"] if event["prize_photo"] else None,
        "version": event["version"], "visibility": event["visibility"], "hidden_at": event["hidden_at"],
        "finished_at": event["finished_at"], "created_at": event["created_at"],
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
                            "reason": e["reason"], "annulled": bool(e["annulled_at"]),
                            "submitted_at": e["submitted_at"]} for e in reversed(mine)]},
    }


def _scoped(conn: sqlite3.Connection, user: dict, where: str, order: str) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM events WHERE deleted_at IS NULL AND status != 'cancelled' AND {where} ORDER BY {order}")]
    return [r for r in rows if r["network_id"] is None or r["network_id"] == user.get("network_id") or perms.sees_everyone(user)]


def _ended(event: dict) -> str | None:
    return event["finished_at"] if event["status"] != "active" else None


def visible(conn: sqlite3.Connection, user: dict, now: datetime | None = None) -> list[dict]:
    """The feed (ТЗ №22): running ones and the recently finished, unless hidden."""
    today, days = (now or now_local()).date(), lifecycle.hide_after_days(conn)
    return [e for e in _scoped(conn, user, "1", "ends_at") if lifecycle.in_feed(e, _ended(e), today, days)]


def archived(conn: sqlite3.Connection, user: dict, now: datetime | None = None, limit: int = 50) -> list[dict]:
    """«Архив»: hidden by hand or finished long enough ago — results and winners stay."""
    today, days = (now or now_local()).date(), lifecycle.hide_after_days(conn)
    rows = _scoped(conn, user, "1", "COALESCE(finished_at, ends_at) DESC")
    return [e for e in rows if not lifecycle.in_feed(e, _ended(e), today, days)][:limit]


def managed(conn: sqlite3.Connection, viewer: dict, limit: int = 100) -> list[dict]:
    """The admin list: everything not deleted (running, finished, hidden, cancelled), newest first."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE deleted_at IS NULL ORDER BY (status = 'active') DESC, id DESC LIMIT ?", (limit,))]
    if perms.sees_everyone(viewer):
        return rows
    return [r for r in rows if r["network_id"] is not None and r["network_id"] == viewer.get("network_id")]


def in_feed(conn: sqlite3.Connection, event: dict, now: datetime | None = None) -> bool:
    return lifecycle.in_feed(event, _ended(event), (now or now_local()).date(), lifecycle.hide_after_days(conn))


def readable(conn: sqlite3.Connection, user: dict, event_id: int) -> dict:
    """An event the person may open: from the feed or from «Архив»."""
    event = get(conn, event_id)
    if (event is None or event["deleted_at"] or event["status"] == "cancelled"
            or not (event["network_id"] is None or event["network_id"] == user.get("network_id") or perms.sees_everyone(user))):
        raise ServiceError("Событие не найдено", 404, "not_found")
    return event


def _clean_title(title: str | None) -> str:
    title = " ".join((title or "").split())[:MAX_TITLE]
    if not title:
        raise ServiceError("Назовите событие", 422, "title_required")
    return title


def _check_target(mode: str, target) -> None:
    if mode == "first_to_reach" and (isinstance(target, bool) or not isinstance(target, int) or not 0 < target <= 1_000_000):
        raise ServiceError("Для гонки нужна цель в штуках", 422, "target_required")


def _check_limits(*values) -> None:
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= 100:
            raise ServiceError("Победители и попытки — от 1 до 100", 422, "bad_limits")


def _check_range(starts_at: datetime, ends_at: datetime) -> None:
    if ends_at <= starts_at or ends_at - starts_at > timedelta(days=31):
        raise ServiceError("Таймер — от минуты до месяца", 422, "bad_range")


def create(conn: sqlite3.Connection, actor: dict, *, title: str, mode: str, starts_at: datetime, ends_at: datetime,
           key: str, description: str | None = None, prize: str | None = None, target: int | None = None,
           max_winners: int = 1, max_attempts: int = 3, network_id: int | None = None, prize_type: str | None = None,
           prize_photo: str | None = None, prize_media_kind: str | None = None) -> dict:
    title = _clean_title(title)
    description_html, description = clean_description(description)
    prize_media_kind = clean_media_kind(prize_media_kind)
    if mode not in ("first_to_reach", "accumulative"):
        raise ServiceError("Выберите тип события", 422, "bad_mode")
    _check_target(mode, target)
    _check_limits(max_winners, max_attempts)
    _check_range(starts_at, ends_at)
    prize_photo = lifecycle.clean_file_id(prize_photo)
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
            """INSERT INTO events (title, description, description_html, prize, prize_type, prize_photo,
                                   prize_media_kind, mode, target, max_winners, max_attempts, starts_at, ends_at,
                                   network_id, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, description_html, (prize or "").strip()[:200] or None,
             lifecycle.clean_prize_type(prize_type), prize_photo, prize_media_kind, mode,
             target if mode == "first_to_reach" else None, max_winners, max_attempts,
             starts_at.isoformat(timespec="minutes"), ends_at.isoformat(timespec="minutes"), network_id,
             actor["telegram_id"], now_iso()),
        )
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="EVENT_CREATED", key=key,
                  new={"event": cur.lastrowid, "mode": mode}, description=title)
    return get(conn, cur.lastrowid)


def check_entry(conn, event: dict | None, user: dict, now: datetime) -> dict:
    if event is None or event["deleted_at"] or not eligible(event, user):
        raise ServiceError("Событие не найдено", 404, "not_found")
    if event["visibility"] == "hidden":
        raise ServiceError("Это событие завершено или скрыто администрацией", 409, "hidden")
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
        if entry["status"] != "pending_review" or event["status"] != "active" or event["deleted_at"]:
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
        actor, event = lifecycle.load_for_change(conn, actor, "event", event_id, None)
        conn.execute("UPDATE events SET status = 'cancelled', finished_at = ?, version = version + 1 WHERE id = ? AND status = 'active'",
                     (now_iso(), event_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="EVENT_CANCELLED", key=key,
                  new={"event": event_id})
    return get(conn, event_id)


def _winners(conn: sqlite3.Connection, event_id: int) -> int:
    return conn.execute("SELECT COUNT(DISTINCT user_id) FROM event_entries WHERE event_id = ? AND status = 'confirmed'",
                        (event_id,)).fetchone()[0]


def update(conn: sqlite3.Connection, actor: dict, event_id: int, version: int, changes: dict, key: str,
           now: datetime | None = None) -> Update:
    """ТЗ №20: live editing. `changes` holds only the fields the form sent (dates as datetimes)."""
    now = now or now_local()
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return Update(get(conn, event_id), [], [])
        actor, old = lifecycle.load_for_change(conn, actor, "event", event_id, version)
        if old["status"] == "cancelled":
            raise ServiceError("Отменённое событие не редактируется", 409, "cancelled")
        new: dict = {}
        if "title" in changes:
            new["title"] = _clean_title(changes["title"])
        if "description" in changes:
            new["description_html"], new["description"] = clean_description(changes["description"])
        if "prize" in changes:
            new["prize"] = (changes["prize"] or "").strip()[:200] or None
        if "prize_type" in changes:
            new["prize_type"] = lifecycle.clean_prize_type(changes["prize_type"])
        if "prize_photo" in changes:
            new["prize_photo"] = lifecycle.clean_file_id(changes["prize_photo"])
            new["prize_media_kind"] = clean_media_kind(changes.get("prize_media_kind"))
        for k in ("starts_at", "ends_at"):
            if changes.get(k) is not None:
                new[k] = lifecycle.iso_minutes(changes[k])
        if "target" in changes and old["mode"] == "first_to_reach":
            _check_target(old["mode"], changes["target"])
            new["target"] = changes["target"]
        for k in ("max_winners", "max_attempts"):
            if changes.get(k) is not None:
                _check_limits(changes[k])
                new[k] = changes[k]
        new = lifecycle.diff(old, new)
        if not new:
            return Update(old, [], [])
        if "starts_at" in new and now >= _dt(old["starts_at"]):
            raise ServiceError("Событие уже началось — дату старта менять нельзя", 409, "started")
        merged = {**old, **new}
        _check_range(_dt(merged["starts_at"]), _dt(merged["ends_at"]))
        winners = _winners(conn, event_id)
        if old["mode"] == "first_to_reach":
            if "target" in new and winners:
                raise ServiceError("У гонки уже есть победители — цель не меняется. Сначала отмените их результат",
                                   409, "has_winners")
            if merged["max_winners"] < winners:
                raise ServiceError(f"Победителей уже {winners} — меньше поставить нельзя", 409, "below_winners")
        race_full = old["mode"] == "first_to_reach" and winners >= merged["max_winners"]
        end = _dt(merged["ends_at"])
        pending = conn.execute("SELECT 1 FROM event_entries WHERE event_id = ? AND status = 'pending_review'",
                               (event_id,)).fetchone()
        # a finish in the past ends it now; entries still under review keep the usual buffer
        over = now >= end + timedelta(minutes=merged["buffer_min"]) or (now >= end and not pending)
        finished = restarted = False
        if old["status"] == "finished":
            if "ends_at" in new and now < end:  # answer 5: a later finish restarts the event
                if race_full:
                    raise ServiceError("Все победители уже определены — увеличьте число победителей", 409, "race_full")
                new.update(status="active", finished_at=None)
                restarted = True
        elif over or race_full:
            new.update(status="finished", finished_at=now.isoformat(timespec="seconds"))
            finished = True
        lifecycle.save(conn, "event", event_id, new)
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="EVENT_UPDATED", key=key,
                  old={k: old[k] for k in new}, new={"event": event_id, **new}, description=merged["title"])
    # the formatting and the media kind ride along with the description and the prize file
    changed = list(dict.fromkeys({"description_html": "description", "prize_media_kind": "prize_photo"}.get(k, k)
                                 for k in new if k not in ("status", "finished_at")))
    return Update(get(conn, event_id), changed, [k for k in changed if k in CRITICAL], finished, restarted, old)


def annul_entry(conn: sqlite3.Connection, actor: dict, entry_id: int, reason: str | None, key: str,
                now: datetime | None = None) -> Review:
    """«Отменить результат»: a confirmed entry stops counting (conditions changed or the work was dishonest).
    It doesn't use up an attempt. A race that was over by winners reopens while its timer runs."""
    now = now or now_local()
    reason = (reason or "").strip()[:300] or None
    if not reason:
        raise ServiceError("Укажите причину", 422, "reason_required")
    _check_key(key)
    with transaction(conn):
        entry = get_entry(conn, entry_id)
        if entry is None:
            raise ServiceError("Заявка не найдена", 404, "not_found")
        if _seen(conn, key):
            return Review(entry, get(conn, entry["event_id"]), False)
        actor, event = lifecycle.load_for_change(conn, actor, "event", entry["event_id"], None)
        if entry["status"] != "confirmed":
            return Review(entry, event, False)
        conn.execute("""UPDATE event_entries SET status = 'rejected', reason = ?, annulled_at = ?, reviewed_by = ?,
                        reviewed_at = ? WHERE id = ?""", (reason, now_iso(), actor["telegram_id"], now.isoformat(), entry_id))
        reopen = event["status"] == "finished" and event["mode"] == "first_to_reach" and now < _dt(event["ends_at"])
        lifecycle.save(conn, "event", event["id"], {"status": "active", "finished_at": None} if reopen else {})
        log_event(conn, user_id=entry["user_id"], actor_id=actor["telegram_id"], event_type="EVENT_RESULT_CANCELLED",
                  key=key, new={"entry": entry_id, "reopened": reopen}, description=reason)
    return Review(get_entry(conn, entry_id), get(conn, event["id"]), True, reopened=reopen)


def finish_due(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """Timer + buffer passed: the event is finished (scheduler). Returns the finished events."""
    now = now or now_local()
    done = []
    for e in [dict(r) for r in conn.execute("SELECT * FROM events WHERE status = 'active' AND deleted_at IS NULL")]:
        if now >= _dt(e["ends_at"]) + timedelta(minutes=e["buffer_min"]):
            with transaction(conn):
                flipped = conn.execute("UPDATE events SET status = 'finished', finished_at = ? WHERE id = ? AND status = 'active'",
                                       (now.isoformat(timespec="seconds"), e["id"])).rowcount
            if flipped:  # only the run that finished it announces it
                done.append(get(conn, e["id"]))
    return done


def pending_entries(conn: sqlite3.Connection, viewer: dict) -> list[dict]:
    out = []
    for r in conn.execute(
            """SELECT x.*, e.title, e.mode, e.target FROM event_entries x JOIN events e ON e.id = x.event_id
               WHERE x.status = 'pending_review' AND e.status = 'active' AND e.deleted_at IS NULL ORDER BY x.id"""):
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
