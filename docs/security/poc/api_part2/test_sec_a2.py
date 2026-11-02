"""Security review proofs (scratch, not part of the repo)."""

from datetime import timedelta

from fastapi.testclient import TestClient

from app import events, finance, training
from app import services as svc
from app.api import create_app
from app.notify import Notifier
from app.timeutil import now_local, today_local

from tests.test_api import headers, key
from tests.test_rates import team  # noqa: F401
from tests.test_tz2810_training import world  # noqa: F401


def own_logged_key(c, h):
    """Any action of the attacker that writes event_logs with a key they chose."""
    k = "attacker-key-0001"
    day = (today_local() + timedelta(days=3)).isoformat()
    r = c.post("/api/shifts/pre-declare-off", json={"shift_date": day, "idempotency_key": k}, headers=h)
    assert r.status_code == 200, r.text
    return k


def test_training_idor_by_replayed_key(conn, world):  # noqa: F811
    c, app, other = world["client"], world["app"], world["other"]
    # Vera is the candidate's trainer
    c.post(f"/api/admin/onboarding/{app['id']}/trainer", json={"trainer_id": world["vera"]["id"], "send": False,
                                                              "idempotency_key": key()}, headers=world["oh"])
    # Oleg has «Обучение» for some other reason (granted, or a candidate of his own)
    training.set_access(conn, world["owner"], other["id"], True, key())
    oh = headers(other["telegram_id"], "Олег", "Петров", "oleg")
    body = {"date": (today_local() + timedelta(days=2)).isoformat(), "time": "11:00", "location": "Европа"}
    # with a fresh key — correctly 403
    assert c.post(f"/api/training/{app['id']}/schedule", json={**body, "idempotency_key": key()}, headers=oh).status_code == 403
    k = own_logged_key(c, oh)
    r = c.post(f"/api/training/{app['id']}/schedule", json={**body, "idempotency_key": k}, headers=oh)
    assert r.status_code == 200, r.text
    a = r.json()["application"]
    print("LEAKED application:", {x: a.get(x) for x in ("full_name", "age", "metro", "telegram_id", "referrer_tg", "communication")})
    assert a["full_name"] == "Сидоров Пётр Ильич" and a["telegram_id"] == 7001
    r = c.post(f"/api/training/{app['id']}/cancel", json={"reason": "x", "idempotency_key": k}, headers=oh)
    assert r.status_code == 200 and r.json()["application"]["telegram_id"] == 7001


def test_trainer_reads_other_network_objects_by_replayed_key(conn, team):  # noqa: F811
    owner, vera, masha = team["owner"], team["vera"], team["masha"]
    client = TestClient(create_app(conn, Notifier(None, conn)))
    vh = headers(vera["telegram_id"], "Вера")
    # Masha is in no network: Vera must not see her
    tx = finance.create(conn, owner, masha["id"], "fine", None, "Секретная причина", key(), units=2).tx
    assert client.post(f"/api/admin/finances/{tx['id']}/forgive", json={"idempotency_key": key()}, headers=vh).status_code == 403
    tomorrow = today_local() + timedelta(days=1)
    svc.admin_mark_day(conn, owner, masha["id"], tomorrow, "off", None, key())
    k = own_logged_key(client, vh)
    r = client.post(f"/api/admin/finances/{tx['id']}/forgive", json={"idempotency_key": k}, headers=vh)
    assert r.status_code == 200, r.text
    print("LEAKED tx:", r.json())
    assert r.json()["tx"]["reason"] == "Секретная причина" and r.json()["tx"]["user_id"] == masha["id"]
    assert finance.get(conn, tx["id"])["status"] == "active"  # read only, nothing changed
    r = client.post(f"/api/admin/day/{tomorrow.isoformat()}/mark",
                    json={"user_id": masha["id"], "kind": "off", "idempotency_key": k}, headers=vh)
    assert r.status_code == 200 and r.json()["shift"] is not None, r.text
    print("LEAKED shift:", r.json())
    # instructor-level / admin_user endpoint too: fill_draft, cancel (admin_user)
    r = client.post(f"/api/admin/finances/{tx['id']}/cancel", json={"reason": "x", "idempotency_key": k}, headers=vh)
    assert r.status_code == 200 and r.json()["tx"]["user_id"] == masha["id"]


def test_admin_forgives_and_cancels_own_fine(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    adm = make_user(first="Админ", username="adm")
    svc.set_role(conn, owner, adm["id"], "admin", key())
    client = TestClient(create_app(conn, Notifier(None, conn)))
    ah = headers(adm["telegram_id"], "Админ", "Петров", "adm")
    tx1 = finance.create(conn, owner, adm["id"], "fine", None, "Опоздание", key(), units=3).tx
    tx2 = finance.create(conn, owner, adm["id"], "fine", None, "Прогул", key(), units=3).tx
    assert tx1["status"] == "active" and tx1["amount"] > 0
    # creating for oneself is refused — but removing one's own fine is not
    r = client.post("/api/admin/finances", json={"user_id": adm["id"], "type": "bonus", "amount": 100, "reason": "x",
                                                 "idempotency_key": key()}, headers=ah)
    assert r.status_code == 403
    r = client.post(f"/api/admin/finances/{tx1['id']}/forgive", json={"idempotency_key": key()}, headers=ah)
    assert r.status_code == 200 and r.json()["tx"]["status"] == "cancelled", r.text
    r = client.post(f"/api/admin/finances/{tx2['id']}/cancel", json={"reason": "сам", "idempotency_key": key()}, headers=ah)
    assert r.status_code == 200 and r.json()["tx"]["status"] == "cancelled", r.text


def test_admin_approves_own_event_entry(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    adm = make_user(first="Админ", username="adm")
    svc.set_role(conn, owner, adm["id"], "admin", key())
    adm = svc.get_user(conn, adm["id"])
    now = now_local()
    ev = events.create(conn, owner, title="Гонка", mode="first_to_reach", target=10, max_winners=1,
                       starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=5), key=key(), prize="5000 ₽")
    entry, _ = events.submit(conn, adm, ev["id"], 50, "local:x.jpg", key())
    client = TestClient(create_app(conn, Notifier(None, conn)))
    ah = headers(adm["telegram_id"], "Админ", "Петров", "adm")
    r = client.post(f"/api/admin/event-entries/{entry['id']}/review", json={"approve": True, "idempotency_key": key()},
                    headers=ah)
    assert r.status_code == 200, r.text
    assert events.get_entry(conn, entry["id"])["status"] == "confirmed"
    assert events.get(conn, ev["id"])["status"] == "finished"  # the admin won their own race


def test_trainer_pauses_admin_of_own_network(conn, team):  # noqa: F811
    owner, vera, ivan = team["owner"], team["vera"], team["ivan"]
    svc.set_role(conn, owner, ivan["id"], "admin", key())  # an employee of «Север» promoted to admin keeps network_id
    client = TestClient(create_app(conn, Notifier(None, conn)))
    vh = headers(vera["telegram_id"], "Вера")
    r = client.post(f"/api/admin/team/{ivan['id']}/state", json={"state": "paused", "reason": "так", "idempotency_key": key()},
                    headers=vh)
    print("pause admin by trainer:", r.status_code, r.json().get("user", {}).get("account_state"))
    assert r.status_code == 200 and svc.get_user(conn, ivan["id"])["account_state"] == "paused"
    # and the trainer sees the admin's money-free stats / can mark their day / fine request
    tx = client.post("/api/admin/finances", json={"user_id": ivan["id"], "type": "fine", "units": 1, "reason": "x",
                                                  "idempotency_key": key()}, headers=vh)
    print("fine request on admin:", tx.status_code, tx.json())


def test_instructor_enumerates_any_shift_with_personal_rate(conn, team, make_user):  # noqa: F811
    from app import networks, rates
    owner, masha = team["owner"], team["masha"]
    olga = make_user(first="Ольга", username="olga")
    networks.set_member(conn, owner, olga["id"], team["north"]["id"], svc.new_key())
    svc.set_role(conn, owner, olga["id"], "instructor", key())
    rates.set_user_rates(conn, owner, masha["id"], {"personal_rate": 173}, key())
    tomorrow = today_local() + timedelta(days=1)
    pt = svc.list_points(conn)
    loc = pt[0]["name"] if pt else svc.create_point(conn, owner, "Европа", None)["name"]
    res = svc.admin_mark_day(conn, owner, masha["id"], tomorrow, "shift",
                             {"start_time": "10:00", "end_time": "18:00", "volume": 30, "location": loc}, key())
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh = headers(olga["telegram_id"], "Ольга", "Петров", "olga")
    k = own_logged_key(client, oh)
    r = client.post(f"/api/admin/shifts/{res.shift['id']}/review", json={"action": "approve", "idempotency_key": k}, headers=oh)
    print("instructor sees:", r.status_code, {x: r.json()["shift"].get(x) for x in ("date", "volume", "location", "rate", "expected_pay")})
    assert r.status_code == 200 and r.json()["shift"]["rate"] == 173
    r = client.post(f"/api/admin/shifts/{res.shift['id']}/result?fact_volume=1&idempotency_key={k}", headers=oh)
    assert r.status_code == 200 and r.json()["shift"]["rate"] == 173
