"""ТЗ 28.10, этап 7: удаление «Алисы» с архивом, штраф за «Алису», правка и аннулирование выплат."""

from types import SimpleNamespace

import pytest

from app import alice, app_settings, finance, payouts, products
from app import services as svc
from app.timeutil import now_local

from .conftest import requisites
from .test_alice import JPG, ForumBot, full_run
from .test_features import run
from .test_lazy import commit
from .test_rates import WEEK, did


def alice_world(conn, make_user, n=3):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Иван")
    alice.connect_forum(conn, -1009, "Алиса")
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    qr = alice.create_qr(conn, owner, "local:qr.png", "QR-1", svc.new_key())
    alice.assign(conn, owner, qr["id"], u["id"], True, svc.new_key())
    bot, ids = ForumBot(), []
    for i in range(n):
        t = full_run(conn, u, JPG + bytes([i, 7]))
        run(alice.deliver(conn, bot, t["id"]))
        alice.moderate(conn, owner, t["id"], True, None, svc.new_key())
        ids.append(t["id"])
    requisites(conn, u)
    app_settings.update(conn, {"alice_rate": 100})
    return owner, u, qr, ids, bot


class ArchiveBot(ForumBot):
    def __init__(self, fail_delete=False):
        super().__init__()
        self.archived, self.deleted, self.fail_delete = [], [], fail_delete

    async def send_media_group(self, chat_id, media, message_thread_id=None):
        if message_thread_id is None:  # the archive chat
            self.archived.append((chat_id, [m.caption for m in media]))
            return [SimpleNamespace(message_id=900 + i) for i in range(len(media))]
        return await super().send_media_group(chat_id, media, message_thread_id)

    async def delete_messages(self, chat_id, message_ids):
        from aiogram.exceptions import TelegramBadRequest

        if self.fail_delete:
            raise TelegramBadRequest(method=None, message="message can't be deleted")
        self.deleted.append((chat_id, list(message_ids)))


def test_deleting_a_task_archives_its_photos_and_removes_the_publication_once(conn, make_user):
    owner, u, qr, ids, _ = alice_world(conn, make_user, 2)
    app_settings.set_raw(conn, "archive_chat_id", "-1777")
    bot = ArchiveBot(fail_delete=True)
    task, changed = alice.delete_task(conn, owner, ids[0], "Повтор фото", svc.new_key())
    assert changed and task["status"] == "deleted"
    task = run(alice.archive_and_unpublish(conn, bot, ids[0], owner))
    assert task["archive_state"] == "done" and task["unpublish_state"] == "failed"
    chat, captions = bot.archived[0]
    assert chat == -1777 and "Алиса #" in captions[0] and "Повтор фото" in captions[0] and "Саня" in captions[0]
    # a repeat does only what failed — no second archive copy
    bot.fail_delete = False
    again, changed = alice.delete_task(conn, owner, ids[0], None, svc.new_key())
    task = run(alice.archive_and_unpublish(conn, bot, ids[0], owner))
    assert not changed and task["unpublish_state"] == "done" and len(bot.archived) == 1
    assert bot.deleted == [(-1009, task and __import__("json").loads(task["message_ids"]))]
    # gone from the lists and statistics, the neighbour untouched
    assert [t["id"] for t in alice.history(conn, u["id"])] == [ids[1]]
    assert alice.my_stats(conn, u["id"], now_local().date())["all"]["accepted"] == 1
    assert alice.get(conn, ids[1])["status"] == "accepted"


def test_a_paid_task_waits_for_its_payout_to_change(conn, make_user):
    owner, u, qr, ids, _ = alice_world(conn, make_user, 1)
    today = now_local().date()
    batch_id, _ = alice.commit_payout(conn, owner, "Алиса", today, today, {qr["id"]: 1}, [], svc.new_key())
    with pytest.raises(svc.ServiceError) as e:
        alice.delete_task(conn, owner, ids[0], None, svc.new_key())
    assert e.value.code == "in_payout"
    payouts.annul(conn, owner, batch_id, "Ошибка сверки", False, svc.new_key())
    assert alice.get(conn, ids[0])["status"] == "accepted"  # waits for payment again
    assert alice.delete_task(conn, owner, ids[0], None, svc.new_key())[1]


def test_alice_fine_is_one_debt_taken_once_and_given_back_by_an_annulment(conn, make_user):
    owner, u, qr, ids, _ = alice_world(conn, make_user, 3)
    trainer = make_user(first="Тренер")
    with pytest.raises(svc.ServiceError):  # В49: trainers don't fine for «Алиса»
        finance.create(conn, trainer, u["id"], "fine", None, "x", svc.new_key(), units=1, product="alice")
    with pytest.raises(svc.ServiceError) as e:
        finance.create(conn, owner, u["id"], "fine", None, "Повтор фото", svc.new_key(), units=1, alice_task_id=99999)
    assert e.value.code == "bad_task"
    fine = finance.create(conn, owner, u["id"], "fine", None, "Повтор фото", svc.new_key(), units=1,
                          alice_task_id=ids[0]).tx
    assert fine["product"] == "alice" and fine["alice_task_id"] == ids[0] and fine["amount"] == 120  # 1 unit × the rate
    today = now_local().date()
    plan = alice.reconcile(conn, today, today, {qr["id"]: 2})
    assert plan["pay"][0]["fines"] == 120 and plan["total"] == 200 - 120
    batch_id, _ = alice.commit_payout(conn, owner, "Алиса", today, today, {qr["id"]: 2}, [], svc.new_key())
    assert finance.get(conn, fine["id"])["status"] == "offset" and finance.get(conn, fine["id"])["payout_id"] == batch_id
    # edited: the same payout, the fine taken once again, not twice
    alice.edit_payout(conn, owner, batch_id, {qr["id"]: 3}, svc.new_key(), "Яндекс досчитал")
    b = payouts.batch(conn, batch_id)
    assert b["total"] == 300 - 120 and b["version"] == 2 and len(b["items"]) == 1 and b["versions"][0]["version"] == 1
    assert len(payouts.history(conn)) == 1  # no duplicate
    # annulled: the fine is active again (to be taken by the next payout), the tasks wait again
    b, made = payouts.annul(conn, owner, batch_id, "Ошибка", False, svc.new_key())
    assert b["status"] == "annulled" and made == [] and finance.get(conn, fine["id"])["status"] == "active"
    assert all(alice.get(conn, i)["status"] == "accepted" for i in ids)
    assert payouts.history(conn)[0]["status"] == "annulled" and b["items"][0]["total"] == 180  # still shows what it was
    with pytest.raises(svc.ServiceError) as e:
        payouts.annul(conn, owner, batch_id, "Ещё раз", False, svc.new_key())
    assert e.value.code == "annulled"


def test_regular_payout_edit_annul_overlap_and_money_already_sent(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u, v = make_user(first="Аня"), make_user(first="Боря")
    for d in WEEK[:3]:
        did(conn, u, d, 10)  # lazy days (below 20) — they fill the pool
        did(conn, v, d, 30)
    fine = finance.create(conn, owner, u["id"], "fine", None, "Опоздание", svc.new_key(), event_date=WEEK[0], units=1).tx
    pool = payouts.lazy_pool(conn, u["id"], 20, WEEK[-1])
    k = svc.new_key()
    b = commit(conn, owner, [u], WEEK[0], WEEK[-1], k)
    assert payouts.commit(conn, owner, "Зарплата", WEEK[0], WEEK[-1], payouts.Rates.from_dict(None), None,
                          [{"user_id": u["id"]}], [], [], k)["id"] == b["id"]  # a repeat returns the same payout
    assert payouts.lazy_pool(conn, u["id"], 20, WEEK[-1]) < pool
    with pytest.raises(svc.ServiceError) as e:  # В55: the same days of the same person — refused
        commit(conn, owner, [u, v], WEEK[1], WEEK[2])
    assert e.value.code == "overlap" and "Аня" in e.value.message
    # edit: Боря added, the same payout; the fine is taken once
    payouts.edit(conn, owner, b["id"], [{"user_id": u["id"]}, {"user_id": v["id"]}], svc.new_key(), "добавили Борю")
    b2 = payouts.batch(conn, b["id"])
    assert len(b2["items"]) == 2 and b2["version"] == 2 and finance.get(conn, fine["id"])["status"] == "offset"
    assert conn.execute("SELECT COUNT(*) FROM payout_items WHERE batch_id = ? AND voided = 0", (b["id"],)).fetchone()[0] == 2
    # annul with money already sent: everything back, an overpayment deduction per person
    paid = {i["user_id"]: i["total"] for i in b2["items"]}
    b3, made = payouts.annul(conn, owner, b["id"], "Ошибка в периоде", True, svc.new_key())
    assert b3["status"] == "annulled" and finance.get(conn, fine["id"])["status"] == "active"
    assert payouts.lazy_pool(conn, u["id"], 20, WEEK[-1]) == pool  # the lazy pieces are back
    assert {t["user_id"]: t["amount"] for t in made} == {k_: v_ for k_, v_ in paid.items() if v_ > 0}
    assert all(t["type"] == "deduction" and "Переплата" in t["reason"] for t in made)
    # the same days may be paid again now
    assert commit(conn, owner, [u, v], WEEK[0], WEEK[-1])["id"] != b["id"]


def test_only_the_latest_payout_of_a_person_changes(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Аня")
    did(conn, u, WEEK[0], 25)
    did(conn, u, WEEK[3], 25)
    first = commit(conn, owner, [u], WEEK[0], WEEK[1])
    second = commit(conn, owner, [u], WEEK[2], WEEK[4])
    with pytest.raises(svc.ServiceError) as e:
        payouts.annul(conn, owner, first["id"], "x", False, svc.new_key())
    assert e.value.code == "not_latest" and f"№{second['id']}" in e.value.message
    with pytest.raises(svc.ServiceError):
        payouts.annul(conn, owner, second["id"], " ", False, svc.new_key())  # the reason is required
    payouts.annul(conn, owner, second["id"], "Ошибка", False, svc.new_key())
    assert payouts.annul(conn, owner, first["id"], "Ошибка", False, svc.new_key())[0]["status"] == "annulled"
