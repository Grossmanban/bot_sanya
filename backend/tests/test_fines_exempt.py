"""19.11: «без штрафов» — the per-person toggle in «Команда» (`users.fines_exempt`). The automatic fines don't
apply to this person at all: the discipline checks (weekly norm, unmarked day) don't create violations, the
broadcast read-deadline fine is skipped, and a no-show shift is excused on the spot — no review card, nothing
for the staff to waive day after day. Turning the toggle on forgives what already waits for a decision. Manual
fines (the staff's explicit decision) keep working."""

from datetime import timedelta

from app import app_settings, broadcasts, day_actual, jobs, reports, violations
from app import services as svc

from .test_broadcast_ack import deliver, expire, owner_of
from .test_features import TODAY, FakeNotifier, THU, at, report, run, shift
from .test_violations import MON, WEEK, one, person, worked


def exempt(conn, user, on=True):
    conn.execute("UPDATE users SET fines_exempt = ? WHERE id = ?", (int(on), user["id"]))


def test_scans_skip_exempt_but_reminders_stay(conn, make_user):
    owner_of(conn)
    app_settings.update(conn, {"weekly_attendance_quota": 2})
    ivan, vera = person(conn, make_user, "Иван"), person(conn, make_user, "Вера")
    for u in (ivan, vera):
        worked(conn, u, WEEK[:1])  # 1 day of 2 — both under the norm
    exempt(conn, ivan)
    # the weekly check: only vera gets the violation
    created = violations.scan(conn, 2, MON)
    assert [c["user_id"] for c in created] == [vera["id"]]
    assert one(conn, ivan) is None and one(conn, vera)["status"] == "pending"
    # the reminder list «отметься на завтра» keeps the exempt person — reminders are not fines
    assert {u["id"] for u in violations.unmarked(conn, MON)} == {ivan["id"], vera["id"]}
    # but the unmarked-day violation is not created for ivan
    assert [c["user_id"] for c in violations.scan_unmarked(conn, MON)] == [vera["id"]]


def test_broadcast_ack_fine_skips_exempt(conn, make_user):
    owner = owner_of(conn)
    ivan, vera = make_user(first="Иван"), make_user(first="Вера")
    exempt(conn, ivan)
    row, _ = svc.create_broadcast(conn, owner, [], "Важно: собрание сегодня", user_ids=[ivan["id"], vera["id"]],
                                  ack_deadline_min=30, ack_fine_units=2)
    deliver(conn, row["id"], ivan)
    deliver(conn, row["id"], vera)
    expire(conn, row["id"])
    n = FakeNotifier(conn)
    assert run(broadcasts.enforce_deadlines(conn, n)) == [{"broadcast": row["id"], "fined": 1, "skipped": 1}]
    # ivan is not fined and his delivery stays unfined (the broadcast is done — no re-fine later)
    assert conn.execute("SELECT COUNT(*) FROM financial_transactions WHERE user_id = ?", (ivan["id"],)).fetchone()[0] == 0
    tx = conn.execute("SELECT * FROM financial_transactions WHERE user_id = ?", (vera["id"],)).fetchone()
    assert tx["source"] == "broadcast" and tx["pieces"] == 2 and tx["status"] == "active"
    assert conn.execute("SELECT fined_at FROM broadcast_deliveries WHERE broadcast_id = ? AND user_id = ?",
                        (row["id"], ivan["id"])).fetchone()[0] is None
    assert conn.execute("SELECT ack_fines_done FROM broadcasts WHERE id = ?", (row["id"],)).fetchone()[0] == 1
    # the author's summary mentions the skipped ones
    summary = [t for chat, t in n.sent if chat == owner["telegram_id"] and "Срок отметки" in t][0]
    assert "Пропущены без штрафов" in summary


def test_no_show_auto_excused(conn, make_user):
    owner_of(conn)
    app_settings.set_raw(conn, "reports_since", TODAY.isoformat())
    u = make_user()
    exempt(conn, u)
    s = shift(conn, u)
    other = shift(conn, make_user(first="Пётр"))
    n = FakeNotifier(conn)
    run(jobs.tick(conn, n, at(THU + timedelta(days=1), "00:01")))
    s2 = svc.get_shift(conn, s["id"])
    assert s2["report_state"] == "no_show" and s2["shortfall_status"] == "waived"
    assert "Автоматически" in (s2["report_note"] or "")
    # the review queue has only the other person's shift — no card for the exempt one, no fine for anyone
    assert [q["shift_id"] for q in reports.queue(conn, "no_show")] == [other["id"]]
    assert conn.execute("SELECT COUNT(*) FROM financial_transactions").fetchone()[0] == 0


def test_toggle_forgives_backlog_http(conn, make_user):
    from fastapi.testclient import TestClient

    from app.api import create_app
    from app.notify import Notifier

    from .test_api import headers, key

    owner_of(conn)
    app_settings.update(conn, {"weekly_attendance_quota": 2})
    u = person(conn, make_user, "Иван")
    worked(conn, u, WEEK[:1])
    v = violations.scan(conn, 2, MON)[0]  # a pending violation of the weekly norm
    s = shift(conn, u)
    reports.mark_no_show(conn, s["id"], at(THU + timedelta(days=2), "00:01"))  # a no-show waiting for a decision
    assert svc.get_shift(conn, s["id"])["shortfall_status"] is None

    client = TestClient(create_app(conn, Notifier(None, conn)))
    owner_h = headers(999, "Саня", "Владелец", "boss")
    emp_h = headers(u["telegram_id"])
    assert client.post(f"/api/admin/team/{u['id']}/rates", json={"fines_exempt": True, "idempotency_key": key()},
                       headers=emp_h).status_code == 403
    r = client.post(f"/api/admin/team/{u['id']}/rates", json={"fines_exempt": True, "idempotency_key": key()},
                    headers=owner_h)
    assert r.status_code == 200
    assert r.json()["user"]["fines_exempt"] is True
    assert r.json()["cleared"] == {"violations": 1, "no_shows": 1}  # the backlog is forgiven with one switch
    assert one(conn, u)["status"] == "waived" and "Освобождён от штрафов" in one(conn, u)["resolution_comment"]
    assert svc.get_shift(conn, s["id"])["shortfall_status"] == "waived"
    # from now on the checks create nothing for the person
    assert violations.scan(conn, 2, MON) == []
    assert violations.scan_unmarked(conn, MON) == []
    # the team list shows the flag (the toggle reads it back)
    team = client.get("/api/admin/team", headers=owner_h).json()
    assert [i["fines_exempt"] for i in team["items"] if i["id"] == u["id"]] == [True]
    # off: just the flag, nothing left to forgive
    r2 = client.post(f"/api/admin/team/{u['id']}/rates", json={"fines_exempt": False, "idempotency_key": key()},
                     headers=owner_h)
    assert r2.status_code == 200 and r2.json()["user"]["fines_exempt"] is False and "cleared" not in r2.json()
    assert [i["fines_exempt"] for i in team["items"] if i["id"] == u["id"]] == [True]  # stale listing, unchanged


def test_manual_fine_still_works(conn, make_user):
    owner = owner_of(conn)
    u = make_user()
    exempt(conn, u)
    s = shift(conn, u)
    report(conn, u, s, 20)  # fact < plan: the fine is the reviewer's explicit decision
    day_actual.set_actual(conn, owner, THU, 10, svc.new_key(), today=THU + timedelta(days=2))
    fined = reports.review(conn, owner, s["id"], "fine", svc.new_key(), units=5, comment="Не выполнена норма")
    assert fined.shift["shortfall_status"] == "fined"
    tx = conn.execute("SELECT * FROM financial_transactions WHERE user_id = ?", (u["id"],)).fetchone()
    assert tx["source"] == "report" and tx["status"] == "active" and tx["pieces"] == 5
