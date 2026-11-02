"""F2: an admin re-sets the penalty of a (legacy) finally-cancelled shift: an already settled fine comes back."""
from app import finance, payouts
from app import services as svc
from tests.test_features import shift
from tests.test_lazy import commit
from tests.test_rates import WEEK, did


def _admin(conn, make_user):
    a = make_user(first="Адм")
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (a["id"],))
    return svc.get_user(conn, a["id"])


def test_offset_fine_is_reactivated_and_taken_twice(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = _admin(conn, make_user)
    u = make_user(first="Иван")
    s = shift(conn, u)
    conn.execute("UPDATE shifts SET status = 'finally_cancelled' WHERE id = ?", (s["id"],))  # legacy row
    svc.set_penalty(conn, s["id"], 500, 999)
    [fine] = finance.linked(conn, s["id"], ("final_cancel",))
    did(conn, u, WEEK[4], 20)  # 20 x 120 = 2400
    b1 = commit(conn, owner, [u], WEEK[4], WEEK[4])
    assert b1["items"][0]["fines"] == 500 and b1["items"][0]["total"] == 1900
    assert finance.get(conn, fine["id"])["status"] == "offset"

    # the admin (not an owner) "corrects" the penalty: the settled fine is active again
    svc.admin_edit_shift(conn, admin, s["id"], {"penalty_amount": 600, "reason": "уточнение"}, svc.new_key())
    tx = finance.get(conn, fine["id"])
    assert tx["status"] == "active" and tx["amount"] == 600 and tx["payout_id"] == b1["id"]
    did(conn, u, WEEK[5], 20)
    b2 = commit(conn, owner, [u], WEEK[5], WEEK[5])
    assert b2["items"][0]["fines"] == 600  # the same fine deducted a second time (500 + 600 in total)


def test_offset_fine_is_cancelled_by_zero_and_lost_on_annul(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = _admin(conn, make_user)
    u = make_user(first="Иван")
    s = shift(conn, u)
    conn.execute("UPDATE shifts SET status = 'finally_cancelled' WHERE id = ?", (s["id"],))
    svc.set_penalty(conn, s["id"], 500, 999)
    [fine] = finance.linked(conn, s["id"], ("final_cancel",))
    did(conn, u, WEEK[4], 20)
    b1 = commit(conn, owner, [u], WEEK[4], WEEK[4])
    svc.admin_edit_shift(conn, admin, s["id"], {"penalty_amount": 0, "reason": "без штрафа"}, svc.new_key())
    assert finance.get(conn, fine["id"])["status"] == "cancelled"  # a settled fine, cancelled by an admin
    payouts.annul(conn, owner, b1["id"], "ошибка в реестре", False, svc.new_key())
    # after the annulment the 500 should be owed again - it is gone
    assert finance.get(conn, fine["id"])["status"] == "cancelled"
    assert finance.balance(conn, u["id"])["fines"] == 0
