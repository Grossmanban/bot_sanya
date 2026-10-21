import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import services as svc
from app.api import create_app
from app.auth import sign_init_data, validate_init_data, InitDataError
from app.config import settings
from app.notify import Notifier
from app.timeutil import today_local

TOKEN = settings.bot_token


def headers(tg_id: int, first="Иван", last="Петров", username="ivan"):
    init = sign_init_data({"id": tg_id, "first_name": first, "last_name": last, "username": username}, TOKEN)
    return {"X-Telegram-Init-Data": init}


@pytest.fixture
def client(conn):
    return TestClient(create_app(conn, Notifier(None, conn)))


def key():
    return str(uuid.uuid4())


def test_init_data_signature():
    init = sign_init_data({"id": 5, "first_name": "A"}, TOKEN)
    assert validate_init_data(init, TOKEN, 3600)["id"] == 5
    with pytest.raises(InitDataError):
        validate_init_data(init, "123:OTHER", 3600)
    with pytest.raises(InitDataError):
        validate_init_data(init.replace("A", "B"), TOKEN, 3600)
    old = sign_init_data({"id": 5}, TOKEN, auth_date=1)
    with pytest.raises(InitDataError):
        validate_init_data(old, TOKEN, 3600)


def test_forged_init_data_is_401(client):
    r = client.get("/api/me", headers={"X-Telegram-Init-Data": "user=%7B%22id%22%3A1%7D&hash=abc&auth_date=1"})
    assert r.status_code == 401


def test_access_by_registration_status(client, conn):
    assert client.get("/api/me", headers=headers(1)).json()["registered"] is False
    assert client.get("/api/schedule/week", headers=headers(1)).status_code == 403
    user, _ = svc.create_registration(conn, 1, "ivan", "Иван", "Петров")
    assert client.get("/api/me", headers=headers(1)).json()["user"]["status"] == "pending"
    r = client.get("/api/schedule/week", headers=headers(1))
    assert r.status_code == 403 and r.json()["code"] == "pending"
    svc.decide_registration(conn, user["id"], True, 999)
    assert client.get("/api/schedule/week", headers=headers(1)).status_code == 200
    assert client.get("/api/admin/users", headers=headers(1)).status_code == 403


def test_admin_from_config_is_auto_created(client):
    me = client.get("/api/me", headers=headers(999, "Саня", "Админ", "sanya")).json()
    assert me["user"]["role"] == "owner" and me["user"]["status"] == "approved"
    assert "money" in me["capabilities"] and me["role_title"] == "Владелец"
    assert client.get("/api/admin/raspred/tomorrow", headers=headers(999)).status_code == 200


def test_full_flow_over_http(client, conn):
    user, _ = svc.create_registration(conn, 7, "ivan", "Иван", "Петров")
    svc.decide_registration(conn, user["id"], True, 999)
    h = headers(7)
    day = (today_local() + timedelta(days=1)).isoformat()
    body = {"shift_date": day, "start_time": "16:00", "end_time": "19:00", "volume": 25, "location": "Европа"}

    r = client.post("/api/shifts/create", json={**body, "idempotency_key": key()}, headers=h)
    assert r.status_code == 403 and r.json()["code"] == "not_working"
    client.patch("/api/me/work-status", json={"work_status": "working", "idempotency_key": key()}, headers=h)

    k = key()
    r1 = client.post("/api/shifts/create", json={**body, "idempotency_key": k}, headers=h)
    r2 = client.post("/api/shifts/create", json={**body, "idempotency_key": k}, headers=h)
    assert r1.status_code == 200 and r1.json()["changed"] and not r2.json()["changed"]
    sid = r1.json()["shift"]["id"]

    raspred = client.get("/api/admin/raspred/tomorrow", headers=headers(999)).json()
    assert raspred["total_count"] == 1 and raspred["total_volume"] == 25

    assert client.post(f"/api/shifts/{sid}/cancel", json={"idempotency_key": key()}, headers=h).json()["shift"]["status"] == "cancelled"
    restore = {k_: body[k_] for k_ in ("start_time", "end_time", "volume", "location")}
    r = client.post(f"/api/shifts/{sid}/restore", json={**restore, "idempotency_key": key()}, headers=h)
    assert r.json()["shift"]["status"] == "restored"
    r = client.post(f"/api/shifts/{sid}/cancel", json={"idempotency_key": key()}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "final_confirm_required"
    r = client.post(f"/api/shifts/{sid}/final-cancel-confirm", json={"idempotency_key": key()}, headers=h)
    assert r.json()["shift"]["status"] == "finally_cancelled"

    r = client.patch(f"/api/admin/shifts/{sid}", json={"penalty_amount": 1000, "reason": "финальная отмена",
                                                       "idempotency_key": key()}, headers=headers(999))
    assert r.status_code == 200 and r.json()["shift"]["penalty_amount"] == 1000
    assert client.get("/api/me/penalties", headers=h).json()["items"][0]["penalty_amount"] == 1000
    assert client.get("/api/admin/raspred/tomorrow", headers=headers(999)).json()["total_count"] == 0


def test_invalid_payload_422(client, conn):
    user, _ = svc.create_registration(conn, 8, None, "Иван", "Петров")
    svc.decide_registration(conn, user["id"], True, 999)
    h = headers(8, username=None)
    client.patch("/api/me/work-status", json={"work_status": "working", "idempotency_key": key()}, headers=h)
    day = (today_local() + timedelta(days=1)).isoformat()
    r = client.post("/api/shifts/create", json={"shift_date": day, "start_time": "19:00", "end_time": "16:00",
                                                "volume": 5, "location": "x", "idempotency_key": key()}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "bad_time_range"


def test_admin_decides_urgent_from_panel(client, conn):
    user, _ = svc.create_registration(conn, 9, "ivan", "Иван", "Петров")
    svc.decide_registration(conn, user["id"], True, 999)
    h = headers(9)
    client.patch("/api/me/work-status", json={"work_status": "working", "idempotency_key": key()}, headers=h)
    body = {"shift_date": today_local().isoformat(), "start_time": "20:00", "end_time": "23:00", "volume": 20,
            "location": "Европа", "idempotency_key": key()}
    sid = client.post("/api/shifts/create", json=body, headers=h).json()["shift"]["id"]
    admin = headers(999)
    r = client.patch(f"/api/admin/shifts/{sid}", json={"action": "approve_urgent", "reason": "", "idempotency_key": key()},
                     headers=admin)
    assert r.status_code == 200 and r.json()["shift"]["status"] == "active"
    r = client.patch(f"/api/admin/shifts/{sid}", json={"action": "reject_urgent", "reason": "", "idempotency_key": key()},
                     headers=admin)
    assert r.status_code == 409


def test_team_endpoints(client, conn):
    owner_h = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner_h)
    newbie, _ = svc.create_registration(conn, 50, "new", "Новый", "Человек")
    team = client.get("/api/admin/team", headers=owner_h).json()["items"]
    assert team[0]["status"] == "pending" and any(u["is_owner"] for u in team)
    r = client.post(f"/api/admin/team/{newbie['id']}/access", json={"allow": True, "idempotency_key": key()}, headers=owner_h)
    assert r.json()["user"]["status"] == "approved"
    r = client.post(f"/api/admin/team/{newbie['id']}/role", json={"role": "admin", "idempotency_key": key()}, headers=owner_h)
    assert r.json()["user"]["role"] == "admin"
    # the new admin sees the team but cannot hand out admin rights
    h = headers(50, "Новый", "Человек", "new")
    assert client.get("/api/admin/team", headers=h).status_code == 200
    other, _ = svc.create_registration(conn, 51, None, "Ещё", "Один")
    svc.decide_registration(conn, other["id"], True, 999)
    r = client.post(f"/api/admin/team/{other['id']}/role", json={"role": "admin", "idempotency_key": key()}, headers=h)
    assert r.status_code == 403 and r.json()["code"] == "forbidden"


def test_points_settings_and_fact_endpoints(client, conn):
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    r = client.post("/api/admin/points", json={"name": "Европа", "capacity": 1}, headers=owner)
    assert r.status_code == 200
    pid = r.json()["point"]["id"]
    assert client.patch(f"/api/admin/points/{pid}", json={"clear_capacity": True}, headers=owner).json()["point"]["capacity"] is None

    s = client.get("/api/admin/settings", headers=owner).json()["settings"]
    assert s["eve_reminder_time"] == "20:00"
    r = client.patch("/api/admin/settings", json={"eve_reminder_time": "19:30", "backup_enabled": False}, headers=owner)
    assert r.json()["settings"]["eve_reminder_time"] == "19:30" and r.json()["settings"]["backup_enabled"] is False
    assert client.patch("/api/admin/settings", json={"eve_reminder_time": "99:00"}, headers=owner).status_code == 422

    # without a running bot, sending files and broadcasts reports it clearly
    assert client.post("/api/admin/broadcasts", json={"audiences": ["working"], "html": "<b>hi</b>"}, headers=owner).status_code == 503
    assert client.post("/api/admin/broadcasts/test", json={"html": "<b>hi</b>"}, headers=owner).status_code == 503
    people = client.get("/api/admin/broadcasts", headers=owner).json()["people"]
    assert people and {"id", "working", "admin"} <= set(people[0])
    r = client.post("/api/admin/export", json={"date_from": "2026-10-12", "date_to": "2026-10-18"}, headers=owner)
    assert r.status_code == 503

    user, _ = svc.create_registration(conn, 60, "w", "Иван", "Петров")
    svc.decide_registration(conn, user["id"], True, 999)
    h = headers(60)
    assert client.patch("/api/me/notifications", json={"enabled": False}, headers=h).json()["user"]["notify_reminders"] is False
    assert client.get("/api/points", headers=h).json()["items"][0]["name"] == "Европа"
    client.patch("/api/me/work-status", json={"work_status": "working", "idempotency_key": key()}, headers=h)
    today = today_local().isoformat()
    sid = client.post("/api/shifts/create", json={"shift_date": today, "start_time": "00:00", "end_time": "00:30", "volume": 10,
                                                  "location": "европа", "idempotency_key": key()}, headers=h).json()["shift"]["id"]
    client.patch(f"/api/admin/shifts/{sid}", json={"action": "approve_urgent", "reason": "", "idempotency_key": key()}, headers=owner)
    k = key()
    url = f"/api/shifts/{sid}/report?fact_volume=12&idempotency_key={k}"
    assert client.post(url, content=b"not an image", headers=h).status_code == 415
    assert client.post(url, headers=h).status_code == 422
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 64
    r = client.post(url, content=jpeg, headers={**h, "Content-Type": "image/jpeg"})
    assert r.status_code == 200, r.text
    shift = r.json()["shift"]
    # fact ≥ plan, still on review: every report waits for an admin
    assert shift["fact_volume"] == 12 and shift["location"] == "Европа" and shift["report"]["state"] == "pending_review"
    photo = shift["report"]["last"]["photo_url"]
    got = client.get(photo)
    assert got.status_code == 200 and got.content == jpeg
    assert client.get(photo.replace("sig=", "sig=0")).status_code == 403
    # a retry with the same key does not upload again
    assert client.post(url, content=jpeg, headers=h).json()["changed"] is False


def test_trainer_sees_only_own_network_and_cannot_fine(client, conn):
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    ids = {}
    for tg, name in ((60, "Тренер"), (61, "Свой"), (62, "Чужой")):
        u, _ = svc.create_registration(conn, tg, None, name, "Тест")
        svc.decide_registration(conn, u["id"], True, 999)
        ids[name] = u["id"]
    conn.execute("UPDATE users SET network_id = 1 WHERE id IN (?, ?)", (ids["Тренер"], ids["Свой"]))
    conn.execute("UPDATE users SET network_id = 2 WHERE id = ?", (ids["Чужой"],))
    r = client.post(f"/api/admin/team/{ids['Тренер']}/role", json={"role": "trainer", "idempotency_key": key()}, headers=owner)
    assert r.json()["user"]["role"] == "trainer"

    th = headers(60, "Тренер", "Тест")
    me = client.get("/api/me", headers=th).json()
    assert me["role_title"] == "Тренер" and "money" not in me["capabilities"] and "review" in me["capabilities"]
    team = {u["id"] for u in client.get("/api/admin/team", headers=th).json()["items"]}
    assert ids["Свой"] in team and ids["Чужой"] not in team
    assert client.get("/api/admin/raspred/tomorrow", headers=th).status_code == 403
    assert client.get("/api/admin/settings", headers=th).status_code == 403
    stats = "/api/admin/stats?user_id={}&from=2026-10-12&to=2026-10-18"
    assert client.get(stats.format(ids["Свой"]), headers=th).status_code == 200
    assert client.get(stats.format(ids["Чужой"]), headers=th).status_code == 403
    # trainers pause their own people only, and never block
    body = {"state": "paused", "reason": "больничный", "idempotency_key": key()}
    assert client.post(f"/api/admin/team/{ids['Чужой']}/state", json=body, headers=th).status_code == 403
    r = client.post(f"/api/admin/team/{ids['Свой']}/state", json={**body, "idempotency_key": key()}, headers=th)
    assert r.json()["user"]["account_state"] == "paused"
    r = client.post(f"/api/admin/team/{ids['Свой']}/state",
                    json={"state": "blocked", "reason": "x", "idempotency_key": key()}, headers=th)
    assert r.status_code == 403


def test_blocked_account_loses_access_and_archive_logs(client, conn):
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    u, _ = svc.create_registration(conn, 70, None, "Анна", "Блок")
    svc.decide_registration(conn, u["id"], True, 999)
    h = headers(70, "Анна", "Блок")
    assert client.get("/api/schedule/week", headers=h).status_code == 200
    r = client.post(f"/api/admin/team/{u['id']}/state",
                    json={"state": "blocked", "reason": "нарушение", "idempotency_key": key()}, headers=owner)
    assert r.json()["changed"] and r.json()["user"]["account_state"] == "blocked"
    r = client.get("/api/schedule/week", headers=h)
    assert r.status_code == 403 and r.json()["code"] == "blocked"
    # no archive group yet: the entry is kept with the reason it was not sent
    rows = conn.execute("SELECT tags, sent, error FROM archive_log").fetchall()
    assert rows and rows[-1]["tags"] == "блок" and rows[-1]["sent"] == 0 and rows[-1]["error"]
    assert client.get("/api/admin/settings", headers=owner).json()["archive"] == {"connected": False, "title": None}


def test_day_actual_endpoints(client, conn):
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    u, _ = svc.create_registration(conn, 80, None, "Адм", "Ин")
    svc.decide_registration(conn, u["id"], True, 999)
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (u["id"],))
    admin = headers(80, "Адм", "Ин")
    yesterday = (today_local() - timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status, created_at, "
                 "updated_at) VALUES (?, ?, '10:00', '12:00', 20, 'x', 'active', 't', 't')", (u["id"], yesterday))
    assert client.get("/api/admin/reports/counts", headers=owner).json()["actual_missing"] == 1
    assert client.get("/api/admin/reports/counts", headers=admin).json()["actual_missing"] == 0
    assert client.get("/api/admin/day-actuals", headers=admin).status_code == 403
    body = {"actual": 25, "idempotency_key": key()}
    assert client.post(f"/api/admin/day-actuals/{yesterday}", json=body, headers=admin).status_code == 403
    r = client.post(f"/api/admin/day-actuals/{yesterday}", json=body, headers=owner)
    assert r.status_code == 200 and r.json()["met"] is True
    items = client.get("/api/admin/day-actuals", headers=owner).json()["items"]
    assert items[0]["actual"] == 25 and items[0]["log"][0]["new_value"] == 25


def test_finance_endpoints(client, conn):
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    ids = {}
    for tg, name, role in ((90, "Тренер", "trainer"), (91, "Иван", "employee")):
        u, _ = svc.create_registration(conn, tg, None, name, "Тест")
        svc.decide_registration(conn, u["id"], True, 999)
        conn.execute("UPDATE users SET role = ?, network_id = 1 WHERE id = ?", (role, u["id"]))
        ids[name] = u["id"]
    th, eh = headers(90, "Тренер", "Тест"), headers(91, "Иван", "Тест")
    body = {"user_id": ids["Иван"], "type": "fine", "amount": 400, "reason": "Опоздание", "idempotency_key": key()}
    r = client.post("/api/admin/finances", json=body, headers=th)
    assert r.status_code == 200 and r.json()["tx"]["status"] == "pending_admin_approval"
    tx_id = r.json()["tx"]["id"]
    assert client.get("/api/me/finances", headers=eh).json()["items"] == []  # the employee doesn't see requests
    assert client.post(f"/api/admin/finances/{tx_id}/decide", json={"approve": True, "idempotency_key": key()},
                       headers=th).status_code == 403
    summary = client.get("/api/admin/finances/summary", headers=owner).json()
    assert summary["pending"][0]["id"] == tx_id
    assert client.get("/api/admin/reports/counts", headers=owner).json()["fines_pending"] == 1
    r = client.post(f"/api/admin/finances/{tx_id}/decide", json={"approve": True, "idempotency_key": key()}, headers=owner)
    assert r.json()["tx"]["status"] == "active"
    mine = client.get("/api/me/finances", headers=eh).json()
    assert mine["balance"]["balance"] == -400 and mine["items"][0]["amount"] == 400
    r = client.post("/api/admin/finances/pay-all", json={"user_id": ids["Иван"], "idempotency_key": key()}, headers=th)
    assert r.status_code == 403
    r = client.post("/api/admin/finances/pay-all", json={"user_id": ids["Иван"], "idempotency_key": key()}, headers=owner)
    assert r.json() == {"paid": 1, "total": 400}


def test_network_endpoints(client, conn):
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    ids = {}
    for tg, name in ((95, "Тренер"), (96, "Свой"), (97, "Чужой")):
        u, _ = svc.create_registration(conn, tg, None, name, "Тест")
        svc.decide_registration(conn, u["id"], True, 999)
        ids[name] = u["id"]
    net = client.post("/api/admin/networks", json={"name": "Север", "idempotency_key": key()}, headers=owner).json()["network"]
    r = client.patch(f"/api/admin/networks/{net['id']}", json={"trainer_id": ids["Тренер"], "idempotency_key": key()},
                     headers=owner)
    assert r.json()["network"]["trainer_id"] == ids["Тренер"]
    r = client.post(f"/api/admin/team/{ids['Свой']}/network", json={"network_id": net["id"], "idempotency_key": key()},
                    headers=owner)
    assert r.json()["user"]["network_id"] == net["id"]
    th = headers(95, "Тренер", "Тест")
    me = client.get("/api/me", headers=th).json()
    assert me["user"]["role"] == "trainer" and me["network"] == "Север"
    team = {u["id"] for u in client.get("/api/admin/team", headers=th).json()["items"]}
    assert ids["Свой"] in team and ids["Чужой"] not in team
    assert client.post("/api/admin/networks", json={"name": "Юг", "idempotency_key": key()}, headers=th).status_code == 403
    tree = client.get("/api/admin/networks", headers=th).json()["items"]
    assert len(tree) == 1 and tree[0]["members"][0]["id"] == ids["Свой"]


def test_payment_profile_and_payout_endpoints(client, conn):
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    u, _ = svc.create_registration(conn, 85, None, "Иван", "Петров")
    svc.decide_registration(conn, u["id"], True, 999)
    a, _ = svc.create_registration(conn, 86, None, "Адм", "Ин")
    svc.decide_registration(conn, a["id"], True, 999)
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (a["id"],))
    h, ah = headers(85, "Иван", "Петров"), headers(86, "Адм", "Ин")
    assert client.get("/api/me/payment-profile", headers=h).json()["profile"] is None
    body = {"phone": "+7 900 123 45 67", "bank": "ВТБ", "recipient": "Иван Петров", "idempotency_key": key()}
    r = client.put("/api/me/payment-profile", json=body, headers=h)
    assert r.json()["profile"]["phone"] == "+7 900 123-45-67"
    masked = client.get(f"/api/admin/users/{u['id']}/payment-profile", headers=ah).json()
    assert masked["full"] is False and masked["profile"]["phone"] == "+7 *** ***-45-67" and masked["history"] == []
    full = client.get(f"/api/admin/users/{u['id']}/payment-profile", headers=owner).json()
    assert full["full"] and full["profile"]["recipient"] == "Иван Петров" and len(full["history"]) == 1
    period = {"date_from": "2026-10-01", "date_to": "2026-10-15"}
    assert client.post("/api/admin/payouts/preview", json=period, headers=ah).status_code == 403
    r = client.post("/api/admin/payouts/preview", json=period, headers=owner)
    assert r.status_code == 200 and r.json()["rates"]["rate_base"] == 120
    assert client.get("/api/admin/payouts", headers=owner).json()["items"] == []


def test_goal_endpoints(client, conn):
    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    u, _ = svc.create_registration(conn, 87, None, "Иван", "Петров")
    svc.decide_registration(conn, u["id"], True, 999)
    h = headers(87, "Иван", "Петров")
    today = today_local()
    body = {"title": "Неделя рекордов", "kind": "team", "target": 100, "date_from": today.isoformat(),
            "date_to": (today + timedelta(days=6)).isoformat(), "idempotency_key": key()}
    assert client.post("/api/admin/goals", json=body, headers=h).status_code == 403
    goal = client.post("/api/admin/goals", json=body, headers=owner).json()["goal"]
    mine = client.get("/api/goals", headers=h).json()["items"]
    assert mine[0]["id"] == goal["id"] and mine[0]["joined"] is False
    r = client.post(f"/api/goals/{goal['id']}/join", headers=h)
    assert r.json()["goal"]["joined"] is True and r.json()["goal"]["progress"] == 0
    assert client.get("/api/admin/goals", headers=owner).json()["items"][0]["participants"][0]["user_id"] == u["id"]


def test_event_endpoints(client, conn):
    from app.timeutil import now_local

    owner = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner)
    u, _ = svc.create_registration(conn, 88, None, "Иван", "Петров")
    svc.decide_registration(conn, u["id"], True, 999)
    h = headers(88, "Иван", "Петров")
    now = now_local().replace(tzinfo=None)  # the Mini App sends local (Moscow) time without a zone
    body = {"title": "Спринт", "mode": "accumulative", "starts_at": (now - timedelta(hours=1)).isoformat(timespec="minutes"),
            "ends_at": (now + timedelta(hours=2)).isoformat(timespec="minutes"), "max_winners": 2, "prize": "Пицца",
            "idempotency_key": key()}
    assert client.post("/api/admin/events", json=body, headers=h).status_code == 403
    ev = client.post("/api/admin/events", json=body, headers=owner).json()["event"]
    assert client.get("/api/me", headers=h).json()["events"] == 1
    jpeg = b"\xff\xd8\xff\xe0" + b"1" * 64
    r = client.post(f"/api/events/{ev['id']}/entries?value=7&idempotency_key={key()}", content=jpeg, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["event"]["my"]["pending"] is True and r.json()["event"]["leaderboard"][0]["score"] == 7
    pending = client.get("/api/admin/events", headers=owner).json()["pending"]
    assert client.get(pending[0]["photo_url"]).content == jpeg
    assert client.get(pending[0]["photo_url"].replace("/event/", "/report/")).status_code == 403  # links don't cross kinds
    r = client.post(f"/api/admin/event-entries/{pending[0]['id']}/review", json={"approve": True, "idempotency_key": key()},
                    headers=owner)
    assert r.json()["event"]["leaderboard"][0]["confirmed"] == 7
