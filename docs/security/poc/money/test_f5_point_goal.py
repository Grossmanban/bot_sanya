"""F5: a point goal is closed for the day by a fact still under review; rejecting the report later changes nothing."""
from datetime import date

from app import goals, reports
from app import services as svc
from tests.test_features import THU, TODAY, at, report, shift


def test_unreviewed_inflated_fact_completes_point_goal(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    svc.create_point(conn, owner, "Европа", None)
    g = goals.create(conn, owner, title="Европа 100", kind="point", target=100, date_from=TODAY,
                     date_to=date.fromisoformat(goals.FOREVER), location="Европа", prize="премия 1000 ₽", key=svc.new_key())
    a, b = make_user(first="Аня"), make_user(first="Боря")
    sa, sb = shift(conn, a), shift(conn, b)
    report(conn, b, sb, 5)
    report(conn, a, sa, 10_000)  # a lie, waits for review
    done = goals.check_completions(conn, THU, at(THU, "19:40"))
    assert [(x["title"], len(p)) for x, p in done] == [("Европа 100", 2)]  # everyone told: norm met, prize
    # the reviewer catches the lie and fines it
    reports.review(conn, owner, sa["id"], "fine", svc.new_key(), units=5, comment="скрин не тот")
    v = goals.view(conn, g, b, at(THU, "20:00"))
    assert v["progress"] == 5 and v["done"] is True  # «выполнено» stays for the day, no way to take it back
