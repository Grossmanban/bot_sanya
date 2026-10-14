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
    assert me["user"]["role"] == "admin" and me["user"]["status"] == "approved"
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
    assert r.status_code == 403 and r.json()["code"] == "owner_only"


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
    sid = client.post("/api/shifts/create", json={"shift_date": today, "start_time": "08:00", "end_time": "09:00", "volume": 10,
                                                  "location": "европа", "idempotency_key": key()}, headers=h).json()["shift"]["id"]
    client.patch(f"/api/admin/shifts/{sid}", json={"action": "approve_urgent", "reason": "", "idempotency_key": key()}, headers=owner)
    r = client.post(f"/api/shifts/{sid}/fact", json={"fact_volume": 12, "idempotency_key": key()}, headers=h)
    assert r.status_code == 200 and r.json()["shift"]["fact_volume"] == 12 and r.json()["shift"]["location"] == "Европа"
