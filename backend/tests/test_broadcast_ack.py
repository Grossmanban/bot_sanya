"""19.11: a broadcast may set the time one has to press «✅ Я ознакомился». Whoever got the message but didn't
mark themselves in time gets the fine in the units the author chose — automatically, active at once, one pass
per broadcast (a restart never fines twice). A reply counts as read; people who never received the message and
blocked accounts are not fined."""

from datetime import timedelta

import pytest

from app import broadcasts, finance, rates
from app import services as svc
from app.timeutil import now_local

from .test_features import FakeNotifier, run


def owner_of(conn):
    return svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")


def deliver(conn, broadcast_id: int, user: dict, delivered: bool = True, read: bool = False) -> None:
    conn.execute(
        "INSERT INTO broadcast_deliveries (broadcast_id, user_id, delivered, sent_at, read_at) VALUES (?, ?, ?, ?, ?)",
        (broadcast_id, user["id"], int(delivered), svc.now_iso(), svc.now_iso() if read else None))


def expire(conn, broadcast_id: int, minutes: int = -30) -> None:
    """The sender normally fixes the deadline when the sending finishes; tests put it where they need it."""
    at = svc.now_local() + timedelta(minutes=minutes)
    conn.execute("UPDATE broadcasts SET ack_deadline_at = ? WHERE id = ?", (at.isoformat(timespec="seconds"), broadcast_id))


def test_deadline_validation(conn, make_user):
    owner = owner_of(conn)
    u = make_user(first="Иван")
    with pytest.raises(svc.ServiceError):
        svc.create_broadcast(conn, owner, [], "Хай", user_ids=[u["id"]], ack_deadline_min=3, ack_fine_units=1)
    with pytest.raises(svc.ServiceError):
        svc.create_broadcast(conn, owner, [], "Хай", user_ids=[u["id"]], ack_deadline_min=60 * 24 * 30 + 1)
    with pytest.raises(svc.ServiceError):
        svc.create_broadcast(conn, owner, [], "Хай", user_ids=[u["id"]], ack_deadline_min=60, ack_fine_units=0)
    with pytest.raises(svc.ServiceError):
        svc.create_broadcast(conn, owner, [], "Хай", user_ids=[u["id"]], ack_deadline_min=60, ack_fine_units="2")
    row, _ = svc.create_broadcast(conn, owner, [], "Собрание", user_ids=[u["id"]], ack_deadline_min=60, ack_fine_units=2)
    assert row["require_ack"] == 1 and row["ack_deadline_min"] == 60 and row["ack_fine_units"] == 2
    # a deadline without a fine is allowed: the count is kept, nobody is fined
    row, _ = svc.create_broadcast(conn, owner, [], "Ещё раз", user_ids=[u["id"]], ack_deadline_min=30)
    assert row["require_ack"] == 1 and row["ack_fine_units"] is None


def test_unread_get_fined_once(conn, make_user):
    owner = owner_of(conn)
    ivan, vera, petya = make_user(first="Иван"), make_user(first="Вера"), make_user(first="Петя")
    row, _ = svc.create_broadcast(conn, owner, [], "Важно: собрание сегодня", user_ids=[ivan["id"], vera["id"], petya["id"]],
                                  ack_deadline_min=30, ack_fine_units=3)
    deliver(conn, row["id"], ivan)                       # got it, didn't mark — fined
    deliver(conn, row["id"], vera, read=True)            # got it and marked — not fined
    deliver(conn, row["id"], petya, delivered=False)     # Telegram didn't take it — not fined
    expire(conn, row["id"])
    n = FakeNotifier(conn)
    assert run(broadcasts.enforce_deadlines(conn, n)) == [{"broadcast": row["id"], "fined": 1}]
    tx = dict(conn.execute("SELECT * FROM financial_transactions WHERE user_id = ?", (ivan["id"],)).fetchone())
    rate = rates.for_user(conn, ivan["id"])["rate_base"]
    assert tx["status"] == "active" and tx["source"] == "broadcast" and tx["created_by"] == 999
    assert tx["pieces"] == 3 and tx["rate"] == rate and tx["amount"] == 3 * rate
    assert "Не подтвердил(а) прочтение рассылки" in tx["reason"]
    assert conn.execute("SELECT fined_at FROM broadcast_deliveries WHERE broadcast_id = ? AND user_id = ?",
                        (row["id"], ivan["id"])).fetchone()[0] is not None
    assert conn.execute("SELECT ack_fines_done FROM broadcasts WHERE id = ?", (row["id"],)).fetchone()[0] == 1
    # the person was messaged («💸 Вам назначен штраф…»), the author got the summary
    assert any(t[0] == ivan["telegram_id"] and "штраф" in t[1] for t in n.sent)
    assert any(t[0] == owner["telegram_id"] and "Срок отметки" in t[1] and "Иван" in t[1] for t in n.sent)
    # a second pass (restart, next tick) doesn't see the broadcast any more — nothing is fined again
    assert run(broadcasts.enforce_deadlines(conn, n)) == []
    assert conn.execute("SELECT COUNT(*) FROM financial_transactions WHERE user_id = ?", (ivan["id"],)).fetchone()[0] == 1


def test_blocked_are_skipped(conn, make_user):
    owner = owner_of(conn)
    ivan = make_user(first="Иван")
    row, _ = svc.create_broadcast(conn, owner, [], "Всем", user_ids=[ivan["id"]], ack_deadline_min=30, ack_fine_units=1)
    conn.execute("UPDATE users SET account_state = 'blocked' WHERE id = ?", (ivan["id"],))  # blocked after the send
    deliver(conn, row["id"], ivan)
    expire(conn, row["id"])
    assert run(broadcasts.enforce_deadlines(conn, FakeNotifier(conn))) == [{"broadcast": row["id"], "fined": 0}]
    assert conn.execute("SELECT COUNT(*) FROM financial_transactions").fetchone()[0] == 0


def test_reply_counts_as_read(conn, make_user):
    owner = owner_of(conn)
    ivan = make_user(first="Иван")
    row, _ = svc.create_broadcast(conn, owner, [], "Отвечайте", user_ids=[ivan["id"]], ack_deadline_min=30,
                                  ack_fine_units=2, allow_reply=True)
    deliver(conn, row["id"], ivan)
    broadcasts.add_reply(conn, row["id"], ivan, "Ок, понял", [])  # the answer sets read_at (a sync service call)
    expire(conn, row["id"])
    assert run(broadcasts.enforce_deadlines(conn, FakeNotifier(conn))) == [{"broadcast": row["id"], "fined": 0}]
    assert conn.execute("SELECT COUNT(*) FROM financial_transactions").fetchone()[0] == 0


def test_future_deadline_waits(conn, make_user):
    owner = owner_of(conn)
    ivan = make_user(first="Иван")
    row, _ = svc.create_broadcast(conn, owner, [], "Скоро", user_ids=[ivan["id"]], ack_deadline_min=60, ack_fine_units=1)
    deliver(conn, row["id"], ivan)
    expire(conn, row["id"], minutes=30)  # still ahead
    assert run(broadcasts.enforce_deadlines(conn, FakeNotifier(conn))) == []
    assert conn.execute("SELECT ack_fines_done FROM broadcasts WHERE id = ?", (row["id"],)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM financial_transactions").fetchone()[0] == 0
    # a deadline without a fine never fines anyone
    row, _ = svc.create_broadcast(conn, owner, [], "Без штрафа", user_ids=[ivan["id"]], ack_deadline_min=30)
    deliver(conn, row["id"], ivan)
    expire(conn, row["id"])
    assert run(broadcasts.enforce_deadlines(conn, FakeNotifier(conn))) == []
    assert conn.execute("SELECT COUNT(*) FROM financial_transactions").fetchone()[0] == 0
