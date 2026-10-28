"""ТЗ 28.10 §28: the candidate's whole path end to end, through the API and the bot's functions."""

from datetime import timedelta

from app import groups, recruiting, training
from app import services as svc
from app.timeutil import today_local

from .test_api import headers, key
from .test_features import run
from .test_groups import MAIN, PRE
from .test_tz2810_training import world  # noqa: F401 — the fixture


def test_candidate_from_trainer_to_the_main_group(conn, world):  # noqa: F811
    c, oh, vh, bot, app = world["client"], world["oh"], world["vh"], world["bot"], world["app"]
    # trainer → «свяжитесь с тренером»
    c.post(f"/api/admin/onboarding/{app['id']}/trainer", json={"trainer_id": world["vera"]["id"], "idempotency_key": key()}, headers=oh)
    assert bot.members.get((PRE, 7001)) is None  # no «Предзакреп» right after the trainer is assigned
    # the date; the candidate confirms in the bot, the trainer in «Обучение» → the personal invitation
    day = (today_local() + timedelta(days=1)).isoformat()
    c.post(f"/api/admin/onboarding/{app['id']}/training", json={"date": day, "time": "11:00", "location": "Европа",
                                                                 "idempotency_key": key()}, headers=oh)
    a, _ = recruiting.confirm_training(conn, 7001, app["id"])
    c.post(f"/api/training/{app['id']}/confirm", json={"idempotency_key": key()}, headers=vh)
    link = bot.links[-1]
    # the real join by the person the link was made for
    ok, _ = run(groups.join_request(conn, bot, PRE, 7001, link))
    assert ok and groups.states(conn)[(world["cand"]["id"], "pre")]["state"] == "member"
    # «Начать обучение» → approval → «Обучение начато»; «Начать стажировку» → approval
    for kind in ("start", "internship"):
        req = c.post(f"/api/training/{app['id']}/request", json={"kind": kind, "idempotency_key": key()}, headers=vh).json()["request"]
        c.post(f"/api/admin/training-requests/{req['id']}/decide", json={"approve": True, "idempotency_key": key()}, headers=oh)
    a = recruiting.get(conn, app["id"])
    assert a["status"] == "internship" and a["training_started_at"]
    # internship units reached → «Завершение ожидает проверки»; the trainer can't accept, the admin does
    conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status, fact_volume,
                    report_state, created_at, updated_at, training) VALUES (?, ?, '10:00', '18:00', 20, 'Европа', 'active', 20,
                    'approved', 't', 't', 1)""", (world["cand"]["id"], today_local().isoformat()))
    item = next(x for x in c.get("/api/training", headers=vh).json()["items"] if x["id"] == app["id"])
    assert item["stage"] == "finish_review"
    assert c.post(f"/api/admin/onboarding/{app['id']}/stage", json={"stage": 1, "counted": 20, "bonus": 500,
                                                                      "idempotency_key": key()}, headers=vh).status_code == 403
    c.post(f"/api/admin/onboarding/{app['id']}/stage", json={"stage": 1, "counted": 20, "bonus": 500, "idempotency_key": key()},
           headers=oh)
    import time
    time.sleep(0.3)
    assert recruiting.get(conn, app["id"])["status"] == "done"
    main_link = next(lnk for lnk in reversed(bot.links) if lnk != link)
    ok, user = run(groups.join_request(conn, bot, MAIN, 7001, main_link))
    assert ok and groups.states(conn)[(user["id"], "main")]["state"] == "member"
    assert any("приняты в команду" in t for t in bot.to(7001))
    # the referrer's bonus and the candidate leaves the trainer's active list
    assert any("Бонус" in t or "бонус" in t for t in bot.to(world["ref"]["telegram_id"]))
    assert c.get("/api/training", headers=vh).json()["items"] == []
    assert training.cancellations(conn, app["id"]) == []


def test_combined_roles_get_one_copy_and_trainers_no_admin_counts(conn, make_user):
    """§13, §26: an admin who also leads a network gets one copy; a trainer sees only their own decisions."""
    from app import networks
    from app.notify import Notifier

    from .test_tz2810_access import Recorder

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    boss, u = make_user(first="Админ"), make_user(first="Аня")
    svc.set_role(conn, owner, boss["id"], "admin", svc.new_key())
    net = networks.create(conn, owner, "Север", svc.new_key())
    networks.set_member(conn, owner, u["id"], net["id"], svc.new_key())
    conn.execute("UPDATE networks SET trainer_id = ? WHERE id = ?", (boss["id"], net["id"]))
    rec = Recorder()
    run(Notifier(rec, conn).to_managers(svc.get_user(conn, u["id"]), "смена"))
    chats = [c for c, _ in rec.sent]
    assert chats.count(boss["telegram_id"]) == 1 and chats.count(999) == 1
    from fastapi.testclient import TestClient

    from app.api import create_app

    client = TestClient(create_app(conn, Notifier(None, conn)))
    trainer = make_user(first="Вера")
    north = networks.create(conn, owner, "Юг", svc.new_key())
    networks.set_member(conn, owner, trainer["id"], north["id"], svc.new_key())
    networks.set_trainer(conn, owner, north["id"], trainer["id"], svc.new_key())
    counts = client.get("/api/admin/pending", headers=headers(trainer["telegram_id"], "Вера", "Петров")).json()
    assert all(counts[k] == 0 for k in ("registrations", "onboarding_new", "fines_pending", "alice_moderation",
                                        "profile_changes", "payment_changes", "training_requests", "actual_missing"))
    me = client.get("/api/me", headers=headers(trainer["telegram_id"], "Вера", "Петров")).json()
    assert me["role_title"] == "Тренер" and "registrations" not in me["capabilities"]
