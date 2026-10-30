"""30.10: event entries are reviewed in the Mini App; every reviewer's bot card shows who decided."""

import dataclasses

import pytest

from app import events, notify
from app import services as svc

from .test_features import THU, FakeNotifier, _event, at, run


class CardBot:
    def __init__(self):
        self.edits: list[tuple[int, int, str, object]] = []

    async def edit_message_caption(self, chat_id, message_id, caption, parse_mode, reply_markup):
        self.edits.append((chat_id, message_id, caption, reply_markup))

    async def edit_message_text(self, text, chat_id, message_id, parse_mode, reply_markup, disable_web_page_preview):
        self.edits.append((chat_id, message_id, text, reply_markup))


@pytest.fixture
def https(monkeypatch):
    monkeypatch.setattr(notify, "settings", dataclasses.replace(notify.settings, webapp_url="https://app.example/?v=2"))


def _setup(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    milana = make_user(first="Милана", last="Федорова", username="mila")
    ev = _event(conn, owner, mode="accumulative", target=None)
    entry, _ = events.submit(conn, milana, ev["id"], 20, "photo-id", svc.new_key(), now=at(THU, "12:00"))
    return owner, milana, ev, entry


def _second(conn, owner, make_user):
    max_ = make_user(first="Максим", last="Селезнев", username="max")
    svc.set_role(conn, owner, max_["id"], "admin", svc.new_key())
    return svc.get_user(conn, max_["id"])


def test_second_reviewer_learns_who_decided(conn, make_user):
    owner, milana, ev, entry = _setup(conn, make_user)
    trainer = _second(conn, owner, make_user)
    events.review(conn, trainer, entry["id"], True, None, svc.new_key())
    with pytest.raises(svc.ServiceError) as e:
        events.review(conn, owner, entry["id"], True, None, svc.new_key())
    assert e.value.code == "already_decided" and e.value.status == 409
    assert e.value.message.startswith("Уже решено: ✅ Подтверждено — Максим Селезнев в ")
    # the same request repeated (same key) is still a quiet no-op
    key = svc.new_key()
    entry2, _ = events.submit(conn, make_user(first="Оля"), ev["id"], 5, "p", svc.new_key(), now=at(THU, "12:10"))
    assert events.review(conn, owner, entry2["id"], False, "Не тот скрин", key).changed
    assert not events.review(conn, owner, entry2["id"], False, "Не тот скрин", key).changed


def test_closed_event_says_so(conn, make_user):
    owner, milana, ev, entry = _setup(conn, make_user)
    conn.execute("UPDATE events SET status = 'finished' WHERE id = ?", (ev["id"],))
    with pytest.raises(svc.ServiceError) as e:
        events.review(conn, owner, entry["id"], True, None, svc.new_key())
    assert e.value.code == "event_closed"


def test_card_opens_the_app_and_is_marked_for_everyone(conn, make_user, https):
    owner, milana, ev, entry = _setup(conn, make_user)
    second = _second(conn, owner, make_user)
    n = FakeNotifier(conn)
    run(n.event_entry(entry))
    cards = {chat: markup for chat, markup in n.markups}
    assert set(cards) >= {owner["telegram_id"], second["telegram_id"]}
    button = cards[owner["telegram_id"]].inline_keyboard[0][0]
    assert button.text == "🏁 Проверить в приложении" and button.callback_data is None
    assert button.web_app.url == f"https://app.example/?v=2&open=events&entry={entry['id']}"
    assert any("Подтвердить или отклонить — в приложении" in t for _, t in n.sent)
    stored = conn.execute("SELECT chat_id, photo FROM event_entry_messages WHERE entry_id = ?", (entry["id"],)).fetchall()
    assert {r[0] for r in stored} == set(cards) and all(r[1] == 1 for r in stored)

    n.bot = CardBot()
    result = events.review(conn, second, entry["id"], True, None, svc.new_key())
    run(n.event_reviewed(result, second))
    assert {e[0] for e in n.bot.edits} == set(cards)  # every reviewer's card, the decider's too
    assert all("✅ Подтверждено</b> — Максим Селезнев" in e[2] and e[3] is None for e in n.bot.edits)


def test_rejection_reason_on_the_cards(conn, make_user, https):
    owner, milana, ev, entry = _setup(conn, make_user)
    n = FakeNotifier(conn)
    run(n.event_entry(entry))
    n.bot = CardBot()
    result = events.review(conn, owner, entry["id"], False, "Скрин не тот", svc.new_key())
    run(n.event_reviewed(result, owner))
    assert n.bot.edits and all("❌ Отклонено" in e[2] and "Причина: Скрин не тот" in e[2] for e in n.bot.edits)


def test_without_https_the_chat_buttons_stay(conn, make_user, monkeypatch):
    monkeypatch.setattr(notify, "settings", dataclasses.replace(notify.settings, webapp_url=""))
    owner, milana, ev, entry = _setup(conn, make_user)
    n = FakeNotifier(conn)
    run(n.event_entry(entry))
    row = n.markups[0][1].inline_keyboard[0]
    assert [b.callback_data for b in row] == [f"ev:ok:{entry['id']}", f"ev:no:{entry['id']}"]


def test_app_link_keeps_the_address(monkeypatch):
    monkeypatch.setattr(notify, "settings", dataclasses.replace(notify.settings, webapp_url="https://x.example/app/"))
    assert notify.app_link(open="events", entry=4) == "https://x.example/app/?open=events&entry=4"
