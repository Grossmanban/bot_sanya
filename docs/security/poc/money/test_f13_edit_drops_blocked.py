"""F13: editing a payout recalculates without blocked people: a blocked person silently drops out of a payout
that was already sent to them; after unblocking, the same pieces are paid again."""
from app import payouts
from app import services as svc
from tests.test_lazy import commit
from tests.test_rates import WEEK, did


def test_edit_drops_blocked_person_and_pays_twice(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u, v = make_user(first="Аня"), make_user(first="Боря")
    did(conn, u, WEEK[0], 20)
    did(conn, v, WEEK[0], 30)
    b = commit(conn, owner, [u, v], WEEK[0], WEEK[0])
    assert {i["user_id"]: i["total"] for i in b["items"]} == {u["id"]: 2400, v["id"]: 3600}  # money sent to both
    svc.set_account_state(conn, owner, v["id"], "blocked", "ушёл", svc.new_key())
    # the owner corrects Аня's base in the same payout (Боря still ticked)
    payouts.edit(conn, owner, b["id"], [{"user_id": u["id"], "base_override": 2500}, {"user_id": v["id"]}],
                 svc.new_key(), "правка базы")
    b2 = payouts.batch(conn, b["id"])
    assert [i["user_id"] for i in b2["items"]] == [u["id"]]  # Боря's 3600 ₽ vanished from the registry
    svc.set_account_state(conn, owner, v["id"], "active", None, svc.new_key())
    again = commit(conn, owner, [v], WEEK[0], WEEK[0])     # no overlap any more
    assert again["items"][0]["total"] == 3600              # the same 30 pieces paid a second time
