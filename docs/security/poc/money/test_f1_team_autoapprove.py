"""F1: after the owner enters a met global actual, any later/returned report is approved with ANY fact."""
from datetime import timedelta

from app import day_actual, payouts, reports
from app import services as svc
from tests.conftest import requisites
from tests.test_features import P, THU, at, report, shift


def test_inflated_fact_auto_approved_after_team_met(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    a, b, c = make_user(first="Аня"), make_user(first="Боря"), make_user(first="Вика")
    sa = shift(conn, a, payload={**P, "volume": 20})
    sb = shift(conn, b, payload={**P, "volume": 20})
    sc = shift(conn, c, payload={**P, "volume": 20})
    report(conn, a, sa, 30)
    report(conn, c, sc, 5)
    reports.review(conn, owner, sc["id"], "return", svc.new_key(), comment="переснимите скрин")  # needs_fix
    # owner enters the real team result in the evening: 60 >= plan 60
    day_actual.set_actual(conn, owner, THU, 60, svc.new_key(), today=THU)
    # b (not reported yet, deadline = midnight) sends an absurd fact: approved at once, no reviewer
    r = reports.submit(conn, b, sb["id"], 10_000, "any-photo", "app", svc.new_key(), now=at(THU, "23:30"))
    assert r.report["status"] == "approved_team" and r.shift["report_state"] == "approved"
    # c's returned report has no deadline: 10 days later, also any number, approved at once
    r2 = reports.submit(conn, c, sc["id"], 10_000, "any-photo", "app", svc.new_key(),
                        now=at(THU + timedelta(days=10), "12:00"))
    assert r2.shift["report_state"] == "approved" and r2.shift["fact_volume"] == 10_000
    # the payout pays it: 10 000 pieces x 120 rub each
    requisites(conn, a, b, c)
    rows = {r["user_id"]: r for r in payouts.calculate(conn, THU, THU, payouts.Rates.from_dict(None))}
    assert rows[b["id"]]["personal_volume"] == 10_000 and rows[b["id"]]["total"] == 1_200_000
    assert rows[c["id"]]["total"] == 1_200_000
    # sum of approved facts (20 030) is far above the actual the owner entered (60) - nothing compares them
    assert sum(r["personal_volume"] for r in rows.values()) == 20_030
