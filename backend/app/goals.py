"""Goals (ТЗ модуль 5): personal KPI, company-wide and network goals.

Progress = approved report volume (incl. auto- and team-approved) inside the goal's period, so every
approval counts in every active goal at once and a correction of a report is reflected automatically.

    personal — one person, joined automatically;
    team     — everyone may press «Участвую»; network — people of one network.
    mode shared — the participants' volumes add up to one target; each — every participant has the target.
"""

import sqlite3
from datetime import date

from . import networks
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ACTIVE, ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, today_local

MAX_TITLE = 80


def get(conn: sqlite3.Connection, goal_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return dict(row) if row else None


def participants(conn: sqlite3.Connection, goal_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT p.*, u.first_name, u.last_name, u.telegram_id FROM goal_participants p JOIN users u ON u.id = p.user_id
           WHERE p.goal_id = ? ORDER BY p.joined_at""", (goal_id,))]


def volumes(conn: sqlite3.Connection, user_ids: list[int], date_from: str, date_to: str) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = conn.execute(
        f"""SELECT user_id, SUM(fact_volume) AS v FROM shifts WHERE status IN {ACTIVE} AND report_state = 'approved'
            AND fact_volume IS NOT NULL AND shift_date BETWEEN ? AND ? AND user_id IN ({','.join('?' * len(user_ids))})
            GROUP BY user_id""", (date_from, date_to, *user_ids))
    return {r["user_id"]: r["v"] for r in rows}


def eligible(conn: sqlite3.Connection, goal: dict, user: dict) -> bool:
    if goal["kind"] == "personal":
        return goal["user_id"] == user["id"]
    if goal["kind"] == "network":
        return user.get("network_id") == goal["network_id"]
    return True


def view(conn: sqlite3.Connection, goal: dict, viewer: dict | None = None) -> dict:
    people = participants(conn, goal["id"])
    vols = volumes(conn, [p["user_id"] for p in people], goal["date_from"], goal["date_to"])
    total = sum(vols.values())
    me = next((p for p in people if viewer and p["user_id"] == viewer["id"]), None)
    mine = vols.get(viewer["id"], 0) if viewer else 0
    out = {
        "id": goal["id"], "title": goal["title"], "description": goal["description"], "kind": goal["kind"],
        "mode": goal["mode"], "target": goal["target"], "date_from": goal["date_from"], "date_to": goal["date_to"],
        "status": goal["status"], "network": (networks.get(conn, goal["network_id"]) or {}).get("name") if goal["network_id"] else None,
        "progress": total if goal["mode"] == "shared" else mine, "total": total,
        "participants": [{"user_id": p["user_id"], "name": svc.full_name(p), "volume": vols.get(p["user_id"], 0),
                          "done": bool(p["completed_at"])} for p in people],
        "joined": me is not None, "done": bool(me and me["completed_at"]) or goal["status"] == "completed",
        "my_volume": mine,
    }
    if goal["kind"] == "personal" and goal["user_id"]:
        out["person"] = svc.full_name(svc.get_user(conn, goal["user_id"]))
    return out


def for_user(conn: sqlite3.Connection, user: dict, today: date | None = None) -> list[dict]:
    """Goals the person can see: theirs, company-wide, their network's — current and recently finished."""
    today = today or today_local()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM goals WHERE status != 'archived' AND date_to >= ? ORDER BY date_to", ((today.replace(day=1)).isoformat(),))]
    return [view(conn, g, user) for g in rows if eligible(conn, g, user)]


def listing(conn: sqlite3.Connection, viewer: dict) -> list[dict]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM goals WHERE status != 'archived' ORDER BY id DESC LIMIT 100")]
    out = []
    for g in rows:
        if not perms.sees_everyone(viewer):
            if g["kind"] == "team":
                continue
            if g["kind"] == "network" and g["network_id"] != viewer.get("network_id"):
                continue
            if g["kind"] == "personal" and not perms.manages(viewer, svc.get_user(conn, g["user_id"])):
                continue
        out.append(view(conn, g))
    return out


def create(conn: sqlite3.Connection, actor: dict, *, title: str, kind: str, target: int, date_from: date, date_to: date,
           key: str, description: str | None = None, user_id: int | None = None, network_id: int | None = None,
           mode: str = "shared") -> dict:
    """Owners/admins: any goal; a trainer: personal goals for their people and their network's goal (ТЗ 9.3)."""
    title = " ".join((title or "").split())[:MAX_TITLE]
    if not title:
        raise ServiceError("Назовите цель", 422, "title_required")
    if kind not in ("personal", "team", "network") or mode not in ("shared", "each"):
        raise ServiceError("Некорректный тип цели", 422, "bad_kind")
    if isinstance(target, bool) or not isinstance(target, int) or not 0 < target <= 1_000_000:
        raise ServiceError("Норма — целое число штук больше нуля", 422, "bad_target")
    if date_to < date_from or (date_to - date_from).days > 366:
        raise ServiceError("Период от 1 дня до года", 422, "bad_range")
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
                                  created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, (description or "").strip()[:500] or None, kind, mode, target, date_from.isoformat(),
             date_to.isoformat(), user_id, network_id, actor["telegram_id"], now_iso()),
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
        if goal is None or goal["status"] != "active" or not eligible(conn, goal, user):
            raise ServiceError("Цель не найдена", 404, "not_found")
        if goal["date_to"] < today.isoformat():
            raise ServiceError("Срок цели уже прошёл", 409, "finished")
        cur = conn.execute("INSERT OR IGNORE INTO goal_participants (goal_id, user_id, joined_at) VALUES (?, ?, ?)",
                           (goal_id, user["id"], now_iso()))
    return goal, cur.rowcount == 1


def archive(conn: sqlite3.Connection, actor: dict, goal_id: int, key: str) -> dict:
    _check_key(key)
    with transaction(conn):
        goal = get(conn, goal_id)
        if goal is None:
            raise ServiceError("Цель не найдена", 404, "not_found")
        if _seen(conn, key):
            return goal
        actor = _fresh_actor(conn, actor)
        if not (perms.sees_everyone(actor) or goal["created_by"] == actor["telegram_id"]):
            raise ServiceError("Недостаточно прав", 403, "forbidden")
        conn.execute("UPDATE goals SET status = 'archived' WHERE id = ?", (goal_id,))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="GOAL_ARCHIVED", key=key,
                  new={"goal": goal_id})
    return get(conn, goal_id)


def check_completions(conn: sqlite3.Connection, today: date | None = None) -> list[tuple[dict, list[dict]]]:
    """Mark reached goals ✅ (scheduler, every tick). Returns (goal, newly completed participants)."""
    today = today or today_local()
    out = []
    for g in [dict(r) for r in conn.execute("SELECT * FROM goals WHERE status = 'active'")]:
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
