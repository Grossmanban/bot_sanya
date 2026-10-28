"""ТЗ 27.10 §28–44: «Работа» → «Яндекс Алиса»: access, QR, three photo stages, the forum group, moderation, payout."""

import json
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from fastapi.testclient import TestClient

from app import alice, app_settings, goals, payouts, products
from app import services as svc
from app.api import create_app
from app.notify import Notifier
from app.timeutil import now_local

from .test_api import headers, key
from .conftest import requisites
from .test_features import run

JPG = b"\xff\xd8\xff" + b"0" * 64
FORUM = -1009


class ForumBot:
    """create_forum_topic / send_media_group / edit / send — enough for the photo reports."""

    def __init__(self, fail=False):
        self.fail = fail
        self.albums, self.edits, self.messages, self.topics = [], [], [], 0

    async def create_forum_topic(self, chat_id, name):
        self.topics += 1
        return SimpleNamespace(message_thread_id=100 + self.topics)

    async def send_media_group(self, chat_id, media, message_thread_id):
        if self.fail:
            raise TelegramBadRequest(method=None, message="not enough rights to send photos")
        self.albums.append((chat_id, message_thread_id, [m.caption for m in media]))
        n = len(self.albums) * 10
        return [SimpleNamespace(message_id=n + i, photo=[SimpleNamespace(file_id=f"tg-{n + i}")]) for i in range(len(media))]

    async def edit_message_caption(self, chat_id, message_id, caption, parse_mode):
        self.edits.append((message_id, caption))

    async def send_message(self, chat_id, text, message_thread_id, reply_to_message_id, parse_mode):
        self.messages.append(text)


@pytest.fixture
def setup(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Иван")
    alice.connect_forum(conn, FORUM, "Отчёты Алиса")
    return owner, u


def full_run(conn, u, photo=JPG):
    task = alice.start(conn, u, svc.new_key())
    alice.scanned(conn, u, task["id"])
    for stage in (1, 2, 3):
        alice.put_photo(conn, u, task["id"], stage, photo + bytes([stage]), "jpg")
        alice.fix_stage(conn, u, task["id"])
    return alice.finish(conn, u, task["id"], svc.new_key())


def test_access_and_qr_are_checked_on_the_server(conn, setup, make_user):
    owner, u = setup
    with pytest.raises(svc.ServiceError) as e:
        alice.start(conn, u, svc.new_key())
    assert e.value.code == "no_product_access"
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    with pytest.raises(svc.ServiceError) as e:  # §31.4
        alice.start(conn, u, svc.new_key())
    assert e.value.code == "no_qr" and "Обратитесь к администратору" in e.value.message
    qr = alice.create_qr(conn, owner, "local:qr.png", "QR-7", svc.new_key())
    stranger = make_user(first="Чужой")
    with pytest.raises(svc.ServiceError):  # no access — no QR either
        alice.assign(conn, owner, qr["id"], stranger["id"], True, svc.new_key())
    assert alice.assign(conn, owner, qr["id"], u["id"], True, svc.new_key())
    task = alice.start(conn, u, svc.new_key())
    assert alice.start(conn, u, svc.new_key())["id"] == task["id"]  # a repeated tap: the same execution
    assert task["qr_number"] == "QR-7" and task["query"] in alice.queries(conn)
    # revoking the access blocks new actions
    products.set_access(conn, owner, u["id"], "alice", False, svc.new_key())
    with pytest.raises(svc.ServiceError):
        alice.scanned(conn, u, task["id"])
    assert [h["revoked_at"] is not None for h in products.history(conn, u["id"])] == [True]


def test_stages_in_order_photos_replaced_and_hidden_qr(conn, setup):
    owner, u = setup
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    qr = alice.create_qr(conn, owner, "local:qr.png", None, svc.new_key())
    alice.assign(conn, owner, qr["id"], u["id"], True, svc.new_key())
    task = alice.start(conn, u, svc.new_key())
    assert alice.view(conn, task)["qr_url"]  # the QR is on screen
    with pytest.raises(svc.ServiceError):  # no photo before «QR-код отсканирован»
        alice.put_photo(conn, u, task["id"], 1, JPG, "jpg")
    task = alice.scanned(conn, u, task["id"])
    assert alice.view(conn, task)["qr_url"] is None and task["qr_id"] == qr["id"]  # hidden, still recorded
    with pytest.raises(svc.ServiceError) as e:  # no skipping
        alice.put_photo(conn, u, task["id"], 2, JPG, "jpg")
    assert e.value.code == "wrong_stage"
    with pytest.raises(svc.ServiceError) as e:
        alice.fix_stage(conn, u, task["id"])
    assert e.value.code == "photo_missing_1"
    alice.put_photo(conn, u, task["id"], 1, JPG + b"a", "jpg")
    alice.put_photo(conn, u, task["id"], 1, JPG + b"b", "jpg")  # «Переснять»: the new one replaces the old
    assert len(alice.photos(conn, task["id"])) == 1
    alice.delete_photo(conn, u, task["id"], 1)
    assert alice.photos(conn, task["id"]) == {}
    alice.put_photo(conn, u, task["id"], 1, JPG + b"c", "jpg")
    alice.fix_stage(conn, u, task["id"])
    with pytest.raises(svc.ServiceError):  # a fixed stage can't be changed quietly
        alice.put_photo(conn, u, task["id"], 1, JPG, "jpg")
    with pytest.raises(svc.ServiceError) as e:  # not all three yet
        alice.finish(conn, u, task["id"], svc.new_key())
    assert e.value.code == "photos_required"


def test_forum_delivery_failure_retry_moderation(conn, setup):
    owner, u = setup
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    qr = alice.create_qr(conn, owner, "local:qr.png", "7", svc.new_key())
    alice.assign(conn, owner, qr["id"], u["id"], True, svc.new_key())
    task = full_run(conn, u)
    assert task["status"] == "sending"
    # the group refuses: «Не отправлено», never a silent success; the files wait on the server
    task = run(alice.deliver(conn, ForumBot(fail=True), task["id"]))
    assert task["status"] == "send_failed" and "rights" in task["send_error"]
    assert all(p["temp_path"] for p in alice.photos(conn, task["id"]).values())
    bot = ForumBot()
    task = run(alice.deliver(conn, bot, task["id"]))
    assert task["status"] == "moderation" and task["thread_id"] == 101
    chat, thread, captions = bot.albums[0]
    # ТЗ 28.10 §22: every photo says the task and its stage, the name opens the profile; the common block — on the first
    assert chat == FORUM and "Алиса #" in captions[0] and "фото 1/3 — Подтверждение участия" in captions[0]
    assert "QR: 7" in captions[0] and "На модерации" in captions[0] and "https://t.me/ivan" in captions[0]
    assert "фото 2/3 — Участие подтверждено" in captions[1] and "фото 3/3 — Поисковый запрос выполнен" in captions[2]
    assert all("Алиса #" in c and "https://t.me/ivan" in c for c in captions) and "Запрос" not in captions[1]
    shots = alice.photos(conn, task["id"])
    assert all(p["temp_path"] is None and p["file_id"] for p in shots.values())  # nothing kept on the server
    # moderation: once, with a reason; the topic shows it
    with pytest.raises(svc.ServiceError):
        alice.moderate(conn, u, task["id"], True, None, svc.new_key())  # not staff
    done, changed = alice.moderate(conn, owner, task["id"], False, "Нет экрана с запросом", svc.new_key())
    assert changed and done["status"] == "rejected"
    with pytest.raises(svc.ServiceError) as e:
        alice.moderate(conn, owner, task["id"], True, None, svc.new_key())
    assert e.value.code == "already_decided"
    run(alice.post_status(conn, bot, task["id"]))
    assert "Отклонено" in bot.edits[0][1] and "Нет экрана с запросом" in bot.messages[0]


def test_stats_duplicates_payout_by_yandex_report(conn, setup, make_user):
    owner, u = setup
    v = make_user(first="Вера")
    for person in (u, v):
        products.set_access(conn, owner, person["id"], "alice", True, svc.new_key())
    qr = alice.create_qr(conn, owner, "local:qr.png", "QR-1", svc.new_key())
    for person in (u, v):  # one QR for two people — allowed
        alice.assign(conn, owner, qr["id"], person["id"], True, svc.new_key())
    bot, ids = ForumBot(), []
    for i, person in enumerate((u, v, u, u)):
        t = full_run(conn, person, JPG + bytes([i]))
        run(alice.deliver(conn, bot, t["id"]))
        ids.append(t["id"])
    for t in ids[:3]:
        alice.moderate(conn, owner, t, True, None, svc.new_key())
    alice.moderate(conn, owner, ids[3], False, None, svc.new_key())
    today = now_local().date()
    st = alice.my_stats(conn, u["id"], today)["all"]
    assert st == {"completed": 3, "moderation": 0, "rejected": 1, "accepted": 2, "paid": 0, "yandex_rejected": 0}
    # the same photo in another execution is flagged for the moderator
    t = full_run(conn, v, JPG + bytes([0]))
    assert alice.duplicates(conn, t["id"]) == [ids[0]]
    # ТЗ 28.10 §25 (В50): Yandex confirmed 2 of the period's 3 for QR-1 — 2 picked at random (stably), 1 «Отклонено Яндексом»
    with pytest.raises(svc.ServiceError) as e:
        alice.reconcile(conn, today, today, {qr["id"]: 4})
    assert e.value.code == "too_many"
    plan = alice.reconcile(conn, today, today, {qr["id"]: 2}, rate=100)
    row = plan["rows"][0]
    assert (row["waiting"], row["confirmed"], row["yandex_rejected"]) == (3, 2, 1) and len(plan["rejected"]) == 1
    assert alice.reconcile(conn, today, today, {qr["id"]: 2}, rate=100)["pay"] == plan["pay"]  # reopening: the same split
    assert sum(p["count"] for p in plan["pay"]) == 2 and plan["total"] == 200
    assert alice.get(conn, ids[0])["status"] == "accepted"  # a preview changes nothing
    assert alice.reconcile(conn, today, today, {})["rejected"] == []  # a QR without a number is left as it is
    app_settings.update(conn, {"alice_rate": 100})
    requisites(conn, u, v)
    k = svc.new_key()
    batch_id, created = alice.commit_payout(conn, owner, "Алиса, октябрь", today, today, {qr["id"]: 2}, [], k)
    assert created and alice.commit_payout(conn, owner, "Алиса, октябрь", today, today, {qr["id"]: 2}, [], k) == (batch_id, False)
    statuses = sorted(alice.get(conn, i)["status"] for i in ids[:3])
    assert statuses == ["paid", "paid", "yandex_rejected"] and alice.get(conn, ids[3])["status"] == "rejected"
    batch = payouts.batch(conn, batch_id)
    assert batch["product"] == "alice" and batch["total"] == 200 and batch["rates"]["alice_rate"] == 100
    assert sorted(t["status"] for t in batch["tasks"]) == ["paid", "paid", "yandex_rejected"]
    # a rate change doesn't touch the paid ones; nothing is left waiting in that period
    app_settings.update(conn, {"alice_rate": 150})
    assert alice.reconcile(conn, today, today, {})["rows"][0]["waiting"] == 0
    # the Alice payout doesn't touch the pieces' «paid through» nor the lazy pool
    assert payouts.employee_view(conn, u["id"], today)["paid_through"] is None


def test_goal_by_product_counts_accepted_and_needs_access(conn, setup, make_user):
    owner, u = setup
    w = make_user(first="Без доступа")
    today = now_local().date()
    with pytest.raises(svc.ServiceError) as e:  # §29: warn and offer to grant
        goals.create(conn, owner, title="10 выполнений", kind="personal", target=2, date_from=today, date_to=today,
                     key=svc.new_key(), user_id=w["id"], product="alice")
    assert e.value.code == "no_product_access"
    g = goals.create(conn, owner, title="10 выполнений", kind="personal", target=2, date_from=today, date_to=today,
                     key=svc.new_key(), user_id=w["id"], product="alice", grant_access=True)
    assert products.has(conn, svc.get_user(conn, w["id"]), "alice")
    team = goals.create(conn, owner, title="Команда", kind="team", target=5, date_from=today, date_to=today,
                        key=svc.new_key(), product="alice")
    assert goals.eligible(conn, team, svc.get_user(conn, w["id"])) and not goals.eligible(conn, team, u)
    qr = alice.create_qr(conn, owner, "local:qr.png", None, svc.new_key())
    alice.assign(conn, owner, qr["id"], w["id"], True, svc.new_key())
    w = svc.get_user(conn, w["id"])
    t = full_run(conn, w)
    run(alice.deliver(conn, ForumBot(), t["id"]))
    view = goals.view(conn, goals.get(conn, g["id"]), w)
    assert (view["progress"], view["pending"], view["product"]) == (0, 1, "alice")
    alice.moderate(conn, owner, t["id"], True, None, svc.new_key())
    assert goals.view(conn, goals.get(conn, g["id"]), w)["progress"] == 1


def test_http_work_section(conn, make_user, tmp_path):
    owner_h = headers(999, "Саня", "Владелец", "boss")
    client = TestClient(create_app(conn, Notifier(None, conn)))
    client.get("/api/me", headers=owner_h)
    u = make_user(first="Иван")
    uh = headers(u["telegram_id"])
    assert client.get("/api/me", headers=uh).json()["products"] == []
    assert client.get("/api/work/alice", headers=uh).status_code == 403  # not by a direct link either
    r = client.post(f"/api/admin/team/{u['id']}/products", json={"product": "alice", "allow": True, "idempotency_key": key()},
                    headers=owner_h)
    assert r.json()["items"][0]["active"]
    assert client.get("/api/work", headers=uh).json()["items"] == [{"product": "alice", "title": "Яндекс Алиса"}]
    r = client.post(f"/api/admin/alice/qr?idempotency_key={key()}&number=QR-9", content=JPG, headers=owner_h)
    qr = r.json()["items"][0]
    client.post(f"/api/admin/alice/qr/{qr['id']}/assign", json={"user_id": u["id"], "idempotency_key": key()}, headers=owner_h)
    t = client.post("/api/work/alice/start", json={"idempotency_key": key()}, headers=uh).json()["task"]
    assert t["qr_url"] and t["stages"][0]["title"] == "Подтверждение участия"
    client.post(f"/api/work/alice/{t['id']}/scanned", headers=uh)
    for stage in (1, 2, 3):
        r = client.put(f"/api/work/alice/{t['id']}/photo/{stage}", content=JPG + bytes([stage]), headers=uh)
        assert r.status_code == 200, r.text
        photo = r.json()["task"]["stages"][stage - 1]["photo_url"]
        assert client.get(photo).status_code == 200  # the person sees what they attached
        client.post(f"/api/work/alice/{t['id']}/next", headers=uh)
    done = client.post(f"/api/work/alice/{t['id']}/finish", json={"idempotency_key": key()}, headers=uh).json()["task"]
    assert done["status"] == "send_failed" and done["send_error"]  # no bot here — honestly «не отправлено»
    assert client.get("/api/admin/alice/queue", headers=uh).status_code == 403
    assert client.get("/api/admin/alice/queue?status=send_failed", headers=owner_h).json()["items"][0]["id"] == t["id"]
    assert json.loads(json.dumps(client.get("/api/admin/alice/stats?from=2026-01-01&to=2030-01-01",
                                            headers=owner_h).json()))["total"]["completed"] == 1


def test_back_and_forth_keeps_photos_and_checks_the_files(conn, setup):
    """ТЗ 28.10 §19–§20: back to the QR and forward without rescanning, photos kept; a vanished photo stops the way
    with its stage named; «Прервать» deletes the photos and a second tap is harmless."""
    from pathlib import Path

    owner, u = setup
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    qr = alice.create_qr(conn, owner, "local:qr.png", None, svc.new_key())
    alice.assign(conn, owner, qr["id"], u["id"], True, svc.new_key())
    task = alice.start(conn, u, svc.new_key())
    alice.scanned(conn, u, task["id"])
    first_scan = alice.get(conn, task["id"])["scanned_at"]
    for stage in (1, 2):
        alice.put_photo(conn, u, task["id"], stage, JPG + bytes([stage]), "jpg")
        alice.fix_stage(conn, u, task["id"])
    task = alice.goto(conn, u, task["id"], 0)  # back to the QR code
    assert task["step"] == 0 and alice.view(conn, task)["qr_url"] and task["stage1_at"] is None
    task = alice.scanned(conn, u, task["id"])
    assert task["scanned_at"] == first_scan and set(alice.photos(conn, task["id"])) == {1, 2}  # nothing re-uploaded
    alice.fix_stage(conn, u, task["id"])
    # the stage-2 photo disappears from the server: «Далее» names it
    Path(alice.photos(conn, task["id"])[2]["temp_path"]).unlink()
    with pytest.raises(svc.ServiceError) as e:
        alice.fix_stage(conn, u, task["id"])
    assert e.value.code == "photo_missing_2" and "пропало" in e.value.message
    alice.put_photo(conn, u, task["id"], 2, JPG + b"new", "jpg")  # uploaded again — the way continues
    task = alice.fix_stage(conn, u, task["id"])
    assert task["step"] == 3
    with pytest.raises(svc.ServiceError):
        alice.goto(conn, u, task["id"], 3)  # only back
    left = alice.photos(conn, task["id"])
    alice.abandon(conn, u, task["id"])
    assert alice.get(conn, task["id"])["status"] == "abandoned" and alice.abandon(conn, u, task["id"])["status"] == "abandoned"
    assert alice.photos(conn, task["id"]) == {} and not any(Path(p["temp_path"]).exists() for p in left.values())
    assert alice.my_stats(conn, u["id"], now_local().date())["all"]["completed"] == 0


def test_a_photo_lost_before_sending_goes_back_to_the_person(conn, setup):
    from pathlib import Path

    owner, u = setup
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    qr = alice.create_qr(conn, owner, "local:qr.png", None, svc.new_key())
    alice.assign(conn, owner, qr["id"], u["id"], True, svc.new_key())
    task = full_run(conn, u)
    Path(alice.photos(conn, task["id"])[3]["temp_path"]).unlink()
    bot = ForumBot()
    task = run(alice.deliver(conn, bot, task["id"]))
    assert task["status"] == "in_progress" and task["step"] == 3 and "этапа 3" in task["send_error"] and bot.albums == []
