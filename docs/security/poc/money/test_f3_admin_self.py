"""F3: an admin (not an owner) decides on their own money and results: forgive/annul own fine, reject a trainer's
fine on themselves, confirm their own event entry, accept their own «Алиса» execution."""
from fastapi.testclient import TestClient

from app import alice, events, finance, networks, products
from app import services as svc
from app.api import create_app
from app.notify import Notifier
from tests.test_alice import JPG, ForumBot, full_run
from tests.test_api import headers, key
from tests.test_features import THU, _event, at, run


def _admin(conn, make_user):
    a = make_user(first="Адм")
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (a["id"],))
    return svc.get_user(conn, a["id"])


def test_admin_forgives_and_annuls_own_fines_over_http(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = _admin(conn, make_user)
    f1 = finance.create(conn, owner, admin["id"], "fine", None, "опоздание", svc.new_key(), units=5).tx
    f2 = finance.create(conn, owner, admin["id"], "fine", None, "неявка", svc.new_key(), units=5).tx
    client = TestClient(create_app(conn, Notifier(None, conn)))
    h = headers(admin["telegram_id"], "Адм")
    r = client.post(f"/api/admin/finances/{f1['id']}/forgive", json={"idempotency_key": key()}, headers=h)
    assert r.status_code == 200 and r.json()["tx"]["status"] == "cancelled"
    r = client.post(f"/api/admin/finances/{f2['id']}/cancel", json={"reason": "ошибка", "idempotency_key": key()}, headers=h)
    assert r.status_code == 200 and r.json()["tx"]["status"] == "cancelled"
    assert finance.balance(conn, admin["id"])["fines"] == 0


def test_admin_rejects_trainers_fine_on_themselves(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = _admin(conn, make_user)
    net = networks.create(conn, owner, "Север", svc.new_key())
    trainer = make_user(first="Тренер")
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    networks.set_member(conn, owner, admin["id"], net["id"], svc.new_key())
    trainer = svc.get_user(conn, trainer["id"])
    tx = finance.create(conn, trainer, admin["id"], "fine", None, "неявка", svc.new_key(), units=3).tx
    assert tx["status"] == "pending_admin_approval"
    res = finance.decide(conn, svc.get_user(conn, admin["id"]), tx["id"], False, None, svc.new_key())
    assert res.changed and res.tx["status"] == "rejected"


def test_admin_confirms_own_event_entry_and_wins(conn, make_user, monkeypatch):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = _admin(conn, make_user)
    ev = _event(conn, owner, prize="iPhone")
    now = at(THU, "12:00")
    entry, _ = events.submit(conn, admin, ev["id"], 30, "photo", svc.new_key(), now=now)
    res = events.review(conn, admin, entry["id"], True, None, svc.new_key())
    assert res.changed and res.entry["status"] == "confirmed" and res.finished
    board = events.leaderboard(conn, res.event)
    assert [r["user_id"] for r in board if r["winner"]] == [admin["id"]]


def test_admin_accepts_own_alice_execution(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = _admin(conn, make_user)
    alice.connect_forum(conn, -1009, "Алиса")
    products.set_access(conn, admin, admin["id"], "alice", True, svc.new_key())  # gives it to themself
    qr = alice.create_qr(conn, admin, "local:qr.png", "QR-1", svc.new_key())
    alice.assign(conn, admin, qr["id"], admin["id"], True, svc.new_key())
    t = full_run(conn, admin, JPG + b"x")
    run(alice.deliver(conn, ForumBot(), t["id"]))
    task, changed = alice.moderate(conn, admin, t["id"], True, None, svc.new_key())
    assert changed and task["status"] == "accepted"  # «Принято, ожидает оплаты» — own execution, own decision
