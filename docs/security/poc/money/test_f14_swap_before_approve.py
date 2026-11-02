"""F14: the employee replaces a pending report (allowed until review); the reviewer's old bot message
«факт 20» + screenshot still carries rv:a:<shift_id>, which approves whatever the report is now."""
from app import reports
from app import services as svc
from tests.test_features import THU, at, shift


def test_old_approve_button_approves_the_swapped_fact(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Иван")
    s = shift(conn, u)
    first = reports.submit(conn, u, s["id"], 20, "photo-honest", "app", svc.new_key(), now=at(THU, "19:10"))
    # reviewers get «План 25, факт 20» with the honest screenshot and buttons bound to the shift only
    swapped = reports.submit(conn, u, s["id"], 9_999, "photo-other", "app", svc.new_key(), now=at(THU, "19:11"))
    assert swapped.report["id"] == first.report["id"]  # the same attempt, overwritten in place
    res = reports.review(conn, owner, s["id"], "approve", svc.new_key())  # the tap on the first message
    assert res.changed and res.shift["fact_volume"] == 9_999 and res.shift["report_state"] == "approved"
