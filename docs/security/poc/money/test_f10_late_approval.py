"""F10: pieces approved after the payout of their period are never paid and vanish from the employee's «к выплате»;
the lazy pool still grows from them. F11: a stale «штраф» button overrides a colleague's approval."""
import pytest

from app import payouts, reports
from app import services as svc
from app.timeutil import now_local
from tests.test_lazy import commit
from tests.test_rates import WEEK, did


def test_late_approved_pieces_are_lost(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Иван")
    did(conn, u, WEEK[0], 25)
    conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status, created_at,
                    updated_at) VALUES (?, ?, '10:00', '18:00', 20, 'Европа', 'active', 't', 't')""",
                 (u["id"], WEEK[1].isoformat()))
    late = conn.execute("SELECT id FROM shifts WHERE user_id = ? AND shift_date = ?", (u["id"], WEEK[1].isoformat())).fetchone()[0]
    b1 = commit(conn, owner, [u], WEEK[0], WEEK[1])
    assert b1["items"][0]["personal_volume"] == 25
    # the report of WEEK[1] is approved only now (needs_fix resubmitted / no-show «внести результат»)
    reports.manual_result(conn, owner, late, 30, svc.new_key(), comment="внесли за сотрудника")
    assert payouts.employee_view(conn, u["id"], now_local().date())["to_pay_pieces"] == 0   # not shown as owed
    with pytest.raises(svc.ServiceError) as e:   # a payout over that day is refused
        commit(conn, owner, [u], WEEK[1], WEEK[1])
    assert e.value.code == "overlap"
    rows = payouts.calculate(conn, WEEK[2], WEEK[6], payouts.Rates.from_dict(None))
    assert all(r["user_id"] != u["id"] for r in rows)  # and the next period doesn't pick them up either


def test_stale_fine_button_overrides_colleague_approval(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = make_user(first="Адм")
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (admin["id"],))
    admin = svc.get_user(conn, admin["id"])
    u = make_user(first="Иван")
    from tests.test_features import THU, report, shift
    s = shift(conn, u)
    report(conn, u, s, 30)
    assert reports.review(conn, owner, s["id"], "approve", svc.new_key()).changed
    # a second reviewer taps «Отклонить + штраф» on the same (now stale) bot message a moment later
    r = reports.review(conn, admin, s["id"], "fine", svc.new_key(), units=5, comment="не тот скрин")
    assert r.changed and r.shift["report_state"] == "rejected"  # no «Уже решено», the approval is silently replaced
