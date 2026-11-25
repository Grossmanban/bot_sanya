"""Broadcast extras (the customer's round after ТЗ 7): saved templates and «Ответить».

Templates: any staff member who writes broadcasts can create one and use everyone's; the author or an
owner/admin edits and deletes (soft — `deleted_at`).

Replies: with «Возможность ответить» on, every message carries «✍️ Ответить». The person writes text and/or
attaches photos and videos in the bot, presses «Отправить» — the bot hands it all to the broadcast's
author in one tidy message (who, @username, a link to their Telegram, ID). A reply also counts as read.
"""

import json
import sqlite3
from datetime import datetime

from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ServiceError
from .timeutil import now_iso

MAX_TITLE = 60
MAX_REPLY = 3500
MAX_REPLY_MEDIA = 10


# ---------------------------------------------------------------- templates


def templates(conn: sqlite3.Connection, kind: str = "broadcast") -> list[dict]:
    """kind (19.11): «broadcast» — the saved messages; «assign» — the schedule templates («Распределение»).
    Each screen sees only its own kind."""
    return [dict(r) for r in conn.execute(
        """SELECT t.id, t.title, t.html, t.created_by, t.created_at, t.updated_at, u.first_name, u.last_name
           FROM broadcast_templates t LEFT JOIN users u ON u.telegram_id = t.created_by
           WHERE t.deleted_at IS NULL AND t.kind = ? ORDER BY t.title COLLATE NOCASE""", (kind,))]


def _template(conn: sqlite3.Connection, template_id: int) -> dict:
    row = conn.execute("SELECT * FROM broadcast_templates WHERE id = ? AND deleted_at IS NULL", (template_id,)).fetchone()
    if row is None:
        raise ServiceError("Шаблон не найден", 404, "not_found")
    return dict(row)


def kind_of(conn: sqlite3.Connection, template_id: int) -> str:
    """19.11: «broadcast» or «assign» — what the template is for (the route checks the matching right)."""
    return _template(conn, template_id)["kind"]


def _may_change(actor: dict, template: dict) -> None:
    if not (perms.sees_everyone(actor) or template["created_by"] == actor["telegram_id"]):
        raise ServiceError("Менять шаблон может его автор или администратор", 403, "forbidden")


def save_template(conn: sqlite3.Connection, actor: dict, title: str, html: str, template_id: int | None = None,
                  kind: str = "broadcast") -> dict:
    """New (template_id None) or an edit of an existing one. kind (19.11): «broadcast» or «assign» —
    the edit keeps the kind the template already has."""
    title = " ".join((title or "").split())[:MAX_TITLE]
    if not title:
        raise ServiceError("Назовите шаблон", 422, "title_required")
    clean, _ = svc.prepare_message(html)
    with transaction(conn):
        if template_id is None:
            cur = conn.execute(
                "INSERT INTO broadcast_templates (title, html, created_by, created_at, kind) VALUES (?, ?, ?, ?, ?)",
                (title, clean, actor["telegram_id"], now_iso(), kind))
            template_id = cur.lastrowid
        else:
            _may_change(actor, _template(conn, template_id))
            conn.execute("UPDATE broadcast_templates SET title = ?, html = ?, updated_at = ? WHERE id = ?",
                         (title, clean, now_iso(), template_id))
    return _template(conn, template_id)


def delete_template(conn: sqlite3.Connection, actor: dict, template_id: int) -> None:
    with transaction(conn):
        _may_change(actor, _template(conn, template_id))
        conn.execute("UPDATE broadcast_templates SET deleted_at = ? WHERE id = ?", (now_iso(), template_id))


# ---------------------------------------------------------------- replies


def get(conn: sqlite3.Connection, broadcast_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
    return dict(row) if row else None


def can_reply(conn: sqlite3.Connection, broadcast_id: int, user: dict | None) -> dict:
    """The broadcast if this person may answer it: replies are on and it was sent to them."""
    b = get(conn, broadcast_id)
    if b is None or user is None or not b["allow_reply"]:
        raise ServiceError("На это сообщение ответить нельзя", 409, "no_reply")
    got = conn.execute("SELECT 1 FROM broadcast_deliveries WHERE broadcast_id = ? AND user_id = ?",
                       (broadcast_id, user["id"])).fetchone()
    if got is None:
        raise ServiceError("Это сообщение было не вам", 403, "forbidden")
    return b


def add_reply(conn: sqlite3.Connection, broadcast_id: int, user: dict, text: str | None,
              media: list[dict]) -> tuple[dict, dict]:
    """media: [{"kind": "photo" | "video", "file_id": …}]. Returns (broadcast, reply)."""
    text = (text or "").strip()[:MAX_REPLY] or None
    media = [m for m in media if m.get("kind") in ("photo", "video") and m.get("file_id")][:MAX_REPLY_MEDIA]
    if not text and not media:
        raise ServiceError("Напишите ответ или приложите фото/видео", 422, "empty")
    with transaction(conn):
        b = can_reply(conn, broadcast_id, user)
        cur = conn.execute("INSERT INTO broadcast_replies (broadcast_id, user_id, text, media, created_at) VALUES (?, ?, ?, ?, ?)",
                           (broadcast_id, user["id"], text, json.dumps(media) if media else None, now_iso()))
        # answering means they read it
        conn.execute("UPDATE broadcast_deliveries SET read_at = ? WHERE broadcast_id = ? AND user_id = ? AND read_at IS NULL",
                     (now_iso(), broadcast_id, user["id"]))
    reply = dict(conn.execute("SELECT * FROM broadcast_replies WHERE id = ?", (cur.lastrowid,)).fetchone())
    return b, {**reply, "media": media}


def replies_by_user(conn: sqlite3.Connection, broadcast_id: int) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in conn.execute("SELECT * FROM broadcast_replies WHERE broadcast_id = ? ORDER BY id", (broadcast_id,)):
        media = json.loads(r["media"]) if r["media"] else []
        out.setdefault(r["user_id"], []).append({
            "text": r["text"], "created_at": r["created_at"],
            "photos": sum(m["kind"] == "photo" for m in media), "videos": sum(m["kind"] == "video" for m in media)})
    return out


# ---------------------------------------------------------------- read deadline (19.11)


def _excerpt(b: dict) -> str:
    text = " ".join((b["text"] or "").split())
    return (text[:48] + "…") if len(text) > 48 else text


async def enforce_deadlines(conn: sqlite3.Connection, notifier, now: datetime | None = None) -> list[dict]:
    """The read deadline passed (the scheduler calls this every tick): everyone who got the message but didn't
    press «✅ Я ознакомился» (a reply counts as read) gets the fine in the units the author set — active at once,
    whoever the author is: the whole point is that it runs without anyone pressing anything. One pass per
    broadcast (`ack_fines_done`), so a restart or a re-tick never fines twice. People who never received the
    message and blocked accounts are not fined. Returns [{broadcast, fined}] for the scheduler's log."""
    from . import finance  # local: finance imports services; it also needs no imports of this module

    now = now or svc.now_local()  # services' clock: tests patch it to noon (deadline_at is written in the same terms)
    done = []
    for b in [dict(r) for r in conn.execute(
            """SELECT * FROM broadcasts WHERE require_ack = 1 AND ack_fines_done = 0 AND ack_fine_units IS NOT NULL
               AND ack_fine_units > 0 AND ack_deadline_at IS NOT NULL AND ack_deadline_at <= ?""",
            (now.isoformat(timespec="seconds"),))]:
        author = svc.get_user_by_tg(conn, b["author_id"])
        fined: list[tuple[dict, dict]] = []
        skipped = 0
        with transaction(conn):
            rows = [dict(r) for r in conn.execute(
                """SELECT d.*, u.status, u.account_state, u.fines_exempt FROM broadcast_deliveries d
                   JOIN users u ON u.id = d.user_id
                   WHERE d.broadcast_id = ? AND d.delivered = 1 AND d.read_at IS NULL AND d.fined_at IS NULL""",
                (b["id"],))]
            for d in rows:
                if d["status"] != "approved" or d["account_state"] == "blocked":
                    continue
                # 19.11: «без штрафов» — the fine is skipped quietly; the delivery stays unfined (never re-fined:
                # the broadcast is marked done below)
                if d["fines_exempt"]:
                    skipped += 1
                    continue
                tx = finance.record_fine(conn, user_id=d["user_id"], units=b["ack_fine_units"],
                                         reason=f"Не подтвердил(а) прочтение рассылки «{_excerpt(b)}» в срок",
                                         actor_tg=b["author_id"], source="broadcast")
                conn.execute("UPDATE broadcast_deliveries SET fined_at = ? WHERE id = ?", (now_iso(), d["id"]))
                fined.append((svc.get_user(conn, d["user_id"]), tx))
            conn.execute("UPDATE broadcasts SET ack_fines_done = 1 WHERE id = ?", (b["id"],))
        for user, tx in fined:
            if author is not None:
                await notifier.tx_created(tx, author)  # «💸 Вам назначен штраф…» to the person + #штраф to the archive
            else:
                await notifier.send(user["telegram_id"], f"💸 Вам назначен штраф: {finance.units_text(tx)}.\n"
                                                        f"Причина: {tx['reason']}")
        if fined and author is not None:
            names = ", ".join(svc.full_name(u) for u, _ in fined[:20]) + ("…" if len(fined) > 20 else "")
            extra = f" Пропущены без штрафов («без штрафов» в «Команде»): {skipped} чел." if skipped else ""
            await notifier.send(author["telegram_id"],
                                f"⏳ Срок отметки по рассылке «{_excerpt(b)}» истёк. Штраф ({b['ack_fine_units']} ед.) "
                                f"назначен: {len(fined)} чел. — {names}.{extra}")
        done.append({"broadcast": b["id"], "fined": len(fined), "skipped": skipped})
    return done
