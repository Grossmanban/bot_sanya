"""Networks (ТЗ модуль 8): teams led by a trainer. A trainer sees and acts only inside their network.

The rule itself lives in permissions.manages (same network_id). Here: creating networks,
appointing the trainer and moving people between networks — owners only. Nothing is deleted:
a network is archived once it is empty.
"""

import sqlite3

from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso

MAX_NAME = 60


def get(conn: sqlite3.Connection, network_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM networks WHERE id = ?", (network_id,)).fetchone()
    return dict(row) if row else None


def names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM networks")}


def members(conn: sqlite3.Connection, network_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM users WHERE network_id = ? AND status = 'approved' ORDER BY first_name, last_name", (network_id,))]


def listing(conn: sqlite3.Connection, viewer: dict, include_archived: bool = False) -> list[dict]:
    """The organisation tree: owners and admins see every network, a trainer only their own."""
    from . import rates

    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM networks" + ("" if include_archived else " WHERE status = 'active'") + " ORDER BY status, name")]
    if not perms.sees_everyone(viewer):
        rows = [r for r in rows if r["id"] == viewer.get("network_id")]
    out = []
    for n in rows:
        people = members(conn, n["id"])
        trainer = svc.get_user(conn, n["trainer_id"]) if n["trainer_id"] else None
        out.append({
            "id": n["id"],
            "name": n["name"],
            "status": n["status"],
            "trainer": {"id": trainer["id"], "name": svc.full_name(trainer), "username": trainer["username"],
                        "telegram_id": trainer["telegram_id"], "is_owner": perms.is_owner(trainer)} if trainer else None,
            # rates are money: owners only (customer round B)
            **({"ladder": rates.parse_ladder(n["ladder"]), "ladder_period": n["ladder_period"],
                "ladder_counts_trainer": bool(n["ladder_counts_trainer"])} if perms.can(viewer, "money") else {}),
            "members": [{"id": u["id"], "name": svc.full_name(u), "username": u["username"], "telegram_id": u["telegram_id"],
                         "role": perms.role_of(u), "work_status": u["work_status"], "account_state": u["account_state"],
                         "status_days": svc.status_days(u)}
                        for u in people if not trainer or u["id"] != trainer["id"]],
        })
    return out


def _owner(conn, actor: dict) -> dict:
    actor = _fresh_actor(conn, actor)
    if not perms.can(actor, "networks"):
        raise ServiceError("Сетями управляет только владелец", 403, "owner_only")
    return actor


def _name(conn, name: str, exclude: int | None = None) -> str:
    name = " ".join((name or "").split())[:MAX_NAME]
    if not name:
        raise ServiceError("Введите название сети", 422, "name_required")
    for r in conn.execute("SELECT id, name FROM networks WHERE status = 'active'"):
        if r["id"] != exclude and r["name"].casefold() == name.casefold():
            raise ServiceError("Сеть с таким названием уже есть", 409, "duplicate")
    return name


def create(conn: sqlite3.Connection, actor: dict, name: str, key: str) -> dict:
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return dict(conn.execute("SELECT * FROM networks ORDER BY id DESC LIMIT 1").fetchone())
        actor = _owner(conn, actor)
        name = _name(conn, name)
        cur = conn.execute("INSERT INTO networks (name, created_by, created_at) VALUES (?, ?, ?)",
                           (name, actor["telegram_id"], now_iso()))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="NETWORK_CREATED", key=key,
                  new={"network": cur.lastrowid, "name": name})
    return get(conn, cur.lastrowid)


def rename(conn: sqlite3.Connection, actor: dict, network_id: int, name: str, key: str) -> dict:
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, network_id)
        actor = _owner(conn, actor)
        net = _active(conn, network_id)
        name = _name(conn, name, exclude=network_id)
        conn.execute("UPDATE networks SET name = ? WHERE id = ?", (name, network_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="NETWORK_RENAMED", key=key,
                  old={"name": net["name"]}, new={"name": name})
    return get(conn, network_id)


def _active(conn, network_id: int) -> dict:
    net = get(conn, network_id)
    if net is None or net["status"] != "active":
        raise ServiceError("Сеть не найдена", 404, "not_found")
    return net


def archive(conn: sqlite3.Connection, actor: dict, network_id: int, key: str) -> dict:
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, network_id)
        actor = _owner(conn, actor)
        _active(conn, network_id)
        if members(conn, network_id):
            raise ServiceError("Сначала переведите людей в другие сети", 409, "not_empty")
        conn.execute("UPDATE networks SET status = 'archived', trainer_id = NULL WHERE id = ?", (network_id,))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="NETWORK_ARCHIVED", key=key,
                  new={"network": network_id})
    return get(conn, network_id)


def set_member(conn: sqlite3.Connection, actor: dict, user_id: int, network_id: int | None, key: str) -> tuple[dict, bool]:
    """Move a person into a network (None = no network). Moving a trainer out frees their network."""
    _check_key(key)
    with transaction(conn):
        user = svc.get_user(conn, user_id)
        if user is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        if _seen(conn, key):
            return user, False
        actor = _owner(conn, actor)
        if network_id is not None:
            _active(conn, network_id)
        if user["network_id"] == network_id:
            return user, False
        led = conn.execute("SELECT id FROM networks WHERE trainer_id = ? AND status = 'active'", (user_id,)).fetchone()
        if led and led["id"] != network_id:
            conn.execute("UPDATE networks SET trainer_id = NULL WHERE id = ?", (led["id"],))
        conn.execute("UPDATE users SET network_id = ? WHERE id = ?", (network_id, user_id))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type="NETWORK_MEMBER_SET", key=key,
                  old={"network": user["network_id"]}, new={"network": network_id})
    return svc.get_user(conn, user_id), True


def set_trainer(conn: sqlite3.Connection, actor: dict, network_id: int, user_id: int | None, key: str) -> tuple[dict, dict | None]:
    """Appoint the network's head (one per network). A trainer leads one network: they join it and get the
    trainer role; the previous trainer stays in the network as an employee. An owner may lead several
    networks (customer round B): their role and network don't change and no trainer share is paid there.
    Returns (network, previous trainer)."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, network_id), None
        actor = _owner(conn, actor)
        net = _active(conn, network_id)
        previous = svc.get_user(conn, net["trainer_id"]) if net["trainer_id"] else None
        if previous and user_id == previous["id"]:
            return net, None
        if previous and perms.role_of(previous) == "trainer":
            conn.execute("UPDATE users SET role = 'employee' WHERE id = ?", (previous["id"],))
        if user_id is not None:
            user = svc.get_user(conn, user_id)
            if user is None or user["status"] != "approved" or user["account_state"] == "blocked":
                raise ServiceError("Сотрудник не найден или заблокирован", 404, "not_found")
            if not perms.is_owner(user):
                if user["role"] == "admin":
                    raise ServiceError("Администратор и так видит все сети", 409, "already_staff")
                conn.execute("UPDATE networks SET trainer_id = NULL WHERE trainer_id = ?", (user_id,))
                conn.execute("UPDATE users SET role = 'trainer', network_id = ? WHERE id = ?", (network_id, user_id))
        conn.execute("UPDATE networks SET trainer_id = ? WHERE id = ?", (user_id, network_id))
        log_event(conn, user_id=user_id or actor["id"], actor_id=actor["telegram_id"], event_type="NETWORK_TRAINER_SET",
                  key=key, old={"trainer": previous["id"] if previous else None}, new={"network": network_id, "trainer": user_id})
    return get(conn, network_id), previous


def set_rates(conn: sqlite3.Connection, actor: dict, network_id: int, changes: dict, key: str) -> dict:
    """Owner: the trainer's ladder (customer round B). Networks no longer have their own employees' rate (27.10)."""
    from . import rates

    fields = {}
    if "ladder" in changes:
        fields["ladder"] = rates.validate_ladder(changes["ladder"])
    if "ladder_period" in changes:
        if changes["ladder_period"] not in ("day", "week"):
            raise ServiceError("Период лестницы — день или неделя", 422, "bad_period")
        fields["ladder_period"] = changes["ladder_period"]
    if "ladder_counts_trainer" in changes:
        fields["ladder_counts_trainer"] = int(bool(changes["ladder_counts_trainer"]))
    if not fields:
        raise ServiceError("Нет изменений", 422, "no_changes")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get(conn, network_id)
        actor = _owner(conn, actor)
        net = _active(conn, network_id)
        conn.execute(f"UPDATE networks SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), network_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="NETWORK_RATES_SET", key=key,
                  old={k: net[k] for k in fields}, new={"network": network_id, **fields})
    return get(conn, network_id)


def trainer_of(conn: sqlite3.Connection, user: dict) -> dict | None:
    """The trainer who leads this person's network (not the person themself)."""
    if user.get("network_id") is None:
        return None
    row = conn.execute(
        """SELECT u.* FROM networks n JOIN users u ON u.id = n.trainer_id WHERE n.id = ? AND n.status = 'active'
           AND u.status = 'approved' AND u.account_state != 'blocked'""", (user["network_id"],)).fetchone()
    return dict(row) if row and row["id"] != user["id"] else None
