"""Access by invitation only (ТЗ 27.10 §5–9).

    ref    — an employee's referral link: t.me/<bot>?start=ref_<token>. Lives `link_ttl_minutes` (1 hour by
             default), then the next one is made on demand and the old one stops working — printed QR codes too;
             the app shows a timer «сменится через …». Any number of people may use it.
    owner  — an owner's invitation: t.me/<bot>?start=inv_<token>, its own term and number of uses. The person
             gives a surname and a name in the chat, admins approve — no training or internship.
A person who opened a valid link is bound to it (invite_uses): they may finish the questionnaire or the request
later, even after the link changed. «Обновить все ссылки» revokes every live link at once; referral links come
back fresh on the next view, owner invitations are re-issued in the same transaction (no gap without a link).

Strangers: /start without a link, or the Mini App opened without one — no answer, only a row in
access_attempts (one per person, kind and parameter; repeats bump the counter). A link that expired, was revoked
or used up gets «Эта ссылка больше неактуальна…» once; the same link again — silence.
"""

import secrets
import sqlite3
from datetime import datetime, timedelta

from . import app_settings
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_local

STALE = ("Эта ссылка больше неактуальна. Пожалуйста, обратитесь к человеку, который вас пригласил, "
         "чтобы получить новую ссылку для регистрации")
PREFIX = {"ref": "ref_", "owner": "inv_"}
MAX_LABEL = 60


def _now(now: datetime | None) -> datetime:
    return now or now_local()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def get(conn: sqlite3.Connection, link_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM invite_links WHERE id = ?", (link_id,)).fetchone()
    return dict(row) if row else None


def state(link: dict, now: datetime | None = None) -> str:
    """active / expired / revoked / used_up."""
    if link["revoked_at"]:
        return "revoked"
    if link["max_uses"] is not None and link["uses"] >= link["max_uses"]:
        return "used_up"
    if link["expires_at"] <= _iso(_now(now)):
        return "expired"
    return "active"


def url(bot_username: str | None, link: dict) -> str | None:
    return f"https://t.me/{bot_username}?start={PREFIX[link['kind']]}{link['token']}" if bot_username else None


def _insert(conn, kind: str, user_id: int | None, ttl: int, now: datetime, label: str | None = None,
            max_uses: int | None = None) -> int:
    while True:
        token = secrets.token_urlsafe(16)  # 22 characters of [A-Za-z0-9_-]: fits a /start parameter
        if conn.execute("SELECT 1 FROM invite_links WHERE token = ?", (token,)).fetchone() is None:
            break
    return conn.execute(
        """INSERT INTO invite_links (kind, token, user_id, label, max_uses, ttl_minutes, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (kind, token, user_id, label, max_uses, ttl, _iso(now + timedelta(minutes=ttl)), _iso(now))).lastrowid


# ---------------------------------------------------------------- referral links


def current_ref(conn: sqlite3.Connection, user: dict, now: datetime | None = None) -> dict:
    """The person's live referral link; a new one when the last expired or was revoked."""
    now = _now(now)
    with transaction(conn):
        row = conn.execute(
            """SELECT * FROM invite_links WHERE kind = 'ref' AND user_id = ? AND revoked_at IS NULL AND expires_at > ?
               ORDER BY id DESC LIMIT 1""", (user["id"], _iso(now))).fetchone()
        if row is not None:
            return dict(row)
        ttl = app_settings.get_all(conn)["link_ttl_minutes"]
        link_id = _insert(conn, "ref", user["id"], ttl, now)
    return get(conn, link_id)


def public(link: dict, bot_username: str | None, now: datetime | None = None, names: dict | None = None) -> dict:
    return {
        "id": link["id"],
        "kind": link["kind"],
        "url": url(bot_username, link),
        "label": link["label"],
        "max_uses": link["max_uses"],
        "uses": link["uses"],
        "ttl_minutes": link["ttl_minutes"],
        "expires_at": link["expires_at"],
        "created_at": link["created_at"],
        "revoked_at": link["revoked_at"],
        "state": state(link, now),
        "people": (names or {}).get(link["id"], []),
    }


# ---------------------------------------------------------------- owner invitations


def _owner(conn, actor: dict) -> dict:
    actor = _fresh_actor(conn, actor)
    if not perms.can(actor, "invites"):
        raise ServiceError("Приглашения создаёт только владелец", 403, "owner_only")
    return actor


def create_owner_invite(conn: sqlite3.Connection, actor: dict, label: str | None, ttl_minutes: int,
                        max_uses: int | None, key: str, now: datetime | None = None) -> dict:
    _check_key(key)
    label = " ".join((label or "").split())[:MAX_LABEL] or None
    if isinstance(ttl_minutes, bool) or not isinstance(ttl_minutes, int) or not 5 <= ttl_minutes <= 30 * 24 * 60:
        raise ServiceError("Срок — от 5 минут до 30 дней", 422, "bad_ttl")
    if max_uses is not None and (isinstance(max_uses, bool) or not isinstance(max_uses, int) or not 1 <= max_uses <= 1000):
        raise ServiceError("Лимит — от 1 до 1000 человек или без лимита", 422, "bad_uses")
    now = _now(now)
    with transaction(conn):
        if _seen(conn, key):
            row = conn.execute("SELECT * FROM invite_links WHERE kind = 'owner' ORDER BY id DESC LIMIT 1").fetchone()
            return dict(row)
        actor = _owner(conn, actor)
        link_id = _insert(conn, "owner", actor["id"], ttl_minutes, now, label, max_uses)
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="INVITE_CREATED", key=key,
                  new={"link": link_id, "ttl": ttl_minutes, "max_uses": max_uses}, description=label)
    return get(conn, link_id)


def owner_invites(conn: sqlite3.Connection, limit: int = 100) -> tuple[list[dict], dict]:
    links = [dict(r) for r in conn.execute(
        "SELECT * FROM invite_links WHERE kind = 'owner' ORDER BY id DESC LIMIT ?", (limit,))]
    names: dict[int, list] = {}
    if links:
        ids = [link["id"] for link in links]
        for r in conn.execute(
                f"""SELECT u.link_id, u.telegram_id, u.used_at, s.id AS user_id, s.first_name, s.last_name, s.status
                    FROM invite_uses u LEFT JOIN users s ON s.telegram_id = u.telegram_id
                    WHERE u.link_id IN ({','.join('?' * len(ids))}) ORDER BY u.used_at""", ids):
            names.setdefault(r["link_id"], []).append({
                "telegram_id": r["telegram_id"], "used_at": r["used_at"], "user_id": r["user_id"],
                "name": svc.full_name(dict(r)) if r["user_id"] else None, "status": r["status"]})
    return links, names


def revoke(conn: sqlite3.Connection, actor: dict, link_id: int, key: str, now: datetime | None = None) -> tuple[dict, bool]:
    _check_key(key)
    with transaction(conn):
        link = get(conn, link_id)
        if link is None or link["kind"] != "owner":
            raise ServiceError("Ссылка не найдена", 404, "not_found")
        if _seen(conn, key) or link["revoked_at"]:
            return link, False
        actor = _owner(conn, actor)
        conn.execute("UPDATE invite_links SET revoked_at = ?, revoked_by = ? WHERE id = ?",
                     (_iso(_now(now)), actor["telegram_id"], link_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="INVITE_REVOKED", key=key,
                  old={"link": link_id})
    return get(conn, link_id), True


def rotate_all(conn: sqlite3.Connection, actor: dict, key: str, now: datetime | None = None) -> dict:
    """«Обновить все ссылки» (§8): every live link stops working at once. Owner invitations get replacements
    with the same label, term and remaining uses in the same transaction; referral links are made on the
    next view. Accounts, referral ties and rewards stay as they are."""
    _check_key(key)
    now = _now(now)
    with transaction(conn):
        if _seen(conn, key):
            return {"ref": 0, "owner": 0}
        actor = _owner(conn, actor)
        live = [dict(r) for r in conn.execute(
            "SELECT * FROM invite_links WHERE revoked_at IS NULL AND expires_at > ?", (_iso(now),))]
        live = [link for link in live if state(link, now) == "active"]
        for link in live:
            new_id = None
            if link["kind"] == "owner":
                left = None if link["max_uses"] is None else link["max_uses"] - link["uses"]
                new_id = _insert(conn, "owner", link["user_id"], link["ttl_minutes"], now, link["label"], left)
            conn.execute("UPDATE invite_links SET revoked_at = ?, revoked_by = ?, replaced_by = ? WHERE id = ?",
                         (_iso(now), actor["telegram_id"], new_id, link["id"]))
        counts = {"ref": sum(link["kind"] == "ref" for link in live), "owner": sum(link["kind"] == "owner" for link in live)}
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="INVITES_ROTATED", key=key,
                  new=counts)
    return counts


# ---------------------------------------------------------------- using a link


def parse(param: str | None) -> tuple[str, str] | None:
    """('ref' | 'owner', token) for a /start or startapp parameter that looks like an invitation."""
    param = (param or "").strip()
    for kind, prefix in PREFIX.items():
        if param.startswith(prefix) and len(param) > len(prefix):
            return kind, param[len(prefix):]
    return None


def resolve(conn: sqlite3.Connection, param: str | None, now: datetime | None = None) -> tuple[dict | None, str]:
    """(link, state); state 'none' — no invitation parameter, 'unknown' — a token we never issued
    (e.g. an old ref_<code> from before managed links: treated as an outdated link)."""
    parsed = parse(param)
    if parsed is None:
        return None, "none"
    kind, token = parsed
    row = conn.execute("SELECT * FROM invite_links WHERE kind = ? AND token = ?", (kind, token)).fetchone()
    if row is None:
        return None, "unknown"
    link = dict(row)
    if link["kind"] == "ref":
        referrer = svc.get_user(conn, link["user_id"])
        if referrer is None or referrer["status"] != "approved" or referrer["account_state"] == "blocked":
            return link, "revoked"
    return link, state(link, now)


def use(conn: sqlite3.Connection, telegram_id: int, link: dict, now: datetime | None = None) -> bool:
    """Bind the person to the link; checked again inside the transaction, so two people can't take the last use.
    The same person again is fine and costs nothing."""
    now = _now(now)
    with transaction(conn):
        fresh = get(conn, link["id"])
        if conn.execute("SELECT 1 FROM invite_uses WHERE link_id = ? AND telegram_id = ?",
                        (link["id"], telegram_id)).fetchone():
            return True
        if fresh is None or state(fresh, now) != "active":
            return False
        if fresh["kind"] == "ref" and svc.get_user(conn, fresh["user_id"])["telegram_id"] == telegram_id:
            return False  # one's own link
        conn.execute("INSERT INTO invite_uses (link_id, telegram_id, used_at) VALUES (?, ?, ?)",
                     (link["id"], telegram_id, _iso(now)))
        conn.execute("UPDATE invite_links SET uses = uses + 1 WHERE id = ?", (link["id"],))
        if fresh["kind"] == "ref":
            conn.execute("""INSERT INTO referral_hits (telegram_id, referrer_id, created_at, link_id) VALUES (?, ?, ?, ?)
                            ON CONFLICT(telegram_id) DO UPDATE SET referrer_id = excluded.referrer_id,
                                                                   link_id = excluded.link_id""",
                         (telegram_id, fresh["user_id"], _iso(now), fresh["id"]))
    return True


def _bound(conn, telegram_id: int, kind: str) -> dict | None:
    """The person's link of this kind. A link explicitly revoked by an owner closes unfinished requests;
    a link replaced by «Обновить все ссылки» does not — they had already come in."""
    row = conn.execute(
        """SELECT l.* FROM invite_uses u JOIN invite_links l ON l.id = u.link_id
           WHERE u.telegram_id = ? AND l.kind = ? AND (l.revoked_at IS NULL OR l.replaced_by IS NOT NULL OR l.kind = 'ref')
           ORDER BY u.used_at DESC LIMIT 1""", (telegram_id, kind)).fetchone()
    return dict(row) if row else None


def owner_invited(conn: sqlite3.Connection, telegram_id: int) -> dict | None:
    return _bound(conn, telegram_id, "owner")


def referred(conn: sqlite3.Connection, telegram_id: int) -> dict | None:
    """The referrer this person came with (through a managed link)."""
    row = conn.execute("SELECT referrer_id FROM referral_hits WHERE telegram_id = ? AND link_id IS NOT NULL",
                       (telegram_id,)).fetchone()
    return svc.get_user(conn, row[0]) if row else None


def entry(conn: sqlite3.Connection, telegram_id: int) -> str | None:
    """How a stranger may come in: 'ref' (the questionnaire), 'owner' (a request in the chat) or None."""
    if referred(conn, telegram_id):
        return "ref"
    if owner_invited(conn, telegram_id):
        return "owner"
    return None


def mark_registered(conn: sqlite3.Connection, telegram_id: int, user_id: int) -> None:
    """Inside the caller's transaction: who the uses turned into."""
    conn.execute("UPDATE invite_uses SET user_id = ? WHERE telegram_id = ? AND user_id IS NULL", (user_id, telegram_id))


def registered(conn: sqlite3.Connection, telegram_id: int, user: dict) -> dict:
    """A request made in the chat: tie the uses to the account and keep the referrer, if any."""
    ref = referred(conn, telegram_id)
    with transaction(conn):
        mark_registered(conn, telegram_id, user["id"])
        if ref is not None:
            conn.execute("UPDATE users SET referrer_id = COALESCE(referrer_id, ?) WHERE id = ?", (ref["id"], user["id"]))
    return svc.get_user(conn, user["id"])


# ---------------------------------------------------------------- strangers


def log_attempt(conn: sqlite3.Connection, tg: dict, kind: str, param: str | None = None,
                now: datetime | None = None) -> dict:
    """One row per person, kind and parameter; repeats bump the counter and the last time."""
    at = _iso(_now(now))
    param = (param or "")[:80]
    with transaction(conn):
        conn.execute(
            """INSERT INTO access_attempts (telegram_id, kind, param, username, first_name, last_name, first_at, last_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(telegram_id, kind, param) DO UPDATE SET count = count + 1, last_at = excluded.last_at,
                   username = excluded.username, first_name = excluded.first_name, last_name = excluded.last_name""",
            (tg["id"], kind, param, tg.get("username"), tg.get("first_name"), tg.get("last_name"), at, at))
        row = conn.execute("SELECT * FROM access_attempts WHERE telegram_id = ? AND kind = ? AND param = ?",
                           (tg["id"], kind, param)).fetchone()
    return dict(row)


def admit(conn: sqlite3.Connection, tg: dict, param: str | None, now: datetime | None = None) -> tuple[str, str | None]:
    """/start of a person without an account: ('in', 'ref' | 'owner') — the questionnaire or the request;
    ('silent', None) — no answer, only the log; ('stale', None) — «ссылка неактуальна» (once per link).

    ТЗ 30.10 §3: an earlier /start without a link closes nothing — a live link later binds the person as usual. A
    person already bound to a live invitation (opened a link before, or an owner let them in by hand) is let in
    even when the link tapped now has expired or was rotated: before, they got «ссылка неактуальна» although the
    questionnaire was open to them."""
    link, st = resolve(conn, param, now)
    if st == "active" and use(conn, tg["id"], link, now):
        return "in", link["kind"]
    kind = entry(conn, tg["id"])
    if kind is not None:
        return "in", kind
    if st == "none":
        log_attempt(conn, tg, "no_link", now=now)
        return "silent", None
    attempt = log_attempt(conn, tg, "bad_link", param, now)
    return ("stale" if should_answer_stale(conn, attempt) else "silent"), None


def should_answer_stale(conn: sqlite3.Connection, attempt: dict) -> bool:
    """«Ссылка неактуальна» only on the first visit with this link."""
    with transaction(conn):
        changed = conn.execute("UPDATE access_attempts SET answered = 1 WHERE id = ? AND answered = 0",
                               (attempt["id"],)).rowcount
    return bool(changed)


def attempts(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        """SELECT a.*, (SELECT 1 FROM users u WHERE u.telegram_id = a.telegram_id) AS known
           FROM access_attempts a ORDER BY a.last_at DESC LIMIT ?""", (limit,))]
    names = {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute(
        "SELECT telegram_id, first_name, last_name FROM users WHERE telegram_id IN (SELECT restored_by FROM access_attempts)")}
    for r in rows:
        r["known"] = bool(r["known"])
        r["param"] = r["param"][:4] + "…" if r["param"] else ""  # the kind of link, not the token itself
        # ТЗ 30.10 §3: where the person stands now and whether an owner let them in by hand
        r["access"] = access_state(conn, r["telegram_id"])
        r["restored_by_name"] = names.get(r["restored_by"]) if r.get("restored_by") else None
    return rows


def access_state(conn: sqlite3.Connection, telegram_id: int) -> str:
    """ТЗ 30.10 §3: blocked / member / rejected / applied / invited (may fill the questionnaire or the request) / none."""
    user = svc.get_user_by_tg(conn, telegram_id)
    if user is not None:
        if user["account_state"] == "blocked":
            return "blocked"
        if user["status"] == "approved":
            return "member"
        if user["status"] == "rejected":
            return "rejected"
        if conn.execute("SELECT 1 FROM applications WHERE user_id = ? AND status IN ('new','scheduled')",
                        (user["id"],)).fetchone():
            return "applied"
        return "invited"
    return "invited" if entry(conn, telegram_id) else "none"


RESTORE_REFUSED = {
    "blocked": ("Человек заблокирован владельцем или администратором — сначала снимите блокировку в «Команде»", "blocked"),
    "member": ("Человек уже в команде", "already_member"),
    "rejected": ("Заявка этого человека отклонена — верните её в «Онбординг» → «Отклонённые» → «Вернуть в резерв»",
                 "rejected"),
    "applied": ("Анкета уже отправлена — кандидат в «Онбординге»", "already_applied"),
}


def restore_access(conn: sqlite3.Connection, actor: dict, telegram_id: int, referrer_id: int | None, key: str,
                   now: datetime | None = None) -> dict:
    """ТЗ 30.10 §3, часть 2: «Разрешить доступ / Отправить в онбординг» from «Попытки доступа» — only an owner, with a
    confirmation in the app. The person is bound to a live referral link of the chosen employee (by default the
    owner themself) — the same binding a link gives, so the usual questionnaire and the referral follow. The existing
    record (if any) is kept and updated; real blocks and refusals are not bypassed. Who, when and the result are
    written to access_attempts and the event log. Returns {link, referrer, state}."""
    _check_key(key)
    now = _now(now)
    actor = _owner(conn, actor)
    if not conn.execute("SELECT 1 FROM access_attempts WHERE telegram_id = ?", (telegram_id,)).fetchone():
        raise ServiceError("Этого человека нет в «Попытках доступа»", 404, "not_found")
    st = access_state(conn, telegram_id)
    if st in RESTORE_REFUSED:
        message, code = RESTORE_REFUSED[st]
        raise ServiceError(message, 409, code)
    referrer = svc.get_user(conn, referrer_id or actor["id"])
    if (referrer is None or referrer["status"] != "approved" or referrer["account_state"] == "blocked"
            or referrer["telegram_id"] == telegram_id):
        raise ServiceError("Выберите действующего сотрудника, от чьего имени приглашение", 422, "bad_referrer")
    link = current_ref(conn, referrer, now)
    if not use(conn, telegram_id, link, now):
        raise ServiceError("Не удалось выдать приглашение — попробуйте ещё раз", 409, "link_failed")
    with transaction(conn):
        # the binding is the referrer's (a later referral link of someone else must not take it over silently)
        conn.execute("""INSERT INTO referral_hits (telegram_id, referrer_id, created_at, link_id) VALUES (?, ?, ?, ?)
                        ON CONFLICT(telegram_id) DO UPDATE SET referrer_id = excluded.referrer_id, link_id = excluded.link_id""",
                     (telegram_id, referrer["id"], _iso(now), link["id"]))
        user = svc.get_user_by_tg(conn, telegram_id)
        if user is not None:  # a record without a questionnaire (returned to the reserve): updated, never a second one
            mark_registered(conn, telegram_id, user["id"])
            conn.execute("UPDATE users SET referrer_id = COALESCE(referrer_id, ?) WHERE id = ?", (referrer["id"], user["id"]))
        conn.execute("""UPDATE access_attempts SET restored_at = ?, restored_by = ?, restore_result = 'granted'
                        WHERE telegram_id = ?""", (_iso(now), actor["telegram_id"], telegram_id))
        if not _seen(conn, key):
            log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="ACCESS_RESTORED", key=key,
                      new={"telegram_id": telegram_id, "referrer": referrer["id"], "link": link["id"]})
    return {"link": get(conn, link["id"]), "referrer": referrer, "state": access_state(conn, telegram_id)}


def restore_result(conn: sqlite3.Connection, telegram_id: int, delivered: bool) -> None:
    """After the bot tried to write: «sent» or «not_delivered» (the person never opened the chat, or blocked the bot)."""
    with transaction(conn):
        conn.execute("UPDATE access_attempts SET restore_result = ? WHERE telegram_id = ? AND restored_at IS NOT NULL",
                     ("sent" if delivered else "not_delivered", telegram_id))


def qr_svg(text: str) -> str:
    import segno

    # viewBox (omitsize) — the picture scales to any width instead of being cut off; the standard 4-module white border
    svg = segno.make(text, error="m").svg_inline(border=4, omitsize=True, dark="#111827", light="#ffffff")
    return svg.replace("<svg ", '<svg shape-rendering="crispEdges" ', 1)


def ref_view(conn: sqlite3.Connection, user: dict, bot_username: str | None, now: datetime | None = None) -> dict:
    link = current_ref(conn, user, now)
    return {**public(link, bot_username, now), "seconds_left": max(0, int(
        (datetime.fromisoformat(link["expires_at"]) - _now(now)).total_seconds()))}

