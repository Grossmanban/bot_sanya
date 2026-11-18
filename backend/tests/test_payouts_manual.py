"""18.11: ручные суммы в мастере выплат — штуки из ставки, комиссии с включённых, полный зачёт денег,
копилка налога на лень платится по ставке лени с переносом остатка, сброс копилок и список для банка."""

from datetime import date, timedelta

import pytest

from app import finance, networks, payouts
from app import permissions as perms
from app import services as svc
from app.api import create_app
from app.notify import Notifier
from app.timeutil import now_local

from .conftest import requisites
from .test_api import headers, key
from .test_rates import WEEK, did, person  # noqa: F401 — person re-exported for the fixtures below
from .test_tz2810_access import Recorder

FUTURE = WEEK[-1] + timedelta(days=30)


def world(conn, make_user):
    """Owner, the network «Север»: trainer Вера, employees Иван и Петя (both with requisites)."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    north = networks.create(conn, owner, "Север", svc.new_key())
    vera = person(conn, make_user, "Вера", network=north["id"], role="trainer")
    ivan = person(conn, make_user, "Иван", network=north["id"])
    petya = person(conn, make_user, "Петя", network=north["id"])
    networks.set_trainer(conn, owner, north["id"], vera["id"], svc.new_key())
    requisites(conn, vera, ivan, petya)
    return owner, north, vera, ivan, petya


def rows_of(conn, d_from, d_to, amounts, included, **common):
    return {r["user_id"]: r for r in payouts.calculate(conn, d_from, d_to, payouts.Rates.from_dict(common or None),
                                                       None, amounts, included)}


# ---------------------------------------------------------------- sums → units → commissions


def test_the_amount_is_the_pay_and_the_units_feed_the_trainer(conn, make_user):
    owner, north, vera, ivan, petya = world(conn, make_user)
    did(conn, ivan, WEEK[0], 10)  # a lazy day (below 20): 10 pieces into the pool
    rows = rows_of(conn, WEEK[0], WEEK[-1], {ivan["id"]: 1200}, {ivan["id"], vera["id"]})
    # 1200 ₽ at the common 120 → 10 units; the pool has 10 — all 10 pay at the lazy 100, so 1000 ₽
    assert rows[ivan["id"]]["entered_amount"] == 1200 and rows[ivan["id"]]["entered_units"] == 10
    assert rows[ivan["id"]]["pieces_pay"] == 1000 and rows[ivan["id"]]["lazy_units"] == 10
    assert rows[ivan["id"]]["auto_pay"] == 10 * 100
    assert rows[ivan["id"]]["plan_volume"] == 20  # the plan of the day (did() defaults to 20)
    # the trainer's commission: 10 units × min(trainer_commission=10, delta=30) — she did nothing herself
    vera_row = rows[vera["id"]]
    assert vera_row["team_volume"] == 10 and vera_row["commission"] == 100
    assert vera_row["commission_detail"] == {"volume": 10, "step_rate": 10, "rate": 10, "amount": 100}
    # a personal rate changes the units: 1200 ₽ at 200 → 6 units; the tax comes off the entered rubles
    from app import rates

    rates.set_user_rates(conn, owner, ivan["id"], {"personal_rate": 200}, svc.new_key())
    rows = rows_of(conn, WEEK[0], WEEK[-1], {ivan["id"]: 1200}, {ivan["id"], vera["id"]})
    assert rows[ivan["id"]]["entered_units"] == 6 and rows[vera["id"]]["team_volume"] == 6
    assert rows[ivan["id"]]["pieces_pay"] == 1200 - 6 * (200 - 100)  # the lazy rate of his personal ladder


def test_commissions_count_only_the_included_people(conn, make_user):
    owner, north, vera, ivan, petya = world(conn, make_user)
    rows = rows_of(conn, WEEK[0], WEEK[-1], {ivan["id"]: 1200, petya["id"]: 2400}, {ivan["id"], vera["id"]})
    # Пётр unticked: his 20 units never reach the trainer
    assert rows[vera["id"]]["team_volume"] == 10 and rows[vera["id"]]["commission"] == 100
    both = rows_of(conn, WEEK[0], WEEK[-1], {ivan["id"]: 1200, petya["id"]: 2400}, {ivan["id"], petya["id"], vera["id"]})
    assert both[vera["id"]]["team_volume"] == 30 and both[vera["id"]]["commission"] == 300


def test_a_trainer_with_zero_of_his_own_lives_on_the_team(conn, make_user):
    owner, north, vera, ivan, petya = world(conn, make_user)
    # her amount 0 — the team's money still pays her; the wizard allows the sum 0 for a trainer
    batch = payouts.commit(conn, owner, "Зарплата", WEEK[0], WEEK[-1], payouts.Rates.from_dict(None), north["id"],
                           [{"user_id": vera["id"], "amount": 0}, {"user_id": ivan["id"], "amount": 1200}], [], [],
                           svc.new_key())
    item = next(i for i in batch["items"] if i["user_id"] == vera["id"])
    assert item["entered_amount"] == 0 and item["entered_units"] == 0
    assert item["commission"] == 100 and item["total"] == 100
    assert next(i for i in batch["items"] if i["user_id"] == ivan["id"])["total"] == 1200


def test_a_ticked_person_without_a_sum_is_refused(conn, make_user):
    owner, north, vera, ivan, petya = world(conn, make_user)
    with pytest.raises(svc.ServiceError) as e:
        payouts.commit(conn, owner, "Зарплата", WEEK[0], WEEK[-1], payouts.Rates.from_dict(None), north["id"],
                       [{"user_id": petya["id"], "amount": None}], [], [], svc.new_key())
    assert e.value.code == "amount_required" and "Петя" in e.value.message


# ---------------------------------------------------------------- the full settling


def test_every_open_money_settles_regardless_of_date(conn, make_user):
    owner, north, vera, ivan, petya = world(conn, make_user)
    bonus = finance.create(conn, owner, ivan["id"], "bonus", 300, "Премия", svc.new_key(),
                           event_date=FUTURE).tx  # dated after the period — still settles (18.11)
    fine = finance.create(conn, owner, ivan["id"], "fine", None, "Прогул", svc.new_key(),
                          event_date=WEEK[0], units=2).tx  # 2 units × 120 = 240
    deduction = finance.create(conn, owner, ivan["id"], "deduction", 150, "Брак", svc.new_key()).tx
    rows = rows_of(conn, WEEK[0], WEEK[-1], {ivan["id"]: 1000}, {ivan["id"]})
    r = rows[ivan["id"]]
    # 1000 + 300 − 150 − 240 = 910; nothing hangs after the payout
    assert (r["bonuses"], r["deductions"], r["fines"]) == (300, 150, 240) and r["total"] == 910
    payouts.commit(conn, owner, "Зарплата", WEEK[0], WEEK[-1], payouts.Rates.from_dict(None), north["id"],
                   [{"user_id": ivan["id"], "amount": 1000}], [], [], svc.new_key())
    assert all(finance.get(conn, t["id"])["status"] == "offset" for t in (bonus, fine, deduction))


def test_a_fine_bigger_than_the_payout_eats_it_all_and_keeps_the_debt(conn, make_user):
    owner, north, vera, ivan, petya = world(conn, make_user)
    fine = finance.create(conn, owner, ivan["id"], "fine", None, "Крупный штраф", svc.new_key(), units=42).tx
    assert fine["amount"] == 42 * 120  # 5040 ₽ — bigger than any payout below
    rows = rows_of(conn, WEEK[0], WEEK[-1], {ivan["id"]: 1200}, {ivan["id"]})
    r = rows[ivan["id"]]
    assert r["total"] == 0 and r["carried_debt"] == 5040 - 1200
    payouts.commit(conn, owner, "Зарплата", WEEK[0], WEEK[-1], payouts.Rates.from_dict(None), north["id"],
                   [{"user_id": ivan["id"], "amount": 1200}], [], [], svc.new_key())
    left = finance.get(conn, fine["id"])
    assert left["status"] == "active" and left["amount"] == 3840  # what didn't fit stays, smaller
    # the next payout eats into the remainder again
    rows = rows_of(conn, FUTURE, FUTURE, {ivan["id"]: 2000}, {ivan["id"]})
    assert rows[ivan["id"]]["fines"] == 2000 and rows[ivan["id"]]["total"] == 0
    payouts.commit(conn, owner, "Зарплата 2", FUTURE, FUTURE, payouts.Rates.from_dict(None), north["id"],
                   [{"user_id": ivan["id"], "amount": 2000}], [], [], svc.new_key())
    rows = rows_of(conn, FUTURE + timedelta(days=1), FUTURE + timedelta(days=1), {ivan["id"]: 5000}, {ivan["id"]})
    assert rows[ivan["id"]]["fines"] == 1840 and rows[ivan["id"]]["total"] == 3160


def test_the_lazy_pool_pays_at_the_lazy_rate_and_carries_over(conn, make_user):
    """18.11: the entered amount is the units; their first min(units, L) pay at the lazy rate, the tax comes
    off the pay. Only what actually fit is written off — the rest waits for the next payout."""
    owner, north, vera, ivan, petya = world(conn, make_user)
    for i, fact in enumerate((10, 15, 5)):
        did(conn, ivan, WEEK[i], fact)
    assert payouts.lazy_pool(conn, ivan["id"], 20, WEEK[-1]) == 30
    rows = rows_of(conn, WEEK[0], WEEK[-1], {ivan["id"]: 1200}, {ivan["id"]})
    r = rows[ivan["id"]]
    # 1200 ₽ → 10 units; the pool has 30 — all 10 pay at the lazy 100 ₽: 1000 ₽
    assert r["lazy_units"] == 10 and r["full_units"] == 0 and r["pieces_pay"] == 1000
    payouts.commit(conn, owner, "Зарплата", WEEK[0], WEEK[-1], payouts.Rates.from_dict(None), north["id"],
                   [{"user_id": ivan["id"], "amount": 1200}], [], [], svc.new_key())
    assert payouts.lazy_pool(conn, ivan["id"], 20, FUTURE) == 20  # 10 of 30 consumed, the rest carried
    # the next payout eats the rest: 2400 ₽ → 20 units → all 20 lazy → 2000 ₽, and the pool is empty
    rows = rows_of(conn, FUTURE, FUTURE, {ivan["id"]: 2400}, {ivan["id"]})
    assert rows[ivan["id"]]["lazy_units"] == 20 and rows[ivan["id"]]["pieces_pay"] == 2000
    payouts.commit(conn, owner, "Зарплата 2", FUTURE, FUTURE, payouts.Rates.from_dict(None), north["id"],
                   [{"user_id": ivan["id"], "amount": 2400}], [], [], svc.new_key())
    assert payouts.lazy_pool(conn, ivan["id"], 20, FUTURE + timedelta(days=30)) == 0


# ---------------------------------------------------------------- the pools reset


def test_resetting_the_pools_wipes_the_past_for_everyone(conn, make_user):
    owner, north, vera, ivan, petya = world(conn, make_user)
    free = person(conn, make_user, "Фря", network=north["id"])
    from app import rates

    rates.set_user_rates(conn, owner, free["id"], {"lazy_exempt": True}, svc.new_key())
    from app import rates

    free_threshold = rates.for_user(conn, free["id"])["lazy_threshold"]
    for u in (ivan, free):
        did(conn, u, WEEK[0], 10)
    assert payouts.lazy_pool(conn, ivan["id"], 20, FUTURE) == 10
    assert payouts.lazy_pool(conn, free["id"], free_threshold, FUTURE) == 0  # exempt — never accumulates

    client = Http(conn)
    r = client.post("/api/admin/lazy-pool/reset", vera["telegram_id"])  # a trainer can't
    assert r.status_code == 403
    r = client.post("/api/admin/lazy-pool/reset", 999)
    assert r.status_code == 200 and date.fromisoformat(r.json()["since"]) == now_local().date()
    # the past is wiped for everyone; nothing accrues until new lazy days happen
    assert payouts.lazy_pool(conn, ivan["id"], 20, FUTURE) == 0
    tomorrow = now_local().date() + timedelta(days=1)
    did(conn, ivan, tomorrow, 7)
    assert payouts.lazy_pool(conn, ivan["id"], 20, tomorrow + timedelta(days=30)) == 7
    did(conn, free, tomorrow, 7)
    assert payouts.lazy_pool(conn, free["id"], free_threshold, tomorrow + timedelta(days=30)) == 0  # still exempt


def test_the_team_shows_each_persons_pool(conn, make_user):
    """The owner watches the pool in «Команда», next to bonuses, fines and deductions; the staff doesn't."""
    owner, north, vera, ivan, petya = world(conn, make_user)
    did(conn, ivan, WEEK[0], 10)  # a lazy day — 10 pieces into the pool
    client = Http(conn)
    r = client.inner.get("/api/admin/team", headers=headers(owner["telegram_id"]))
    assert r.status_code == 200
    items = {u["id"]: u for u in r.json()["items"]}
    assert items[ivan["id"]]["lazy_pool"] == 10 and items[petya["id"]]["lazy_pool"] == 0
    r = client.inner.get("/api/admin/team", headers=headers(vera["telegram_id"]))
    assert r.status_code == 200 and all("lazy_pool" not in u for u in r.json()["items"])


# ---------------------------------------------------------------- the bank list


def test_bank_text_puts_every_field_on_its_own_line(conn, make_user):
    owner, north, vera, ivan, petya = world(conn, make_user)
    payouts.commit(conn, owner, "Зарплата", WEEK[0], WEEK[-1], payouts.Rates.from_dict(None), north["id"],
                   [{"user_id": ivan["id"], "amount": 1200}, {"user_id": petya["id"], "amount": 800}], [], [],
                   svc.new_key())
    b = payouts.history(conn)[0]
    text = payouts.bank_text(payouts.batch(conn, b["id"]))
    # the phone and the amount are separate lines: a long tap copies one field, never «phone — сумма»
    assert "+7 900 000-00-00" in text.splitlines() and "1200 ₽" in text.splitlines()
    assert "— +7" not in text and "₽ —" not in text
    assert "🏦 Сбербанк — 2 чел. — 2000 ₽" in text.splitlines()
    assert text.strip().endswith("Всего к выплате: 2000 ₽")


class Http:
    """A tiny TestClient stand-in for one-off posts."""

    def __init__(self, conn):
        from fastapi.testclient import TestClient

        self.inner = TestClient(create_app(conn, Notifier(Recorder(), conn)))

    def post(self, path, tg_id):
        return self.inner.post(path, json={"idempotency_key": key()}, headers=headers(tg_id))

    def post_raw(self, path, tg_id, data: bytes, content_type="image/png"):
        return self.inner.post(path, content=data, headers={**headers(tg_id), "Content-Type": content_type})
