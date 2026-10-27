"""ТЗ 27.10 §5–9: invitation only — hourly referral links, owner invitations, rotation, silence for strangers."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import invites
from app import services as svc
from app.api import create_app
from app.notify import Notifier
from app.timeutil import now_local

from .test_api import headers, key


def test_referral_link_changes_every_hour_and_binds_the_candidate(conn, make_user):
    friend = make_user(first="Аня")
    now = now_local()
    first = invites.current_ref(conn, friend, now)
    assert invites.current_ref(conn, friend, now + timedelta(minutes=59))["id"] == first["id"]
    later = invites.current_ref(conn, friend, now + timedelta(minutes=61))
    assert later["id"] != first["id"] and later["token"] != first["token"]
    # the old link (and its printed QR) no longer opens the registration
    link, st = invites.resolve(conn, f"ref_{first['token']}", now + timedelta(minutes=61))
    assert st == "expired" and not invites.use(conn, 5001, link, now + timedelta(minutes=61))
    # the live one binds; the person may finish the questionnaire after it changes
    link, st = invites.resolve(conn, f"ref_{later['token']}", now + timedelta(minutes=61))
    assert st == "active" and invites.use(conn, 5001, link, now + timedelta(minutes=61))
    assert invites.entry(conn, 5001) == "ref" and invites.referred(conn, 5001)["id"] == friend["id"]
    assert not invites.use(conn, friend["telegram_id"], link, now + timedelta(minutes=61))  # not one's own link
    # an old ref_<code> from before managed links, a garbage token — outdated; no parameter — nothing
    assert invites.resolve(conn, "ref_deadbeef")[1] == "unknown"
    assert invites.resolve(conn, None)[1] == "none" and invites.resolve(conn, "hello")[1] == "none"


def test_owner_invitation_limit_revoke_and_rotation(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = make_user(first="Админ")
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (admin["id"],))
    with pytest.raises(svc.ServiceError) as e:  # owners only
        invites.create_owner_invite(conn, svc.get_user(conn, admin["id"]), "x", 60, 1, svc.new_key())
    assert e.value.status == 403
    with pytest.raises(svc.ServiceError):
        invites.create_owner_invite(conn, owner, "x", 60, 0, svc.new_key())
    inv = invites.create_owner_invite(conn, owner, "Новый курьер", 60, 1, svc.new_key())
    link, st = invites.resolve(conn, f"inv_{inv['token']}")
    assert st == "active" and invites.use(conn, 7001, link)
    assert invites.use(conn, 7001, link)  # the same person again — fine, costs nothing
    assert not invites.use(conn, 7002, link)  # the limit of one person
    assert invites.resolve(conn, f"inv_{inv['token']}")[1] == "used_up"
    assert invites.entry(conn, 7001) == "owner" and invites.entry(conn, 7002) is None

    # «Обновить все ссылки»: live links die at once, an owner's invitation is re-issued with what was left
    friend = make_user(first="Аня")
    ref = invites.current_ref(conn, friend)
    many = invites.create_owner_invite(conn, owner, "Листовки", 120, 10, svc.new_key())
    invites.use(conn, 7003, invites.get(conn, many["id"]))
    counts = invites.rotate_all(conn, owner, svc.new_key())
    assert counts == {"ref": 1, "owner": 1}
    assert invites.resolve(conn, f"ref_{ref['token']}")[1] == "revoked"
    assert invites.resolve(conn, f"inv_{many['token']}")[1] == "revoked"
    assert invites.current_ref(conn, friend)["id"] != ref["id"]  # a fresh one, no manual work
    fresh = invites.get(conn, invites.get(conn, many["id"])["replaced_by"])
    assert (fresh["label"], fresh["max_uses"], fresh["ttl_minutes"], invites.state(fresh)) == ("Листовки", 9, 120, "active")
    assert invites.entry(conn, 7003) == "owner"  # someone who already came in keeps going

    # an explicit revoke closes an unfinished request
    one = invites.create_owner_invite(conn, owner, None, 60, 1, svc.new_key())
    invites.use(conn, 7004, invites.get(conn, one["id"]))
    _, changed = invites.revoke(conn, owner, one["id"], svc.new_key())
    assert changed and invites.entry(conn, 7004) is None


def test_strangers_are_logged_once_per_link_and_answered_once(conn):
    tg = {"id": 8001, "username": "x", "first_name": "Икс", "last_name": None}
    a = invites.log_attempt(conn, tg, "no_link")
    b = invites.log_attempt(conn, tg, "no_link")
    assert a["id"] == b["id"] and b["count"] == 2
    stale = invites.log_attempt(conn, tg, "bad_link", "ref_old")
    assert invites.should_answer_stale(conn, stale)
    assert not invites.should_answer_stale(conn, invites.log_attempt(conn, tg, "bad_link", "ref_old"))
    rows = invites.attempts(conn)
    assert {(r["kind"], r["count"]) for r in rows} == {("no_link", 2), ("bad_link", 2)}
    assert all("old" not in r["param"] for r in rows)  # tokens are not shown in full


def test_http_access(conn, make_user):
    client = TestClient(create_app(conn, Notifier(None, conn), "test_bot"))
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    stranger = headers(8101, "Незнакомец", "")
    assert client.get("/api/me", headers=stranger).json() == {"registered": False, "entry": None, "stale_link": False,
                                                             "bot_username": "test_bot"}
    assert client.post("/api/register", json={"full_name": "Иванов Иван", "age": 20, "metro": "Чкаловская",
                                              "min_days": 3, "training_days": [1], "communication": 5,
                                              "truthful": True, "consent": True},
                       headers=stranger).json()["code"] == "no_invite"
    r = client.post("/api/admin/invites", json={"label": "Курьер", "ttl_minutes": 60, "max_uses": 1,
                                                "idempotency_key": key()}, headers=owner)
    assert r.status_code == 200 and r.json()["invite"]["url"].startswith("https://t.me/test_bot?start=inv_")
    inv = r.json()["invite"]
    assert client.get(f"/api/admin/invites/{inv['id']}/qr", headers=owner).json()["svg"].startswith("<svg")
    assert client.post(f"/api/admin/invites/{inv['id']}/revoke", json={"idempotency_key": key()},
                       headers=owner).json()["items"][0]["state"] == "revoked"
    attempts = client.get("/api/admin/access-attempts", headers=owner).json()["items"]
    assert [a["kind"] for a in attempts] == ["app_no_link"] and attempts[0]["telegram_id"] == 8101
    # an employee: their link with a timer and a QR, but no owner tools
    emp = make_user(first="Аня")
    eh = headers(emp["telegram_id"])
    refs = client.get("/api/me/referrals", headers=eh).json()
    assert refs["link"].startswith("https://t.me/test_bot?start=ref_") and 3500 < refs["seconds_left"] <= 3600
    assert client.get("/api/me/referrals/qr", headers=eh).json()["link"] == refs["link"]
    assert client.get("/api/admin/invites", headers=eh).status_code == 403
    rotated = client.post("/api/admin/invites/rotate", json={"idempotency_key": key()}, headers=owner).json()
    assert rotated["rotated"]["ref"] == 1
    assert client.get("/api/me/referrals", headers=eh).json()["link"] != refs["link"]


def test_profile_card_for_people_without_username(conn, make_user):
    """ТЗ 27.10 §11: the Telegram ID is the key; without a username the bot sends a card with the name as a link."""
    from app import notify

    sent = []

    class Bot:
        async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
            sent.append((chat_id, text, reply_markup))

    n = Notifier(Bot(), conn)
    client = TestClient(create_app(conn, n, "test_bot"))
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    plain = make_user(first="Без", username=None)
    other = make_user(first="Чужой")
    r = client.post("/api/profile-link", json={"telegram_id": plain["telegram_id"]}, headers=owner)
    assert r.json() == {"sent": True, "bot_username": "test_bot"}
    chat, text, markup = sent[-1]
    assert chat == 999 and f'tg://user?id={plain["telegram_id"]}' in text and str(plain["telegram_id"]) in text
    assert markup.inline_keyboard[0][0].url == f"tg://user?id={plain['telegram_id']}"
    # an employee may not ask about anyone; a stranger from the access log — only for those who see the log
    assert client.post("/api/profile-link", json={"telegram_id": plain["telegram_id"]},
                       headers=headers(other["telegram_id"])).status_code == 403
    invites.log_attempt(conn, {"id": 8301, "first_name": "Ольга"}, "no_link")
    assert client.post("/api/profile-link", json={"telegram_id": 8301}, headers=owner).json()["sent"]
    assert "(без username, ID <code>" in notify.who(svc.get_user(conn, plain["id"]))
