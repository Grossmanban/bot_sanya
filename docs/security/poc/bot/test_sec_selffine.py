import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import harness  # noqa
from app import finance, networks, services as svc
from app.db import connect


def approved(conn, tg, first):
    u, _ = svc.create_registration(conn, tg, None, first, "Т")
    return svc.decide_registration(conn, u["id"], True, 999)[0]


def test_admin_rejects_fine_on_himself():
    conn = connect(":memory:")
    owner = svc.ensure_admin(conn, 999, "O", "W", "own")
    trainer, admin = approved(conn, 7001, "Тренер"), approved(conn, 7002, "Админ")
    net = networks.create(conn, owner, "Север", svc.new_key())
    for p in (trainer, admin):
        networks.set_member(conn, owner, p["id"], net["id"], svc.new_key())
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    svc.set_role(conn, owner, admin["id"], "admin", svc.new_key())
    tx = finance.create(conn, svc.get_user(conn, trainer["id"]), admin["id"], "fine", None, "опоздание", svc.new_key(), units=2)
    tx = tx.tx if hasattr(tx, "tx") else tx
    print(tx["status"])
    assert tx["status"] == "pending_admin_approval"
    r = finance.decide(conn, svc.get_user(conn, admin["id"]), tx["id"], False, None, svc.new_key())
    assert r.changed and r.tx["status"] == "rejected"


def test_admin_bonus_to_himself():
    conn = connect(":memory:")
    owner = svc.ensure_admin(conn, 999, "O", "W", "own")
    admin = approved(conn, 7002, "Админ")
    svc.set_role(conn, owner, admin["id"], "admin", svc.new_key())
    a = svc.get_user(conn, admin["id"])
    r = finance.create(conn, a, a["id"], "bonus", 50000, "сам себе", svc.new_key())
    print(r.tx["status"], r.tx["amount"], finance.balance(conn, a["id"]))
    r2 = finance.create(conn, a, a["id"], "fine", None, "сам себе штраф", svc.new_key(), units=1)
    print("self-fine", r2.tx["status"])


def test_admin_confirms_own_event_entry():
    from datetime import timedelta
    from app import events
    from app.timeutil import now_local
    conn = connect(":memory:")
    owner = svc.ensure_admin(conn, 999, "O", "W", "own")
    admin = approved(conn, 7003, "Админ")
    svc.set_role(conn, owner, admin["id"], "admin", svc.new_key())
    a = svc.set_work_status(conn, svc.get_user(conn, admin["id"]), "working", svc.new_key())
    ev = events.create(conn, owner, title="Гонка", mode="first_to_reach", target=30, max_winners=1, max_attempts=2,
                       starts_at=now_local() - timedelta(hours=1), ends_at=now_local() + timedelta(hours=5), key=svc.new_key(),
                       prize="iPhone")
    entry, _ = events.submit(conn, a, ev["id"], 1_000_000, "AgACfake", svc.new_key())
    r = events.review(conn, svc.get_user(conn, a["id"]), entry["id"], True, None, svc.new_key())
    print(r.entry["status"], r.event["status"], r.finished)
    assert r.changed and r.entry["status"] == "confirmed" and r.finished


def test_via_bot_callbacks_self_decisions():
    from datetime import timedelta
    from app import events
    from app.timeutil import now_local
    from harness import make, call_update, feed
    conn = connect(":memory:")
    bot, sess, dp, _ = make(conn)
    owner = svc.ensure_admin(conn, 999, "O", "W", "own")
    admin = approved(conn, 7004, "Админ")
    svc.set_role(conn, owner, admin["id"], "admin", svc.new_key())
    a = svc.set_work_status(conn, svc.get_user(conn, admin["id"]), "working", svc.new_key())
    ev = events.create(conn, owner, title="Гонка", mode="first_to_reach", target=30, max_winners=1, max_attempts=2,
                       starts_at=now_local() - timedelta(hours=1), ends_at=now_local() + timedelta(hours=5), key=svc.new_key())
    entry, _ = events.submit(conn, a, ev["id"], 30, "AgACfake", svc.new_key())
    feed(bot, dp, call_update(7004, f"ev:ok:{entry['id']}"))   # forged on any bot message in his chat
    assert events.get_entry(conn, entry["id"])["status"] == "confirmed"
    # a trainer's fine against the admin — the admin rejects it himself with fx:r
    trainer = approved(conn, 7005, "Тренер")
    net = networks.create(conn, owner, "Север", svc.new_key())
    for p in (trainer, a):
        networks.set_member(conn, owner, p["id"], net["id"], svc.new_key())
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    tx = finance.create(conn, svc.get_user(conn, trainer["id"]), a["id"], "fine", None, "опоздание", svc.new_key(), units=2).tx
    feed(bot, dp, call_update(7004, f"fx:r:{tx['id']}"))
    assert finance.get(conn, tx["id"])["status"] == "rejected"
