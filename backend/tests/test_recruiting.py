"""The customer's module 3: referral link → questionnaire → training → internship → the referrer's bonuses."""

import hashlib
import hmac
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import finance, payouts, recruiting
from app import services as svc
from app.api import create_app
from app.auth import sign_init_data
from app.config import settings
from app.timeutil import now_local

from .test_features import FakeNotifier, run

FORM = {"full_name": "Сидоров Пётр Ильич", "age": 19, "metro": "Чкаловская", "min_days": 3, "training_days": [1, 3],
        "communication": 8, "truthful": True, "consent": True}


def test_full_path_with_referrer_bonuses(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    friend = make_user(first="Аня")
    trainer = make_user(first="Тренер")
    code = recruiting.ref_code(conn, friend)
    assert recruiting.ref_code(conn, svc.get_user(conn, friend["id"])) == code  # stable

    # the questionnaire: validation, then the referral from the link
    with pytest.raises(svc.ServiceError) as e:
        recruiting.submit(conn, {"id": 5001}, {**FORM, "consent": False})
    assert e.value.code == "consent_required"
    with pytest.raises(svc.ServiceError):
        recruiting.submit(conn, {"id": 5001}, {**FORM, "training_days": []})
    user, app = recruiting.submit(conn, {"id": 5001, "username": "petya"}, FORM, f"ref_{code}")
    assert (user["status"], user["last_name"], user["first_name"]) == ("pending", "Сидоров", "Пётр Ильич")
    assert app["referrer_id"] == friend["id"] and app["status"] == "new"
    with pytest.raises(svc.ServiceError) as e:  # one questionnaire at a time
        recruiting.submit(conn, {"id": 5001}, FORM)
    assert e.value.code == "already_sent"
    assert [r["status"] for r in recruiting.referrals(conn, friend)] == ["new"]

    # the candidate hears back in the chat; when the bot can't reach them, the staff are warned
    class Unreachable(FakeNotifier):
        async def send(self, chat_id, text, markup=None):
            await super().send(chat_id, text, markup)
            return chat_id != 5001

    n = Unreachable(conn)
    n.bot = object()
    run(n.application_submitted(app))
    assert any(chat == 5001 and "Анкета получена" in t for chat, t in n.sent)
    assert any(chat == 999 and "Бот не может написать кандидату" in t for chat, t in n.sent)

    # the training: set, confirmed by the candidate, reminded once (not right after it was set)
    day = now_local().date() + timedelta(days=1)
    app, changed = recruiting.assign_training(conn, owner, app["id"], day, "11:00", "Европа", trainer["id"], svc.new_key())
    assert changed and app["status"] == "scheduled"
    n = FakeNotifier(conn)
    run(n.training_assigned(app))
    to_candidate = [t for chat, t in n.sent if chat == 5001]
    assert "Ваше обучение запланировано" in to_candidate[0] and "11:00" in to_candidate[0] and "Европа" in to_candidate[0]
    assert recruiting.confirm_training(conn, 5001, app["id"])[1] and not recruiting.confirm_training(conn, 5001, app["id"])[1]
    conn.execute("UPDATE applications SET training_set_at = '2000-01-01T00:00:00' WHERE id = ?", (app["id"],))
    eve = now_local().replace(hour=20, minute=5)
    assert [k for _, k in recruiting.training_reminders(conn, eve)] == ["eve"]
    assert recruiting.training_reminders(conn, eve) == []

    # the internship: a trainee with training shifts, not paid
    app, _ = recruiting.start_internship(conn, owner, app["id"], svc.new_key())
    trainee = svc.get_user(conn, user["id"])
    assert trainee["status"] == "approved" and recruiting.is_trainee(trainee) and trainee["work_status"] == "working"
    today = now_local().date()
    s = svc.create_shift(conn, trainee, today + timedelta(days=1), {"start_time": "10:00", "end_time": "16:00", "volume": 20,
                                                                   "location": "Европа"}, svc.new_key(), today=today).shift
    assert s["training"] == 1
    view = svc.serialize_shift(s, today, conn)
    assert view["training"] and view["expected_pay"] == 0 and view["rate"] == 0
    assert "стажёр" in svc.raspred(conn, today + timedelta(days=1))["text"]
    conn.execute("UPDATE shifts SET status = 'active', report_state = 'approved', fact_volume = 21 WHERE id = ?", (s["id"],))
    p = recruiting.progress(conn, app)
    assert (p["stage"], p["units"], p["review"]) == (1, 21, True)
    rows = payouts.calculate(conn, today, today + timedelta(days=3), payouts.Rates.from_dict(None))
    assert all(r["user_id"] != trainee["id"] or r["base_calc"] == 0 for r in rows)  # training units aren't paid
    reached = recruiting.reached(conn)
    assert [r["stage"] for r in reached] == [1] and recruiting.reached(conn) == []  # staff told once

    # staff count 15 of 21 and give the referrer 500 ₽; the trainee is a regular employee from now on
    app, tx, changed = recruiting.confirm_stage(conn, owner, app["id"], 1, 15, 500, svc.new_key())
    assert changed and app["status"] == "done" and app["stage1_counted"] == 15
    assert tx["user_id"] == friend["id"] and tx["amount"] == 500 and tx["type"] == "bonus"
    assert not recruiting.is_trainee(svc.get_user(conn, user["id"]))
    assert recruiting.referrals(conn, friend)[0]["progress"]["stage"] == 2

    # the second stage: work units after the internship
    app, tx2, _ = recruiting.confirm_stage(conn, owner, app["id"], 2, 50, 1000, svc.new_key())
    assert app["stage2_counted"] == 50 and tx2["amount"] == 1000
    assert finance.balance(conn, friend["id"])["bonuses"] == 1500


def test_reject_contact_and_return_to_reserve(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    user, app = recruiting.submit(conn, {"id": 6001, "username": "masha"}, {**FORM, "full_name": "Петрова Маша"})
    a = recruiting.contact(conn, owner, app["id"], "@manager_1")
    assert a["contact_manager"] == "manager_1"
    a, changed = recruiting.contact_answer(conn, 6001, app["id"], "cant_write")
    assert changed and a["contact_answer"] == "cant_write"
    n = FakeNotifier(conn)
    run(n.contact_answered(a))
    assert any("просит написать ему сам" in t for chat, t in n.sent if chat == 999)

    a, _ = recruiting.reject(conn, owner, app["id"], "Не подходит график", svc.new_key())
    assert a["status"] == "rejected" and svc.get_user(conn, user["id"])["status"] == "rejected"
    with pytest.raises(svc.ServiceError):  # rejected: no new questionnaire until returned
        recruiting.submit(conn, {"id": 6001}, FORM)
    a, _ = recruiting.return_to_reserve(conn, owner, app["id"], svc.new_key())
    assert a["status"] == "returned" and svc.get_user(conn, user["id"])["status"] == "pending"
    assert recruiting.current(conn, user["id"]) is None
    _, again = recruiting.submit(conn, {"id": 6001}, {**FORM, "full_name": "Петрова Мария"})
    assert again["id"] != app["id"]
    assert recruiting.view(conn, again)["earlier"] == 1  # the old one stays in the history
    assert recruiting.counts(conn)["new"] == 1 and recruiting.counts(conn)["rejected"] == 0


def test_http_register_with_start_param_and_onboarding(conn, make_user):
    n = FakeNotifier(conn)
    client = TestClient(create_app(conn, n))
    owner = {"X-Telegram-Init-Data": sign_init_data({"id": 999, "first_name": "Саня"}, settings.bot_token)}
    client.get("/api/me", headers=owner)
    friend = make_user(first="Аня")
    code = recruiting.ref_code(conn, friend)
    pairs = sign_init_data({"id": 7001, "first_name": "Пётр", "username": "pp"}, settings.bot_token)
    # start_param is signed together with the user (Telegram puts it into initData)
    from urllib.parse import parse_qsl, urlencode

    data = dict(parse_qsl(pairs))
    data.pop("hash")
    data["start_param"] = f"ref_{code}"
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    candidate = {"X-Telegram-Init-Data": urlencode(data)}

    assert client.get("/api/me", headers=candidate).json()["registered"] is False
    r = client.post("/api/register", json=FORM, headers=candidate)
    assert r.status_code == 200, r.text
    me = client.get("/api/me", headers=candidate).json()
    assert me["registered"] and me["application"]["status"] == "new"
    assert recruiting.current(conn, me["user"]["id"])["referrer_id"] == friend["id"]
    board = client.get("/api/admin/onboarding?tab=new", headers=owner).json()
    assert [a["full_name"] for a in board["items"]] == ["Сидоров Пётр Ильич"] and board["counts"]["new"] == 1
    assert board["items"][0]["referrer"].startswith("Аня")
    employee = {"X-Telegram-Init-Data": sign_init_data({"id": friend["telegram_id"], "first_name": "Аня"}, settings.bot_token)}
    assert client.get("/api/admin/onboarding", headers=employee).status_code == 403
    refs = client.get("/api/me/referrals", headers=employee).json()
    assert refs["code"] == code and refs["items"][0]["status"] == "new"


def test_cancel_training_tabs_and_a_corrected_stage(conn, make_user):
    """ТЗ 27.10 §12–14: «Отменить обучение» returns to the waiting list (not a refusal); «Завершённые» is its own tab;
    a corrected count is explained to the person."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer = make_user(first="Тренер")
    user, app = recruiting.submit(conn, {"id": 6101, "username": "kolya"}, {**FORM, "full_name": "Колин Коля"})
    day = now_local().date() + timedelta(days=1)
    recruiting.assign_training(conn, owner, app["id"], day, "11:00", "Европа", trainer["id"], svc.new_key())
    waiting = recruiting.submit(conn, {"id": 6102}, FORM)[1]
    assert recruiting.cancel_training(conn, owner, waiting["id"], svc.new_key())[1] is False  # nothing to cancel
    a, changed = recruiting.cancel_training(conn, owner, app["id"], svc.new_key())
    assert changed and a["status"] == "new" and a["training_date"] is None and a["trainer_id"] is None
    assert svc.get_user(conn, user["id"])["status"] == "pending"  # not a refusal
    n = FakeNotifier(conn)
    run(n.training_cancelled(a))
    assert any(chat == 6101 and "Обучение" in t and "отменено" in t for chat, t in n.sent)
    assert any(chat == trainer["telegram_id"] for chat, _ in n.sent)
    assert recruiting.counts(conn) | {} == {"new": 2, "scheduled": 0, "internship": 0, "done": 0, "rejected": 0}

    recruiting.assign_training(conn, owner, app["id"], day, "11:00", "Европа", trainer["id"], svc.new_key())
    recruiting.start_internship(conn, owner, app["id"], svc.new_key())
    a, _, _ = recruiting.confirm_stage(conn, owner, app["id"], 1, 18, 0, svc.new_key())
    assert recruiting.counts(conn)["done"] == 1 and recruiting.counts(conn)["internship"] == 0
    assert [x["id"] for x in recruiting.listing(conn, "done")] == [app["id"]]
    n.sent.clear()
    run(n.stage_confirmed(a, 1, None, reached=20))
    [text] = [t for chat, t in n.sent if chat == 6101]
    assert "Стажировка засчитана: 18" in text and "20 → 18" in text
    n.sent.clear()
    run(n.stage_confirmed(a, 1, None, reached=18))  # the same value — no correction line
    assert not any("скорректировал" in t for _, t in n.sent)
