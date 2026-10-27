"""ТЗ 27.10 §20–21: «налог на лень» is a pool — lazy days add pieces, a committed payout takes min(P, L)."""

from datetime import date, timedelta

from app import payouts
from app import services as svc

from .test_api import client, headers  # noqa: F401 — the fixture
from .test_rates import WEEK, did
from .test_violations import person


def commit(conn, owner, users, d_from, d_to, k=None, **common):
    return payouts.commit(conn, owner, "Зарплата", d_from, d_to, payouts.Rates.from_dict(common or None), None,
                          [{"user_id": u["id"]} for u in users], [], [], k or svc.new_key())


def row(conn, user, d_from, d_to, **common):
    return next(r for r in payouts.calculate(conn, d_from, d_to, payouts.Rates.from_dict(common or None))
                if r["user_id"] == user["id"])


def test_pool_examples_from_the_spec(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = person(conn, make_user, "Иван")
    for i, fact in enumerate((10, 15, 19, 6)):  # four lazy days: L = 50
        did(conn, u, WEEK[i], fact)
    first = row(conn, u, WEEK[0], WEEK[3])
    # P = 50, L = 50: everything lazy
    assert (first["lazy_before"], first["lazy_units"], first["full_units"], first["lazy_after"]) == (50, 50, 0, 0)
    assert first["pieces_pay"] == 50 * 100
    # a preview takes nothing: the same numbers again
    assert row(conn, u, WEEK[0], WEEK[3])["lazy_before"] == 50

    # a good week, 100 pieces
    nxt = [WEEK[-1] + timedelta(days=i) for i in range(1, 8)]
    for d in nxt[:4]:
        did(conn, u, d, 25)
    # the next week paid first (the lazy week still unpaid): P = 100, L = 50 → 50 lazy, 50 full, the pool → 0
    r = row(conn, u, nxt[0], nxt[-1])
    assert (r["lazy_before"], r["lazy_units"], r["full_units"], r["lazy_after"]) == (50, 50, 50, 0)
    assert r["pieces_pay"] == 50 * 100 + 50 * 120
    commit(conn, owner, [u], WEEK[0], WEEK[3])  # the lazy week paid instead: it takes all 50
    r = row(conn, u, nxt[0], nxt[-1])
    assert (r["lazy_before"], r["lazy_units"], r["full_units"]) == (0, 0, 100) and r["pieces_pay"] == 100 * 120


def test_pool_bigger_than_the_pieces_waits_and_rates_are_taken_at_payout(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = person(conn, make_user, "Иван")
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(20)]
    for d in days:
        did(conn, u, d, 10)  # 20 lazy days: L = 200, approved and unpaid
    oct_ = [date(2026, 10, 1) + timedelta(days=i) for i in range(4)]
    for d in oct_:
        did(conn, u, d, 25)
    # only October is paid: P = 100, L = 200 (built up in September) → all 100 lazy, 100 stay
    r = row(conn, u, oct_[0], oct_[-1], rate_lazy=90)
    assert (r["lazy_before"], r["lazy_units"], r["full_units"], r["lazy_after"]) == (200, 100, 0, 100)
    assert r["pieces_pay"] == 100 * 90  # the lazy rate as set for this payout
    k = svc.new_key()
    batch = commit(conn, owner, [u], oct_[0], oct_[-1], k, rate_lazy=90)
    item = batch["items"][0]
    assert (item["lazy_units"], item["lazy_before"], item["rate_lazy"], item["rate_base"]) == (100, 200, 90, 120)
    assert payouts.lazy_pool(conn, u["id"], 20, date(2026, 10, 31)) == 100
    # the same click again (the same key) — nothing is written off twice
    commit(conn, owner, [u], oct_[0], oct_[-1], k, rate_lazy=90)
    assert payouts.lazy_pool(conn, u["id"], 20, date(2026, 10, 31)) == 100
    assert conn.execute("SELECT COUNT(*) FROM payout_items").fetchone()[0] == 1


def test_pool_30_of_100_and_exempt_people(conn, make_user):
    from app import rates

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u, free = person(conn, make_user, "Иван"), person(conn, make_user, "Петя")
    for i, fact in enumerate((10, 20, 15, 30, 25, 5)):  # lazy days: 10 + 15 + 5 = 30
        did(conn, u, WEEK[i], fact)
    # P = 105, L = 30 → 30 lazy, 75 full, the pool ends at 0
    r = row(conn, u, WEEK[0], WEEK[-1])
    assert (r["lazy_before"], r["lazy_units"], r["full_units"], r["lazy_after"]) == (30, 30, 75, 0)
    assert r["pieces_pay"] == 30 * 100 + 75 * 120
    # «не применять налог на лень»: no pool at all
    rates.set_user_rates(conn, owner, free["id"], {"lazy_exempt": True}, svc.new_key())
    did(conn, free, WEEK[0], 5)
    assert row(conn, free, WEEK[0], WEEK[-1])["lazy_units"] == 0


def test_payouts_from_before_the_pool_are_not_counted_again(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = person(conn, make_user, "Иван")
    did(conn, u, WEEK[0], 10)
    commit(conn, owner, [u], WEEK[0], WEEK[0])
    # pretend that payout was made before the pool existed: the lazy day was paid at 100 then
    conn.execute("UPDATE payout_items SET lazy_before = NULL, lazy_units = 0")
    did(conn, u, WEEK[1], 30)
    r = row(conn, u, WEEK[1], WEEK[1])
    assert r["lazy_before"] == 0 and r["pieces_pay"] == 30 * 120


def test_employee_sees_pieces_to_pay_and_the_pool(client, conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = person(conn, make_user, "Иван")
    did(conn, u, WEEK[0], 10)
    did(conn, u, WEEK[1], 30)
    s = client.get("/api/me/finances", headers=headers(u["telegram_id"])).json()["summary"]
    assert (s["to_pay_pieces"], s["lazy_units"]) == (40, 10)
    commit(conn, owner, [u], WEEK[0], WEEK[1])
    s = client.get("/api/me/finances", headers=headers(u["telegram_id"])).json()["summary"]
    assert (s["to_pay_pieces"], s["lazy_units"], s["paid_through"]) == (0, 0, WEEK[1].isoformat())
