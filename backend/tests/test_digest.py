"""19.11: service notices to managers («встал в график», «взял выходной»…) may be grouped into one digest
per chat instead of pinging every time. The digest flushes at most once per the configured interval and
never during the quiet hours (a night item comes in the morning); «без звука» sends the notices silently —
in both modes. Cards with decision buttons (urgent, final cancel, fine requests) and personal messages to
a person always go straight away."""

from datetime import timedelta

import pytest

from app import app_settings, digest, finance, jobs
from app import services as svc

from .test_features import FakeNotifier, THU, TODAY, at, run, shift


def owner_of(conn):
    return svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")


def enable(conn, **changes):
    app_settings.update(conn, {"staff_digest_enabled": True, **changes})


def queued(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM notify_queue ORDER BY id")]


def test_off_by_default(conn, make_user):
    owner_of(conn)
    u = make_user(first="Иван")
    n = FakeNotifier(conn)
    s = shift(conn, u)
    run(n.shift_event("SHIFT_CREATED", s["id"]))
    assert len(n.sent) == 1 and queued(conn) == []  # as before: at once, the full card
    assert "Новая запись в график" in n.sent[0][1] and n.silents[-1] is False


def test_grouped_schedule_events(conn, make_user):
    owner = owner_of(conn)
    enable(conn)
    u = make_user(first="Иван")
    n = FakeNotifier(conn)
    s = shift(conn, u)
    run(n.shift_event("SHIFT_CREATED", s["id"]))
    assert n.sent == []  # nothing pings yet
    rows = queued(conn)
    assert len(rows) == 1 and rows[0]["chat_id"] == owner["telegram_id"] and rows[0]["category"] == "schedule"
    assert "встал(а) в график" in rows[0]["text"] and "Иван" in rows[0]["text"]
    run(n.shift_event("SHIFT_CANCELLED", s["id"]))
    run(n.shift_event("SHIFT_RESTORED", s["id"]))
    assert len(queued(conn)) == 3
    # the flush: one message, the items grouped under their category, the queue drained
    assert run(digest.flush(conn, n, now=at(TODAY, "13:00"))) == 1
    assert len(n.sent) == 1 and "Сводка" in n.sent[0][1] and "График" in n.sent[0][1]
    assert n.sent[0][1].count("•") == 3 and queued(conn) == []
    assert run(digest.flush(conn, n, now=at(TODAY, "13:01"))) == 0  # nothing left — nothing sent


def test_trainer_gets_their_network_only(conn, make_user):
    from app import networks

    owner = owner_of(conn)
    enable(conn)
    trainer, u = make_user(first="Вера"), make_user(first="Аня")
    net = networks.create(conn, owner, "Север", svc.new_key())
    networks.set_member(conn, owner, u["id"], net["id"], svc.new_key())
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    n = FakeNotifier(conn)
    run(n.shift_event("SHIFT_CREATED", shift(conn, u)["id"]))
    chats = {r["chat_id"] for r in queued(conn)}
    assert chats == {999, trainer["telegram_id"]}  # the owner and the network's trainer, each once


def test_interval_gate(conn, make_user):
    owner_of(conn)
    enable(conn)  # default: once an hour
    u = make_user(first="Иван")
    n = FakeNotifier(conn)
    run(n.shift_event("SHIFT_CREATED", shift(conn, u)["id"]))
    now = at(TODAY, "13:00")
    assert any(r.startswith("digest") for r in run(jobs.tick(conn, n, now=now)))  # the first due flush
    assert len(n.sent) == 1
    run(n.shift_event("SHIFT_CANCELLED", shift(conn, u, THU + timedelta(days=1))["id"]))  # another day
    soon = now + timedelta(minutes=30)
    assert not any(r.startswith("digest") for r in run(jobs.tick(conn, n, now=soon)))  # not due yet
    assert len(queued(conn)) == 1
    later = now + timedelta(minutes=61)
    assert any(r.startswith("digest") for r in run(jobs.tick(conn, n, now=later)))
    assert len(queued(conn)) == 0


def test_quiet_hours_hold_the_digest(conn, make_user):
    owner_of(conn)
    enable(conn)  # quiet hours on by default: 22:00 → 09:00
    u = make_user(first="Иван")
    n = FakeNotifier(conn)
    run(n.shift_event("SHIFT_CANCELLED", shift(conn, u)["id"]))  # the night decision
    night = at(TODAY, "23:00")
    morning = at(TODAY + timedelta(days=1), "09:00")
    assert digest.quiet_now(conn, night) and not digest.quiet_now(conn, morning)
    assert digest.quiet_now(conn, at(TODAY, "08:30")) and not digest.quiet_now(conn, at(TODAY, "13:00"))
    # a tick at night may run its daily jobs, but never the digest — and the interval is not burnt
    assert not any(r.startswith("digest") for r in run(jobs.tick(conn, n, now=night)))
    assert len(queued(conn)) == 1
    assert any(r.startswith("digest") for r in run(jobs.tick(conn, n, now=morning)))
    assert queued(conn) == []  # the accumulated item comes right after the quiet hours end
    # a quiet window without night wrap is honoured the same way
    app_settings.update(conn, {"staff_digest_quiet_from": "13:30", "staff_digest_quiet_to": "14:30"})
    run(n.shift_event("SHIFT_CREATED", shift(conn, u, THU + timedelta(days=1))["id"]))
    assert run(digest.flush(conn, n, now=at(TODAY, "13:35"))) == 0 and len(queued(conn)) == 1
    assert run(digest.flush(conn, n, now=at(TODAY, "14:31"))) == 1 and queued(conn) == []


def test_silent(conn, make_user):
    owner_of(conn)
    app_settings.update(conn, {"staff_digest_silent": True})
    u = make_user(first="Иван")
    n = FakeNotifier(conn)
    s = shift(conn, u)
    run(n.shift_event("SHIFT_CREATED", s["id"]))  # instant mode, but «без звука» is on
    assert len(n.sent) == 1 and n.silents[-1] is True and queued(conn) == []
    enable(conn)
    run(n.shift_event("SHIFT_CANCELLED", s["id"]))
    run(digest.flush(conn, n, now=at(TODAY, "13:00")))
    assert len(n.sent) == 2 and n.silents[-1] is True  # the digest is silent too


def test_action_cards_always_instant(conn, make_user):
    owner_of(conn)
    enable(conn)
    u = make_user(first="Иван")
    n = FakeNotifier(conn)
    s = shift(conn, u)
    run(n.shift_event("SHIFT_URGENT_REQUESTED", s["id"]))
    run(n.shift_event("SHIFT_FINAL_CANCELLED", s["id"]))
    assert len(n.sent) == 2 and queued(conn) == []  # buttons and a same-day decision — never queued


def test_personal_messages_unaffected(conn, make_user):
    owner_of(conn)
    enable(conn)
    u = make_user(first="Иван")
    n = FakeNotifier(conn)
    tx = finance.record_fine(conn, user_id=u["id"], units=2, reason="Проверка", actor_tg=999)
    run(n.tx_created(tx, svc.get_user_by_tg(conn, 999)))
    assert any(t[0] == u["telegram_id"] and "штраф" in t[1] for t in n.sent)  # to the person at once
    assert queued(conn) == []


def test_sections_order_and_split(conn):
    owner_of(conn)
    enable(conn)
    n = FakeNotifier(conn)
    digest.queue(conn, 999, "reports", "отчёт")
    digest.queue(conn, 999, "schedule", "график 1")
    digest.queue(conn, 999, "onboarding", "онбординг")
    digest.queue(conn, 999, "schedule", "график 2")
    assert run(digest.flush(conn, n, now=at(TODAY, "13:00"))) == 1
    text = n.sent[0][1]
    assert text.index("График") < text.index("Отчёты") < text.index("Онбординг")  # the fixed order
    assert text.count("• график") == 2  # the two schedule items share one section
    # a digest longer than one message continues in the next one
    for i in range(60):
        digest.queue(conn, 999, "schedule", f"длинное уведомление номер {i} " + "х" * 100)
    n.sent.clear()
    assert run(digest.flush(conn, n, now=at(TODAY, "13:00"))) == 1
    assert all(len(t) <= 4096 for _, t in n.sent) and len(n.sent) > 1


def test_unknown_category_and_stale_items(conn):
    owner_of(conn)
    enable(conn)
    digest.queue(conn, 999, "weird", "что-то редкое")
    assert queued(conn)[0]["category"] == "staff"  # falls under «Команда»
    conn.execute("UPDATE notify_queue SET created_at = ?",
                 ((svc.now_local() - timedelta(days=3)).isoformat(timespec="seconds"),))
    assert run(digest.flush(conn, FakeNotifier(conn), now=at(TODAY, "13:00"))) == 0  # stale news are dropped
    assert queued(conn) == []


def test_turning_off_drains_the_queue(conn, make_user):
    owner_of(conn)
    enable(conn)
    u = make_user(first="Иван")
    run(FakeNotifier(conn).shift_event("SHIFT_CREATED", shift(conn, u)["id"]))
    assert len(queued(conn)) == 1
    app_settings.update(conn, {"staff_digest_enabled": False})
    assert run(digest.flush(conn, FakeNotifier(conn), now=at(TODAY, "13:00"))) == 0
    assert queued(conn) == []  # the leftover queue is drained, not dumped later


def test_settings_validation(conn):
    with pytest.raises(svc.ServiceError):
        app_settings.update(conn, {"staff_digest_every_min": 45})
    with pytest.raises(svc.ServiceError):
        app_settings.update(conn, {"staff_digest_quiet_from": "25:00"})
    app_settings.update(conn, {"staff_digest_every_min": 180, "staff_digest_silent": True})
    cfg = app_settings.get_all(conn)
    assert cfg["staff_digest_every_min"] == 180 and cfg["staff_digest_silent"] is True
