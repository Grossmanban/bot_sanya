"""Customer round B: personal/network rates, the trainer's ladder, deductions in pieces, owner's earnings."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import app_settings, finance, networks, payouts, rates
from app import services as svc
from app.api import create_app
from app.notify import Notifier

from .test_api import headers, key
from .test_violations import person

MON = date(2026, 10, 12)
WEEK = [MON + timedelta(days=i) for i in range(7)]


def did(conn, user, day, fact, plan=20):
    conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status, fact_volume,
                    report_state, created_at, updated_at) VALUES (?, ?, '10:00', '18:00', ?, 'Европа', 'active', ?,
                    'approved', 't', 't')""", (user["id"], day.isoformat(), plan, fact))


@pytest.fixture
def team(conn, make_user):
    """Owner, a network «Север» with trainer Вера and employees Иван and Петя; Маша without a network."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    north = networks.create(conn, owner, "Север", svc.new_key())
    vera, ivan, petya = person(conn, make_user, "Вера"), person(conn, make_user, "Иван"), person(conn, make_user, "Петя")
    masha = person(conn, make_user, "Маша")
    networks.set_trainer(conn, owner, north["id"], vera["id"], svc.new_key())
    for u in (ivan, petya):
        networks.set_member(conn, owner, u["id"], north["id"], svc.new_key())
    get = lambda u: svc.get_user(conn, u["id"])  # noqa: E731
    return {"owner": owner, "north": north, "vera": get(vera), "ivan": get(ivan), "petya": get(petya), "masha": get(masha)}


def calc(conn, d_from=WEEK[0], d_to=WEEK[-1], **common):
    rows = payouts.calculate(conn, d_from, d_to, payouts.Rates.from_dict(common or None))
    return {r["name"].split()[0]: r for r in rows}, rows


def test_rate_hierarchy(conn, team):
    owner = team["owner"]
    networks.set_rates(conn, owner, team["north"]["id"], {"rate_base": 130}, svc.new_key())
    rates.set_user_rates(conn, owner, team["petya"]["id"], {"personal_rate": 150}, svc.new_key())
    book = rates.Book.load(conn)
    assert book.day_rate(team["masha"]["id"], 25) == 120      # common
    assert book.day_rate(team["ivan"]["id"], 25) == 130       # network
    assert book.day_rate(team["petya"]["id"], 25) == 150      # personal
    assert book.day_rate(team["petya"]["id"], 10) == 100      # «налог на лень» applies to personal rates too
    rates.set_user_rates(conn, owner, team["petya"]["id"], {"lazy_exempt": True}, svc.new_key())
    assert rates.Book.load(conn).day_rate(team["petya"]["id"], 10) == 150
    did(conn, team["petya"], WEEK[0], 10)
    did(conn, team["petya"], WEEK[1], 30)
    by, _ = calc(conn)
    assert by["Петя"]["pieces_pay"] == 40 * 150
    # admins can't touch rates
    admin = svc.get_user(conn, svc.get_user(conn, team["masha"]["id"])["id"])
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (admin["id"],))
    with pytest.raises(svc.ServiceError) as e:
        rates.set_user_rates(conn, svc.get_user(conn, admin["id"]), team["ivan"]["id"], {"personal_rate": 1}, svc.new_key())
    assert e.value.code == "owner_only"


def test_daily_ladder_pays_every_piece_at_the_step(conn, team):
    owner, north = team["owner"], team["north"]["id"]
    networks.set_rates(conn, owner, north, {"ladder": [{"from": 0, "rate": 10}, {"from": 250, "rate": 20}]}, svc.new_key())
    did(conn, team["ivan"], WEEK[0], 60)
    did(conn, team["petya"], WEEK[0], 40)       # Monday: team 100 → all at 10 ₽
    did(conn, team["ivan"], WEEK[1], 150)
    did(conn, team["petya"], WEEK[1], 100)      # Tuesday: team 250 → all at 20 ₽
    did(conn, team["vera"], WEEK[1], 30)        # the trainer's own pieces don't count by default
    by, _ = calc(conn)
    vera = by["Вера"]
    assert vera["team_volume"] == 350 and vera["commission"] == 100 * 10 + 250 * 20
    assert vera["pieces_pay"] == 30 * 120 and vera["base_calc"] == 3600 + 6000
    assert [(d["date"], d["rate"]) for d in vera["commission_days"]] == [(WEEK[0].isoformat(), 10), (WEEK[1].isoformat(), 20)]
    # the toggle counts the trainer too: Tuesday becomes 280, Monday stays 100
    networks.set_rates(conn, owner, north, {"ladder_counts_trainer": True}, svc.new_key())
    by, _ = calc(conn)
    assert by["Вера"]["commission"] == 100 * 10 + 280 * 20
    # a ladder whose first step starts above zero pays nothing below it
    networks.set_rates(conn, owner, north, {"ladder": [{"from": 200, "rate": 15}], "ladder_counts_trainer": False},
                       svc.new_key())
    by, _ = calc(conn)
    assert by["Вера"]["commission"] == 250 * 15


def test_weekly_ladder_and_a_cut_period(conn, team):
    owner, north = team["owner"], team["north"]["id"]
    networks.set_rates(conn, owner, north, {"ladder": [{"from": 0, "rate": 10}, {"from": 300, "rate": 20}],
                                            "ladder_period": "week"}, svc.new_key())
    for d in WEEK[:5]:
        did(conn, team["ivan"], d, 70)          # 350 a week → the 20 ₽ step for the whole week
    by, _ = calc(conn)
    assert by["Вера"]["commission"] == 350 * 20
    # a payout for Mon–Wed pays only those days, but at the week's step
    by, _ = calc(conn, WEEK[0], WEEK[2])
    assert by["Вера"]["commission"] == 210 * 20


def test_no_ladder_keeps_the_common_commission(conn, team):
    did(conn, team["ivan"], WEEK[0], 40)
    by, _ = calc(conn)
    assert by["Вера"]["commission"] == 40 * 10


def test_deduction_in_pieces_hits_the_trainer_at_that_days_rate(conn, team):
    owner, north = team["owner"], team["north"]["id"]
    networks.set_rates(conn, owner, north, {"ladder": [{"from": 0, "rate": 10}, {"from": 250, "rate": 20}]}, svc.new_key())
    did(conn, team["ivan"], WEEK[0], 25)                              # Monday: team 25 → 10 ₽
    did(conn, team["ivan"], WEEK[1], 150)
    did(conn, team["petya"], WEEK[1], 100)                            # Tuesday: team 250 → 20 ₽
    rates.set_user_rates(conn, owner, team["ivan"]["id"], {"personal_rate": 140}, svc.new_key())

    own, trainer = finance.piece_deduction(conn, owner, team["ivan"]["id"], WEEK[0], 5, "Брак", svc.new_key())
    assert (own["amount"], own["rate"], own["pieces"]) == (5 * 140, 140, 5)
    assert (trainer["user_id"], trainer["amount"], trainer["rate"], trainer["linked_tx_id"]) == \
        (team["vera"]["id"], 50, 10, own["id"])
    assert "5 шт. × 140 ₽" in own["reason"] and "Вычет у Иван" in trainer["reason"]
    # Tuesday was paid at the 20 ₽ step: the deduction takes 20 ₽ a piece and doesn't move the step
    _, t2 = finance.piece_deduction(conn, owner, team["ivan"]["id"], WEEK[1], 5, "Брак", svc.new_key())
    assert t2["amount"] == 100
    by, _ = calc(conn)
    assert by["Вера"]["commission"] == 25 * 10 + 250 * 20             # unchanged
    assert by["Вера"]["deductions"] == 150 and by["Иван"]["deductions"] == 5 * 140 * 2

    # annulling the employee's deduction annuls the trainer's part
    finance.cancel(conn, owner, own["id"], "Ошибка", svc.new_key())
    assert finance.get(conn, trainer["id"])["status"] == "cancelled"
    # never in the future; the trainer's own deduction has no trainer part
    with pytest.raises(svc.ServiceError):
        finance.piece_deduction(conn, owner, team["ivan"]["id"], date.today() + timedelta(days=400), 1, "x", svc.new_key())
    assert len(finance.piece_deduction(conn, owner, team["vera"]["id"], WEEK[0], 2, "Брак", svc.new_key())) == 1


def test_owner_led_networks_and_owner_earnings(conn, team, make_user):
    owner = team["owner"]
    south = networks.create(conn, owner, "Юг", svc.new_key())
    east = networks.create(conn, owner, "Восток", svc.new_key())
    networks.set_trainer(conn, owner, south["id"], owner["id"], svc.new_key())
    networks.set_trainer(conn, owner, east["id"], owner["id"], svc.new_key())   # an owner may lead several
    assert networks.get(conn, south["id"])["trainer_id"] == owner["id"] == networks.get(conn, east["id"])["trainer_id"]
    assert svc.get_user_by_tg(conn, 999)["role"] != "trainer"
    networks.set_member(conn, owner, team["masha"]["id"], south["id"], svc.new_key())
    did(conn, team["masha"], WEEK[0], 30)
    did(conn, team["ivan"], WEEK[0], 40)
    did(conn, owner, WEEK[0], 10)                                 # the owner's own pieces: revenue, not pay
    app_settings.update(conn, {"revenue_per_piece": 200})
    by, rows = calc(conn)
    assert "Саня" not in by                                       # owners are not in the payout
    assert by["Маша"]["commission"] == 0 and by["Вера"]["commission"] == 40 * 10
    s = payouts.summary(conn, rows, WEEK[0], WEEK[-1])
    assert s["pieces"] == 80 and s["revenue"] == 16_000
    paid = 30 * 120 + 40 * 120 + 40 * 10
    assert (s["paid"], s["owner"]) == (paid, 16_000 - paid)
    # a trainer still leads only one network
    networks.set_trainer(conn, owner, south["id"], team["vera"]["id"], svc.new_key())
    assert networks.get(conn, team["north"]["id"])["trainer_id"] is None


def test_http_rates_are_owner_only(conn, team):
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh = headers(999, "Саня", "Владелец", "boss")
    admin = svc.get_user(conn, team["masha"]["id"])
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (admin["id"],))
    ah = headers(admin["telegram_id"])
    north = team["north"]["id"]

    assert "revenue_per_piece" not in client.get("/api/admin/settings", headers=ah).json()["settings"]
    assert client.patch("/api/admin/settings", json={"revenue_per_piece": 300}, headers=ah).status_code == 403
    assert client.patch("/api/admin/settings", json={"revenue_per_piece": 300}, headers=oh).json()["settings"]["revenue_per_piece"] == 300

    body = {"ladder": [{"from": 0, "rate": 10}, {"from": 250, "rate": 20}], "ladder_period": "week", "idempotency_key": key()}
    assert client.patch(f"/api/admin/networks/{north}", json=body, headers=ah).status_code == 403
    net = client.patch(f"/api/admin/networks/{north}", json=body, headers=oh).json()["network"]
    assert net["ladder_period"] == "week"
    listed = next(n for n in client.get("/api/admin/networks", headers=oh).json()["items"] if n["id"] == north)
    assert listed["ladder"] == [{"from": 0, "rate": 10}, {"from": 250, "rate": 20}]
    assert "ladder" not in next(n for n in client.get("/api/admin/networks", headers=ah).json()["items"] if n["id"] == north)
    bad = client.patch(f"/api/admin/networks/{north}", json={"ladder": [{"from": 5, "rate": 1}, {"from": 5, "rate": 2}],
                                                              "idempotency_key": key()}, headers=oh)
    assert bad.status_code == 422

    ivan = team["ivan"]
    r = client.post(f"/api/admin/team/{ivan['id']}/rates", json={"personal_rate": 150, "lazy_exempt": True,
                                                                  "idempotency_key": key()}, headers=oh)
    assert r.json()["user"]["personal_rate"] == 150 and r.json()["user"]["lazy_exempt"] is True
    assert client.post(f"/api/admin/team/{ivan['id']}/rates", json={"personal_rate": 1, "idempotency_key": key()},
                       headers=ah).status_code == 403
    me = client.get("/api/me", headers=headers(ivan["telegram_id"])).json()
    assert me["pricing"]["base_rate"] == 150 and me["pricing"]["lazy_threshold"] == 0
    team_rows = client.get("/api/admin/team", headers=ah).json()["items"]
    assert all("personal_rate" not in u for u in team_rows)

    did(conn, ivan, WEEK[0], 25)
    r = client.post("/api/admin/finances/pieces", json={"user_id": ivan["id"], "day": WEEK[0].isoformat(), "pieces": 3,
                                                          "reason": "Брак", "idempotency_key": key()}, headers=oh)
    items = r.json()["items"]
    assert [i["amount"] for i in items] == [450, 30] and items[0]["pieces"] == 3
    assert client.post("/api/admin/finances/pieces", json={"user_id": ivan["id"], "day": WEEK[0].isoformat(), "pieces": 3,
                                                            "reason": "x", "idempotency_key": key()}, headers=ah).status_code == 403

    prev = client.post("/api/admin/payouts/preview", json={"date_from": WEEK[0].isoformat(), "date_to": WEEK[-1].isoformat(),
                                                            "network_id": None, "rates": {}}, headers=oh).json()
    assert prev["summary"]["revenue"] == 25 * 300 and prev["summary"]["owner"] == 7500 - prev["summary"]["paid"]


def test_migration_to_16_keeps_networks(tmp_path):
    import sqlite3

    from app import db

    old = sqlite3.connect(tmp_path / "old.db", isolation_level=None)
    old.row_factory = sqlite3.Row
    old.executescript(db.SCHEMA)
    saved = db.MIGRATIONS[:]
    try:
        db.MIGRATIONS[:] = saved[:15]
        db.migrate(old)
        old.execute("INSERT INTO users (telegram_id, first_name, last_name, status, created_at) VALUES (1,'a','b','approved','x')")
        old.execute("INSERT INTO networks (name, trainer_id, created_by, created_at) VALUES ('Север', 1, 1, 't')")
        db.MIGRATIONS[:] = saved
        db.migrate(old)
    finally:
        db.MIGRATIONS[:] = saved
    row = old.execute("SELECT rate_base, ladder, ladder_period, ladder_counts_trainer, trainer_id FROM networks").fetchone()
    assert tuple(row) == (None, None, "day", 0, 1)
    assert old.execute("SELECT personal_rate, lazy_exempt FROM users").fetchone()[1] == 0
    # the one-network-per-trainer index is gone: an owner may lead several networks
    old.execute("INSERT INTO networks (name, trainer_id, created_by, created_at) VALUES ('Юг', 1, 1, 't')")
    assert old.execute("PRAGMA foreign_key_check").fetchall() == []
