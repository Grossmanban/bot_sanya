"""«Работа»: products a person may work on (ТЗ 27.10 §28). One access mechanism for every product.

Access is a row per person and product; revoking closes the row (history stays). The server checks it on every
action of the product, not only the button: opening, starting, the API, goals and events tied to the product.
Revoking blocks new actions; executions, moderation and payouts already made stay.
"""

import sqlite3

from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso

# ТЗ «Яндекс Плюс»: a separate product with its own access, QR codes and photo-report group; the mechanics are
# those of «Алиса» (alice.py serves both)
PRODUCTS = {"alice": "Яндекс Алиса", "plus": "Яндекс Плюс"}


def check_product(product: str | None) -> str | None:
    if product is not None and product not in PRODUCTS:
        raise ServiceError("Неизвестный продукт", 422, "bad_product")
    return product


def of_user(conn: sqlite3.Connection, user_id: int) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT product FROM product_access WHERE user_id = ? AND revoked_at IS NULL ORDER BY product", (user_id,))]


def has(conn: sqlite3.Connection, user: dict | None, product: str | None) -> bool:
    if product is None:
        return True
    if user is None or user["status"] != "approved" or user["account_state"] == "blocked":
        return False
    return conn.execute("SELECT 1 FROM product_access WHERE user_id = ? AND product = ? AND revoked_at IS NULL",
                        (user["id"], product)).fetchone() is not None


def manageable(conn: sqlite3.Connection, actor: dict | None) -> list[str]:
    """ТЗ «Яндекс Плюс» §11: products the actor may tie goals, events and the like to. Those who hand out access to
    products (owner, admins) — every product; anyone else (trainers, instructors) — only the products they have access
    to themselves."""
    if actor is None or not perms.is_staff(actor):
        return []
    if perms.can(actor, "products"):
        return list(PRODUCTS)
    mine = set(of_user(conn, actor["id"]))  # is_staff: approved and not blocked
    return [p for p in PRODUCTS if p in mine]


def require_manageable(conn: sqlite3.Connection, actor: dict, product: str | None) -> None:
    """The server's side of §11: a product the actor can't manage is refused even in a hand-made request."""
    if product is not None and product not in manageable(conn, actor):
        raise ServiceError(f"Нет доступа к продукту «{PRODUCTS[product]}» — выбрать его нельзя", 403, "no_product_access")


def require(conn: sqlite3.Connection, user: dict, product: str) -> None:
    if not has(conn, user, product):
        raise ServiceError(f"Нет доступа к продукту «{PRODUCTS[product]}»", 403, "no_product_access")


def set_access(conn: sqlite3.Connection, actor: dict, user_id: int, product: str, allow: bool, key: str) -> tuple[dict, bool]:
    check_product(product)
    _check_key(key)
    with transaction(conn):
        user = svc.get_user(conn, user_id)
        if user is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        if _seen(conn, key):
            return user, False
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "products", user):
            raise ServiceError("Доступ к продуктам выдают владелец и администраторы", 403, "forbidden")
        if allow and user["status"] != "approved":
            raise ServiceError("Сначала откройте человеку доступ к приложению", 409, "not_approved")
        live = conn.execute("SELECT id FROM product_access WHERE user_id = ? AND product = ? AND revoked_at IS NULL",
                            (user_id, product)).fetchone()
        if bool(live) == allow:
            return user, False
        if allow:
            conn.execute("INSERT INTO product_access (user_id, product, granted_at, granted_by) VALUES (?, ?, ?, ?)",
                         (user_id, product, now_iso(), actor["telegram_id"]))
        else:
            conn.execute("UPDATE product_access SET revoked_at = ?, revoked_by = ? WHERE id = ?",
                         (now_iso(), actor["telegram_id"], live["id"]))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], key=key,
                  event_type="PRODUCT_GRANTED" if allow else "PRODUCT_REVOKED", new={"product": product})
    return user, True


def history(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    names = {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT telegram_id, first_name, last_name FROM users")}
    out = []
    for r in conn.execute("SELECT * FROM product_access WHERE user_id = ? ORDER BY id DESC", (user_id,)):
        out.append({"product": r["product"], "title": PRODUCTS.get(r["product"], r["product"]),
                    "granted_at": r["granted_at"], "granted_by": names.get(r["granted_by"]),
                    "revoked_at": r["revoked_at"], "revoked_by": names.get(r["revoked_by"]) if r["revoked_by"] else None})
    return out


def view(conn: sqlite3.Connection, user_id: int) -> dict:
    mine = set(of_user(conn, user_id))
    return {"items": [{"product": p, "title": t, "active": p in mine} for p, t in PRODUCTS.items()],
            "history": history(conn, user_id)}
