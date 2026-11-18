"""18.11: «Алиса» and «Яндекс Плюс» are switched off (products.PRODUCTS_ENABLED = False): nothing is handed out,
nothing is shown, nothing is counted — and nothing else loads the bot. The data (access rows, tasks, payouts,
the product's rates) stays untouched: flipping the flag back re-opens everything as it was."""

import pytest

from app import alice, events, finance, goals, pending, payouts, products
from app import services as svc
from app.timeutil import today_local


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setattr(products, "PRODUCTS_ENABLED", False)


@pytest.fixture
def team(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Иван")
    return owner, u


def test_access_is_refused_everywhere(conn, team, off):
    owner, u = team
    assert products.of_user(conn, u["id"]) == []
    assert products.manageable(conn, owner) == []
    assert not products.has(conn, u, "alice")
    with pytest.raises(svc.ServiceError) as exc:
        products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    assert exc.value.code == "products_disabled"
    with pytest.raises(svc.ServiceError) as exc:
        alice.check("alice")
    assert exc.value.code == "products_disabled"
    with pytest.raises(svc.ServiceError) as exc:
        finance.create(conn, owner, u["id"], "fine", None, "за Алису", svc.new_key(), units=1, product="alice")
    assert exc.value.code == "products_disabled"


def test_pending_has_no_product_counts(conn, team, off):
    owner, u = team
    # a moderation task sits in the database, but with the products off the counters don't even run — zeros everywhere
    conn.execute("INSERT INTO alice_qr (image, created_at, created_by) VALUES ('x', '2026-11-01T12:00:00', 999)")
    conn.execute("""INSERT INTO alice_tasks (user_id, qr_id, query, status, started_at)
                    VALUES (?, 1, 'запрос', 'moderation', '2026-11-01T12:00:00')""", (u["id"],))
    counts = pending.counts(conn, owner, today_local())
    assert counts["alice_moderation"] == 0 and counts["alice_failed"] == 0
    assert counts["plus_moderation"] == 0 and counts["plus_failed"] == 0


def test_registry_hides_product_payouts(conn, off):
    conn.execute("""INSERT INTO payout_batches (title, period_from, period_to, rates, total, created_by, created_at)
                    VALUES ('Выплата', '2026-11-01', '2026-11-15', '{}', 0, 999, '2026-11-16T12:00:00')""")
    conn.execute("""INSERT INTO payout_batches (title, period_from, period_to, rates, total, created_by, created_at,
                    product) VALUES ('Алиса 1–15', '2026-11-01', '2026-11-15', '{}', 0, 999, '2026-11-16T12:00:00',
                    'alice')""")
    titles = [b["title"] for b in payouts.history(conn)]
    assert titles == ["Выплата"]


def test_goals_and_events_of_products_are_hidden(conn, team, off):
    owner, u = team
    conn.execute("""INSERT INTO goals (title, kind, target, date_from, date_to, status, created_by, created_at,
                    product) VALUES ('Цель Алисы', 'team', 10, '2026-11-01', '2026-11-30', 'active', 999,
                    '2026-11-01T12:00:00', 'alice')""")
    conn.execute("""INSERT INTO events (title, mode, starts_at, ends_at, status, created_by, created_at, product)
                    VALUES ('Гонка Алисы', 'accumulative', '2026-11-20T12:00:00', '2026-11-21T12:00:00', 'active',
                    999, '2026-11-01T12:00:00', 'alice')""")
    assert [g["title"] for g in goals.listing(conn, owner)] == []
    assert [e["title"] for e in events.managed(conn, owner)] == []
    assert events.visible(conn, u) == []
