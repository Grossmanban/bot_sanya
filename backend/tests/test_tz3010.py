"""ТЗ 30.10: дата обучения от тренера, вход по ссылке после попытки без приглашения и ручное восстановление доступа,
сортировка «Обучения» по взаимному подтверждению, отмена обучения с возвратом в «Ожидают рассмотрения» (попытки,
счётчик, история), дата и время отправки анкеты по Москве."""

import sqlite3
import time
from datetime import datetime, timedelta

import pytest

from app import invites, recruiting, training
from app import services as svc
from app.db import MIGRATIONS, migrate
from app.timeutil import msk_datetime, now_local, today_local

from .test_api import headers, key
from .test_tz2810_training import FORM, assigned, world  # noqa: F401 — the fixture


def wait_for(cond, timeout=2.0):
    """Notifications are spawned after the response: wait until they are out."""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def give_trainer(world):
    c, oh, app = world["client"], world["oh"], world["app"]
    c.post(f"/api/admin/onboarding/{app['id']}/trainer", json={"trainer_id": world["vera"]["id"], "send": False,
                                                              "idempotency_key": key()}, headers=oh)


def day(offset):
    return (today_local() + timedelta(days=offset)).isoformat()


# ---------------------------------------------------------------- §2: the trainer sets and changes the date


def test_trainer_sets_and_changes_the_date_with_notifications(conn, world):
    c, vh, bot, app = world["client"], world["vh"], world["bot"], world["app"]
    give_trainer(world)
    bot.sent.clear()
    r = c.post(f"/api/training/{app['id']}/schedule",
               json={"date": day(2), "time": "11:00", "location": "Европа", "idempotency_key": key()}, headers=vh)
    assert r.status_code == 200 and r.json()["changed"] is True
    a = r.json()["application"]
    assert a["training_date"] == day(2) and a["training_time"] == "11:00"
    # the trainer who set it has confirmed it; the candidate confirms with the usual button
    assert a["trainer_confirmed_at"] and not a["training_confirmed_at"]
    assert a["agreement"] == "waiting_candidate" and a["stage"] == "confirm"
    assert wait_for(lambda: bot.to(999) and bot.to(world["vera"]["telegram_id"]))
    cand = [m for ch, t, m in bot.sent if ch == 7001]
    version = recruiting.get(conn, app["id"])["version"]
    assert cand and cand[-1].inline_keyboard[0][0].callback_data == f"trc:{app['id']}:{version}"
    assert "Ваше обучение запланировано" in bot.to(7001)[-1] and "11:00" in bot.to(7001)[-1]
    owner = bot.to(999)[-1]
    assert "Тренер назначил дату обучения" in owner and "Сидоров Пётр Ильич" in owner and "Вера" in owner
    assert "Дата обучения назначена" in bot.to(world["vera"]["telegram_id"])[-1]
    # the admin's list shows the same date
    board = c.get("/api/admin/onboarding?tab=scheduled", headers=world["oh"]).json()
    assert board["items"][0]["training_date"] == day(2)

    # a change: the old and the new date to the owner, «перенесено» to the candidate, the candidate confirms again
    recruiting.confirm_training(conn, 7001, app["id"])
    bot.sent.clear()
    a = c.post(f"/api/training/{app['id']}/schedule",
               json={"date": day(3), "time": "15:30", "location": "Европа", "idempotency_key": key()}, headers=vh).json()["application"]
    assert a["training_date"] == day(3) and not a["training_confirmed_at"] and a["trainer_confirmed_at"]
    assert wait_for(lambda: bot.to(999))
    owner = bot.to(999)[-1]
    assert "Тренер изменил дату обучения" in owner and "Было:" in owner and "11:00" in owner and "15:30" in owner
    assert "Обучение перенесено" in bot.to(7001)[-1]
    assert wait_for(lambda: bot.to(world["vera"]["telegram_id"]))
    assert "изменена" in bot.to(world["vera"]["telegram_id"])[-1]
    # the same values again — nothing changes, nobody is disturbed, the confirmations stay
    recruiting.confirm_training(conn, 7001, app["id"])
    bot.sent.clear()
    r = c.post(f"/api/training/{app['id']}/schedule",
               json={"date": day(3), "time": "15:30", "location": "Европа", "idempotency_key": key()}, headers=vh).json()
    assert r["changed"] is False and r["application"]["training_confirmed_at"]
    time.sleep(0.1)
    assert bot.sent == []
    # history: the date set by the trainer is in the attempt's events
    titles = [e["title"] for e in r["application"]["attempts"][-1]["events"]]
    assert any("Назначены дата и время" in t and "(тренер)" in t for t in titles)
    assert "Кандидат подтвердил дату" in titles


def test_only_the_own_trainer_may_set_the_date(conn, world):
    c, app, other = world["client"], world["app"], world["other"]
    give_trainer(world)
    body = {"date": day(2), "time": "11:00", "location": "Европа"}
    oh = headers(other["telegram_id"], "Олег", "Петров", "oleg")
    assert c.post(f"/api/training/{app['id']}/schedule", json={**body, "idempotency_key": key()}, headers=oh).status_code == 403
    # even with «Обучение» given by an admin — not their candidate
    training.set_access(conn, world["owner"], other["id"], True, key())
    r = c.post(f"/api/training/{app['id']}/schedule", json={**body, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    r = c.post(f"/api/training/{app['id']}/cancel", json={"reason": "Хочу", "idempotency_key": key()}, headers=oh)
    assert r.status_code == 403
    assert recruiting.get(conn, app["id"])["training_date"] is None
    # a past date and a bad time are refused for the real trainer too
    vh = world["vh"]
    r = c.post(f"/api/training/{app['id']}/schedule", json={**body, "date": day(-1), "idempotency_key": key()}, headers=vh)
    assert r.status_code == 422 and r.json()["code"] == "past_date"
    r = c.post(f"/api/training/{app['id']}/schedule", json={**body, "time": "25:99", "idempotency_key": key()}, headers=vh)
    assert r.status_code == 422
    # the admin keeps managing the training
    r = c.post(f"/api/admin/onboarding/{app['id']}/training",
               json={"date": day(4), "time": "10:00", "location": "Европа", "idempotency_key": key()}, headers=world["oh"])
    assert r.status_code == 200 and r.json()["application"]["training_date"] == day(4)


# ---------------------------------------------------------------- §5: cancellation → «Ожидают рассмотрения»


def test_trainer_cancels_candidate_goes_back_to_review_with_history(conn, world):
    c, vh, oh, bot = world["client"], world["vh"], world["oh"], world["bot"]
    app = assigned(conn, world)
    recruiting.confirm_training(conn, 7001, app["id"])
    training.confirm_by_trainer(conn, world["vera"], app["id"])
    r = c.post(f"/api/training/{app['id']}/cancel", json={"reason": " ", "idempotency_key": key()}, headers=vh)
    assert r.status_code == 422  # a reason is required
    bot.sent.clear()
    r = c.post(f"/api/training/{app['id']}/cancel", json={"reason": "Кандидат заболел", "idempotency_key": key()}, headers=vh)
    assert r.status_code == 200
    a = r.json()["application"]
    assert a["status"] == "new" and a["trainer"] is None and a["training_date"] is None
    assert a["stage"] == "cancelled" and a["agreement"] == "cancelled" and a["cancel_count"] == 1
    # out of the trainer's «Обучение», into «Ожидают рассмотрения»
    assert c.get("/api/training", headers=vh).json()["items"] == []
    board = c.get("/api/admin/onboarding?tab=new", headers=oh).json()
    assert [x["id"] for x in board["items"]] == [app["id"]] and board["counts"]["scheduled"] == 0
    # the attempt is kept with everything it had
    [att] = a["attempts"]
    assert att["status"] == "cancelled" and att["trainer"].startswith("Вера") and att["reason"] == "Кандидат заболел"
    assert att["training_date"] == app["training_date"] and att["training_time"] == "11:00"
    assert att["candidate_confirmed_at"] and att["trainer_confirmed_at"] and att["cancelled_at"]
    assert [e["title"] for e in att["events"]][0].startswith("Назначен тренер") and att["events"][-1]["title"] == "Обучение отменено"
    # the candidate, the trainer and the owner are told: who, which trainer, why
    assert wait_for(lambda: bot.to(999) and bot.to(7001) and bot.to(world["vera"]["telegram_id"]))
    assert "отменено" in bot.to(7001)[-1] and "Кандидат заболел" in bot.to(7001)[-1]
    assert "Кандидат заболел" in bot.to(world["vera"]["telegram_id"])[-1] and "Ожидают рассмотрения" in bot.to(world["vera"]["telegram_id"])[-1]
    owner = bot.to(999)[-1]
    assert "Сидоров Пётр Ильич" in owner and "Вера" in owner and "Кандидат заболел" in owner
    # the old trainer can't touch the candidate any more
    r = c.post(f"/api/training/{app['id']}/schedule",
               json={"date": day(2), "time": "11:00", "location": "Европа", "idempotency_key": key()}, headers=vh)
    assert r.status_code == 403

    # a new attempt with another trainer — the counter stays, the first attempt is not overwritten
    c.post(f"/api/admin/onboarding/{app['id']}/trainer", json={"trainer_id": world["other"]["id"], "send": False,
                                                              "idempotency_key": key()}, headers=oh)
    a = recruiting.view(conn, recruiting.get(conn, app["id"]))
    assert a["status"] == "scheduled" and a["cancel_count"] == 1 and a["attempt"] == 2
    assert [x["status"] for x in a["attempts"]] == ["cancelled", "active"] and a["attempts"][0]["trainer"].startswith("Вера")
    assert a["attempts"][1]["trainer"].startswith("Олег") and a["attempts"][1]["training_date"] is None
    # the admin cancels the second one: 2/3, the history keeps both
    r = c.post(f"/api/admin/onboarding/{app['id']}/cancel-training",
               json={"reason": "Не отвечает", "idempotency_key": key()}, headers=oh).json()["application"]
    assert r["status"] == "new" and r["cancel_count"] == 2
    assert [x["reason"] for x in r["attempts"]] == ["Кандидат заболел", "Не отвечает"]
    # the same or another trainer again — 3/3 warns, no automatic refusal
    for i in range(2):
        c.post(f"/api/admin/onboarding/{app['id']}/trainer", json={"trainer_id": world["vera"]["id"], "send": False,
                                                                  "idempotency_key": key()}, headers=oh)
        if i == 0:
            r = c.post(f"/api/admin/onboarding/{app['id']}/cancel-training",
                       json={"reason": "Перенос", "idempotency_key": key()}, headers=oh).json()["application"]
    assert r["cancel_count"] == 3 and r["cancel_warning"] is True
    assert recruiting.get(conn, app["id"])["status"] == "scheduled" and svc.get_user(conn, world["cand"]["id"])["status"] == "pending"


def test_cancel_closes_requests_and_a_repeat_changes_nothing(conn, world):
    c, vh, oh = world["client"], world["vh"], world["oh"]
    app = assigned(conn, world)
    c.post(f"/api/training/{app['id']}/request", json={"kind": "start", "idempotency_key": key()}, headers=vh)
    k = key()
    first = c.post(f"/api/training/{app['id']}/cancel", json={"reason": "Нет связи", "idempotency_key": k}, headers=vh).json()
    again = c.post(f"/api/training/{app['id']}/cancel", json={"reason": "Нет связи", "idempotency_key": k}, headers=vh).json()
    assert first["changed"] is True and again["changed"] is False
    assert len(training.cancellations(conn, app["id"])) == 1
    assert training.pending_requests(conn) == []  # the trainer's pending request is closed
    # a cancelled training can't be cancelled again
    r = c.post(f"/api/admin/onboarding/{app['id']}/cancel-training", json={"reason": "x", "idempotency_key": key()}, headers=oh)
    assert r.status_code == 409


def test_an_approved_cancel_request_also_returns_to_review(conn, world):
    """A trainer's request made before 30.10 and approved now works the new way."""
    c, vh, oh = world["client"], world["vh"], world["oh"]
    app = assigned(conn, world)
    req = c.post(f"/api/training/{app['id']}/request", json={"kind": "cancel", "reason": "Болеет", "idempotency_key": key()},
                 headers=vh).json()["request"]
    c.post(f"/api/admin/training-requests/{req['id']}/decide", json={"approve": True, "idempotency_key": key()}, headers=oh)
    a = recruiting.get(conn, app["id"])
    assert a["status"] == "new" and a["trainer_id"] is None
    [cancel] = training.cancellations(conn, app["id"])
    assert cancel["role"] == "trainer" and cancel["initiator"].startswith("Вера") and cancel["trainer"].startswith("Вера")


def test_migration_moves_waiting_candidates_back_but_not_refused_ones(tmp_path):
    """«Прямо сейчас есть люди с 1/3 и более» — candidates waiting for a new date after a cancellation go back to
    «Ожидают рассмотрения» at once; refused ones stay in «Отклонённые»; an active new date stays."""
    path = str(tmp_path / "bot.db")
    old = sqlite3.connect(path, isolation_level=None)
    old.row_factory = sqlite3.Row
    from app.db import SCHEMA

    old.executescript(SCHEMA)
    MIGRATIONS_BEFORE = len(MIGRATIONS) - 1
    old.execute("PRAGMA user_version = 0")

    import app.db as db

    saved = db.MIGRATIONS
    db.MIGRATIONS = saved[:MIGRATIONS_BEFORE]
    try:
        migrate(old)
    finally:
        db.MIGRATIONS = saved
    stamp = now_local().isoformat(timespec="seconds")
    for tg in (1, 2, 3, 4):
        old.execute("INSERT INTO users (telegram_id, first_name, last_name, created_at, status) VALUES (?, 'A', 'B', ?, ?)",
                    (tg, stamp, "approved" if tg == 4 else "pending"))
    form = ("ФИО Тест", 20, "Сокол", 3, "[0]", 5, stamp, stamp)
    ins = """INSERT INTO applications (user_id, status, full_name, age, metro, min_days, training_days, communication,
             consent_at, created_at, trainer_id, training_date, trainer_assigned_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    old.execute(ins, (1, "scheduled", *form, 4, None, stamp))           # waiting for a new date after 1/3 → «new»
    old.execute(ins, (2, "rejected", *form, None, None, None))          # refused with 1/3 → stays
    old.execute(ins, (3, "scheduled", *form, 4, "2099-01-01", stamp))   # rescheduled after a cancel → stays
    for app_id in (1, 2, 3):
        old.execute("""INSERT INTO event_logs (user_id, actor_id, event_type, old_value, new_value, idempotency_key, created_at)
                       VALUES (?, 999, 'TRAINING_CANCELLED', ?, ?, ?, ?)""",
                    (app_id, '{"trainer_id": 4, "training_date": "2026-10-29", "training_time": "11:00", "training_location": "X"}',
                     f'{{"application": {app_id}}}', f"k{app_id}", stamp))
        old.execute("""INSERT INTO training_cancellations (application_id, kind, at, initiator_id, initiator_role, reason)
                       VALUES (?, 'cancel', ?, 999, 'admin', 'r')""", (app_id, stamp))
    old.execute("""INSERT INTO training_requests (application_id, kind, initiator_id, initiator_role, app_version, created_at)
                   VALUES (1, 'change', 1, 'candidate', 1, ?)""", (stamp,))
    migrate(old)
    rows = {r["id"]: dict(r) for r in old.execute("SELECT * FROM applications")}
    assert rows[1]["status"] == "new" and rows[1]["trainer_id"] is None
    assert rows[2]["status"] == "rejected" and rows[3]["status"] == "scheduled" and rows[3]["trainer_id"] == 4
    snap = dict(old.execute("SELECT * FROM training_cancellations WHERE application_id = 1").fetchone())
    assert snap["trainer_id"] == 4 and snap["training_date"] == "2026-10-29" and snap["training_time"] == "11:00"
    assert old.execute("SELECT status FROM training_requests").fetchone()[0] == "stale"
    assert old.execute("SELECT COUNT(*) FROM event_logs WHERE event_type = 'TRAINING_BACK_TO_REVIEW'").fetchone()[0] == 1
    assert old.execute("SELECT COUNT(*) FROM training_cancellations").fetchone()[0] == 3  # the counters stay
    old.close()


# ---------------------------------------------------------------- §4: «Обучение» sorted by the mutual confirmation


def test_training_sorted_by_mutual_confirmation(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    ref, vera = make_user(first="Пригласил"), make_user(first="Вера")
    apps = {}
    for i, name in enumerate(["Нет Даты", "Ждёт Кандидата", "Кандидат Подтвердил", "Оба Подтвердили", "Тренер Только"]):
        tg = 8100 + i
        invites.use(conn, tg, invites.current_ref(conn, ref))
        apps[name] = recruiting.submit(conn, {"id": tg}, {**FORM, "full_name": name})[1]["id"]
        training.assign_trainer(conn, owner, apps[name], vera["id"], key())
        if name != "Нет Даты":
            training.schedule(conn, owner, apps[name], today_local() + timedelta(days=2), "11:00", "Европа", None, key())
    recruiting.confirm_training(conn, 8102, apps["Кандидат Подтвердил"])
    recruiting.confirm_training(conn, 8103, apps["Оба Подтвердили"])
    training.confirm_by_trainer(conn, vera, apps["Оба Подтвердили"])
    training.confirm_by_trainer(conn, vera, apps["Тренер Только"])
    order = [a["full_name"] for a in recruiting.listing(conn, "scheduled")]
    assert order[0] == "Оба Подтвердили" and order[1] == "Кандидат Подтвердил" and order[-1] == "Нет Даты"
    # the trainer's own list — the same order; the green state only for the mutual confirmation
    mine = training.my_candidates(conn, vera)
    assert [a["full_name"] for a in mine] == order
    views = {a["full_name"]: recruiting.view(conn, a) for a in mine}
    assert views["Оба Подтвердили"]["agreement"] == "agreed"
    assert views["Тренер Только"]["agreement"] == "waiting_candidate"  # the trainer alone is not an agreement
    assert views["Кандидат Подтвердил"]["agreement"] == "candidate_confirmed"
    assert views["Нет Даты"]["agreement"] == "no_date"
    assert [v["agreement_rank"] for v in views.values()] == sorted(v["agreement_rank"] for v in views.values())
    # a change of the date drops the agreement (and the place in the list) automatically
    training.schedule(conn, owner, apps["Оба Подтвердили"], today_local() + timedelta(days=3), "12:00", "Европа", None, key())
    assert recruiting.listing(conn, "scheduled")[0]["full_name"] != "Оба Подтвердили"
    # a cancelled training never shows as agreed
    training.cancel(conn, owner, apps["Кандидат Подтвердил"], "Отмена", key())
    cancelled = recruiting.view(conn, recruiting.get(conn, apps["Кандидат Подтвердил"]))
    assert cancelled["agreement"] == "cancelled" and cancelled["status"] == "new"


# ---------------------------------------------------------------- §3: access


def tg(n):
    return {"id": n, "username": f"u{n}", "first_name": "Гость", "last_name": None}


def test_start_without_a_link_then_a_live_link_starts_onboarding(conn, make_user):
    ref = make_user(first="Пригласил")
    assert invites.admit(conn, tg(5001), None) == ("silent", None)  # /start without a link: silence and the log
    assert invites.admit(conn, tg(5001), None) == ("silent", None)
    [row] = invites.attempts(conn)
    assert row["kind"] == "no_link" and row["count"] == 2 and row["access"] == "none"
    link = invites.current_ref(conn, ref)
    assert invites.admit(conn, tg(5001), "ref_" + link["token"]) == ("in", "ref")  # the way in is not closed
    assert invites.attempts(conn)[0]["access"] == "invited"
    user, app = recruiting.submit(conn, tg(5001), FORM)
    assert app["status"] == "new" and user["referrer_id"] == ref["id"]
    assert conn.execute("SELECT COUNT(*) FROM users WHERE telegram_id = 5001").fetchone()[0] == 1
    assert invites.attempts(conn)[0]["access"] == "applied"


def test_a_bound_person_is_not_told_the_link_is_stale(conn, make_user):
    """The real case from 30.10: a person opened a live link, didn't finish, the link was rotated, they tapped it
    again — and got «ссылка неактуальна» although the questionnaire was open to them."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    ref = make_user(first="Пригласил")
    link = invites.current_ref(conn, ref)
    assert invites.admit(conn, tg(5002), "ref_" + link["token"]) == ("in", "ref")
    invites.rotate_all(conn, owner, key())
    assert invites.resolve(conn, "ref_" + link["token"])[1] == "revoked"
    assert invites.admit(conn, tg(5002), "ref_" + link["token"]) == ("in", "ref")
    assert invites.attempts(conn) == []  # not a stranger: nothing logged
    # a stranger with the same dead link gets the answer once, then silence
    assert invites.admit(conn, tg(5003), "ref_" + link["token"]) == ("stale", None)
    assert invites.admit(conn, tg(5003), "ref_" + link["token"]) == ("silent", None)
    assert invites.admit(conn, tg(5003), "ref_nonsense") == ("stale", None)  # an unknown token — outdated too


def test_owner_restores_access_by_hand(conn, make_user, world):
    c, oh, bot = world["client"], world["oh"], world["bot"]
    invites.admit(conn, tg(5004), None)
    invites.admit(conn, tg(5005), None)
    board = c.get("/api/admin/access-attempts", headers=oh).json()
    assert {a["telegram_id"] for a in board["items"]} == {5004, 5005} and board["people"]
    row = next(a for a in board["items"] if a["telegram_id"] == 5004)
    assert row["username"] == "u5004" and row["access"] == "none" and row["restored_at"] is None
    # only the owner: an admin sees the log but can't let anyone in
    admin = make_user(first="Админ", username="adm")
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (admin["id"],))
    ah = headers(admin["telegram_id"], "Админ", "Петров", "adm")
    assert c.get("/api/admin/access-attempts", headers=ah).json()["people"] == []
    r = c.post("/api/admin/access-attempts/restore", json={"telegram_id": 5004, "idempotency_key": key()}, headers=ah)
    assert r.status_code == 403
    # the owner, on behalf of an employee
    r = c.post("/api/admin/access-attempts/restore",
               json={"telegram_id": 5004, "referrer_id": world["ref"]["id"], "idempotency_key": key()}, headers=oh)
    assert r.status_code == 200
    res = r.json()
    assert res["link"].startswith("https://t.me/test_bot?start=ref_") and res["referrer"].startswith("Пригласил")
    row = next(a for a in res["items"] if a["telegram_id"] == 5004)
    assert row["access"] == "invited" and row["restored_at"] and row["restored_by_name"].startswith("Саня")
    assert row["restore_result"] == "sent" and res["delivered"] is True
    assert any(ch == 5004 and "анкету" in t for ch, t, _ in bot.sent)
    # the usual questionnaire with the chosen inviter; the existing record is the only one
    user, app = recruiting.submit(conn, tg(5004), FORM)
    assert user["referrer_id"] == world["ref"]["id"] and app["status"] == "new"
    assert conn.execute("SELECT COUNT(*) FROM event_logs WHERE event_type = 'ACCESS_RESTORED'").fetchone()[0] == 1
    # a second time — already applied
    r = c.post("/api/admin/access-attempts/restore", json={"telegram_id": 5004, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 409 and r.json()["code"] == "already_applied"
    # someone who never knocked can't be added this way
    r = c.post("/api/admin/access-attempts/restore", json={"telegram_id": 4242, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 404


def test_restore_never_bypasses_a_real_block(conn, make_user, world):
    c, oh = world["client"], world["oh"]
    blocked = make_user(first="Злой")
    svc.set_account_state(conn, world["owner"], blocked["id"], "blocked", "Нарушения", key())
    invites.log_attempt(conn, {"id": blocked["telegram_id"], "username": None, "first_name": "Злой", "last_name": None}, "no_link")
    r = c.post("/api/admin/access-attempts/restore", json={"telegram_id": blocked["telegram_id"], "idempotency_key": key()},
               headers=oh)
    assert r.status_code == 409 and r.json()["code"] == "blocked"
    assert svc.get_user(conn, blocked["id"])["account_state"] == "blocked"
    # a refused candidate: only through «Вернуть в резерв»
    app = assigned(conn, world)
    training.reject(conn, world["owner"], app["id"], "Не подошёл", key())
    invites.log_attempt(conn, tg(7001), "no_link")
    r = c.post("/api/admin/access-attempts/restore", json={"telegram_id": 7001, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 409 and r.json()["code"] in ("blocked", "rejected")


def test_restore_when_the_bot_cannot_write_gives_the_link(conn, world):
    c, oh, bot = world["client"], world["oh"], world["bot"]
    invites.admit(conn, tg(5006), None)

    async def fail(*a, **kw):
        from aiogram.exceptions import TelegramForbiddenError

        raise TelegramForbiddenError(method=None, message="bot was blocked by the user")

    bot.send_message = fail
    res = c.post("/api/admin/access-attempts/restore", json={"telegram_id": 5006, "idempotency_key": key()}, headers=oh).json()
    assert res["delivered"] is False and res["link"]
    row = next(a for a in res["items"] if a["telegram_id"] == 5006)
    assert row["restore_result"] == "not_delivered" and row["access"] == "invited"


# ---------------------------------------------------------------- §7: the questionnaire's time in Moscow


@pytest.mark.parametrize("stored, shown", [
    ("2026-10-30T03:47:12+03:00", "30.10.2026 в 03:47 МСК"),        # night
    ("2026-10-30T06:05:00+03:00", "30.10.2026 в 06:05 МСК"),        # early morning
    ("2026-10-30T13:30:59+03:00", "30.10.2026 в 13:30 МСК"),        # day
    ("2026-10-30T21:15:00+03:00", "30.10.2026 в 21:15 МСК"),        # evening
    ("2026-10-30T23:59:59+03:00", "30.10.2026 в 23:59 МСК"),        # just before midnight
    ("2026-10-30T21:00:00+00:00", "31.10.2026 в 00:00 МСК"),        # written in UTC — the date moves to Moscow's
    ("2026-10-30T20:59:00Z", "30.10.2026 в 23:59 МСК"),
    ("2026-10-31T01:10:00+05:00", "30.10.2026 в 23:10 МСК"),        # another server zone
])
def test_questionnaire_time_is_moscow_time(stored, shown):
    assert msk_datetime(stored) == shown


def test_questionnaire_time_on_cards_and_messages(conn, world):
    c, oh, bot = world["client"], world["oh"], world["bot"]
    conn.execute("UPDATE applications SET created_at = '2026-10-30T03:47:00+03:00' WHERE id = ?", (world["app"]["id"],))
    board = c.get("/api/admin/onboarding?tab=new", headers=oh).json()
    assert board["items"][0]["submitted_text"] == "30.10.2026 в 03:47 МСК"
    assert board["items"][0]["created_at"] == "2026-10-30T03:47:00+03:00"  # the stored moment is untouched
    give_trainer(world)
    assert "Анкета отправлена: 30.10.2026 в 03:47 МСК" in bot.to(world["vera"]["telegram_id"])[-1]
    items = c.get("/api/training", headers=world["vh"]).json()["items"]
    assert items[0]["submitted_text"] == "30.10.2026 в 03:47 МСК"


def test_naive_time_is_read_as_the_project_zone():
    assert msk_datetime("2026-10-30T03:47:00") == "30.10.2026 в 03:47 МСК"
    assert msk_datetime(None) is None and msk_datetime("garbage") is None
    assert datetime.fromisoformat("2026-10-30T03:47:00+03:00")  # sanity


def test_an_old_confirm_button_does_not_confirm_a_new_date(conn, world):
    """§4: green only for the mutual confirmation of the current date — a button from the earlier date is refused."""
    c, vh, app = world["client"], world["vh"], world["app"]
    give_trainer(world)
    c.post(f"/api/training/{app['id']}/schedule", json={"date": day(2), "time": "11:00", "location": "Европа",
                                                       "idempotency_key": key()}, headers=vh)
    old_version = recruiting.get(conn, app["id"])["version"]
    c.post(f"/api/training/{app['id']}/schedule", json={"date": day(3), "time": "11:00", "location": "Европа",
                                                       "idempotency_key": key()}, headers=vh)
    with pytest.raises(svc.ServiceError) as exc:
        recruiting.confirm_training(conn, 7001, app["id"], old_version)
    assert exc.value.code == "stale"
    a, changed = recruiting.confirm_training(conn, 7001, app["id"], recruiting.get(conn, app["id"])["version"])
    assert changed and training.agreement(a) == "agreed"
