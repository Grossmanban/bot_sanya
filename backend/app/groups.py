"""Telegram groups by real work days (ТЗ 27.10 §26–27).

    «Предзакреп» (pre)   — newcomers: 0–2 confirmed work days
    «Основная команда» (main) — from the 3rd confirmed day (`main_team_days`)
A work day is a calendar date with an approved report (an admin's approval, the team's auto-approval «команда
выполнила план» and training shifts count — agreed with the owner); several reports on one date are one day.
Nothing new is reported: the existing approved reports are counted.

Invitations are personal: the bot makes a link that only sends a *join request* (creates_join_request) and
approves the request only from the person it was made for — a forwarded link lets nobody else in (§27 and the
owner's note about leaked links). An invitation sent is not membership: membership is what Telegram says
(getChatMember); when it can't be checked it stays «unknown», never guessed.

The bot must be an admin of both groups (invite users; ban users — to move people out of «Предзакреп»).
The groups are connected by an owner with /group_pre and /group_main inside each group.
"""

import logging
import sqlite3
from datetime import datetime, timedelta

from aiogram.exceptions import TelegramAPIError

from . import app_settings
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ACTIVE, ServiceError, log_event
from .timeutil import now_iso, now_local

log = logging.getLogger(__name__)

KINDS = {"pre": "Предзакреп", "main": "Основная команда"}
INVITE_DAYS = 7
MEMBER = ("creator", "administrator", "member")


# ---------------------------------------------------------------- connected groups


def chat(conn: sqlite3.Connection, kind: str) -> dict | None:
    chat_id = app_settings.get_raw(conn, f"group_{kind}_chat_id")
    if not chat_id:
        return None
    return {"kind": kind, "chat_id": int(chat_id), "title": app_settings.get_raw(conn, f"group_{kind}_title") or KINDS[kind]}


def connect(conn: sqlite3.Connection, kind: str, chat_id: int | None, title: str | None, actor_tg: int) -> None:
    if kind not in KINDS:
        raise ServiceError("Неизвестная группа", 422, "bad_group")
    other = chat(conn, "main" if kind == "pre" else "pre")
    if chat_id is not None and other and other["chat_id"] == chat_id:
        raise ServiceError("Эта группа уже подключена как другая", 409, "same_chat")
    app_settings.set_raw(conn, f"group_{kind}_chat_id", str(chat_id) if chat_id is not None else None)
    app_settings.set_raw(conn, f"group_{kind}_title", title if chat_id is not None else None)
    actor = svc.get_user_by_tg(conn, actor_tg)
    if actor is not None:
        with transaction(conn):
            log_event(conn, user_id=actor["id"], actor_id=actor_tg, key=svc.new_key(),
                      event_type="GROUP_CONNECTED" if chat_id else "GROUP_DISCONNECTED",
                      new={"kind": kind, "chat": chat_id, "title": title})


# ---------------------------------------------------------------- work days


def work_days(conn: sqlite3.Connection) -> dict[int, tuple[int, str]]:
    """user_id → (confirmed work days, the first such date)."""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        f"""SELECT user_id, COUNT(DISTINCT shift_date), MIN(shift_date) FROM shifts
            WHERE status IN {ACTIVE} AND report_state = 'approved' GROUP BY user_id""")}


def expected(days: int, threshold: int) -> str:
    return "main" if days >= threshold else "pre"


def workers(conn: sqlite3.Connection) -> list[dict]:
    """Who the groups are for: approved people who work shifts (owners and admins aside), not blocked."""
    return [u for u in svc.list_users(conn)
            if u["status"] == "approved" and u["account_state"] != "blocked"
            and perms.role_of(u) in (perms.EMPLOYEE, perms.TRAINER, perms.INSTRUCTOR)]


# ---------------------------------------------------------------- membership and invitations


def states(conn: sqlite3.Connection) -> dict[tuple[int, str], dict]:
    return {(r["user_id"], r["kind"]): dict(r) for r in conn.execute("SELECT * FROM group_members")}


def _set_state(conn, user_id: int, kind: str, state: str) -> None:
    conn.execute("""INSERT INTO group_members (user_id, kind, state, checked_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, kind) DO UPDATE SET state = excluded.state, checked_at = excluded.checked_at""",
                 (user_id, kind, state, now_iso()))


def open_invite(conn: sqlite3.Connection, user_id: int, kind: str) -> dict | None:
    row = conn.execute("""SELECT * FROM group_invites WHERE user_id = ? AND kind = ? AND used_at IS NULL
                          AND revoked_at IS NULL AND expires_at > ? ORDER BY id DESC LIMIT 1""",
                       (user_id, kind, now_iso())).fetchone()
    return dict(row) if row else None


def last_invite(conn: sqlite3.Connection, user_id: int, kind: str) -> dict | None:
    row = conn.execute("SELECT * FROM group_invites WHERE user_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
                       (user_id, kind)).fetchone()
    return dict(row) if row else None


async def member_state(bot, chat_id: int, telegram_id: int) -> str:
    """member / out / unknown — what Telegram says, or «unknown» when it can't be checked."""
    try:
        m = await bot.get_chat_member(chat_id, telegram_id)
    except TelegramAPIError:
        return "unknown"
    if m.status in MEMBER or (m.status == "restricted" and getattr(m, "is_member", False)):
        return "member"
    return "out"


async def bot_rights(bot, chat_id: int) -> dict:
    """Is the bot an admin here, may it invite and remove people (§27: check, don't assume)."""
    try:
        me = await bot.get_chat_member(chat_id, bot.id)
    except TelegramAPIError as exc:
        return {"ok": False, "admin": False, "invite": False, "remove": False, "error": str(exc)[:200]}
    admin = me.status in ("administrator", "creator")
    return {"ok": True, "admin": admin,
            "invite": admin and (me.status == "creator" or bool(getattr(me, "can_invite_users", False))),
            "remove": admin and (me.status == "creator" or bool(getattr(me, "can_restrict_members", False))),
            "error": None}


async def check(conn: sqlite3.Connection, bot, user_ids: set[int] | None = None) -> int:
    """Ask Telegram about everyone (or these people) in both groups; returns how many answers were stored."""
    n = 0
    for kind in KINDS:
        g = chat(conn, kind)
        if g is None:
            continue
        for u in workers(conn):
            if user_ids is not None and u["id"] not in user_ids:
                continue
            state = await member_state(bot, g["chat_id"], u["telegram_id"])
            with transaction(conn):
                _set_state(conn, u["id"], kind, state)
            n += 1
    return n


async def invite(conn: sqlite3.Connection, bot, user: dict, kind: str, actor: dict | None, auto: bool = False) -> dict:
    """A personal link: it only sends a join request, which the bot approves for this person alone."""
    g = chat(conn, kind)
    if g is None:
        raise ServiceError(f"Группа «{KINDS[kind]}» не подключена", 409, "no_group")
    if bot is None:
        raise ServiceError("Бот не запущен", 409, "no_bot")
    expires = now_local() + timedelta(days=INVITE_DAYS)
    try:
        link = await bot.create_chat_invite_link(g["chat_id"], name=f"u{user['id']} {svc.full_name(user)}"[:32],
                                                 expire_date=expires, creates_join_request=True)
    except TelegramAPIError as exc:
        raise ServiceError("Бот не может создать ссылку: сделайте его администратором группы с правом приглашать",
                           409, "no_rights") from exc
    with transaction(conn):
        conn.execute("UPDATE group_invites SET revoked_at = ? WHERE user_id = ? AND kind = ? AND used_at IS NULL "
                     "AND revoked_at IS NULL", (now_iso(), user["id"], kind))
        cur = conn.execute(
            """INSERT INTO group_invites (user_id, kind, link, created_at, expires_at, auto, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], kind, link.invite_link, now_iso(), expires.isoformat(timespec="seconds"), int(auto),
             actor["telegram_id"] if actor else None))
        log_event(conn, user_id=user["id"], actor_id=actor["telegram_id"] if actor else 0, event_type="GROUP_INVITED",
                  key=svc.new_key(), new={"kind": kind, "invite": cur.lastrowid, "auto": auto})
    return dict(conn.execute("SELECT * FROM group_invites WHERE id = ?", (cur.lastrowid,)).fetchone())


async def remove(conn: sqlite3.Connection, bot, user: dict, kind: str, actor: dict | None) -> None:
    """Out of the group (ban + unban: they may come back later by a new invitation)."""
    g = chat(conn, kind)
    if g is None or bot is None:
        raise ServiceError(f"Группа «{KINDS[kind]}» не подключена", 409, "no_group")
    try:
        await bot.ban_chat_member(g["chat_id"], user["telegram_id"])
        await bot.unban_chat_member(g["chat_id"], user["telegram_id"], only_if_banned=True)
    except TelegramAPIError as exc:
        raise ServiceError("Бот не может удалить участника: нужно право банить в этой группе", 409, "no_rights") from exc
    with transaction(conn):
        _set_state(conn, user["id"], kind, "out")
        log_event(conn, user_id=user["id"], actor_id=actor["telegram_id"] if actor else 0, event_type="GROUP_REMOVED",
                  key=svc.new_key(), new={"kind": kind})


async def join_request(conn: sqlite3.Connection, bot, chat_id: int, telegram_id: int, link: str | None) -> tuple[bool, dict | None]:
    """A join request: approved only for the person the link was made for. Returns (approved, the person)."""
    kind = next((k for k in KINDS if (chat(conn, k) or {}).get("chat_id") == chat_id), None)
    if kind is None:
        return False, None
    row = conn.execute("SELECT * FROM group_invites WHERE link = ? AND kind = ?", (link or "", kind)).fetchone()
    user = svc.get_user(conn, row["user_id"]) if row else None
    ok = (row is not None and user is not None and user["telegram_id"] == telegram_id and row["revoked_at"] is None
          and user["account_state"] != "blocked")
    try:
        if ok:
            await bot.approve_chat_join_request(chat_id, telegram_id)
        else:
            await bot.decline_chat_join_request(chat_id, telegram_id)
    except TelegramAPIError:
        log.warning("join request %s in %s: could not answer", telegram_id, chat_id)
        return False, user
    with transaction(conn):
        if ok:
            conn.execute("UPDATE group_invites SET used_at = ? WHERE id = ?", (now_iso(), row["id"]))
            _set_state(conn, user["id"], kind, "member")
        if user is not None:  # someone else with this person's link — written to the person's history
            log_event(conn, user_id=user["id"], actor_id=telegram_id,
                      event_type="GROUP_JOIN_APPROVED" if ok else "GROUP_JOIN_DECLINED", key=svc.new_key(),
                      new={"kind": kind, "invite": row["id"], "by": telegram_id})
    return ok, user


# ---------------------------------------------------------------- the board and the reconciliation


ACTIONS = {
    "invite_pre": "Пригласить в предзакреп",
    "invite_main": "Пригласить в основную команду",
    "move": "Перевести: пригласить в основную и убрать из предзакрепа",
    "remove_main": "Новичок в основной команде — проверить",
    "remove_pre": "Убрать из предзакрепа",
    "check": "Сверить членство",
}


def board(conn: sqlite3.Connection) -> dict:
    threshold = app_settings.get_all(conn)["main_team_days"]
    days, st = work_days(conn), states(conn)
    connected = {k: chat(conn, k) for k in KINDS}
    rows = []
    for u in workers(conn):
        count, first = days.get(u["id"], (0, None))
        want = expected(count, threshold)
        member = {k: (st.get((u["id"], k)) or {}).get("state", "unknown") if connected[k] else None for k in KINDS}
        checked = max(((st.get((u["id"], k)) or {}).get("checked_at") or "" for k in KINDS), default="") or None
        issues, action = [], None
        if member["pre"] == "out" and member["main"] == "out":
            issues.append("Не состоит ни в одной группе")
        if count < threshold and member["main"] == "member":
            issues.append("Новичок в основной команде")
            action = "remove_main"
        if count >= threshold and member["pre"] == "member":
            issues.append(f"Уже {count} подтв. дня — пора переводить из предзакрепа")
        if member["pre"] == "member" and member["main"] == "member":
            issues.append("Состоит в обеих группах")
        if action is None:
            if member[want] == "member":
                action = "remove_pre" if want == "main" and member["pre"] == "member" else None
            elif member[want] == "unknown" and connected[want]:
                action = "check"
            elif connected[want]:
                action = "move" if want == "main" and member["pre"] == "member" else f"invite_{want}"
        invites = {k: last_invite(conn, u["id"], k) for k in KINDS}
        rows.append({
            "user_id": u["id"], "name": svc.full_name(u), "username": u["username"], "telegram_id": u["telegram_id"],
            "days": count, "first_day": first, "expected": want, "member": member, "checked_at": checked,
            "invites": {k: ({"created_at": i["created_at"], "used_at": i["used_at"], "expires_at": i["expires_at"],
                             "revoked": bool(i["revoked_at"]), "auto": bool(i["auto"])} if i else None)
                        for k, i in invites.items()},
            "issues": issues, "action": action, "action_label": ACTIONS.get(action),
        })
    rows.sort(key=lambda r: (not r["issues"], r["action"] is None, r["name"]))
    return {"threshold": threshold, "groups": {k: g and {"title": g["title"], "chat_id": g["chat_id"]} for k, g in connected.items()},
            "items": rows}


async def auto_invites(conn: sqlite3.Connection, bot, notify) -> list[tuple[dict, str]]:
    """Once per person and group: newcomers get «Предзакреп», the third confirmed day — «Основная команда».
    Telegram is asked first, so a person already inside gets nothing. notify(user, kind, invite) sends the link."""
    cfg = app_settings.get_all(conn)
    if bot is None or not cfg["groups_auto_invite"]:
        return []
    days, st = work_days(conn), states(conn)
    sent = []
    for u in workers(conn):
        kind = expected(days.get(u["id"], (0, None))[0], cfg["main_team_days"])
        g = chat(conn, kind)
        if g is None or (st.get((u["id"], kind)) or {}).get("state") == "member":
            continue
        if conn.execute("SELECT 1 FROM group_invites WHERE user_id = ? AND kind = ? AND auto = 1",
                        (u["id"], kind)).fetchone() or open_invite(conn, u["id"], kind):
            continue  # automatically — once; later invitations are the admins' action
        state = await member_state(bot, g["chat_id"], u["telegram_id"])
        with transaction(conn):
            _set_state(conn, u["id"], kind, state)
        if state != "out":
            continue
        try:
            inv = await invite(conn, bot, u, kind, None, auto=True)
        except ServiceError:
            log.warning("auto invite to %s failed: no rights", kind)
            return sent
        await notify(u, kind, inv)
        sent.append((u, kind))
    return sent


def invite_text(kind: str, inv: dict) -> str:
    until = datetime.fromisoformat(inv["expires_at"]).strftime("%d.%m")
    if kind == "main":
        head = "🎉 У тебя 3 подтверждённых рабочих дня — добро пожаловать в «Основную команду»!"
    else:
        head = "👋 Приглашаем в группу «Предзакреп» — здесь новички команды."
    return (f"{head}\n\nНажми на ссылку и отправь заявку — бот одобрит её сразу. Ссылка личная: по ней войдёшь только ты, "
            f"пересылать её бесполезно. Действует до {until}.\n{inv['link']}")
