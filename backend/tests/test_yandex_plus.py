"""ТЗ «Яндекс Плюс»: a separate product of «Работа» on the mechanics of «Алиса» — its own access, QR codes, ONE photo,
its own photo-report group with topics, its name everywhere; moderation, statistics, payouts, fines, goals shared;
§11 — products in goals/events only for those who may manage them; §12 — reminders to the unmarked."""

from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import alice, app_settings, events, finance, goals, payouts, pending, products
from app import services as svc
from app.api import create_app
from app.config import settings
from app.notify import Notifier
from app.timeutil import now_local

from .conftest import requisites
from .test_alice import FORUM, JPG, ForumBot, full_run
from .test_api import headers, key
from .test_features import run

PLUS_FORUM = -1010


class PlusBot(ForumBot):
    """+ send_photo: one photo can't be an album (Telegram wants 2–10)."""

    def __init__(self, fail=False):
        super().__init__(fail)
        self.photos = []

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, message_thread_id=None):
        if self.fail:
            from aiogram.exceptions import TelegramBadRequest

            raise TelegramBadRequest(method=None, message="not enough rights to send photos")
        self.photos.append((chat_id, message_thread_id, caption))
        n = 500 + len(self.photos)
        return SimpleNamespace(message_id=n, photo=[SimpleNamespace(file_id=f"tg-{n}")])

    async def delete_messages(self, chat_id, ids):
        self.deleted = (chat_id, ids)


@pytest.fixture
def setup(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Иван")
    alice.connect_forum(conn, FORUM, "Отчёты Алиса")
    alice.connect_forum(conn, PLUS_FORUM, "Отчёты Плюс", alice.PLUS)
    return owner, u


def plus_ready(conn, owner, u, number="Плюс-1"):
    products.set_access(conn, owner, u["id"], "plus", True, svc.new_key())
    qr = alice.create_qr(conn, owner, "local:qr.png", number, svc.new_key(), alice.PLUS)
    alice.assign(conn, owner, qr["id"], u["id"], True, svc.new_key())
    return qr


def plus_run(conn, u, photo=JPG):
    task = alice.start(conn, u, svc.new_key(), alice.PLUS)
    alice.scanned(conn, u, task["id"])
    alice.put_photo(conn, u, task["id"], 1, photo, "jpg")
    alice.fix_stage(conn, u, task["id"])
    return alice.finish(conn, u, task["id"], svc.new_key())


def test_access_and_qr_codes_are_separate_from_alice(conn, setup):
    owner, u = setup
    with pytest.raises(svc.ServiceError) as e:
        alice.start(conn, u, svc.new_key(), alice.PLUS)
    assert e.value.code == "no_product_access" and "Яндекс Плюс" in e.value.message
    # «Алиса» alone doesn't open «Яндекс Плюс», nor the other way round
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    with pytest.raises(svc.ServiceError):
        alice.start(conn, u, svc.new_key(), alice.PLUS)
    a_qr = alice.create_qr(conn, owner, "local:a.png", "A-1", svc.new_key())
    p_qr = alice.create_qr(conn, owner, "local:p.png", "P-1", svc.new_key(), alice.PLUS)
    assert p_qr["product"] == "plus" and a_qr["product"] == "alice"
    with pytest.raises(svc.ServiceError) as e:  # a «Яндекс Плюс» QR needs «Яндекс Плюс» access
        alice.assign(conn, owner, p_qr["id"], u["id"], True, svc.new_key())
    assert e.value.code == "no_product_access" and "Яндекс Плюс" in e.value.message
    alice.assign(conn, owner, a_qr["id"], u["id"], True, svc.new_key())
    products.set_access(conn, owner, u["id"], "plus", True, svc.new_key())
    with pytest.raises(svc.ServiceError) as e:  # the Alice QR never serves «Яндекс Плюс»
        alice.start(conn, u, svc.new_key(), alice.PLUS)
    assert e.value.code == "no_qr"
    alice.assign(conn, owner, p_qr["id"], u["id"], True, svc.new_key())
    # one QR per product: giving the Plus QR keeps the Alice one
    assert alice.assigned_qr(conn, u["id"])["id"] == a_qr["id"]
    assert alice.assigned_qr(conn, u["id"], alice.PLUS)["id"] == p_qr["id"]
    assert [q["id"] for q in alice.qr_list(conn, product=alice.PLUS)] == [p_qr["id"]]
    assert [q["id"] for q in alice.qr_list(conn)] == [a_qr["id"]]
    # both products may go at once, one execution each; a repeated tap returns the same one
    a = alice.start(conn, u, svc.new_key())
    p = alice.start(conn, u, svc.new_key(), alice.PLUS)
    assert a["id"] != p["id"] and alice.start(conn, u, svc.new_key(), alice.PLUS)["id"] == p["id"]
    assert p["qr_id"] == p_qr["id"] and p["query"] == "" and a["qr_id"] == a_qr["id"]
    # one product's execution is not reachable as the other's
    with pytest.raises(svc.ServiceError) as e:
        alice.scanned(conn, u, p["id"], alice.PRODUCT)
    assert e.value.code == "not_found"
    # revoking «Яндекс Плюс» blocks only it
    products.set_access(conn, owner, u["id"], "plus", False, svc.new_key())
    with pytest.raises(svc.ServiceError):
        alice.scanned(conn, u, p["id"])
    alice.scanned(conn, u, a["id"])


def test_one_photo_flow_and_delivery_to_its_own_group(conn, setup):
    owner, u = setup
    plus_ready(conn, owner, u)
    task = alice.start(conn, u, svc.new_key(), alice.PLUS)
    view = alice.view(conn, task)
    assert view["product"] == "plus" and view["product_title"] == "Яндекс Плюс" and view["last_step"] == 2
    assert [s["stage"] for s in view["stages"]] == [1] and view["query"] is None and view["qr_url"]
    alice.scanned(conn, u, task["id"])
    with pytest.raises(svc.ServiceError) as e:  # there is no second photo
        alice.put_photo(conn, u, task["id"], 2, JPG, "jpg")
    assert e.value.code == "wrong_stage"
    with pytest.raises(svc.ServiceError) as e:  # finishing needs the photo
        alice.finish(conn, u, task["id"], svc.new_key())
    assert e.value.code == "photos_required" and "личного кабинета" in e.value.message
    with pytest.raises(svc.ServiceError) as e:
        alice.fix_stage(conn, u, task["id"])
    assert e.value.code == "photo_missing_1" and "Личный кабинет" in e.value.message
    alice.put_photo(conn, u, task["id"], 1, JPG + b"a", "jpg")
    alice.put_photo(conn, u, task["id"], 1, JPG + b"b", "jpg")  # retake replaces
    assert len(alice.photos(conn, task["id"])) == 1
    task = alice.fix_stage(conn, u, task["id"])
    assert task["step"] == 2
    with pytest.raises(svc.ServiceError):
        alice.fix_stage(conn, u, task["id"])
    k = svc.new_key()
    task = alice.finish(conn, u, task["id"], k)
    assert task["status"] == "sending" and alice.finish(conn, u, task["id"], k)["id"] == task["id"]  # a repeated tap
    # the group refuses: honestly «Не отправлено»
    task = run(alice.deliver(conn, PlusBot(fail=True), task["id"]))
    assert task["status"] == "send_failed"
    bot = PlusBot()
    task = run(alice.deliver(conn, bot, task["id"]))
    assert task["status"] == "moderation" and bot.albums == [] and len(bot.photos) == 1
    chat, thread, caption = bot.photos[0]
    assert chat == PLUS_FORUM and task["chat_id"] == PLUS_FORUM  # its own group, never the Alice one
    for part in ("<b>Яндекс Плюс</b>", "Сотрудник:", "https://t.me/ivan", f"Задание: #{task['id']}", "QR-код: «Плюс-1»",
                 "Дата:", "Время:", "На модерации"):
        assert part in caption
    assert "Алиса" not in caption
    topic = conn.execute("SELECT * FROM alice_topics WHERE user_id = ?", (u["id"],)).fetchall()
    assert [(r["product"], r["chat_id"]) for r in topic] == [("plus", PLUS_FORUM)]
    assert all(p["temp_path"] is None and p["file_id"] for p in alice.photos(conn, task["id"]).values())


def test_topics_per_product_and_groups_never_mix(conn, setup):
    owner, u = setup
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    a_qr = alice.create_qr(conn, owner, "local:a.png", None, svc.new_key())
    alice.assign(conn, owner, a_qr["id"], u["id"], True, svc.new_key())
    plus_ready(conn, owner, u)
    bot = PlusBot()
    a = run(alice.deliver(conn, bot, full_run(conn, u)["id"]))
    p = run(alice.deliver(conn, bot, plus_run(conn, u)["id"]))
    p2 = run(alice.deliver(conn, bot, plus_run(conn, u, JPG + b"2")["id"]))
    assert (a["chat_id"], p["chat_id"]) == (FORUM, PLUS_FORUM)
    assert p["thread_id"] == p2["thread_id"] != a["thread_id"] and bot.topics == 2  # the topic is reused
    # a chat serves one product: connecting the Alice group for «Яндекс Плюс» is refused, nothing overwritten
    with pytest.raises(svc.ServiceError) as e:
        alice.connect_forum(conn, FORUM, "x", alice.PLUS)
    assert e.value.code == "chat_taken"
    assert alice.forum(conn)["chat_id"] == FORUM and alice.forum(conn, alice.PLUS)["chat_id"] == PLUS_FORUM
    alice.connect_forum(conn, -1011, "Новая Плюс", alice.PLUS)  # reconnecting Plus doesn't touch Alice
    assert alice.forum(conn)["chat_id"] == FORUM and alice.forum(conn, alice.PLUS)["title"] == "Новая Плюс"


def test_moderation_statistics_goals_and_notifications_by_product(conn, setup, make_user):
    owner, u = setup
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    a_qr = alice.create_qr(conn, owner, "local:a.png", None, svc.new_key())
    alice.assign(conn, owner, a_qr["id"], u["id"], True, svc.new_key())
    plus_ready(conn, owner, u)
    today = now_local().date()
    g_plus = goals.create(conn, owner, title="Плюс", kind="personal", target=3, date_from=today, date_to=today,
                          key=svc.new_key(), user_id=u["id"], product="plus")
    g_alice = goals.create(conn, owner, title="Алиса", kind="personal", target=3, date_from=today, date_to=today,
                           key=svc.new_key(), user_id=u["id"], product="alice")
    bot = PlusBot()
    a = run(alice.deliver(conn, bot, full_run(conn, u)["id"]))
    p = run(alice.deliver(conn, bot, plus_run(conn, u)["id"]))
    assert [t["id"] for t in alice.queue(conn, product=alice.PLUS)] == [p["id"]]
    assert [t["id"] for t in alice.queue(conn)] == [a["id"]]
    assert pending.counts(conn, owner)["plus_moderation"] == 1 and pending.counts(conn, owner)["alice_moderation"] == 1
    done, _ = alice.moderate(conn, owner, p["id"], True, None, svc.new_key())
    assert done["status"] == "accepted"
    # statistics and goals never mix the products
    assert alice.my_stats(conn, u["id"], today, product=alice.PLUS)["all"]["accepted"] == 1
    assert alice.my_stats(conn, u["id"], today)["all"] == {"completed": 1, "moderation": 1, "rejected": 0, "accepted": 0,
                                                         "paid": 0, "yandex_rejected": 0}
    assert alice.admin_stats(conn, today, today, alice.PLUS)["total"]["accepted"] == 1
    assert alice.admin_stats(conn, today, today)["total"]["accepted"] == 0
    assert [h["id"] for h in alice.history(conn, u["id"], product=alice.PLUS)] == [p["id"]]
    assert goals.view(conn, goals.get(conn, g_plus["id"]), u)["progress"] == 1
    assert goals.view(conn, goals.get(conn, g_alice["id"]), u)["progress"] == 0
    # the forum reply and the employee's message name «Яндекс Плюс»
    run(alice.post_status(conn, bot, p["id"]))
    assert bot.messages[-1].startswith(f"Яндекс Плюс #{p['id']}") and "Принято" in bot.messages[-1]
    sent = []
    notifier = Notifier(None, conn)

    async def fake_send(chat_id, text, *a, **kw):
        sent.append(text)
        return True

    notifier.send = fake_send
    run(notifier.alice_decided(alice.get(conn, p["id"])))
    assert "«Яндекс Плюс»" in sent[-1] and "принято" in sent[-1] and "Алиса" not in sent[-1]
    run(notifier.alice_qr_changed(u, True, "plus"))
    assert "«Яндекс Плюса»" in sent[-1]


def test_payout_fines_and_deletion_reuse_the_alice_logic(conn, setup, make_user):
    owner, u = setup
    v = make_user(first="Вера")
    qr = plus_ready(conn, owner, u)
    products.set_access(conn, owner, v["id"], "plus", True, svc.new_key())
    alice.assign(conn, owner, qr["id"], v["id"], True, svc.new_key())
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    a_qr = alice.create_qr(conn, owner, "local:a.png", "A", svc.new_key())
    alice.assign(conn, owner, a_qr["id"], u["id"], True, svc.new_key())
    bot, ids = PlusBot(), []
    for i, person in enumerate((u, v, u)):
        t = run(alice.deliver(conn, bot, plus_run(conn, person, JPG + bytes([i]))["id"]))
        alice.moderate(conn, owner, t["id"], True, None, svc.new_key())
        ids.append(t["id"])
    a = run(alice.deliver(conn, bot, full_run(conn, u)["id"]))
    alice.moderate(conn, owner, a["id"], True, None, svc.new_key())
    today = now_local().date()
    # the Alice reconciliation doesn't see Plus QR codes and the other way round
    assert [r["qr_id"] for r in alice.reconcile(conn, today, today)["rows"]] == [a_qr["id"]]
    plan = alice.reconcile(conn, today, today, {qr["id"]: 2}, rate=100, product=alice.PLUS)
    assert [r["qr_id"] for r in plan["rows"]] == [qr["id"]] and len(plan["rejected"]) == 1 and plan["total"] == 200
    # a fine for «Яндекс Плюс» — one debt, taken by the payout
    tx = finance.create(conn, owner, u["id"], "fine", None, "Повтор фото", svc.new_key(), units=1, product="plus",
                        alice_task_id=ids[0]).tx
    assert tx["product"] == "plus"
    with pytest.raises(svc.ServiceError) as e:  # an Alice task can't be named for a Plus fine
        finance.create(conn, owner, u["id"], "fine", None, "x", svc.new_key(), units=1, product="plus", alice_task_id=a["id"])
    assert e.value.code == "bad_task"
    requisites(conn, u, v)
    batch_id, created = alice.commit_payout(conn, owner, "Плюс, октябрь", today, today, {qr["id"]: 2}, [], svc.new_key(),
                                            alice.PLUS)
    batch = payouts.batch(conn, batch_id)
    assert created and batch["product"] == "plus" and sorted(t["status"] for t in batch["tasks"]) == ["paid", "paid",
                                                                                                    "yandex_rejected"]
    assert alice.get(conn, a["id"])["status"] == "accepted"  # the Alice task waits for its own payout
    # edited (same mechanism): everyone is in now, the fine is taken once; annulled: the tasks wait again
    alice.edit_payout(conn, owner, batch_id, {qr["id"]: 3}, svc.new_key())
    batch = payouts.batch(conn, batch_id)
    assert sorted(t["status"] for t in batch["tasks"]) == ["paid", "paid", "paid"]
    assert finance.get(conn, tx["id"])["status"] == "offset" and sum(i["fines"] for i in batch["items"]) == tx["amount"]
    payouts.annul(conn, owner, batch_id, "Ошибка", False, svc.new_key())
    assert {alice.get(conn, i)["status"] for i in ids} == {"accepted"} and finance.get(conn, tx["id"])["status"] == "active"
    # deletion: the single photo goes to the archive as a photo, the post leaves the Plus topic
    app_settings.set_raw(conn, "archive_chat_id", "-1999")
    task, changed = alice.delete_task(conn, owner, ids[1], "Дубль", svc.new_key())
    task = run(alice.archive_and_unpublish(conn, bot, ids[1], owner))
    assert changed and task["archive_state"] == "done" and task["unpublish_state"] == "done"
    archived = bot.photos[-1]
    assert archived[0] == -1999 and "#плюс #удаление" in archived[2] and "Яндекс Плюс" in archived[2]
    assert bot.deleted[0] == PLUS_FORUM
    assert alice.admin_stats(conn, today, today, alice.PLUS)["total"]["completed"] == 2


def test_http_plus_section(conn, make_user):
    owner_h = headers(999, "Саня", "Владелец", "boss")
    client = TestClient(create_app(conn, Notifier(None, conn)))
    client.get("/api/me", headers=owner_h)
    u = make_user(first="Иван")
    uh = headers(u["telegram_id"])
    assert client.get("/api/work/plus", headers=uh).status_code == 403
    assert client.get("/api/work/nope", headers=uh).status_code == 404
    r = client.post(f"/api/admin/team/{u['id']}/products", json={"product": "plus", "allow": True, "idempotency_key": key()},
                    headers=owner_h)
    assert {i["product"]: i["active"] for i in r.json()["items"]} == {"alice": False, "plus": True}
    assert client.get("/api/work", headers=uh).json()["items"] == [{"product": "plus", "title": "Яндекс Плюс"}]
    assert client.get("/api/work/alice", headers=uh).status_code == 403  # independent
    r = client.post(f"/api/admin/alice/qr?idempotency_key={key()}&number=P-9&product=plus", content=JPG, headers=owner_h)
    qr = r.json()["items"][0]
    assert qr["product"] == "plus" and client.get("/api/admin/alice/qr", headers=owner_h).json()["items"] == []
    r = client.post(f"/api/admin/alice/qr/{qr['id']}/assign", json={"user_id": u["id"], "idempotency_key": key()},
                    headers=owner_h)
    assert r.json()["items"][0]["people"][0]["user_id"] == u["id"]
    info = client.get("/api/admin/alice?product=plus", headers=owner_h).json()
    assert info["title"] == "Яндекс Плюс" and info["command"] == "/plus_here" and info["people"][0]["id"] == u["id"]
    t = client.post("/api/work/plus/start", json={"idempotency_key": key()}, headers=uh).json()["task"]
    assert t["product"] == "plus" and len(t["stages"]) == 1 and t["title"].startswith("Яндекс Плюс #")
    assert client.post(f"/api/work/alice/{t['id']}/scanned", headers=uh).status_code == 404  # not an Alice task
    client.post(f"/api/work/plus/{t['id']}/scanned", headers=uh)
    r = client.put(f"/api/work/plus/{t['id']}/photo/1", content=JPG, headers=uh)
    assert r.status_code == 200 and client.get(r.json()["task"]["stages"][0]["photo_url"]).status_code == 200
    assert client.post(f"/api/work/plus/{t['id']}/next", headers=uh).json()["task"]["step"] == 2
    done = client.post(f"/api/work/plus/{t['id']}/finish", json={"idempotency_key": key()}, headers=uh).json()["task"]
    assert done["status"] == "send_failed" and done["send_error"]  # no bot here — honestly «не отправлено»
    me = client.get("/api/work/plus", headers=uh).json()
    assert me["title"] == "Яндекс Плюс" and me["history"][0]["id"] == t["id"] and me["stats"]["all"]["completed"] == 1
    assert client.get("/api/admin/alice/queue?status=send_failed&product=plus", headers=owner_h).json()["items"][0]["id"] == t["id"]
    assert client.get("/api/admin/alice/queue?status=send_failed", headers=owner_h).json()["items"] == []
    stats = client.get("/api/admin/alice/stats?from=2026-01-01&to=2030-01-01&product=plus", headers=owner_h).json()
    assert stats["total"]["completed"] == 1
    r = client.post("/api/admin/alice/payout/preview", json={"date_from": "2026-01-01", "date_to": "2030-01-01",
                                                             "product": "plus"}, headers=owner_h)
    assert r.status_code == 200 and r.json()["rows"] == []  # nothing accepted yet


# ---------------------------------------------------------------- §11: products in goals and events


def trainer_of(conn, owner, make_user, people=()):
    trainer = make_user(first="Вера", last="Тренер")
    conn.execute("INSERT INTO networks (id, name, trainer_id, created_by, created_at) VALUES (7, 'Север', ?, 0, '')",
                 (trainer["id"],))
    conn.execute("UPDATE users SET role = 'trainer', network_id = 7 WHERE id = ?", (trainer["id"],))
    for p in people:
        conn.execute("UPDATE users SET network_id = 7 WHERE id = ?", (p["id"],))
    return svc.get_user(conn, trainer["id"])


def test_goals_and_events_offer_only_manageable_products(conn, setup, make_user):
    owner, u = setup
    trainer = trainer_of(conn, owner, make_user, [u])
    today = now_local().date()
    assert products.manageable(conn, owner) == ["alice", "plus"]
    assert products.manageable(conn, trainer) == [] and products.manageable(conn, u) == []
    for product in ("alice", "plus"):  # §11.5 (1), (4): neither shown nor accepted by a hand-made request
        with pytest.raises(svc.ServiceError) as e:
            goals.create(conn, trainer, title="Цель", kind="network", target=3, date_from=today, date_to=today,
                         key=svc.new_key(), network_id=7, product=product)
        assert e.value.code == "no_product_access"
        with pytest.raises(svc.ServiceError) as e:
            events.create(conn, trainer, title="Гонка", mode="race", target=5, starts_at=now_local(),
                          ends_at=now_local() + timedelta(hours=2), key=svc.new_key(), product=product)
        assert e.value.code == "no_product_access"
    # without a product the trainer's goals work as before
    goals.create(conn, trainer, title="Штуки", kind="network", target=3, date_from=today, date_to=today,
                 key=svc.new_key(), network_id=7)
    # (2) access to one product — only that one
    products.set_access(conn, owner, trainer["id"], "plus", True, svc.new_key())
    assert products.manageable(conn, trainer) == ["plus"]
    g = goals.create(conn, trainer, title="Плюс сети", kind="network", target=3, date_from=today, date_to=today,
                     key=svc.new_key(), network_id=7, product="plus")
    assert g["product"] == "plus"
    with pytest.raises(svc.ServiceError):
        goals.create(conn, trainer, title="Алиса", kind="network", target=3, date_from=today, date_to=today,
                     key=svc.new_key(), network_id=7, product="alice")
    # (3) both
    products.set_access(conn, owner, trainer["id"], "alice", True, svc.new_key())
    assert products.manageable(conn, trainer) == ["alice", "plus"]
    # the API tells the app which ones to show
    client = TestClient(create_app(conn, Notifier(None, conn)))
    me = client.get("/api/me", headers=headers(trainer["telegram_id"])).json()
    assert me["manageable_products"] == ["alice", "plus"]
    assert client.get("/api/me", headers=headers(u["telegram_id"])).json()["manageable_products"] == []
    # (6) the goal made before keeps working after the access is taken back — nothing is reset
    products.set_access(conn, owner, trainer["id"], "plus", False, svc.new_key())
    assert goals.get(conn, g["id"])["product"] == "plus"


# ---------------------------------------------------------------- §12: reminders to the unmarked, the countdown


def test_remind_unmarked_one_and_all_within_the_network(conn, setup, make_user):
    owner, u = setup
    v = make_user(first="Вера", username="vera")
    w = make_user(first="Петя", username="petya")
    stranger = make_user(first="Чужой", username="x")
    trainer = trainer_of(conn, owner, make_user, [u, v, w])
    tomorrow = now_local().date() + timedelta(days=1)
    sent = []
    notifier = Notifier(None, conn)

    async def fake_send(chat_id, text, *a, **kw):
        sent.append((chat_id, text))
        return True

    notifier.send = fake_send
    client = TestClient(create_app(conn, notifier))
    th = headers(trainer["telegram_id"])
    day = client.get(f"/api/admin/day?day={tomorrow.isoformat()}", headers=th).json()
    assert {p["user_id"] for p in day["unmarked"]} == {u["id"], v["id"], w["id"]} and day["can_remind"]
    assert day["deadline"]["day"] == now_local().date().isoformat() and day["deadline"]["server_now"]
    # one person — only them, with the day and the deadline
    r = client.post(f"/api/admin/day/{tomorrow.isoformat()}/remind", json={"user_ids": [u["id"]], "idempotency_key": key()},
                    headers=th)
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == 1 and [c for c, _ in sent] == [u["telegram_id"]]
    deadline = app_settings.get_all(conn)["daily_mark_deadline"]
    assert tomorrow.strftime("%d.%m") in sent[0][1] and deadline in sent[0][1] and "штраф" in sent[0][1]
    # someone of another network can't be reached by swapping the id
    r = client.post(f"/api/admin/day/{tomorrow.isoformat()}/remind", json={"user_ids": [stranger["id"]], "idempotency_key": key()},
                    headers=th)
    assert r.status_code == 403 and len(sent) == 1
    # v marks a day off — «Напомнить всем» reaches only those still unmarked; u, reminded a minute ago, is skipped
    svc.admin_mark_day(conn, owner, v["id"], tomorrow, "off", None, svc.new_key())
    sent.clear()
    k = key()
    r = client.post(f"/api/admin/day/{tomorrow.isoformat()}/remind", json={"all": True, "idempotency_key": k}, headers=th)
    body = r.json()
    assert body["sent"] == 1 and [c for c, _ in sent] == [w["telegram_id"]]
    assert [x["user_id"] for x in body["skipped"]] == [u["id"]]
    # a repeated tap (the same key) sends nothing again
    r = client.post(f"/api/admin/day/{tomorrow.isoformat()}/remind", json={"all": True, "idempotency_key": k}, headers=th)
    assert r.json()["sent"] == 1 and r.json()["repeat"] and len(sent) == 1
    # the marked one is gone from the list; a reminder is not a fine
    day = client.get(f"/api/admin/day?day={tomorrow.isoformat()}", headers=th).json()
    assert {p["user_id"] for p in day["unmarked"]} == {u["id"], w["id"]}
    assert conn.execute("SELECT COUNT(*) FROM financial_transactions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM attendance_violations").fetchone()[0] == 0


def test_delivery_failures_dont_stop_the_mass_reminder(conn, setup, make_user):
    owner, u = setup
    v = make_user(first="Вера", username="vera")
    tomorrow = now_local().date() + timedelta(days=1)
    notifier = Notifier(None, conn)
    got = []

    async def flaky(chat_id, text, *a, **kw):
        if chat_id == u["telegram_id"]:
            raise RuntimeError("blocked by the user")
        got.append(chat_id)
        return True

    notifier.send = flaky
    client = TestClient(create_app(conn, notifier))
    r = client.post(f"/api/admin/day/{tomorrow.isoformat()}/remind", json={"all": True, "idempotency_key": key()},
                    headers=headers(999))
    body = r.json()
    assert r.status_code == 200 and body["sent"] == 1 and got == [v["telegram_id"]]
    assert [f["user_id"] for f in body["failed"]] == [u["id"]]


def test_deadline_for_the_countdown(conn):
    from app import violations

    today = now_local().date()
    late = datetime.combine(today, time(23, 0), tzinfo=settings.tz)
    noon = datetime.combine(today, time(12, 0), tzinfo=settings.tz)
    info = violations.mark_deadline_info(today + timedelta(days=1), noon)
    assert info["passed"] is False and info["seconds_left"] == 10 * 3600  # 22:00 by default
    assert violations.mark_deadline_info(today + timedelta(days=1), late)["passed"] is True
    assert violations.mark_deadline_info(today, noon)["passed"] is True  # today's was yesterday evening
