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


def came_by_questionnaire(conn: sqlite3.Connection) -> dict[int, str]:
    """user_id → the status of their latest questionnaire (people who came by an employee's referral link)."""
    return {r[0]: r[1] for r in conn.execute(
        """SELECT a.user_id, a.status FROM applications a WHERE a.id = (SELECT MAX(b.id) FROM applications b
           WHERE b.user_id = a.user_id AND b.status != 'returned')""")}


def expected_for(conn: sqlite3.Connection, user: dict, apps: dict[int, str] | None = None) -> str | None:
    """ТЗ 28.10 §6 (В11–В12): a candidate — «Предзакреп» once the training is agreed, the main group only after
    «Принять в команду»; people without a questionnaire (an owner's invitation, the old team) — the main group."""
    apps = came_by_questionnaire(conn) if apps is None else apps
    status = apps.get(user["id"])
    if status is None:
        return "main"
    return {"scheduled": "pre", "internship": "pre", "done": "main"}.get(status)


async def invite_person(conn: sqlite3.Connection, bot, notifier, user: dict, kind: str, text: str,
                        actor: dict | None) -> str:
    """One personal invitation with a button, only when Telegram says the person isn't in the group yet.
    Returns sent | member | no_group | failed (the admins are told about a failure)."""
    g = chat(conn, kind)
    if g is None or bot is None:
        return "no_group"
    state = await member_state(bot, g["chat_id"], user["telegram_id"])
    with transaction(conn):
        _set_state(conn, user["id"], kind, state)
    if state == "member":
        return "member"
    try:
        inv = await invite(conn, bot, user, kind, actor)
    except ServiceError as exc:
        await notifier.to_admins(f"⚠️ Не удалось пригласить {svc.full_name(user)} в «{KINDS[kind]}»: {exc.message}")
        return "failed"
    return "sent" if await notifier.group_invite_button(user, kind, inv, text) else "failed"


async def kick(conn: sqlite3.Connection, bot, notifier, user: dict, kinds: tuple[str, ...], actor: dict | None,
               attempts: int = 3) -> dict[str, str]:
    """ТЗ 28.10 §6, §16: out of the work groups where Telegram says the person is. A network hiccup is retried;
    when it still fails the admins are told and the result is in the person's history. kind → removed / not_member /
    no_group / failed."""
    import asyncio

    out = {}
    for kind in kinds:
        g = chat(conn, kind)
        if g is None or bot is None:
            out[kind] = "no_group"
            continue
        state = await member_state(bot, g["chat_id"], user["telegram_id"])
        if state == "out":
            with transaction(conn):
                _set_state(conn, user["id"], kind, "out")
            out[kind] = "not_member"
            continue
        result = "failed"
        for attempt in range(attempts):
            try:
                await remove(conn, bot, user, kind, actor)
                result = "removed"
                break
            except ServiceError:
                if attempt + 1 < attempts:
                    await asyncio.sleep(1 + attempt)
        out[kind] = result
        if result == "failed":
            with transaction(conn):
                log_event(conn, user_id=user["id"], actor_id=actor["telegram_id"] if actor else 0, event_type="GROUP_REMOVE_FAILED",
                          key=svc.new_key(), new={"kind": kind})
            await notifier.to_admins(f"⚠️ Не удалось исключить {svc.full_name(user)} из «{KINDS[kind]}»: у бота нет права "
                                     "банить участников или Telegram не ответил. Исключите вручную.")
    return out


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
        for u in board_people(conn):
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
    "invite_pre": "Пригласить в «Предзакреп»",
    "invite_main": "Пригласить в «Основную команду»",
    "remove_pre": "Убрать из «Предзакрепа»",
    "remove_main": "Убрать из «Основной команды»",
    "allow_both": "Можно в обеих группах",
    "not_newbie": "Не новичок — подтвердить",
    "check": "Сверить членство",
}
MODES = {"pre": ("pre",), "main": ("main",), "both": ("pre", "main")}
MODE_TITLES = {None: "Авто", "pre": "Только «Предзакреп»", "main": "Только «Основная команда»", "both": "Обе группы"}


def required(conn: sqlite3.Connection, user: dict, apps: dict[int, str] | None = None) -> tuple[str, ...]:
    """ТЗ 28.10 §15: the groups this person must be in. The admin's choice for the person wins; «Не новичок» —
    the main group; otherwise by the candidate's step (В11–В12). A blocked person — none."""
    if user["account_state"] == "blocked" or user["status"] == "rejected":
        return ()
    if user.get("group_mode") in MODES:
        return MODES[user["group_mode"]]
    if user.get("not_newbie_at"):
        return ("main",)
    want = expected_for(conn, user, apps)
    return (want,) if want else ()


def board_people(conn: sqlite3.Connection) -> list[dict]:
    """Workers, candidates whose training is under way, and blocked people Telegram still shows in a group."""
    apps = came_by_questionnaire(conn)
    st = states(conn)
    out = []
    for u in svc.list_users(conn):
        if u["status"] == "approved" and u["account_state"] != "blocked" and perms.role_of(u) in (
                perms.EMPLOYEE, perms.TRAINER, perms.INSTRUCTOR):
            out.append(u)
        elif u["status"] == "pending" and apps.get(u["id"]) == "scheduled" and u["account_state"] != "blocked":
            out.append(u)
        elif any((st.get((u["id"], k)) or {}).get("state") == "member" for k in KINDS) and not perms.is_owner(u):
            out.append(u)  # blocked / refused, still inside
    return out


def board(conn: sqlite3.Connection) -> dict:
    threshold = app_settings.get_all(conn)["main_team_days"]
    days, st, apps = work_days(conn), states(conn), came_by_questionnaire(conn)
    connected = {k: chat(conn, k) for k in KINDS}
    rows = []
    for u in board_people(conn):
        count, first = days.get(u["id"], (0, None))
        need = required(conn, u, apps)
        member = {k: (st.get((u["id"], k)) or {}).get("state", "unknown") if connected[k] else None for k in KINDS}
        checked = max(((st.get((u["id"], k)) or {}).get("checked_at") or "" for k in KINDS), default="") or None
        blocked = u["account_state"] == "blocked" or u["status"] == "rejected"
        issues = []
        for k in need:
            if member[k] == "out":
                issues.append({"text": f"Не хватает: «{KINDS[k]}»", "actions": [f"invite_{k}"]})
            elif member[k] == "unknown":
                issues.append({"text": f"«{KINDS[k]}»: членство не проверено", "actions": ["check"]})
        for k in KINDS:
            if k in need or member[k] != "member":
                continue
            if blocked:
                issues.append({"text": f"Заблокирован, но состоит в «{KINDS[k]}»", "actions": [f"remove_{k}"]})
            elif k == "pre" and "main" in need:
                issues.append({"text": "Состоит в обеих группах" if member["main"] == "member" else "Остался в «Предзакрепе»",
                               "actions": ["remove_pre", "allow_both"]})
            elif k == "main" and "pre" in need:
                issues.append({"text": "Новичок в основной команде", "actions": ["not_newbie", "remove_main"]})
            else:
                issues.append({"text": f"В «{KINDS[k]}» раньше времени (обучение не согласовано)", "actions": [f"remove_{k}"]})
        invites = {k: last_invite(conn, u["id"], k) for k in KINDS}
        app_status = apps.get(u["id"])
        rows.append({
            "user_id": u["id"], "name": svc.full_name(u), "username": u["username"], "telegram_id": u["telegram_id"],
            "days": count, "first_day": first, "member": member, "checked_at": checked, "required": list(need),
            "mode": u.get("group_mode"), "mode_title": MODE_TITLES.get(u.get("group_mode"), "Авто"),
            "not_newbie": bool(u.get("not_newbie_at")), "blocked": blocked,
            "who": "blocked" if blocked else "candidate" if u["status"] == "pending" else
                   "trainee" if app_status == "internship" else "employee",
            "expected": need[0] if len(need) == 1 else ("both" if need else None),  # kept for old clients
            "invites": {k: ({"created_at": i["created_at"], "used_at": i["used_at"], "expires_at": i["expires_at"],
                             "revoked": bool(i["revoked_at"]), "auto": bool(i["auto"])} if i else None)
                        for k, i in invites.items()},
            "issues": [i["text"] for i in issues], "problems": issues,
            "action": issues[0]["actions"][0] if issues else None,
            "action_label": ACTIONS.get(issues[0]["actions"][0]) if issues else None,
        })
    rows.sort(key=lambda r: (not r["issues"], r["name"]))
    return {"threshold": threshold, "groups": {k: g and {"title": g["title"], "chat_id": g["chat_id"]} for k, g in connected.items()},
            "items": rows, "actions": ACTIONS, "modes": {str(k): v for k, v in MODE_TITLES.items()}}


def set_mode(conn: sqlite3.Connection, actor: dict, user_id: int, mode: str | None, key: str) -> dict:
    """§15 (В13–В14): the admin decides per person — «Предзакреп», the main group, both, or automatic."""
    if mode not in (None, *MODES):
        raise ServiceError("Неизвестный режим", 422, "bad_mode")
    svc._check_key(key)
    with transaction(conn):
        if svc._seen(conn, key):
            return svc.get_user(conn, user_id)
        actor = svc._fresh_actor(conn, actor)
        user = svc.get_user(conn, user_id)
        if user is None or not perms.can(actor, "groups"):
            raise ServiceError("Нет прав", 403, "forbidden")
        conn.execute("UPDATE users SET group_mode = ? WHERE id = ?", (mode, user_id))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type="GROUP_MODE_SET", key=key,
                  old={"mode": user.get("group_mode")}, new={"mode": mode})
    return svc.get_user(conn, user_id)


def set_not_newbie(conn: sqlite3.Connection, actor: dict, user_id: int, value: bool, key: str) -> dict:
    """§15: «Не новичок — подтвердить» (and its undo). Roles and rights stay as they are."""
    svc._check_key(key)
    with transaction(conn):
        if svc._seen(conn, key):
            return svc.get_user(conn, user_id)
        actor = svc._fresh_actor(conn, actor)
        user = svc.get_user(conn, user_id)
        if user is None or not perms.can(actor, "groups"):
            raise ServiceError("Нет прав", 403, "forbidden")
        conn.execute("UPDATE users SET not_newbie_at = ?, not_newbie_by = ? WHERE id = ?",
                     (now_iso() if value else None, actor["telegram_id"] if value else None, user_id))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], key=key,
                  event_type="NOT_NEWBIE_SET" if value else "NOT_NEWBIE_CLEARED")
    return svc.get_user(conn, user_id)


def keeps_both(conn: sqlite3.Connection, user: dict) -> bool:
    """Joining the main group moves a person out of «Предзакреп» — unless the admin allowed both (В13)."""
    return "pre" in required(conn, user)


async def auto_invites(conn: sqlite3.Connection, bot, notify) -> list[tuple[dict, str]]:
    """Once per person: people without a questionnaire get «Основная команда» (ТЗ 28.10 В11); candidates are invited
    by their steps instead. Telegram is asked first, so a person already inside gets nothing."""
    cfg = app_settings.get_all(conn)
    if bot is None or not cfg["groups_auto_invite"]:
        return []
    st, apps = states(conn), came_by_questionnaire(conn)
    sent = []
    for u in workers(conn):
        if u["id"] in apps:  # candidates are invited by their own steps: training agreed, «Принять в команду» (§6)
            continue
        need = required(conn, u, apps)  # ТЗ 28.10 В11: without a questionnaire — straight to the main group
        if not need:
            continue
        kind = "main" if "main" in need else need[0]
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
