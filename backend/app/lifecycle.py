"""What goals and events share once they are running (ТЗ №20, №22).

Who: owners and admins — any goal or event; a trainer — only the ones they created, inside their network
(a personal goal — for a person of their network).

Feed visibility (`visibility`):
    auto   — in the feed while it runs and `feed_hide_after_days` days after the end, then in «Архив»;
    shown  — pinned to the feed by hand (never hidden automatically);
    hidden — hidden by hand: out of the feed, entries are closed, results are kept.
Deleting is soft (`deleted_at`): the item disappears for everyone, the history stays in the database.

Live editing uses optimistic locking: the client sends the `version` it saw, a stale one gets 409.
"""

import sqlite3
from datetime import date, datetime, timedelta

from . import permissions as perms
from . import services as svc
from .db import transaction
from .media import photo_url
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso

TABLES = {"goal": "goals", "event": "events"}
NOT_FOUND = {"goal": "Цель не найдена", "event": "Событие не найдено"}
# ТЗ №20, answer 3: a type from the list or the creator's own
PRIZE_TYPES = ["Деньги", "Подарок", "Сертификат", "Выходной", "Мерч"]
MAX_PRIZE_TYPE = 40
MAX_FILE_ID = 300


def hide_after_days(conn: sqlite3.Connection) -> int:
    from . import app_settings

    return app_settings.get_all(conn)["feed_hide_after_days"]


def can_manage(conn: sqlite3.Connection, actor: dict | None, kind: str, item: dict) -> bool:
    if not perms.is_staff(actor):
        return False
    if perms.sees_everyone(actor):
        return True
    if item["created_by"] != actor["telegram_id"] or actor.get("network_id") is None:
        return False
    if kind == "goal" and item["kind"] == "personal":
        return perms.manages(actor, svc.get_user(conn, item["user_id"]))
    if kind == "goal" and item["kind"] == "group":  # their own group of their own people
        ids = [r[0] for r in conn.execute("SELECT user_id FROM goal_participants WHERE goal_id = ?", (item["id"],))]
        return bool(ids) and all(perms.manages(actor, svc.get_user(conn, i)) for i in ids)
    return item["network_id"] == actor["network_id"]


def in_feed(item: dict, ended: str | None, today: date, days: int) -> bool:
    """`ended` — when it was over (ISO date or datetime), None while it runs."""
    if item["deleted_at"] or item["visibility"] == "hidden":
        return False
    if item["visibility"] == "shown" or ended is None:
        return True
    return date.fromisoformat(ended[:10]) + timedelta(days=days) >= today


def clean_prize_type(value: str | None) -> str | None:
    value = " ".join((value or "").split())[:MAX_PRIZE_TYPE]
    return value or None


def prize_photo_url(kind: str, item: dict) -> str | None:
    if not item.get("prize_photo"):
        return None
    return f"{photo_url(item['id'], kind=f'prize_{kind}')}&v={item['version']}"  # a new photo — a new link


def clean_file_id(value: str | None) -> str | None:
    value = (value or "").strip()
    if len(value) > MAX_FILE_ID:
        raise ServiceError("Некорректное фото", 422, "bad_photo")
    return value or None


def _load(conn: sqlite3.Connection, kind: str, item_id: int) -> dict:
    row = conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ?", (item_id,)).fetchone()
    if row is None or row["deleted_at"]:
        raise ServiceError(NOT_FOUND[kind], 404, "not_found")
    return dict(row)


def load_for_change(conn: sqlite3.Connection, actor: dict, kind: str, item_id: int, version: int | None) -> tuple[dict, dict]:
    """Inside a transaction: the fresh actor and the item, after the rights and the version check."""
    item = _load(conn, kind, item_id)
    actor = _fresh_actor(conn, actor)
    if not can_manage(conn, actor, kind, item):
        raise ServiceError("Тренер меняет только свои цели и события в своей сети", 403, "forbidden")
    if version is not None and version != item["version"]:
        raise ServiceError("Данные устарели: кто-то уже изменил это. Откройте заново", 409, "stale")
    return actor, item


def set_visibility(conn: sqlite3.Connection, actor: dict, kind: str, item_id: int, hidden: bool, key: str) -> dict:
    """The eye: hide from the feed / show again (always available, also for auto-hidden ones)."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return _load(conn, kind, item_id)
        actor, item = load_for_change(conn, actor, kind, item_id, None)
        visibility = "hidden" if hidden else "shown"
        conn.execute(f"UPDATE {TABLES[kind]} SET visibility = ?, hidden_at = ?, version = version + 1, updated_at = ? WHERE id = ?",
                     (visibility, now_iso() if hidden else None, now_iso(), item_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"],
                  event_type=f"{kind.upper()}_{'HIDDEN' if hidden else 'SHOWN'}", key=key,
                  old={"visibility": item["visibility"]}, new={kind: item_id, "visibility": visibility})
    return _load(conn, kind, item_id)


def delete(conn: sqlite3.Connection, actor: dict, kind: str, item_id: int, key: str) -> dict:
    _check_key(key)
    with transaction(conn):
        row = conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ?", (item_id,)).fetchone()
        if row is not None and _seen(conn, key):
            return dict(row)
        actor, item = load_for_change(conn, actor, kind, item_id, None)
        conn.execute(f"UPDATE {TABLES[kind]} SET deleted_at = ?, version = version + 1, updated_at = ? WHERE id = ?",
                     (now_iso(), now_iso(), item_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type=f"{kind.upper()}_DELETED", key=key,
                  new={kind: item_id}, description=item["title"])
    return dict(conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id = ?", (item_id,)).fetchone())


def save(conn: sqlite3.Connection, kind: str, item_id: int, fields: dict) -> None:
    """Write the changed fields and bump the version (inside the caller's transaction)."""
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE {TABLES[kind]} SET {sets}{', ' if sets else ''}version = version + 1, updated_at = ? WHERE id = ?",
                 (*fields.values(), now_iso(), item_id))


def diff(old: dict, new: dict) -> dict:
    return {k: v for k, v in new.items() if old.get(k) != v}


def iso_minutes(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")
