"""ТЗ 28.10, этап 2: онбординг и раздел «Обучение» — тренер, согласование, запросы, отмены, отказ, группы."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import groups, recruiting, templates, training
from app import services as svc
from app.api import create_app
from app.notify import Notifier
from app.timeutil import today_local

from .test_api import headers, key
from .test_features import run
from .test_groups import MAIN, PRE, FakeBot, connected


class Bot(FakeBot):
    """FakeBot that also remembers messages."""

    def __init__(self):
        super().__init__()
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, disable_web_page_preview=None):
        self.sent.append((chat_id, text, reply_markup))
        from types import SimpleNamespace

        return SimpleNamespace(message_id=len(self.sent))

    async def edit_message_text(self, text, chat_id, message_id, parse_mode=None, disable_web_page_preview=None):
        self.sent.append((chat_id, "EDIT:" + text, None))

    def to(self, chat_id) -> list[str]:
        return [t for c, t, _ in self.sent if c == chat_id]


FORM = {"full_name": "Сидоров Пётр Ильич", "age": 22, "metro": "Сокол", "min_days": 3, "training_days": [0, 2],
        "communication": 8, "consent": True, "truthful": True}


@pytest.fixture
def world(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    ref = make_user(first="Пригласил", username="ref")
    vera = make_user(first="Вера", username=None)  # the trainer — any employee, no username
    other = make_user(first="Олег", username="oleg")
    from app import invites

    assert invites.use(conn, 7001, invites.current_ref(conn, ref))  # the candidate opened the inviter's live link
    user, app = recruiting.submit(conn, {"id": 7001, "username": None}, FORM)
    bot = Bot()
    n = Notifier(bot, conn)
    connected(conn, owner)
    client = TestClient(create_app(conn, n, "test_bot"))
    oh = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=oh)
    return {"owner": owner, "ref": ref, "vera": vera, "other": other, "cand": user, "app": recruiting.get(conn, app["id"]),
            "bot": bot, "n": n, "client": client, "oh": oh, "vh": headers(vera["telegram_id"], "Вера", "Петров", None)}


def test_trainer_then_date_then_both_confirm_then_predzakrep(conn, world):
    c, oh, bot, app, vera = world["client"], world["oh"], world["bot"], world["app"], world["vera"]
    # §2: the preview first — nothing is sent until «Отправить»
    preview = c.get(f"/api/admin/onboarding/{app['id']}/trainer-preview?trainer_id={vera['id']}", headers=oh).json()["text"]
    assert preview.startswith("🎉 Хорошие новости!") and f'tg://user?id={vera["telegram_id"]}' in preview
    assert bot.to(7001) == []
    r = c.post(f"/api/admin/onboarding/{app['id']}/trainer", json={"trainer_id": vera["id"], "idempotency_key": key()}, headers=oh)
    a = r.json()["application"]
    assert a["status"] == "scheduled" and a["stage"] == "trainer" and a["contact_timer_at"] and len(a["messages"]) == 1
    assert "Свяжитесь с" in bot.to(7001)[-1] and "48 часов" in bot.to(7001)[-1]
    assert a["trainer"]["id"] == vera["id"] and a["referrer_id"] == world["ref"]["id"]  # inviter and trainer apart
    # «Обучение» opened for the trainer by itself; they see the candidate
    assert c.get("/api/me", headers=world["vh"]).json()["training"] is True
    assert [x["id"] for x in c.get("/api/training", headers=world["vh"]).json()["items"]] == [app["id"]]
    assert c.get("/api/training", headers=headers(world["other"]["telegram_id"])).status_code == 403
    # a resend restarts the count and keeps the history; a manual reset too
    c.post(f"/api/admin/onboarding/{app['id']}/trainer-message", json={"idempotency_key": key()}, headers=oh)
    a = c.post(f"/api/admin/onboarding/{app['id']}/timer-reset", json={"idempotency_key": key()}, headers=oh).json()["application"]
    assert [m["kind"] for m in a["messages"]] == ["trainer", "trainer", "timer_reset"] and a["status"] == "scheduled"
    # the inviter is asked to help
    c.post(f"/api/admin/onboarding/{app['id']}/remind-referrer", json={"idempotency_key": key()}, headers=oh)
    assert "Сидоров Пётр Ильич" in bot.to(world["ref"]["telegram_id"])[-1]
    # the date: the candidate and the trainer confirm; «Предзакреп» only after both
    day = (today_local() + timedelta(days=2)).isoformat()
    a = c.post(f"/api/admin/onboarding/{app['id']}/training",
               json={"date": day, "time": "11:00", "location": "Европа", "idempotency_key": key()}, headers=oh).json()["application"]
    assert a["stage"] == "confirm" and "кандидата и тренера" in a["stage_title"]
    run(world["n"].training_assigned(recruiting.get(conn, app["id"])))
    a, changed = recruiting.confirm_training(conn, 7001, app["id"])
    assert changed and run(world["n"].invite_pre_if_agreed(a)) is None  # the trainer hasn't confirmed yet
    r = c.post(f"/api/training/{app['id']}/confirm", json={"idempotency_key": key()}, headers=world["vh"])
    assert r.json()["application"]["stage"] == "agreed"  # the app invites to «Предзакреп» right away
    assert recruiting.get(conn, app["id"])["pre_invited_at"]
    invite = next(x for x in reversed(bot.sent) if x[0] == 7001)
    assert invite[0] == 7001 and "Обучение согласовано" in invite[1]
    assert invite[2].inline_keyboard[0][0].text == "Вступить в группу «Предзакреп»"
    assert run(world["n"].invite_pre_if_agreed(recruiting.get(conn, app["id"]))) is None  # once


def assigned(conn, world, day_offset=2):
    c, oh, app = world["client"], world["oh"], world["app"]
    c.post(f"/api/admin/onboarding/{app['id']}/trainer", json={"trainer_id": world["vera"]["id"], "idempotency_key": key()}, headers=oh)
    day = (today_local() + timedelta(days=day_offset)).isoformat()
    c.post(f"/api/admin/onboarding/{app['id']}/training",
           json={"date": day, "time": "11:00", "location": "Европа", "idempotency_key": key()}, headers=oh)
    return recruiting.get(conn, app["id"])


def test_trainer_requests_wait_for_an_admin(conn, world):
    c, oh, vh, app = world["client"], world["oh"], world["vh"], assigned(conn, world)
    k = key()
    r = c.post(f"/api/training/{app['id']}/request", json={"kind": "start", "idempotency_key": k}, headers=vh).json()
    again = c.post(f"/api/training/{app['id']}/request", json={"kind": "start", "idempotency_key": key()}, headers=vh).json()
    assert again["request"]["id"] == r["request"]["id"]  # a second tap — the same request
    assert r["application"]["stage"] == "confirm" and r["application"]["requests"][0]["kind"] == "start"
    assert c.get("/api/admin/pending", headers=oh).json()["training_requests"] == 1
    assert c.get("/api/admin/pending", headers=vh).status_code == 403  # an «Обучающий» decides nothing
    # admins got a card with buttons; deciding edits every card
    cards = [t for _, t, m in world["bot"].sent if m is not None and "Запрос: Начать обучение" in t]
    assert cards
    d = c.post(f"/api/admin/training-requests/{r['request']['id']}/decide", json={"approve": True, "idempotency_key": key()},
               headers=oh).json()
    assert d["requests"] == [] and recruiting.get(conn, app["id"])["training_started_at"]
    assert any(t.startswith("EDIT:") and "Одобрено" in t for _, t, _ in world["bot"].sent)
    # a refusal and a cancellation need a reason; rejecting a request needs a reason too
    bad = c.post(f"/api/training/{app['id']}/request", json={"kind": "refuse", "reason": " ", "idempotency_key": key()}, headers=vh)
    assert bad.status_code == 422 and bad.json()["code"] == "reason_required"
    req = c.post(f"/api/training/{app['id']}/request", json={"kind": "internship", "idempotency_key": key()}, headers=vh).json()["request"]
    no = c.post(f"/api/admin/training-requests/{req['id']}/decide", json={"approve": False, "idempotency_key": key()}, headers=oh)
    assert no.status_code == 422
    c.post(f"/api/admin/training-requests/{req['id']}/decide",
           json={"approve": False, "reason": "Рано", "idempotency_key": key()}, headers=oh)
    assert recruiting.get(conn, app["id"])["status"] == "scheduled"  # rejected: nothing changed
    assert "Рано" in world["bot"].to(world["vera"]["telegram_id"])[-1]


def test_change_request_keeps_the_old_until_approved_and_both_confirm_again(conn, world):
    c, oh, vh = world["client"], world["oh"], world["vh"]
    app = assigned(conn, world)
    training.confirm_by_trainer(conn, world["vera"], app["id"])
    recruiting.confirm_training(conn, 7001, app["id"])
    new_day = (today_local() + timedelta(days=5)).isoformat()
    req = c.post(f"/api/training/{app['id']}/request", json={"kind": "change", "reason": "Заболел", "date": new_day,
                                                              "trainer_id": world["other"]["id"], "idempotency_key": key()},
                 headers=vh).json()["request"]
    still = recruiting.get(conn, app["id"])
    assert still["training_date"] == app["training_date"] and still["trainer_id"] == world["vera"]["id"]
    card = next(t for _, t, m in world["bot"].sent if m is not None and "Запрос: Изменить обучение" in t)
    assert "→ <b>" + new_day in card and "Новый тренер" in card and "Заболел" in card
    c.post(f"/api/admin/training-requests/{req['id']}/decide", json={"approve": True, "idempotency_key": key()}, headers=oh)
    a = recruiting.get(conn, app["id"])
    assert a["training_date"] == new_day and a["trainer_id"] == world["other"]["id"]
    assert not a["training_confirmed_at"] and not a["trainer_confirmed_at"]  # both confirm the new terms (В9)
    assert training.cancellations(conn, app["id"]) == []  # a reschedule is not a cancellation
    # the old trainer is told and loses the candidate; the new one gets «Обучение»
    assert any("передан другому тренеру" in t for t in world["bot"].to(world["vera"]["telegram_id"]))
    assert c.get("/api/training", headers=vh).json()["items"] == []
    oleg = headers(world["other"]["telegram_id"], "Олег", "Петров", "oleg")
    assert [x["id"] for x in c.get("/api/training", headers=oleg).json()["items"]] == [app["id"]]
    # an old confirmation button (older version) is refused
    with pytest.raises(svc.ServiceError) as e:
        training.confirm_by_trainer(conn, world["other"], app["id"], version=1)
    assert e.value.code == "stale"


def test_a_stale_change_request_is_not_applied(conn, world):
    c, oh, vh = world["client"], world["oh"], world["vh"]
    app = assigned(conn, world)
    req = c.post(f"/api/training/{app['id']}/request", json={"kind": "change", "reason": "Неудобно", "time": "15:00",
                                                              "idempotency_key": key()}, headers=vh).json()["request"]
    # meanwhile the admin changes the training directly
    c.post(f"/api/admin/onboarding/{app['id']}/training", json={"date": app["training_date"], "time": "12:00",
                                                                 "location": "Европа", "idempotency_key": key()}, headers=oh)
    r = c.post(f"/api/admin/training-requests/{req['id']}/decide", json={"approve": True, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 404 or r.json().get("request", {}).get("status") != "approved"
    assert recruiting.get(conn, app["id"])["training_time"] == "12:00"


def test_cancellations_are_counted_and_three_only_warn(conn, world):
    c, oh, app = world["client"], world["oh"], world["app"]
    for i in range(3):
        app = assigned(conn, world, day_offset=2 + i)
        r = c.post(f"/api/admin/onboarding/{app['id']}/cancel-training",
                   json={"reason": "" if i == 0 else "Кандидат попросил", "kind": "no_show" if i == 2 else "cancel",
                         "idempotency_key": key()}, headers=oh)
        if i == 0:
            assert r.status_code == 422  # the reason is required
            r = c.post(f"/api/admin/onboarding/{app['id']}/cancel-training",
                       json={"reason": "Тренер заболел", "idempotency_key": key()}, headers=oh)
        a = r.json()["application"]
        # ТЗ 30.10 §5: a cancellation ends the attempt — back to «Ожидают рассмотрения», without the trainer
        assert a["status"] == "new" and a["trainer"] is None and not a["training_date"]
    assert a["cancel_count"] == 3 and a["cancel_limit"] == 3 and a["cancel_warning"] is True
    assert a["stage"] == "cancelled" and [x["kind"] for x in a["cancellations"]] == ["cancel", "cancel", "no_show"]
    assert recruiting.get(conn, app["id"])["status"] == "new"  # no automatic refusal
    a = c.post(f"/api/admin/onboarding/{app['id']}/cancel-ack", json={"idempotency_key": key()}, headers=oh).json()["application"]
    assert a["cancel_warning"] is False and a["cancel_count"] == 3


def test_refusal_blocks_kicks_from_predzakrep_and_can_be_undone(conn, world):
    c, oh, bot = world["client"], world["oh"], world["bot"]
    app = assigned(conn, world)
    bot.members[(PRE, 7001)] = "member"
    r = c.post(f"/api/admin/onboarding/{app['id']}/reject", json={"reason": "", "idempotency_key": key()}, headers=oh)
    assert r.status_code == 422
    r = c.post(f"/api/admin/onboarding/{app['id']}/reject", json={"reason": "Не явился", "idempotency_key": key()}, headers=oh)
    assert r.json()["application"]["status"] == "rejected"
    user = svc.get_user(conn, world["cand"]["id"])
    assert user["account_state"] == "blocked" and user["status"] == "rejected"
    assert bot.members[(PRE, 7001)] == "left"  # out of «Предзакреп»
    assert any("отказать" in t and "Не явился" in t for t in bot.to(7001))  # told despite the block
    assert c.get("/api/training", headers=world["vh"]).json()["items"] == []  # gone from the trainer's list
    # «Вернуть в резерв» undoes it
    c.post(f"/api/admin/onboarding/{app['id']}/return", json={"idempotency_key": key()}, headers=oh)
    user = svc.get_user(conn, world["cand"]["id"])
    assert user["account_state"] == "active" and user["status"] == "pending"


def test_training_access_cannot_be_revoked_while_candidates_remain(conn, world):
    c, oh = world["client"], world["oh"]
    app = assigned(conn, world)
    r = c.post(f"/api/admin/team/{world['vera']['id']}/training-access", json={"allow": False, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 409 and r.json()["code"] == "has_candidates"
    c.post(f"/api/admin/onboarding/{app['id']}/trainer", json={"trainer_id": world["other"]["id"], "idempotency_key": key()},
           headers=oh)
    r = c.post(f"/api/admin/team/{world['vera']['id']}/training-access", json={"allow": False, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 200 and c.get("/api/me", headers=world["vh"]).json()["training"] is False
    r = c.post(f"/api/admin/team/{world['other']['id']}/training-access", json={"allow": True, "idempotency_key": key()}, headers=oh)
    assert r.json()["training"]["granted"] is True


def test_internship_finish_and_accept_to_team_invites_to_the_main_group(conn, world):
    c, oh, vh, bot = world["client"], world["oh"], world["vh"], world["bot"]
    app = assigned(conn, world)
    recruiting.start_internship(conn, world["owner"], app["id"], key())
    r = c.post(f"/api/training/{app['id']}/finish", json={"idempotency_key": key()}, headers=vh).json()["application"]
    assert r["stage"] == "finish_review" and "ожидает проверки администратора" in r["stage_title"]
    assert recruiting.get(conn, app["id"])["status"] == "internship"  # not accepted by itself
    assert c.get("/api/admin/pending", headers=oh).json()["internship_review"] == 1
    c.post(f"/api/admin/onboarding/{app['id']}/stage", json={"stage": 1, "counted": 0, "bonus": 0, "idempotency_key": key()},
           headers=oh)
    import time
    time.sleep(0.2)
    assert recruiting.get(conn, app["id"])["status"] == "done"


def test_legacy_cancellations_and_the_message_template(conn):
    """The texts are editable; the inviter and the trainer stay different fields."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    templates.save_text(conn, owner, "candidate_trainer", "Напишите {ФИО_тренера}", svc.new_key())
    n = Notifier(None, conn)
    trainer = {"telegram_id": 5, "username": "vera", "first_name": "Вера", "last_name": "П"}
    assert n.trainer_message_text(trainer) == 'Напишите <a href="https://t.me/vera">Вера П</a> (@vera)'
    assert groups.expected_for(conn, {"id": 12345}) == "main"  # no questionnaire — the main group
