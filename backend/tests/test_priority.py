"""19.11: «приоритетная анкета» — the candidate's own toggle with the responsibility warning, the staff's
switch on any questionnaire (⭐ on the cards, in «Обучение» and in the bot's messages), and the waiting
indicator of «Ожидают рассмотрения» («1 день 12 часов», «7 дней»)."""

from datetime import timedelta

from fastapi.testclient import TestClient

from app import invites, recruiting, training
from app import services as svc
from app.api import create_app
from app.auth import sign_init_data
from app.config import settings
from app.timeutil import now_local

from .test_features import FakeNotifier, run
from .test_recruiting import FORM, invited


def test_wait_text_formats():
    """The waiting text: minutes, then hours, then «дни + часы»; a whole number of days — without the zero hours."""
    f = recruiting.wait_text
    assert f(0) == "0 мин" and f(0.5) == "30 мин"
    assert f(1) == "1 час" and f(2) == "2 часа" and f(5) == "5 часов" and f(12) == "12 часов"
    assert f(24) == "1 день" and f(36) == "1 день 12 часов"
    assert f(48) == "2 дня" and f(72) == "3 дня" and f(120) == "5 дней"
    assert f(168) == "7 дней" and f(169) == "7 дней 1 час"
    assert f(24 * 21) == "21 день" and f(24 * 22) == "22 дня" and f(24 * 11) == "11 дней"


def test_candidate_marks_the_questionnaire_priority(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    friend = make_user(first="Аня")
    invited(conn, friend, 5301)
    _, app = recruiting.submit(conn, {"id": 5301, "username": "olga"}, {**FORM, "full_name": "Приорова Ольга", "priority": True})
    assert (app["priority"], app["priority_by"]) == (1, "candidate") and app["priority_at"]
    v = recruiting.view(conn, app)
    assert v["priority"] is True
    # just sent: minutes, not days
    assert v["waiting"]["text"].endswith("мин") and 0 <= v["waiting"]["hours"] < 1
    # the admins hear about the priority in the questionnaire's notice
    n = FakeNotifier(conn)
    run(n.application_submitted(app))
    assert any("⭐ <b>Приоритетная анкета</b>" in t for _, t in n.sent)


def test_waiting_ages_and_only_in_new(conn, make_user):
    """«Ожидают рассмотрения»: how long the person has been waiting (the server's MSK clock); once the training
    is set the waiting indicator is gone — the questionnaire is no longer waiting for the staff."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    friend = make_user(first="Аня")
    invited(conn, friend, 5401)
    _, app = recruiting.submit(conn, {"id": 5401}, FORM)
    assert recruiting.view(conn, app)["priority"] is False
    past = (svc.now_local() - timedelta(days=1, hours=12)).isoformat(timespec="seconds")
    conn.execute("UPDATE applications SET created_at = ? WHERE id = ?", (past, app["id"]))
    v = recruiting.view(conn, recruiting.get(conn, app["id"]))
    assert v["waiting"]["text"] == "1 день 12 часов" and 36 <= v["waiting"]["hours"] <= 37
    trainer = make_user(first="Тренер")
    scheduled, _ = recruiting.assign_training(conn, owner, app["id"], now_local().date() + timedelta(days=1),
                                              "11:00", "Европа", trainer["id"], svc.new_key())
    assert recruiting.view(conn, scheduled)["waiting"] is None


def test_staff_switches_priority(conn, make_user):
    """The staff's switch works on any questionnaire (even already scheduled); the same key is a no-op; turning
    it off clears the mark."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    friend = make_user(first="Аня")
    invited(conn, friend, 5501)
    _, app = recruiting.submit(conn, {"id": 5501}, FORM)
    a, changed = recruiting.set_priority(conn, owner, app["id"], True, svc.new_key())
    assert changed and a["priority"] == 1 and a["priority_by"] == "999"
    assert recruiting.set_priority(conn, owner, app["id"], True, svc.new_key())[1] is False  # the same key — no-op
    a, changed = recruiting.set_priority(conn, owner, a["id"], False, svc.new_key())
    assert changed and a["priority"] == 0 and a["priority_at"] is None and a["priority_by"] is None
    trainer = make_user(first="Тренер")
    scheduled, _ = recruiting.assign_training(conn, owner, a["id"], now_local().date() + timedelta(days=1),
                                              "11:00", "Европа", trainer["id"], svc.new_key())
    a2, changed = recruiting.set_priority(conn, owner, scheduled["id"], True, svc.new_key())
    assert changed and a2["priority"] == 1


def test_trainer_sees_the_priority_in_the_message_and_in_training(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer = make_user(first="Тренер")
    friend = make_user(first="Аня")
    invited(conn, friend, 5801)
    _, app = recruiting.submit(conn, {"id": 5801}, {**FORM, "full_name": "Тренерова Ольга", "priority": True})
    scheduled, changed, previous = training.assign_trainer(conn, owner, app["id"], trainer["id"], svc.new_key())
    assert changed and previous is None
    n = FakeNotifier(conn)
    run(n.trainer_assigned(scheduled, previous, True))
    [to_trainer] = [t for chat, t in n.sent if chat == trainer["telegram_id"]]
    assert "⭐ <b>Приоритетная анкета</b>" in to_trainer
    assert recruiting.view(conn, scheduled)["priority"] is True


def test_http_priority_on_the_board_and_for_the_trainer(conn, make_user):
    n = FakeNotifier(conn)
    client = TestClient(create_app(conn, n))
    owner = {"X-Telegram-Init-Data": sign_init_data({"id": 999, "first_name": "Саня"}, settings.bot_token)}
    client.get("/api/me", headers=owner)
    friend = make_user(first="Аня")
    invited(conn, friend, 5601)
    candidate = {"X-Telegram-Init-Data": sign_init_data({"id": 5601, "first_name": "Оля"}, settings.bot_token)}
    r = client.post("/api/register", json={**FORM, "full_name": "Приорова Ольга", "priority": True}, headers=candidate)
    assert r.status_code == 200, r.text
    me = client.get("/api/me", headers=candidate).json()
    assert me["application"]["priority"] is True
    board = client.get("/api/admin/onboarding?tab=new", headers=owner).json()
    (a,) = board["items"]
    assert a["priority"] is True and a["waiting"]["text"].endswith("мин")
    trainer = make_user(first="Тренер")
    training.assign_trainer(conn, svc.get_user_by_tg(conn, 999), a["id"], trainer["id"], svc.new_key())
    tr = {"X-Telegram-Init-Data": sign_init_data({"id": trainer["telegram_id"], "first_name": "Тренер"}, settings.bot_token)}
    items = client.get("/api/training", headers=tr).json()["items"]
    assert [x["priority"] for x in items if x["id"] == a["id"]] == [True]
    # the staff switch: an ordinary employee is refused, the owner turns it off and on
    emp = {"X-Telegram-Init-Data": sign_init_data({"id": friend["telegram_id"], "first_name": "Аня"}, settings.bot_token)}
    assert client.post(f"/api/admin/onboarding/{a['id']}/priority", json={"on": False, "idempotency_key": svc.new_key()},
                       headers=emp).status_code == 403
    r2 = client.post(f"/api/admin/onboarding/{a['id']}/priority", json={"on": False, "idempotency_key": svc.new_key()}, headers=owner)
    assert r2.status_code == 200, r2.text
    assert r2.json()["application"]["priority"] is False
    r3 = client.post(f"/api/admin/onboarding/{a['id']}/priority", json={"on": True, "idempotency_key": svc.new_key()}, headers=owner)
    assert r3.json()["application"]["priority"] is True
