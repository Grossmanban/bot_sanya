"""ТЗ 12.4: weekly norm of work days, violations, «штраф» vs «оправдание»."""

import json
import sqlite3
from datetime import date, timedelta

import pytest

from app import app_settings, finance, jobs, violations
from app import services as svc

from .test_features import FakeNotifier, _staff, at, run

MON = date(2026, 10, 19)  # the check runs on this Monday for 12.10–18.10
WEEK = [date(2026, 10, 12) + timedelta(days=i) for i in range(7)]


class Recorder(FakeNotifier):
    def __init__(self, conn):
        super().__init__(conn)
        self.albums: list[tuple[int, list[str]]] = []

    async def send_album(self, chat_id, files):
        if files:
            self.albums.append((chat_id, list(files)))
        return len(files)


def person(conn, make_user, first, network=None, role=None):
    u = make_user(first=first)
    conn.execute("UPDATE users SET decided_at = '2026-01-01T10:00:00', network_id = ?, role = COALESCE(?, role) "
                 "WHERE id = ?", (network, role, u["id"]))
    return svc.get_user(conn, u["id"])


def worked(conn, user, days, status="active", report_state="approved"):
    for d in days:
        conn.execute(
            """INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status, report_state,
                                   created_at, updated_at) VALUES (?, ?, '16:00', '19:00', 20, 'Европа', ?, ?, 't', 't')""",
            (user["id"], d.isoformat(), status, report_state))


def one(conn, user) -> dict:
    return conn.execute("SELECT * FROM attendance_violations WHERE user_id = ?", (user["id"],)).fetchone()


def test_scan_counts_real_work_days_and_skips_who_it_should(conn, make_user):
    svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    short = person(conn, make_user, "Иван")
    worked(conn, short, WEEK[:1])
    worked(conn, short, [WEEK[1]], status="restored", report_state=None)  # restored, report not in yet: counts
    worked(conn, short, [WEEK[2]], report_state="no_show")        # a no-show is not a work day
    worked(conn, short, [WEEK[3]], status="cancelled")            # neither is a cancelled shift
    fine_person = person(conn, make_user, "Вера")
    worked(conn, fine_person, WEEK[4:7])
    paused = person(conn, make_user, "Пауза")
    conn.execute("UPDATE users SET account_state = 'paused' WHERE id = ?", (paused["id"],))
    newbie = make_user(first="Новичок")                           # approved during that week
    conn.execute("UPDATE users SET decided_at = '2026-10-14T10:00:00' WHERE id = ?", (newbie["id"],))
    _staff(conn, make_user, "admin", "Админ")                     # admins have no norm
    trainer = person(conn, make_user, "Тренер", role="trainer")   # trainers do

    created = violations.scan(conn, 3, MON)
    assert {v["user_id"] for v in created} == {short["id"], trainer["id"]}
    v = one(conn, short)
    assert (v["week_start_date"], v["week_end_date"], v["required_days"], v["actual_days"], v["status"]) == \
        ("2026-10-12", "2026-10-18", 3, 2, "pending")
    assert json.loads(v["absent_dates"]) == [d.isoformat() for d in WEEK[2:]]
    assert violations.scan(conn, 3, MON) == []                    # one violation per person and week
    assert violations.scan(conn, 0, MON + timedelta(days=7)) == []  # no norm — no check
    pub = violations.public(conn, dict(v))
    assert pub["week_number"] == 42 and pub["default_comment"] == \
        "Невыполнение плана выходов за период 12.10–18.10. Вышли 2 из 3 дней."


def test_fine_and_waive_are_final_and_validated(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = _staff(conn, make_user, "admin", "Админ")
    a, b = person(conn, make_user, "Иван"), person(conn, make_user, "Вера")
    worked(conn, a, WEEK[:1])
    violations.scan(conn, 3, MON)
    va, vb = one(conn, a)["id"], one(conn, b)["id"]

    for bad, code in (({"units": 0}, "bad_units"), ({"comment": "  "}, "comment_required"),
                      ({"files": ["f"] * 6}, "too_many_files")):
        args = {"units": 4, "comment": "Неявка", "files": [], **bad}
        with pytest.raises(svc.ServiceError) as e:
            violations.fine(conn, admin, va, args["units"], args["comment"], args["files"], svc.new_key())
        assert e.value.code == code

    k = svc.new_key()
    res = violations.fine(conn, admin, va, 4, "Неявка без предупреждения во вторник.", ["p1", "p2"], k)
    assert res.changed and res.violation["status"] == "penalized"
    tx = res.tx
    assert (tx["type"], tx["status"], tx["source"], tx["amount"], tx["event_date"]) == \
        ("fine", "active", "attendance", 480, "2026-10-18")  # 4 units × 120 ₽
    assert res.violation["linked_fine_transaction_id"] == tx["id"] and res.violation["resolution_admin_id"] == admin["id"]
    assert violations.media_ids(res.violation) == ["p1", "p2"]
    assert finance.balance(conn, a["id"])["balance"] == -480
    assert not violations.fine(conn, admin, va, 4, "x", [], k).changed  # the same click again
    with pytest.raises(svc.ServiceError) as e:                              # a second decision
        violations.waive(conn, owner, va, "Передумали", [], svc.new_key())
    assert (e.value.status, e.value.code) == (409, "already_resolved")

    res = violations.waive(conn, owner, vb, "Больничный лист предоставлен", ["doc"], svc.new_key())
    assert res.violation["status"] == "waived" and res.violation["linked_fine_transaction_id"] is None
    assert finance.balance(conn, b["id"])["balance"] == 0 and finance.journal(conn, user_id=b["id"]) == []
    with pytest.raises(svc.ServiceError) as e:
        violations.fine(conn, owner, vb, 4, "Всё-таки штраф", [], svc.new_key())
    assert e.value.code == "already_resolved"
    assert [v["id"] for v in violations.listing(conn, owner, "pending")] == []
    assert {v["id"] for v in violations.listing(conn, owner, "resolved")} == {va, vb}


def test_trainer_scope_and_owner_approval(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer = _staff(conn, make_user, "trainer", "Тренер", network=1)
    a, b = person(conn, make_user, "Иван", network=1), person(conn, make_user, "Вера", network=1)
    stranger = person(conn, make_user, "Чужой")
    employee = person(conn, make_user, "Петя", network=1)
    worked(conn, employee, WEEK[:3])
    conn.execute("UPDATE users SET decided_at = '2026-01-01' WHERE id = ?", (trainer["id"],))
    violations.scan(conn, 3, MON)
    va, vb, vs = one(conn, a)["id"], one(conn, b)["id"], one(conn, stranger)["id"]

    assert {v["user_id"] for v in violations.listing(conn, trainer)} == {a["id"], b["id"]}  # not self, not others
    assert violations.pending_count(conn, trainer) == 2 and violations.pending_count(conn, owner) == 4
    for actor, vid in ((trainer, vs), (employee, va)):
        with pytest.raises(svc.ServiceError) as e:
            violations.waive(conn, actor, vid, "x", [], svc.new_key())
        assert e.value.status == 403

    n = Recorder(conn)
    res = violations.fine(conn, trainer, va, 700, "Пропуск", [], svc.new_key())
    assert res.tx["status"] == "pending_admin_approval" and finance.balance(conn, a["id"])["balance"] == 0
    run(n.violation_resolved(res.violation, res.tx, trainer))
    assert any("Заявка на штраф" in t for chat, t in n.sent if chat == 999)
    assert not any(chat == a["telegram_id"] for chat, _ in n.sent)  # the employee waits for the owner

    finance.decide(conn, owner, res.tx["id"], False, "Нет доказательств", svc.new_key())
    back = violations.get(conn, va)
    assert back["status"] == "pending" and back["linked_fine_transaction_id"] is None
    violations.waive(conn, trainer, va, "Согласовано с руководителем", [], svc.new_key())

    res = violations.fine(conn, trainer, vb, 4, "Пропуск", ["proof"], svc.new_key())
    ok = finance.decide(conn, owner, res.tx["id"], True, None, svc.new_key())
    n.sent.clear()
    run(n.tx_decided(ok.tx, owner))
    [text] = [t for chat, t in n.sent if chat == b["telegram_id"]]
    assert "ШТРАФ НАЗНАЧЕН" in text and "4 штрафные единицы" in text and "₽" not in text
    assert (b["telegram_id"], ["proof"]) in n.albums
    assert finance.balance(conn, b["id"])["balance"] == -480


def test_messages_to_employee_and_archive(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    a = person(conn, make_user, "Иван")
    b = person(conn, make_user, "Вера")
    worked(conn, a, WEEK[:1])
    violations.scan(conn, 3, MON)
    app_settings.set_raw(conn, "archive_chat_id", "-1001")
    n = Recorder(conn)

    res = violations.fine(conn, owner, one(conn, a)["id"], 4, "Неявка во вторник.", ["s1", "s2"], svc.new_key())
    run(n.violation_resolved(res.violation, res.tx, owner))
    [msg] = [t for chat, t in n.sent if chat == a["telegram_id"]]
    for part in ("НАРУШЕНИЕ ГРАФИКА | ШТРАФ НАЗНАЧЕН", "Неделя 42 (12.10 – 18.10)", "Требование: 3 дня",
                 "Факт: 1 день", "Пропущено: 2 дня", "4 штрафные единицы", "«Неявка во вторник.»", "2 файла — ниже"):
        assert part in msg, part
    [arch] = [t for chat, t in n.sent if chat == -1001]
    for part in ("#нарушение #штраф", "ШТРАФ ЗА ПРОПУСК ГРАФИКА", "Назначил: Владелец", "1/3", "МСК"):
        assert part in arch, part
    assert n.albums == [(a["telegram_id"], ["s1", "s2"]), (-1001, ["s1", "s2"])]

    n.sent.clear()
    n.albums.clear()
    res = violations.waive(conn, owner, one(conn, b)["id"], "  ", [], svc.new_key())  # «Простить»: no reason is fine
    run(n.violation_resolved(res.violation, None, owner))
    [msg] = [t for chat, t in n.sent if chat == b["telegram_id"]]
    assert "штраф не начислен" in msg and "Комментарий" not in msg
    [arch] = [t for chat, t in n.sent if chat == -1001]
    assert "Основание: без указания причины" in arch
    conn.execute("""UPDATE attendance_violations SET status = 'pending', resolution_admin_id = NULL, resolution_comment = NULL,
                    resolved_at = NULL WHERE id = ?""", (res.violation["id"],))
    n.sent.clear()
    res = violations.waive(conn, owner, one(conn, b)["id"], "Больничный лист.", [], svc.new_key())
    run(n.violation_resolved(res.violation, None, owner))
    [msg] = [t for chat, t in n.sent if chat == b["telegram_id"]]
    assert "СНЯТО АДМИНИСТРАЦИЕЙ" in msg and "Факт: 0 дней" in msg and "Документы: нет" in msg
    [arch] = [t for chat, t in n.sent if chat == -1001]
    assert "#оправдание" in arch and "НАРУШЕНИЕ ПОГАШЕНО (БЕЗ ШТРАФА)" in arch and "Основание: Больничный лист." in arch
    assert n.albums == []


def test_monday_job(conn, make_user):
    svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    a = person(conn, make_user, "Иван")
    n = Recorder(conn)
    assert "attendance_check" not in run(jobs.tick(conn, n, at(MON, "10:00")))
    assert "attendance_check" in run(jobs.tick(conn, n, at(MON, "10:31")))
    assert one(conn, a)["status"] == "pending"
    assert any("Нарушения графика" in t and "Иван" in t for chat, t in n.sent if chat == 999)
    assert "attendance_check" not in run(jobs.tick(conn, n, at(MON, "10:45")))  # once a day
    app_settings.update(conn, {"weekly_attendance_quota": 0})
    assert "attendance_check" not in run(jobs.tick(conn, n, at(MON + timedelta(days=7), "10:31")))
    with pytest.raises(svc.ServiceError):
        app_settings.update(conn, {"weekly_attendance_quota": 8})


def test_migration_keeps_the_ledger(tmp_path):
    from app import db

    old = sqlite3.connect(tmp_path / "old.db", isolation_level=None)
    old.row_factory = sqlite3.Row
    old.executescript(db.SCHEMA)
    saved = db.MIGRATIONS[:]
    try:
        db.MIGRATIONS[:] = saved[:13]
        db.migrate(old)
        old.execute("INSERT INTO users (telegram_id, first_name, last_name, status, created_at) VALUES (1,'a','b','approved','x')")
        for i in range(3):
            old.execute("INSERT INTO financial_transactions (user_id, type, amount, status, reason, source, event_date, "
                        "created_by, created_at) VALUES (1, 'fine', ?, 'active', 'r', 'manual', '2026-10-01', 999, 't')",
                        (100 * (i + 1),))
        old.execute("DELETE FROM financial_transactions WHERE amount = 300")  # a gap in ids must survive
        db.MIGRATIONS[:] = saved
        db.migrate(old)
    finally:
        db.MIGRATIONS[:] = saved
    assert old.execute("PRAGMA user_version").fetchone()[0] == len(saved)
    assert [tuple(r) for r in old.execute("SELECT id, amount FROM financial_transactions")] == [(1, 100), (2, 200)]
    old.execute("INSERT INTO financial_transactions (user_id, type, amount, status, reason, source, event_date, "
                "created_by, created_at) VALUES (1, 'fine', 50, 'active', 'r', 'attendance', '2026-10-18', 999, 't')")
    assert old.execute("SELECT max(id) FROM financial_transactions").fetchone()[0] == 4  # AUTOINCREMENT kept
    assert old.execute("PRAGMA foreign_key_check").fetchall() == []
    assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_http_endpoints(conn, make_user):
    from fastapi.testclient import TestClient

    from app import media
    from app.api import create_app
    from app.notify import Notifier

    from .test_api import headers, key

    client = TestClient(create_app(conn, Notifier(None, conn)))
    owner_h = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=owner_h)  # the owner from config is created on first visit
    a, b = person(conn, make_user, "Иван"), person(conn, make_user, "Вера")
    emp_h = headers(a["telegram_id"])
    app_settings.update(conn, {"weekly_attendance_quota": 2})
    assert client.post("/api/admin/violations/scan", headers=emp_h).status_code == 403
    assert client.get("/api/admin/violations", headers=emp_h).status_code == 403

    r = client.post("/api/admin/violations/scan", headers=owner_h)
    assert r.json()["created"] == 2  # the week before today: nobody worked
    assert client.post("/api/admin/violations/scan", headers=owner_h).json()["created"] == 0
    listing = client.get("/api/admin/violations", headers=owner_h).json()
    assert listing["pending"] == 2 and listing["quota"] == 2 and len(listing["items"]) == 2
    assert client.get("/api/admin/reports/counts", headers=owner_h).json()["violations"] == 2
    va, vb = one(conn, a)["id"], one(conn, b)["id"]

    file_id = media.save_local(b"\xff\xd8\xff" + b"0" * 100, "jpg")
    body = {"units": 4, "comment": "Неявка", "media_files": [file_id], "idempotency_key": key()}
    assert client.post(f"/api/admin/violations/{va}/fine", json={**body, "comment": ""}, headers=owner_h).status_code == 422
    r = client.post(f"/api/admin/violations/{va}/fine", json=body, headers=owner_h)
    assert r.status_code == 200 and r.json()["violation"]["status"] == "penalized"
    assert r.json()["violation"]["fine"]["amount"] == 480
    again = client.post(f"/api/admin/violations/{va}/fine", json={**body, "idempotency_key": key()}, headers=owner_h)
    assert again.status_code == 409 and again.json()["code"] == "already_resolved"
    photo = r.json()["violation"]["media"][0]
    assert client.get(photo).status_code == 200
    assert client.get(photo.replace("/0?", "/1?")).status_code == 403  # each proof has its own signature

    w = {"comment": "Больничный", "media_files": [], "idempotency_key": key()}
    assert client.post(f"/api/admin/violations/{vb}/waive", json=w, headers=emp_h).status_code == 403
    r = client.post(f"/api/admin/violations/{vb}/waive", json=w, headers=owner_h)
    assert r.json()["violation"]["status"] == "waived" and r.json()["violation"]["fine"] is None
    done = client.get("/api/admin/violations?status=resolved", headers=owner_h).json()
    assert done["pending"] == 0 and {i["status"] for i in done["items"]} == {"penalized", "waived"}


def test_unmarked_tomorrow_reminder_then_violation(conn, make_user):
    """The customer's module 1: mark tomorrow by 22:00 — a reminder at 20:00, a violation for the silent at 22:00."""
    from app.timeutil import now_local

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    going, off, silent, resting = (person(conn, make_user, n) for n in ("Аня", "Боря", "Вика", "Гоша"))
    resting = svc.set_work_status(conn, resting, "not_working", svc.new_key())  # «Не работаю» isn't checked
    today = now_local().date()
    tomorrow = today + timedelta(days=1)
    conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status, created_at, updated_at)
                    VALUES (?, ?, '10:00', '16:00', 20, 'Европа', 'active', 't', 't')""", (going["id"], tomorrow.isoformat()))
    svc.pre_declare_off(conn, off, tomorrow, svc.new_key(), today=today)
    assert [u["first_name"] for u in violations.unmarked(conn, tomorrow)] == ["Вика"]

    n = FakeNotifier(conn)
    ran = run(jobs.tick(conn, n, at(today, "20:00")))
    assert "daily_mark_reminder" in ran
    asks = [chat for chat, t in n.sent if "Отметь график на завтра" in t]
    assert asks == [silent["telegram_id"]]
    run(jobs.tick(conn, n, at(today, "22:00")))
    pending = violations.listing(conn, owner)
    assert [(v["name"].split()[0], v["kind"]) for v in pending] == [("Вика", "unmarked")]
    assert any("Не отметили график" in t for chat, t in n.sent if chat == 999)
    run(jobs.tick(conn, n, at(today, "22:01")))  # once a day
    assert len(violations.listing(conn, owner)) == 1
    # ТЗ 27.10 §17: the suggested fine — 3 units at the person's own rate (120 ₽ common → 360 ₽)
    assert (pending[0]["suggested_units"], pending[0]["suggested_amount"]) == (3, 360)
    # decided like any violation: «простить»
    res = violations.waive(conn, owner, pending[0]["id"], "Предупредила тренера заранее", [], svc.new_key())
    assert res.changed and res.violation["status"] == "waived"
    assert "не отмечен график" in violations.what(res.violation)
    from app.notify import violation_text
    text = violation_text(res.violation, svc.get_user(conn, silent["id"]), None)
    assert "не отмечен график" in text and "Требование" not in text
