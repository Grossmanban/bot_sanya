"""ТЗ 28.10, этап 3: профиль сотрудника и реквизиты — заявки, рассмотрение, журнал, напоминание."""

import pytest
from fastapi.testclient import TestClient

from app import networks, profiles
from app import services as svc
from app.api import create_app
from app.notify import Notifier

from .test_api import headers, key

FORM = {"last_name": "Петров", "first_name": "Иван", "middle_name": "Ильич", "age": 25, "metro": "Сокол",
        "min_days": 3, "work_days": [0, 2, 4]}


@pytest.fixture
def setup(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Иван", username="ivan")
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=oh)
    return owner, u, client, oh, headers(u["telegram_id"])


def test_days_must_match_the_minimum(conn, setup):
    _, u, client, _, h = setup
    r = client.post("/api/me/profile", json={**FORM, "work_days": [0, 2], "idempotency_key": key()}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "days_mismatch" and "минимум — 3" in r.json()["detail"]
    r = client.post("/api/me/profile", json={**FORM, "first_name": "Ив4н", "idempotency_key": key()}, headers=h)
    assert r.json()["code"] == "bad_name"


def test_request_review_reject_resend_approve_and_the_log(conn, setup):
    owner, u, client, oh, h = setup
    view = client.get("/api/me/profile", headers=h).json()
    assert view["state"]["status"] == "incomplete" and "Отчество" in view["missing"]
    r = client.post("/api/me/profile", json={**FORM, "idempotency_key": key()}, headers=h).json()
    assert r["pending"]["data"]["middle_name"] == "Ильич" and r["state"]["status"] == "pending"
    assert svc.get_user(conn, u["id"])["middle_name"] is None  # nothing changes until the admin decides
    again = client.post("/api/me/profile", json={**FORM, "age": 26, "idempotency_key": key()}, headers=h)
    assert again.status_code == 409 and again.json()["code"] == "pending"
    assert client.get("/api/admin/pending", headers=oh).json()["profile_changes"] == 1
    req = client.get("/api/admin/profile-requests", headers=oh).json()["items"][0]
    assert req["old"]["middle_name"] is None and req["data"]["work_days"] == [0, 2, 4]
    no = client.post(f"/api/admin/profile-requests/{req['id']}/decide", json={"approve": False, "idempotency_key": key()}, headers=oh)
    assert no.status_code == 422  # a reason is required
    client.post(f"/api/admin/profile-requests/{req['id']}/decide",
                json={"approve": False, "reason": "Возраст не совпадает", "idempotency_key": key()}, headers=oh)
    view = client.get("/api/me/profile", headers=h).json()
    assert view["state"] == {"status": "rejected", "reason": "Возраст не совпадает"} and view["rejected"]
    client.post("/api/me/profile", json={**FORM, "age": 26, "idempotency_key": key()}, headers=h)  # fixed and sent again
    req = client.get("/api/admin/profile-requests", headers=oh).json()["items"][0]
    client.post(f"/api/admin/profile-requests/{req['id']}/decide", json={"approve": True, "idempotency_key": key()}, headers=oh)
    fresh = svc.get_user(conn, u["id"])
    assert (fresh["middle_name"], fresh["age"], fresh["min_days"]) == ("Ильич", 26, 3)
    assert client.get("/api/me/profile", headers=h).json()["state"]["status"] == "complete"
    log = client.get(f"/api/admin/users/{u['id']}/profile", headers=oh).json()["history"]
    assert log[0]["how"] == "одобрена заявка сотрудника" and log[0]["new"]["age"] == 26 and log[0]["by"] == "Саня Владелец"


def test_admin_edits_at_once_and_the_waiting_request_is_superseded(conn, setup):
    owner, u, client, oh, h = setup
    client.post("/api/me/profile", json={**FORM, "idempotency_key": key()}, headers=h)
    r = client.put(f"/api/admin/users/{u['id']}/profile", json={**FORM, "metro": "Аэропорт", "idempotency_key": key()}, headers=oh)
    assert r.json()["profile"]["metro"] == "Аэропорт" and r.json()["history"][0]["how"] == "изменено администратором"
    assert client.get("/api/admin/profile-requests", headers=oh).json()["items"] == []
    assert client.put(f"/api/admin/users/{u['id']}/profile", json={**FORM, "idempotency_key": key()}, headers=h).status_code == 403


def test_payment_change_waits_for_the_owner_and_other_bank_needs_a_name(conn, setup, make_user):
    owner, u, client, oh, h = setup
    body = {"phone": "+7 900 123 45 67", "bank": "Другой банк", "recipient": "Иван Петров", "idempotency_key": key()}
    r = client.put("/api/me/payment-profile", json=body, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "bank_name_required"
    client.put("/api/me/payment-profile", json={**body, "bank_name": "Банк Точка", "idempotency_key": key()}, headers=h)
    assert client.get("/api/admin/pending", headers=oh).json()["payment_changes"] == 1
    req = client.get("/api/admin/profile-requests", headers=oh).json()["items"][0]
    client.post(f"/api/admin/profile-requests/{req['id']}/decide", json={"approve": True, "idempotency_key": key()}, headers=oh)
    mine = client.get("/api/me/payment-profile", headers=h).json()["profile"]
    assert mine["bank"] == "Банк Точка" and mine["phone"] == "+7 900 123-45-67"
    # a new change: the old details stay in use until approved
    client.put("/api/me/payment-profile", json={"phone": "+7 900 000 00 01", "bank": "ВТБ", "recipient": "Иван Петров",
                                                 "idempotency_key": key()}, headers=h)
    assert client.get("/api/me/payment-profile", headers=h).json()["profile"]["bank"] == "Банк Точка"


def test_team_states_and_the_reminder_goes_only_to_the_incomplete(conn, setup, make_user):
    owner, u, client, oh, h = setup
    done = make_user(first="Готов")
    profiles.admin_edit(conn, owner, done["id"], {**FORM, "first_name": "Готов"}, svc.new_key())
    from app import payouts

    payouts.save_profile(conn, owner, done["id"], "+79001112233", "Сбербанк", "Готов Петров", svc.new_key())
    team = {x["id"]: x for x in client.get("/api/admin/team", headers=oh).json()["items"]}
    assert team[done["id"]]["profile_state"]["profile"]["status"] == "complete"
    assert team[u["id"]]["profile_state"] == {"profile": {"status": "incomplete", "reason": None},
                                              "payment": {"status": "incomplete", "reason": None}}
    preview = client.get("/api/admin/profile-reminder", headers=oh).json()
    assert preview["count"] == 1 and preview["names"] == [svc.full_name(u)] and "Срочно заполните" in preview["text"]
    k = key()
    assert client.post("/api/admin/profile-reminder", json={"idempotency_key": k}, headers=oh).json()["count"] == 1
    assert client.post("/api/admin/profile-reminder", json={"idempotency_key": k}, headers=oh).json()["sent"] == 0  # once
    # a trainer reminds only their own people
    net = networks.create(conn, owner, "Север", svc.new_key())
    trainer = make_user(first="Вера")
    networks.set_member(conn, owner, trainer["id"], net["id"], svc.new_key())
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    th = headers(trainer["telegram_id"], "Вера", "Петров")
    assert client.get("/api/admin/profile-reminder", headers=th).json()["count"] == 0
    assert client.get("/api/admin/profile-requests", headers=th).status_code == 403
