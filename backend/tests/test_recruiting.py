"""The customer's module 3: referral link → questionnaire → training → internship → the referrer's bonuses."""

import hashlib
import hmac
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import finance, invites, payouts, pending, recruiting, training
from app import services as svc
from app.api import create_app
from app.auth import sign_init_data
from app.config import settings
from app.timeutil import now_local

from .conftest import requisites
from .test_api import headers
from .test_features import FakeNotifier, run

def invited(conn, referrer: dict, telegram_id: int) -> None:
    """The candidate opened the referrer's live link in the bot (ТЗ 27.10 §9)."""
    assert invites.use(conn, telegram_id, invites.current_ref(conn, referrer))


FORM = {"full_name": "Сидоров Пётр Ильич", "age": 19, "metro": "Чкаловская", "min_days": 3, "training_days": [1, 3],
        "communication": 8, "truthful": True, "consent": True}


def test_full_path_with_referrer_bonuses(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    friend = make_user(first="Аня")
    trainer = make_user(first="Тренер")
    link = invites.current_ref(conn, friend)
    assert invites.current_ref(conn, svc.get_user(conn, friend["id"]))["id"] == link["id"]  # the same within the hour
    with pytest.raises(svc.ServiceError) as e:  # ТЗ 27.10 §5: no questionnaire without a live link
        recruiting.submit(conn, {"id": 5001}, FORM)
    assert e.value.code == "no_invite"

    # the questionnaire: validation, then the referral from the link
    with pytest.raises(svc.ServiceError) as e:
        recruiting.submit(conn, {"id": 5001}, {**FORM, "consent": False})
    assert e.value.code == "consent_required"
    with pytest.raises(svc.ServiceError):
        recruiting.submit(conn, {"id": 5001}, {**FORM, "training_days": []})
    user, app = recruiting.submit(conn, {"id": 5001, "username": "petya"}, FORM, f"ref_{link['token']}")
    assert (user["status"], user["last_name"], user["first_name"]) == ("pending", "Сидоров", "Пётр Ильич")
    assert app["referrer_id"] == friend["id"] and app["status"] == "new"
    with pytest.raises(svc.ServiceError) as e:  # one questionnaire at a time
        recruiting.submit(conn, {"id": 5001}, FORM)
    assert e.value.code == "already_sent"
    assert [r["status"] for r in recruiting.referrals(conn, friend)] == ["new"]

    # the candidate hears back in the chat; when the bot can't reach them, the staff are warned
    class Unreachable(FakeNotifier):
        async def send(self, chat_id, text, markup=None, force=False):
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
    # 18.11: the goal shows the approved fact before the first payout, capped at the target
    assert (p["stage"], p["units"], p["review"]) == (1, 20, True) and p["fact"] == 21 and p["paid"] == 0
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
    invited(conn, make_user(first="Аня"), 6001)
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
    token = invites.current_ref(conn, friend)["token"]
    pairs = sign_init_data({"id": 7001, "first_name": "Пётр", "username": "pp"}, settings.bot_token)
    # start_param is signed together with the user (Telegram puts it into initData)
    from urllib.parse import parse_qsl, urlencode

    data = dict(parse_qsl(pairs))
    data.pop("hash")
    data["start_param"] = f"ref_{token}"
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    candidate = {"X-Telegram-Init-Data": urlencode(data)}

    assert client.get("/api/me", headers=candidate).json() | {} == {"registered": False, "entry": "ref", "stale_link": False,
                                                                   "bot_username": None}
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
    assert refs["items"][0]["status"] == "new" and 0 < refs["seconds_left"] <= 3600 and refs["ttl_minutes"] == 60


def test_cancel_training_tabs_and_a_corrected_stage(conn, make_user):
    """ТЗ 27.10 §12–14: «Отменить обучение» returns to the waiting list (not a refusal); «Завершённые» is its own tab;
    a corrected count is explained to the person."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer = make_user(first="Тренер")
    friend = make_user(first="Аня")
    invited(conn, friend, 6101)
    invited(conn, friend, 6102)
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


def test_the_payout_drives_and_closes_the_internship(conn, make_user):
    """18.11: the internship's goal counts what was PAID: a manual payout's units advance it at once, reaching
    the target closes the internship (the trainee becomes a regular employee). Only the first
    `internship_units` pieces of a trainee are training ones — the excess pays as usual."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer = make_user(first="Тренер")
    invited(conn, make_user(first="Аня"), 8001)
    user, app = recruiting.submit(conn, {"id": 8001, "username": "stazh"}, {**FORM, "full_name": "Стажёров Стажёр"})
    app, _ = recruiting.assign_training(conn, owner, app["id"], now_local().date() + timedelta(days=1), "11:00",
                                        "Европа", trainer["id"], svc.new_key())
    app, _ = recruiting.start_internship(conn, owner, app["id"], svc.new_key())
    trainee = svc.get_user(conn, user["id"])
    requisites(conn, trainee)
    today = now_local().date()

    def shift(day, volume, approved=True):
        s = svc.create_shift(conn, trainee, day, {"start_time": "10:00", "end_time": "16:00", "volume": volume,
                                                  "location": "Европа"}, svc.new_key(), today=today).shift
        if approved:
            conn.execute("UPDATE shifts SET report_state = 'approved', fact_volume = ? WHERE id = ?", (volume, s["id"]))
        return s

    # the first 20 pieces are the internship (training shifts); the third shift is beyond the goal — a regular one
    first, second = shift(today + timedelta(days=1), 10), shift(today + timedelta(days=2), 10)
    assert first["training"] == 1 and second["training"] == 1
    extra = shift(today + timedelta(days=3), 10)
    assert extra["training"] == 0

    d_from, d_to = today + timedelta(days=1), today + timedelta(days=3)
    amounts, included = {trainee["id"]: 1200}, {trainee["id"]}  # 1200 ₽ at the common 120 → 10 units
    rows = {r["user_id"]: r for r in payouts.calculate(conn, d_from, d_to, payouts.Rates.from_dict(None), None,
                                                       amounts, included)}
    assert rows[trainee["id"]]["entered_units"] == 10
    assert rows[trainee["id"]]["internship"] == {"app_id": app["id"], "target": 20, "left": 20, "take": 10}
    batch = payouts.commit(conn, owner, "Стажёру", d_from, d_to, payouts.Rates.from_dict(None), None,
                           [{"user_id": trainee["id"], "amount": 1200}], [], [], svc.new_key())
    assert not batch.get("internship_closed")  # 10 of 20 — the internship goes on
    # the goal is corrected by the payout at once: it shows what was paid, not the fact
    assert recruiting.progress(conn, recruiting.get(conn, app["id"]))["units"] == 10

    # the next payout pays the missing 10 — the internship closes itself, the person is in the team
    batch = payouts.commit(conn, owner, "Стажёру 2", today + timedelta(days=4), today + timedelta(days=4),
                           payouts.Rates.from_dict(None), None, [{"user_id": trainee["id"], "amount": 1200}],
                           [], [], svc.new_key())
    assert len(batch["internship_closed"]) == 1 and batch["internship_closed"][0]["stage1_counted"] == 20
    assert not recruiting.is_trainee(svc.get_user(conn, user["id"]))
    assert recruiting.get(conn, app["id"])["status"] == "done"


def test_the_owner_corrects_the_internship_by_hand(conn, make_user):
    """18.11: the owner sets the paid counter by hand — the target closes the internship; an auto-closed one
    returns to «Стажировка» when the value drops below the target."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer = make_user(first="Тренер")
    invited(conn, make_user(first="Аня"), 8101)
    user, app = recruiting.submit(conn, {"id": 8101, "username": "stazh2"}, {**FORM, "full_name": "Второй Стажёр"})
    app, _ = recruiting.assign_training(conn, owner, app["id"], now_local().date() + timedelta(days=1), "11:00",
                                        "Европа", trainer["id"], svc.new_key())
    app, _ = recruiting.start_internship(conn, owner, app["id"], svc.new_key())

    a, closed = recruiting.set_internship_units(conn, owner, app["id"], 7, svc.new_key())
    assert not closed and recruiting.progress(conn, a)["units"] == 7
    a, closed = recruiting.set_internship_units(conn, owner, app["id"], 20, svc.new_key())
    assert closed and a["status"] == "done" and a["stage1_counted"] == 20
    assert not recruiting.is_trainee(svc.get_user(conn, user["id"]))
    a, closed = recruiting.set_internship_units(conn, owner, app["id"], 5, svc.new_key())
    assert not closed and a["status"] == "internship" and recruiting.is_trainee(svc.get_user(conn, user["id"]))
    with pytest.raises(svc.ServiceError) as e:  # beyond the target makes no sense
        recruiting.set_internship_units(conn, owner, app["id"], 25, svc.new_key())
    assert e.value.code == "bad_units"
    with pytest.raises(svc.ServiceError):  # a trainer without the registrations cap can't
        recruiting.set_internship_units(conn, trainer, app["id"], 10, svc.new_key())


def test_app_access_opens_before_the_internship(conn, make_user):
    """18.11: «Выдать доступ к приложению» approves the candidate's account while the application is still
    «scheduled» — the Mini App opens, the person is not working yet; the internship then starts the goal."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer = make_user(first="Тренер")
    invited(conn, make_user(first="Аня"), 8201)
    user, app = recruiting.submit(conn, {"id": 8201, "username": "stazh3"}, {**FORM, "full_name": "Третий Стажёр"})
    with pytest.raises(svc.ServiceError):  # no trainer assigned yet
        training.start(conn, owner, app["id"], svc.new_key())
    app, _ = recruiting.assign_training(conn, owner, app["id"], now_local().date() + timedelta(days=1), "11:00",
                                        "Европа", trainer["id"], svc.new_key())
    app, changed = training.start(conn, owner, app["id"], svc.new_key())
    assert changed and app["training_started_at"]
    u = svc.get_user(conn, user["id"])
    assert u["status"] == "approved" and not recruiting.is_trainee(u) and u["work_status"] == "not_working"
    app, _ = recruiting.start_internship(conn, owner, app["id"], svc.new_key())
    assert recruiting.is_trainee(svc.get_user(conn, user["id"]))


def test_unnetworked_people_count_as_a_decision(conn, make_user):
    """18.11: an approved employee of no network is a pending decision for the owner and the admins."""
    from app import networks

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    loose, netted = make_user(first="Гуля"), make_user(first="Всети")
    north = networks.create(conn, owner, "Север", svc.new_key())
    networks.set_member(conn, owner, netted["id"], north["id"], svc.new_key())
    assert pending.counts(conn, owner)["no_network"] == 1  # the owner without a network doesn't count
    networks.set_member(conn, owner, loose["id"], north["id"], svc.new_key())
    assert pending.counts(conn, owner)["no_network"] == 0


# ---------------------------------------------------------------- 19.11: «Скрыть» и заблокированные в «Завершённых»


def _finished(conn, referrer: dict, telegram_id: int) -> tuple[dict, dict]:
    """A hired person: the application is done (as after «Принять в команду»)."""
    link = invites.current_ref(conn, referrer)
    user, app = recruiting.submit(conn, {"id": telegram_id}, FORM, f"ref_{link['token']}")
    conn.execute("UPDATE applications SET status = 'done', stage1_at = ?, stage1_counted = 20 WHERE id = ?",
                 (svc.now_iso(), app["id"]))
    return user, app


def test_blocked_hired_vanishes_from_done(conn):
    """A person the team blocked is not shown in «Завершённые» at all — «принят в команду» would be a lie."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    good = _finished(conn, owner, 5101)
    bad = _finished(conn, owner, 5102)
    conn.execute("UPDATE users SET account_state = 'blocked' WHERE id = ?", (bad[0]["id"],))
    assert [a["id"] for a in recruiting.listing(conn, "done")] == [good[1]["id"]]
    assert recruiting.counts(conn)["done"] == 1


def test_hide_and_return_finished(conn):
    """«Скрыть» takes a finished card out of «Завершённые» and its count; «Вернуть» brings it back."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    user, app = _finished(conn, owner, 5103)
    hidden = recruiting.set_hidden(conn, owner, app["id"], True, svc.new_key())
    assert hidden["hidden_at"] and recruiting.view(conn, hidden)["hidden"] is True
    assert [a["id"] for a in recruiting.listing(conn, "done") if not a["hidden_at"]] == []
    assert recruiting.counts(conn)["done"] == 0
    assert recruiting.set_hidden(conn, owner, app["id"], False, svc.new_key())["hidden_at"] is None
    assert [a["id"] for a in recruiting.listing(conn, "done")] == [app["id"]]
    conn.execute("UPDATE applications SET status = 'scheduled' WHERE id = ?", (app["id"],))
    with pytest.raises(svc.ServiceError) as e:  # only the finished ones can be hidden
        recruiting.set_hidden(conn, owner, app["id"], True, svc.new_key())
    assert e.value.code == "bad_status"


def test_hide_http_and_pending_counts(conn):
    """The route works, is idempotent and feeds the same listing; a finished internship without the checked
    second stage is not counted as «Стажировки на проверку» anywhere — only a running one is."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    user, app = _finished(conn, owner, 5104)
    assert pending.counts(conn, owner)["internship_review"] == 0
    conn.execute("UPDATE applications SET status = 'internship', finish_requested_at = ? WHERE id = ?",
                 (svc.now_iso(), app["id"]))
    assert pending.counts(conn, owner)["internship_review"] == 1
    conn.execute("UPDATE applications SET status = 'done', finish_requested_at = NULL WHERE id = ?", (app["id"],))

    n = FakeNotifier(conn)
    client = TestClient(create_app(conn, n))
    h = headers(999, "Саня", "Владелец", "boss")
    key = svc.new_key()
    r = client.post(f"/api/admin/onboarding/{app['id']}/hide", json={"on": True, "idempotency_key": key}, headers=h)
    assert r.status_code == 200 and r.json()["application"]["hidden"] is True
    r = client.post(f"/api/admin/onboarding/{app['id']}/hide", json={"on": True, "idempotency_key": key}, headers=h)
    assert r.json()["application"]["hidden"] is True  # the same key — the same answer, nothing breaks
    r = client.get("/api/admin/onboarding?tab=done", headers=h)
    assert r.json()["counts"]["done"] == 0  # the tab pill doesn't count the hidden one
    assert [(a["id"], a["hidden"]) for a in r.json()["items"]] == [(app["id"], True)]  # the card — to «Скрытые»
    r = client.post(f"/api/admin/onboarding/{app['id']}/hide", json={"on": False, "idempotency_key": svc.new_key()}, headers=h)
    assert r.json()["application"]["hidden"] is False
    assert [a["id"] for a in recruiting.listing(conn, "done")] == [app["id"]]
