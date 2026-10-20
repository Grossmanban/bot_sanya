"""Goals (ТЗ модуль 5): personal KPI, company-wide and network goals.

Progress = approved report volume (incl. auto- and team-approved) inside the goal's period, so every
approval counts in every active goal at once and a correction of a report is reflected automatically.
It is computed, never accumulated: a goal created on Wednesday for Monday–Sunday already includes the
reports approved for Monday and Tuesday (ТЗ №18 «backfill»), and one report can't be counted twice.
Reports still waiting for review are shown apart («на проверке») and join the progress once approved.

    personal — one person, joined automatically;
    team     — everyone may press «Участвую»; network — people of one network.
    mode shared — the participants' volumes add up to one target; each — every participant has the target.

ТЗ №20: a running goal can be edited (lifecycle.py: rights, version). Lowering the target completes it
on the next check; raising it doesn't take «выполнено» away by itself — staff press «снять выполнение»
(only for those now below the target). «Аннулировать результат» (with a reason, e.g. dishonest work) takes
a participant out of the goal: their volume no longer counts. ТЗ №22: feed, «Архив», hiding — lifecycle.py.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from . import lifecycle, networks
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ACTIVE, ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, today_local

MAX_TITLE = 80
CRITICAL = ("target", "date_from", "date_to", "prize", "prize_type")


def get(conn: sqlite3.Connection, goal_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return dict(row) if row else None


def participants(conn: sqlite3.Connection, goal_id: int, annulled: bool = False) -> list[dict]:
    """Those who count; annulled=True — everyone, incl. those whose result was annulled."""
    return [dict(r) for r in conn.execute(
        f"""SELECT p.*, u.first_name, u.last_name, u.telegram_id FROM goal_participants p JOIN users u ON u.id = p.user_id
            WHERE p.goal_id = ? {'' if annulled else 'AND p.annulled_at IS NULL'} ORDER BY p.joined_at""", (goal_id,))]


def volumes(conn: sqlite3.Connection, user_ids: list[int], date_from: str, date_to: str) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = conn.execute(
        f"""SELECT user_id, SUM(fact_volume) AS v FROM shifts WHERE status IN {ACTIVE} AND report_state = 'approved'
            AND fact_volume IS NOT NULL AND shift_date BETWEEN ? AND ? AND user_id IN ({','.join('?' * len(user_ids))})
            GROUP BY user_id""", (date_from, date_to, *user_ids))
    return {r["user_id"]: r["v"] for r in rows}


def pending_volumes(conn: sqlite3.Connection, user_ids: list[int], date_from: str, date_to: str) -> dict[int, int]:
    """Facts sent but not reviewed yet — shown next to the progress, not in it."""
    if not user_ids:
        return {}
    rows = conn.execute(
        f"""SELECT user_id, SUM(fact_volume) AS v FROM shifts WHERE status IN {ACTIVE} AND report_state = 'pending_review'
            AND fact_volume IS NOT NULL AND shift_date BETWEEN ? AND ? AND user_id IN ({','.join('?' * len(user_ids))})
            GROUP BY user_id""", (date_from, date_to, *user_ids))
    return {r["user_id"]: r["v"] or 0 for r in rows}


def history_before(conn: sqlite3.Connection, goal: dict, user_ids: list[int]) -> dict[int, int]:
    """Approved volume for days before the goal was created: counted automatically (ТЗ №18)."""
    created = goal["created_at"][:10]
    if created <= goal["date_from"]:
        return {}
    last = min(goal["date_to"], (date.fromisoformat(created) - timedelta(days=1)).isoformat())
    return volumes(conn, user_ids, goal["date_from"], last)


def counted_so_far(conn: sqlite3.Connection, goal: dict, user_ids: list[int]) -> tuple[int, int]:
    """(approved reports, pieces) already inside the goal's period — what a new goal or a new participant
    starts with (ТЗ №18)."""
    if not user_ids:
        return 0, 0
    row = conn.execute(
        f"""SELECT COUNT(*), COALESCE(SUM(fact_volume), 0) FROM shifts WHERE status IN {ACTIVE}
            AND report_state = 'approved' AND fact_volume IS NOT NULL AND shift_date BETWEEN ? AND ?
            AND user_id IN ({','.join('?' * len(user_ids))})""", (goal["date_from"], goal["date_to"], *user_ids)).fetchone()
    return row[0], row[1]


def eligible(conn: sqlite3.Connection, goal: dict, user: dict) -> bool:
    if goal["kind"] == "personal":
        return goal["user_id"] == user["id"]
    if goal["kind"] == "network":
        return user.get("network_id") == goal["network_id"]
    return True


def view(conn: sqlite3.Connection, goal: dict, viewer: dict | None = None) -> dict:
    everyone = participants(conn, goal["id"], annulled=True)
    people = [p for p in everyone if not p["annulled_at"]]
    vols = volumes(conn, [p["user_id"] for p in people], goal["date_from"], goal["date_to"])
    total = sum(vols.values())
    me = next((p for p in people if viewer and p["user_id"] == viewer["id"]), None)
    my_annulled = next((p for p in everyone if viewer and p["user_id"] == viewer["id"] and p["annulled_at"]), None)
    mine = vols.get(viewer["id"], 0) if viewer else 0
    # the breakdown follows what the bar shows: the team's sum, or the viewer's own result («each» mode)
    if goal["mode"] == "each" and goal["kind"] != "personal" and viewer is not None:
        ids = [viewer["id"]] if me else []
    else:
        ids = [p["user_id"] for p in people]
    before = sum(history_before(conn, goal, ids).values())
    pending = sum(pending_volumes(conn, ids, goal["date_from"], goal["date_to"]).values())
    out = {
        "id": goal["id"], "title": goal["title"], "description": goal["description"], "kind": goal["kind"],
        "mode": goal["mode"], "target": goal["target"], "date_from": goal["date_from"], "date_to": goal["date_to"],
        "status": goal["status"], "network": (networks.get(conn, goal["network_id"]) or {}).get("name") if goal["network_id"] else None,
        "progress": total if goal["mode"] == "shared" else mine, "total": total,
        "participants": [{"user_id": p["user_id"], "name": svc.full_name(p), "volume": vols.get(p["user_id"], 0),
                          "done": bool(p["completed_at"]), "annulled": bool(p["annulled_at"]),
                          "annul_reason": p["annul_reason"]} for p in everyone],
        "prize": goal["prize"], "prize_type": goal["prize_type"], "prize_photo_url": lifecycle.prize_photo_url("goal", goal),
        "version": goal["version"], "visibility": goal["visibility"], "hidden_at": goal["hidden_at"],
        "completed_at": goal["completed_at"],
        # ТЗ №20: the norm was raised — «выполнено» can be taken back from those now below it
        "revocable": _revocable(goal, people, vols),
        "my_annulled": my_annulled["annul_reason"] if my_annulled else None,
        "joined": me is not None, "done": bool(me and me["completed_at"]) or (goal["status"] == "completed" and not my_annulled),
        "my_volume": mine,
        # ТЗ №18: what was counted from before the goal existed, and what is still being reviewed
        "backfill": before, "pending": pending,
    }
    if goal["kind"] == "personal" and goal["user_id"]:
        out["person"] = svc.full_name(svc.get_user(conn, goal["user_id"]))
    return out


def _revocable(goal: dict, people: list[dict], vols: dict[int, int]) -> int:
    """How many have «выполнено» but are below the target now."""
    if goal["mode"] == "shared":
        done = goal["status"] == "completed" or any(p["completed_at"] for p in people)
        return len(people) if done and sum(vols.values()) < goal["target"] else 0
    return sum(1 for p in people if p["completed_at"] and vols.get(p["user_id"], 0) < goal["target"])


def _ended(goal: dict, today: date) -> str | None:
    return goal["date_to"] if goal["date_to"] < today.isoformat() else None


def in_feed(conn: sqlite3.Connection, goal: dict, today: date | None = None) -> bool:
    today = today or today_local()
    return lifecycle.in_feed(goal, _ended(goal, today), today, lifecycle.hide_after_days(conn))


def for_user(conn: sqlite3.Connection, user: dict, today: date | None = None) -> list[dict]:
    """The feed: the person's goals, company-wide, their network's — current and recently finished (ТЗ №22)."""
    today = today or today_local()
    rows = [dict(r) for r in conn.execute("SELECT * FROM goals WHERE deleted_at IS NULL ORDER BY date_to")]
    return [view(conn, g, user) for g in rows if eligible(conn, g, user) and in_feed(conn, g, today)]


def archived(conn: sqlite3.Connection, user: dict, today: date | None = None, limit: int = 50) -> list[dict]:
    """«Архив»: hidden by hand or finished long enough ago."""
    today = today or today_local()
    rows = [dict(r) for r in conn.execute("SELECT * FROM goals WHERE deleted_at IS NULL ORDER BY date_to DESC")]
    return [view(conn, g, user) for g in rows if eligible(conn, g, user) and not in_feed(conn, g, today)][:limit]


def listing(conn: sqlite3.Connection, viewer: dict) -> list[dict]:
    """The admin list: everything not deleted, hidden ones too (with the eye crossed out)."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM goals WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 100")]
    out = []
    for g in rows:
        if not perms.sees_everyone(viewer):
            if g["kind"] == "team":
                continue
            if g["kind"] == "network" and g["network_id"] != viewer.get("network_id"):
                continue
            if g["kind"] == "personal" and not perms.manages(viewer, svc.get_user(conn, g["user_id"])):
                continue
        out.append({**view(conn, g), "in_feed": in_feed(conn, g), "can_manage": lifecycle.can_manage(conn, viewer, "goal", g)})
    return out


def _clean_title(title: str | None) -> str:
    title = " ".join((title or "").split())[:MAX_TITLE]
    if not title:
        raise ServiceError("Назовите цель", 422, "title_required")
    return title


def _check_target(target) -> None:
    if isinstance(target, bool) or not isinstance(target, int) or not 0 < target <= 1_000_000:
        raise ServiceError("Норма — целое число штук больше нуля", 422, "bad_target")


def _check_range(date_from: date, date_to: date) -> None:
    if date_to < date_from or (date_to - date_from).days > 366:
        raise ServiceError("Период от 1 дня до года", 422, "bad_range")


def create(conn: sqlite3.Connection, actor: dict, *, title: str, kind: str, target: int, date_from: date, date_to: date,
           key: str, description: str | None = None, user_id: int | None = None, network_id: int | None = None,
           mode: str = "shared", prize: str | None = None, prize_type: str | None = None,
           prize_photo: str | None = None) -> dict:
    """Owners/admins: any goal; a trainer: personal goals for their people and their network's goal (ТЗ 9.3)."""
    title = _clean_title(title)
    if kind not in ("personal", "team", "network") or mode not in ("shared", "each"):
        raise ServiceError("Некорректный тип цели", 422, "bad_kind")
    _check_target(target)
    _check_range(date_from, date_to)
    prize_photo = lifecycle.clean_file_id(prize_photo)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, conn.execute("SELECT MAX(id) FROM goals").fetchone()[0])
        actor = _fresh_actor(conn, actor)
        if not perms.is_staff(actor):
            raise ServiceError("Недостаточно прав", 403, "forbidden")
        target_user = None
        if kind == "personal":
            target_user = svc.get_user(conn, user_id) if user_id else None
            if target_user is None or not perms.manages(actor, target_user):
                raise ServiceError("Выберите сотрудника из своей команды", 403, "forbidden")
            network_id, mode = None, "each"
        elif kind == "network":
            net = networks.get(conn, network_id) if network_id else None
            if net is None or net["status"] != "active":
                raise ServiceError("Выберите сеть", 422, "network_required")
            if not perms.sees_everyone(actor) and actor.get("network_id") != network_id:
                raise ServiceError("Тренер ставит цели только своей сети", 403, "forbidden")
            user_id = None
        else:
            if not perms.sees_everyone(actor):
                raise ServiceError("Командные цели ставят владелец и администраторы", 403, "forbidden")
            user_id = network_id = None
        cur = conn.execute(
            """INSERT INTO goals (title, description, kind, mode, target, date_from, date_to, user_id, network_id,
                                  created_by, created_at, prize, prize_type, prize_photo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, (description or "").strip()[:500] or None, kind, mode, target, date_from.isoformat(),
             date_to.isoformat(), user_id, network_id, actor["telegram_id"], now_iso(),
             (prize or "").strip()[:200] or None, lifecycle.clean_prize_type(prize_type), prize_photo),
        )
        if target_user is not None:
            conn.execute("INSERT INTO goal_participants (goal_id, user_id, joined_at) VALUES (?, ?, ?)",
                         (cur.lastrowid, target_user["id"], now_iso()))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="GOAL_CREATED", key=key,
                  new={"goal": cur.lastrowid, "kind": kind, "target": target}, description=title)
    return get(conn, cur.lastrowid)


def join(conn: sqlite3.Connection, user: dict, goal_id: int, today: date | None = None) -> tuple[dict, bool]:
    """«Участвую»."""
    today = today or today_local()
    with transaction(conn):
        goal = get(conn, goal_id)
        if (goal is None or goal["status"] != "active" or goal["deleted_at"] or goal["visibility"] == "hidden"
                or not eligible(conn, goal, user)):
            raise ServiceError("Цель не найдена", 404, "not_found")
        if goal["date_to"] < today.isoformat():
            raise ServiceError("Срок цели уже прошёл", 409, "finished")
        cur = conn.execute("INSERT OR IGNORE INTO goal_participants (goal_id, user_id, joined_at) VALUES (?, ?, ?)",
                           (goal_id, user["id"], now_iso()))
    return goal, cur.rowcount == 1


@dataclass
class Update:
    goal: dict
    changed: list[str]
    critical: list[str]
    old: dict | None = None


def update(conn: sqlite3.Connection, actor: dict, goal_id: int, version: int, changes: dict, key: str) -> Update:
    """ТЗ №20: live editing; `changes` — only the fields the form sent (dates as dates)."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return Update(get(conn, goal_id), [], [])
        actor, old = lifecycle.load_for_change(conn, actor, "goal", goal_id, version)
        new: dict = {}
        if "title" in changes:
            new["title"] = _clean_title(changes["title"])
        if "description" in changes:
            new["description"] = (changes["description"] or "").strip()[:500] or None
        if "prize" in changes:
            new["prize"] = (changes["prize"] or "").strip()[:200] or None
        if "prize_type" in changes:
            new["prize_type"] = lifecycle.clean_prize_type(changes["prize_type"])
        if "prize_photo" in changes:
            new["prize_photo"] = lifecycle.clean_file_id(changes["prize_photo"])
        if changes.get("target") is not None:
            _check_target(changes["target"])
            new["target"] = changes["target"]
        for k in ("date_from", "date_to"):
            if changes.get(k) is not None:
                new[k] = changes[k].isoformat()
        new = lifecycle.diff(old, new)
        if not new:
            return Update(old, [], [])
        merged = {**old, **new}
        _check_range(date.fromisoformat(merged["date_from"]), date.fromisoformat(merged["date_to"]))
        lifecycle.save(conn, "goal", goal_id, new)
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="GOAL_UPDATED", key=key,
                  old={k: old[k] for k in new}, new={"goal": goal_id, **new}, description=merged["title"])
    return Update(get(conn, goal_id), list(new), [k for k in new if k in CRITICAL], old)


def revoke(conn: sqlite3.Connection, actor: dict, goal_id: int, key: str) -> tuple[dict, list[dict]]:
    """«Снять выполнение» after the norm was raised: only from those below the target now.
    Returns the goal and the people who lost «выполнено»."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, goal_id), []
        actor, goal = lifecycle.load_for_change(conn, actor, "goal", goal_id, None)
        people = participants(conn, goal_id)
        vols = volumes(conn, [p["user_id"] for p in people], goal["date_from"], goal["date_to"])
        if not _revocable(goal, people, vols):
            raise ServiceError("Все отметившие «выполнено» и сейчас не ниже нормы", 409, "nothing_to_revoke")
        if goal["mode"] == "shared":
            lost = [p for p in people if p["completed_at"]]
            conn.execute("UPDATE goal_participants SET completed_at = NULL WHERE goal_id = ?", (goal_id,))
            lifecycle.save(conn, "goal", goal_id, {"status": "active", "completed_at": None})
        else:
            lost = [p for p in people if p["completed_at"] and vols.get(p["user_id"], 0) < goal["target"]]
            conn.executemany("UPDATE goal_participants SET completed_at = NULL WHERE goal_id = ? AND user_id = ?",
                             [(goal_id, p["user_id"]) for p in lost])
            lifecycle.save(conn, "goal", goal_id, {"status": "active", "completed_at": None} if goal["kind"] == "personal" else {})
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="GOAL_COMPLETION_REVOKED", key=key,
                  new={"goal": goal_id, "users": [p["user_id"] for p in lost]})
    return get(conn, goal_id), lost


def annul(conn: sqlite3.Connection, actor: dict, goal_id: int, user_id: int, reason: str | None, key: str) -> tuple[dict, bool]:
    """«Аннулировать результат»: the participant's volume stops counting in this goal; the reason is shown to them."""
    reason = (reason or "").strip()[:300] or None
    if not reason:
        raise ServiceError("Укажите причину", 422, "reason_required")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, goal_id), False
        actor, goal = lifecycle.load_for_change(conn, actor, "goal", goal_id, None)
        cur = conn.execute("""UPDATE goal_participants SET annulled_at = ?, annul_reason = ?, completed_at = NULL
                              WHERE goal_id = ? AND user_id = ? AND annulled_at IS NULL""", (now_iso(), reason, goal_id, user_id))
        if cur.rowcount == 0:
            raise ServiceError("Участник не найден", 404, "not_found")
        if goal["kind"] == "personal":
            lifecycle.save(conn, "goal", goal_id, {"status": "active", "completed_at": None})
        else:
            lifecycle.save(conn, "goal", goal_id, {})
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type="GOAL_RESULT_ANNULLED", key=key,
                  new={"goal": goal_id}, description=reason)
    return get(conn, goal_id), True


def check_completions(conn: sqlite3.Connection, today: date | None = None) -> list[tuple[dict, list[dict]]]:
    """Mark reached goals ✅ (scheduler, every tick). Returns (goal, newly completed participants)."""
    today = today or today_local()
    out = []
    for g in [dict(r) for r in conn.execute(
            "SELECT * FROM goals WHERE status = 'active' AND deleted_at IS NULL AND visibility != 'hidden'")]:
        people = participants(conn, g["id"])
        if not people:
            continue
        vols = volumes(conn, [p["user_id"] for p in people], g["date_from"], g["date_to"])
        fresh = []
        with transaction(conn):
            if g["mode"] == "shared":
                if sum(vols.values()) >= g["target"]:
                    fresh = [p for p in people if not p["completed_at"]]
                    conn.execute("UPDATE goal_participants SET completed_at = ? WHERE goal_id = ? AND completed_at IS NULL",
                                 (now_iso(), g["id"]))
                    conn.execute("UPDATE goals SET status = 'completed', completed_at = ? WHERE id = ?", (now_iso(), g["id"]))
            else:
                for p in people:
                    if not p["completed_at"] and vols.get(p["user_id"], 0) >= g["target"]:
                        conn.execute("UPDATE goal_participants SET completed_at = ? WHERE goal_id = ? AND user_id = ?",
                                     (now_iso(), g["id"], p["user_id"]))
                        fresh.append(p)
                if g["kind"] == "personal" and fresh:
                    conn.execute("UPDATE goals SET status = 'completed', completed_at = ? WHERE id = ?", (now_iso(), g["id"]))
        if fresh:
            out.append((get(conn, g["id"]), fresh))
    return out
