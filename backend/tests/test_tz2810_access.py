"""ТЗ 28.10, этап 1а: блокировка в боте, подписи ролей, один «тренер сети», «Работаю / Не работаю» на сервере."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from fastapi.testclient import TestClient

from app import alice, bot, networks, notify, products, reports, violations
from app import services as svc
from app.api import create_app
from app.notify import Notifier
from app.timeutil import today_local, week_start

from .test_api import headers, key
from .test_features import P, TODAY, run, shift


class Recorder:
    """A bot that only remembers what it was asked to send."""

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, disable_web_page_preview=None):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))


def blocked_user(conn, make_user, owner):
    u = make_user(first="Блок")
    svc.set_account_state(conn, owner, u["id"], "blocked", "нарушение", svc.new_key())
    return svc.get_user(conn, u["id"])


# ---------------------------------------------------------------- §16: the block


def test_block_needs_a_reason_and_mutes_every_work_message(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Блок")
    with pytest.raises(svc.ServiceError) as e:
        svc.set_account_state(conn, owner, u["id"], "blocked", "  ", svc.new_key())
    assert e.value.code == "reason_required"
    svc.set_account_state(conn, owner, u["id"], "blocked", "нарушение", svc.new_key())
    rec = Recorder()
    n = Notifier(rec, conn)
    assert n.muted(u["telegram_id"]) and not n.muted(999) and not n.muted(-1001)
    assert run(n.send(u["telegram_id"], "напоминание")) is False  # any sender, any job
    assert rec.sent == []
    # the message about the block itself goes through, with the one text for the bot and the app
    run(n.account_state_changed(svc.get_user(conn, u["id"]), owner, "active"))
    assert rec.sent[0][0] == u["telegram_id"] and rec.sent[0][1].startswith(notify.BLOCKED_TEXT)


def test_bot_answers_a_blocked_person_with_one_text_and_does_nothing(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = blocked_user(conn, make_user, owner)
    ok = make_user(first="Свой")
    replies, handled = [], []

    class Msg(Message):
        async def answer(self, text, **kw):
            replies.append(text)

    class Call(CallbackQuery):
        async def answer(self, text=None, show_alert=None, **kw):
            replies.append((text, show_alert))

    def message(tg, chat_type="private"):
        return Msg(message_id=1, date=datetime.now(), chat=Chat(id=tg, type=chat_type),
                   from_user=User(id=tg, is_bot=False, first_name="x"), text="/start")

    async def handler(event, data):
        handled.append(event)
        return "done"

    class State:
        cleared = False

        async def clear(self):
            State.cleared = True

    guard = bot.make_block_guard(conn)
    assert run(guard(handler, message(u["telegram_id"]), {"state": State()})) is None
    assert replies == [notify.BLOCKED_TEXT] and State.cleared and handled == []
    # an old button under an old message — the same answer, nothing runs
    call = Call(id="1", from_user=User(id=u["telegram_id"], is_bot=False, first_name="x"), chat_instance="c", data="ack:5")
    assert run(guard(handler, call, {})) is None
    assert replies[-1] == (notify.BLOCKED_TEXT, True) and handled == []
    # others pass; group chats are not touched
    assert run(guard(handler, message(ok["telegram_id"]), {})) == "done"
    assert run(guard(handler, message(u["telegram_id"], "supergroup"), {})) == "done"


# ---------------------------------------------------------------- §13: who signs a broadcast


def test_broadcast_signature_follows_the_real_role(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    net = networks.create(conn, owner, "Север", svc.new_key())
    trainer = make_user(first="Вера", username=None)
    instructor = make_user(first="Ира", username="ira")
    worker, mine, other = make_user(first="Аня"), make_user(first="Мой"), make_user(first="Чужой")
    for p in (trainer, instructor, worker, mine, other):
        networks.set_member(conn, owner, p["id"], net["id"], svc.new_key())
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    svc.set_role(conn, owner, instructor["id"], "instructor", svc.new_key())
    networks.set_instructor(conn, owner, mine["id"], instructor["id"], svc.new_key())

    rec = Recorder()
    n = Notifier(rec, conn)
    client = TestClient(create_app(conn, n, "test_bot"))
    th = headers(trainer["telegram_id"], "Вера", "Петров", None)
    ih = headers(instructor["telegram_id"], "Ира", "Петров", "ira")
    oh = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=oh)

    # a trainer can't send «Сообщение от администратора» by switching it on — nor send without a signature
    view = client.get("/api/admin/broadcasts", headers=th).json()
    assert view["signer"]["optional"] is False and "Сообщение от тренера" in view["signer"]["text"]
    for signed in (True, False):
        r = client.post("/api/admin/broadcasts", json={"audiences": [], "html": "Сбор в 9", "signed": signed,
                                                       "user_ids": [worker["id"]], "idempotency_key": key()}, headers=th)
        assert r.status_code == 200, r.text
        sig = r.json()["broadcast"]["signature"]
        assert "Сообщение от тренера" in sig and "администратора" not in sig
        assert f'tg://user?id={trainer["telegram_id"]}' in sig and str(trainer["telegram_id"]) in sig  # no username: ID

    # an instructor broadcasts too, only to the people assigned to them
    view = client.get("/api/admin/broadcasts", headers=ih).json()
    assert {p["id"] for p in view["people"]} - {instructor["id"]} == {mine["id"]} and "Сообщение от инструктора" in view["signer"]["text"]
    r = client.post("/api/admin/broadcasts", json={"audiences": [], "html": "Привет", "signed": False,
                                                   "user_ids": [mine["id"], other["id"]], "idempotency_key": key()}, headers=ih)
    assert r.status_code == 200 and r.json()["broadcast"]["recipients"] == 1
    assert "https://t.me/ira" in r.json()["broadcast"]["signature"]

    # owners and admins choose
    assert client.get("/api/admin/broadcasts", headers=oh).json()["signer"]["optional"] is True
    r = client.post("/api/admin/broadcasts", json={"audiences": [], "html": "Без подписи", "signed": False,
                                                   "user_ids": [worker["id"]], "idempotency_key": key()}, headers=oh)
    assert r.json()["broadcast"]["signature"] is None
    r = client.post("/api/admin/broadcasts", json={"audiences": [], "html": "С подписью", "signed": True,
                                                   "user_ids": [worker["id"]], "idempotency_key": key()}, headers=oh)
    assert r.json()["broadcast"]["signature"] == notify.ADMIN_SIGNATURE
    items = client.get("/api/admin/broadcasts", headers=oh).json()["items"]
    assert items[0]["signature_text"] == "📣 Сообщение от администратора" and items[1]["signature_text"] is None


def test_trainer_actions_are_not_called_admin(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    net = networks.create(conn, owner, "Север", svc.new_key())
    trainer, u = make_user(first="Вера"), make_user(first="Аня")
    for p in (trainer, u):
        networks.set_member(conn, owner, p["id"], net["id"], svc.new_key())
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    trainer = svc.get_user(conn, trainer["id"])
    s = shift(conn, u, payload=P)
    text = notify.admin_edited_text(trainer, u, s, "поправил время")
    assert text.startswith("⚙️ Тренер ") and "Админ" not in text
    assert "Тренер" in notify.STAFF_TAB.values() and notify.STAFF_TAB["instructor"] == "Инструктор"


# ---------------------------------------------------------------- В32: one definition of «тренер сети»


def test_network_trainer_is_networks_trainer_id(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    north = networks.create(conn, owner, "Север", svc.new_key())
    vera, ira, u = make_user(first="Вера"), make_user(first="Ира"), make_user(first="Аня")
    for p in (vera, ira, u):
        networks.set_member(conn, owner, p["id"], north["id"], svc.new_key())
    # «Тренер» through the role: they become the head of their network
    svc.set_role(conn, owner, vera["id"], "trainer", svc.new_key())
    assert networks.get(conn, north["id"])["trainer_id"] == vera["id"]
    with pytest.raises(svc.ServiceError) as e:  # one trainer per network
        svc.set_role(conn, owner, ira["id"], "trainer", svc.new_key())
    assert e.value.code == "network_has_trainer"
    vera = svc.get_user(conn, vera["id"])
    assert vera["led_network_id"] == north["id"]
    assert reports.reviewers(conn, svc.get_user(conn, u["id"])) == sorted([999, vera["telegram_id"]])
    # moving the trainer elsewhere frees the network: nobody manages it as «trainer» any more
    south = networks.create(conn, owner, "Юг", svc.new_key())
    networks.set_member(conn, owner, vera["id"], south["id"], svc.new_key())
    assert networks.get(conn, north["id"])["trainer_id"] is None and networks.get(conn, south["id"])["trainer_id"] == vera["id"]
    assert reports.reviewers(conn, svc.get_user(conn, u["id"])) == [999]


# ---------------------------------------------------------------- §9: «Работаю / Не работаю»


def test_not_working_blocks_work_actions_on_the_server(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user()
    s = shift(conn, u, payload=P)
    u = svc.set_work_status(conn, u, "not_working", svc.new_key())
    with pytest.raises(svc.ServiceError) as e:  # editing a booked shift
        svc.edit_shift(conn, u, s["id"], {**P, "volume": 30}, svc.new_key(), today=TODAY)
    assert e.value.code == "not_working"
    assert svc.cancel_shift(conn, u, s["id"], svc.new_key(), today=TODAY).changed  # cancelling stays possible
    # «Алиса»: no new execution; one already started can be finished
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    qr = alice.create_qr(conn, owner, "local:qr.png", None, svc.new_key())
    alice.assign(conn, owner, qr["id"], u["id"], True, svc.new_key())
    with pytest.raises(svc.ServiceError) as e:
        alice.start(conn, u, svc.new_key())
    assert e.value.code == "not_working"
    u = svc.set_work_status(conn, u, "working", svc.new_key())
    task = alice.start(conn, u, svc.new_key())
    u = svc.set_work_status(conn, u, "not_working", svc.new_key())
    assert alice.start(conn, u, svc.new_key())["id"] == task["id"]
    assert alice.scanned(conn, u, task["id"])["step"] == 1


def test_not_working_skips_the_weekly_norm_but_switching_off_later_does_not_dodge_it(conn, make_user):
    svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    today = today_local()
    week = week_start(today) - timedelta(days=7)
    old = (week - timedelta(days=30)).isoformat() + "T10:00:00+03:00"
    off, late, on = make_user(first="Выкл"), make_user(first="Поздно"), make_user(first="Вкл")
    conn.execute("UPDATE users SET decided_at = ?", (old,))
    conn.execute("UPDATE users SET work_status = 'not_working', work_status_at = ? WHERE id = ?", (old, off["id"]))
    conn.execute("UPDATE users SET work_status = 'not_working', work_status_at = ? WHERE id = ?",
                 (today.isoformat() + "T08:00:00+03:00", late["id"]))
    created = {v["user_id"] for v in violations.scan(conn, 3, today)}
    assert off["id"] not in created and {late["id"], on["id"]} <= created


def test_not_working_gets_no_goal_announcements(conn, make_user):
    from app import goals

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    on, off = make_user(first="Вкл"), make_user(first="Выкл", working=False)
    goal = goals.create(conn, owner, title="Неделя", kind="team", target=100, date_from=today_local(),
                        date_to=today_local() + timedelta(days=6), key=svc.new_key())
    rec = Recorder()
    run(Notifier(rec, conn).goal_created(goal))
    chats = {c for c, _ in rec.sent}
    assert on["telegram_id"] in chats and off["telegram_id"] not in chats


def test_a_block_takes_the_person_out_of_every_work_group_and_nobody_brings_them_back(conn, make_user):
    """ТЗ 28.10 §16 (В37): «Предзакреп», the main group and the «Алиса» forum — where Telegram says they are;
    a failure is reported; after unblocking there's no automatic invitation."""
    from app import alice, groups

    from .test_groups import MAIN, PRE, FakeBot, connected

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    connected(conn, owner)
    alice.connect_forum(conn, -1003, "Алиса")
    u = make_user(first="Блок")

    class Bot(FakeBot):
        async def get_chat_member(self, chat_id, user_id):
            if chat_id == -1003:
                from types import SimpleNamespace
                return SimpleNamespace(status=self.members.get((chat_id, user_id), "left"))
            return await super().get_chat_member(chat_id, user_id)

    bot = Bot()
    bot.members[(MAIN, u["telegram_id"])] = "member"
    bot.members[(-1003, u["telegram_id"])] = "member"
    told = []

    class N:
        async def to_admins(self, text, *a, **k):
            told.append(text)

    svc.set_account_state(conn, owner, u["id"], "blocked", "нарушение", svc.new_key())
    out = run(groups.kick_blocked(conn, bot, N(), svc.get_user(conn, u["id"]), owner))
    assert out == {"pre": "not_member", "main": "removed", "alice": "removed"} and told == []
    assert bot.members[(MAIN, u["telegram_id"])] == "left" and bot.members[(-1003, u["telegram_id"])] == "left"
    weak = Bot(rights=False)
    weak.members[(PRE, u["telegram_id"])] = "member"
    out = run(groups.kick(conn, weak, N(), svc.get_user(conn, u["id"]), ("pre",), owner, attempts=1))
    assert out == {"pre": "failed"} and "Не удалось исключить" in told[-1]
    svc.set_account_state(conn, owner, u["id"], "active", None, svc.new_key())
    sent = []

    async def notify(user, kind, inv):
        sent.append(user["id"])

    run(groups.auto_invites(conn, bot, notify))
    assert u["id"] not in sent
