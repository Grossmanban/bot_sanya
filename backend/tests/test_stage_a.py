"""Customer round after 12.4: owner-only rates, proof photos on money, personal norms, hidden names."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import app_settings, finance, jobs, media, violations
from app import services as svc
from app.api import create_app
from app.notify import Notifier

from .test_api import headers, key
from .test_features import _staff, at, run
from .test_violations import MON, WEEK, Recorder, one, person, worked


@pytest.fixture
def client(conn):
    return TestClient(create_app(conn, Notifier(None, conn)))


def owner_h(client):
    h = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=h)
    return h


def test_only_owners_change_rates(client, conn, make_user):
    oh = owner_h(client)
    admin = _staff(conn, make_user, "admin", "Админ")
    ah = headers(admin["telegram_id"])
    r = client.patch("/api/admin/settings", json={"rate_base": 150}, headers=ah)
    assert r.status_code == 403 and r.json()["code"] == "owner_only"
    cfg = client.get("/api/admin/settings", headers=ah).json()["settings"]
    # an admin saving the whole form with unchanged rates is fine
    assert client.patch("/api/admin/settings", json={**cfg, "eve_reminder_time": "19:00"}, headers=ah).status_code == 200
    assert client.patch("/api/admin/settings", json={"rate_base": 150}, headers=oh).json()["settings"]["rate_base"] == 150


def test_money_with_a_proof_photo(client, conn, make_user):
    oh = owner_h(client)
    u = person(conn, make_user, "Иван")
    fid = media.save_local(b"\xff\xd8\xff" + b"1" * 50, "jpg")
    r = client.post("/api/admin/finances", json={"user_id": u["id"], "type": "fine", "amount": 300, "reason": "Опоздание",
                                                  "media_file_id": fid, "idempotency_key": key()}, headers=oh)
    tx = r.json()["tx"]
    assert tx["has_media"] and tx["media_url"].startswith(f"/api/media/tx/{tx['id']}?")
    assert client.get(tx["media_url"]).status_code == 200
    # the employee sees it in their own history
    mine = client.get("/api/me/finances", headers=headers(u["telegram_id"])).json()["items"]
    assert mine[0]["media_url"] == tx["media_url"]
    # and the archive copy carries the photo
    app_settings.set_raw(conn, "archive_chat_id", "-1001")
    n = Recorder(conn)
    run(n.tx_created(finance.get(conn, tx["id"]), svc.get_user_by_tg(conn, 999)))
    assert (-1001, fid) in n.photos


def test_personal_norms(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer = _staff(conn, make_user, "trainer", "Тренер", network=1)
    a = person(conn, make_user, "Иван", network=1)
    b = person(conn, make_user, "Вера")
    c = person(conn, make_user, "Петя")
    for u in (a, b, c):
        worked(conn, u, WEEK[:2])
    conn.execute("UPDATE users SET decided_at = '2026-01-01' WHERE id = ?", (trainer["id"],))

    violations.set_norm(conn, trainer, a["id"], 4, svc.new_key())          # trainer: own network
    with pytest.raises(svc.ServiceError) as e:
        violations.set_norm(conn, trainer, b["id"], 4, svc.new_key())      # not their network
    assert e.value.status == 403
    with pytest.raises(svc.ServiceError):
        violations.set_norm(conn, owner, b["id"], 8, svc.new_key())
    violations.set_norm(conn, owner, b["id"], 0, svc.new_key())            # 0 = no norm for Вера

    # the common norm switched off: only personal norms are checked
    assert {v["user_id"] for v in violations.scan(conn, 0, MON)} == {a["id"]}
    assert one(conn, a)["required_days"] == 4
    # common norm 3 on: Петя (2 days, no personal norm) and the trainer are checked too, Вера is not
    created = violations.scan(conn, 3, MON)
    assert {v["user_id"] for v in created} == {c["id"], trainer["id"]}
    violations.set_norm(conn, owner, a["id"], None, svc.new_key())         # back to the common norm
    assert svc.get_user(conn, a["id"])["min_weekly_shifts"] is None


def test_monday_job_with_only_personal_norms(conn, make_user):
    svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    owner = svc.get_user_by_tg(conn, 999)
    a, b = person(conn, make_user, "Иван"), person(conn, make_user, "Вера")
    app_settings.update(conn, {"attendance_check_enabled": False})
    n = Recorder(conn)
    assert "attendance_check" not in run(jobs.tick(conn, n, at(MON, "10:31")))  # nobody has a norm
    violations.set_norm(conn, owner, a["id"], 2, svc.new_key())
    assert "attendance_check" in run(jobs.tick(conn, n, at(MON + timedelta(days=7), "10:31")))
    assert {r["user_id"] for r in conn.execute("SELECT user_id FROM attendance_violations")} == {a["id"]}


def test_norm_over_http(client, conn, make_user):
    oh = owner_h(client)
    u = person(conn, make_user, "Иван")
    r = client.post(f"/api/admin/team/{u['id']}/norm", json={"norm": 5, "idempotency_key": key()}, headers=oh)
    assert r.json()["user"]["min_weekly_shifts"] == 5
    assert client.post(f"/api/admin/team/{u['id']}/norm", json={"norm": 5, "idempotency_key": key()},
                       headers=headers(u["telegram_id"])).status_code == 403


def test_hidden_colleague_names_and_raspred_today(client, conn, make_user):
    oh = owner_h(client)
    a, b = person(conn, make_user, "Иван"), person(conn, make_user, "Вера")
    from app.timeutil import today_local
    today = today_local()
    for u in (a, b):
        conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status,
                        created_at, updated_at) VALUES (?, ?, '16:00', '19:00', 20, 'Европа', 'active', 't', 't')""",
                     (u["id"], today.isoformat()))
    day = lambda h: next(d for d in client.get("/api/schedule/week", headers=h).json()["days"] if d["is_today"])  # noqa: E731
    assert day(headers(a["telegram_id"]))["colleagues"][0]["name"] == "Вера Петров"
    app_settings.update(conn, {"show_colleague_names": False})
    hidden = day(headers(a["telegram_id"]))
    assert hidden["names_hidden"] and hidden["colleagues"][0]["name"] == "Коллега" and hidden["colleagues"][0]["first_name"] == ""
    assert day(oh)["colleagues"][0]["name"] != "Коллега"  # staff still see names
    r = client.get("/api/admin/raspred/today", headers=oh).json()
    assert r["date"] == today.isoformat() and r["total_count"] == 2


def test_goal_counts_history_from_its_start(conn, make_user):
    """ТЗ №18: a goal created mid-period already holds the approved reports from its first day."""
    from app import goals
    from app.timeutil import today_local

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    a, b = person(conn, make_user, "Иван"), person(conn, make_user, "Мария")
    today = today_local()
    start = today - timedelta(days=2)

    def shift(u, d, fact, state):
        conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status,
                        fact_volume, report_state, created_at, updated_at)
                        VALUES (?, ?, '10:00', '18:00', 20, 'Европа', 'active', ?, ?, 't', 't')""",
                     (u["id"], d.isoformat(), fact, state))

    shift(a, start, 50, "approved")                       # before the goal existed
    shift(b, start + timedelta(days=1), 30, "approved")   # before the goal existed
    shift(b, today, 12, "pending_review")                 # waits for review: shown apart
    shift(a, start - timedelta(days=1), 99, "approved")   # outside the period
    goal = goals.create(conn, owner, title="Неделя", kind="team", target=1000, date_from=start,
                        date_to=today + timedelta(days=4), key=svc.new_key())
    for u in (a, b):
        goals.join(conn, u, goal["id"])
    v = goals.view(conn, goals.get(conn, goal["id"]))
    assert (v["progress"], v["backfill"], v["pending"]) == (80, 80, 12)
    assert goals.counted_so_far(conn, goal, [a["id"]]) == (1, 50)

    app_settings.set_raw(conn, "archive_chat_id", "-1001")
    n = Recorder(conn)
    run(n.goal_created(goals.get(conn, goal["id"])))
    [arch] = [t for chat, t in n.sent if chat == -1001]
    assert "ЦЕЛЬ СОЗДАНА: Неделя" in arch and "Принятых отчётов за период уже: 2 (80 шт.)" in arch
