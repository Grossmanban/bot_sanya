"""ТЗ 29.10: «День» — сегодня и завтра, «Оштрафовать» без двойных штрафов (§5–6), надпись над ролью (§7),
кнопка «Приложение» (§8)."""

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from app import app_settings, finance, networks, pending, violations
from app import services as svc
from app.api import create_app
from app.config import settings
from app.notify import Notifier
from app.timeutil import today_local

from .test_api import headers, key

OWNER = (999, "Саня", "Владелец", "boss")


def setup(conn, make_user):
    owner = svc.ensure_admin(conn, *OWNER)
    net = networks.create(conn, owner, "Север", svc.new_key())
    trainer, mine = make_user(first="Вера"), make_user(first="Свой")
    for p in (trainer, mine):
        networks.set_member(conn, owner, p["id"], net["id"], svc.new_key())
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    return owner, svc.get_user(conn, trainer["id"]), svc.get_user(conn, mine["id"])


def fines_of(conn, user_id):
    return conn.execute("SELECT * FROM financial_transactions WHERE user_id = ? AND source = 'attendance'", (user_id,)).fetchall()


def body(user_id, k=None, units=3):
    return {"user_id": user_id, "units": units, "comment": "Не отметил день", "idempotency_key": k or key()}


# ---------------------------------------------------------------- §5: today and tomorrow, nobody lost


def test_today_keeps_people_the_22_check_already_gave_a_violation(conn, make_user):
    owner, trainer, mine = setup(conn, make_user)
    today = today_local()
    violations.scan_unmarked(conn, today)  # what the 22:00 check did yesterday evening
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh = headers(*OWNER)
    client.get("/api/me", headers=oh)
    day = client.get(f"/api/admin/day?day={today}", headers=oh).json()
    row = next(p for p in day["unmarked"] if p["user_id"] == mine["id"])
    assert row["violation"]["status"] == "pending" and row["violation"]["state"] == "нарушение ждёт решения"
    assert day["today"] == today.isoformat() and day["tomorrow"] == (today + timedelta(days=1)).isoformat()
    assert day["can_mark"] is True and day["can_fine"] is True
    # and a manager can still mark today: the violation stays in «Дисциплина» (customer 29.10)
    r = client.post(f"/api/admin/day/{today}/mark", json={"user_id": mine["id"], "kind": "off", "idempotency_key": key()},
                    headers=oh)
    assert r.status_code == 200
    assert violations.unmarked_violation(conn, mine["id"], today)["status"] == "pending"
    left = client.get(f"/api/admin/day?day={today}", headers=oh).json()["unmarked"]
    assert mine["id"] not in {p["user_id"] for p in left}


def test_a_declined_urgent_request_no_longer_blocks_marking(conn, make_user):
    owner, _, mine = setup(conn, make_user)
    today = today_local()
    s = svc.create_shift(conn, mine, today, {"start_time": "10:00", "end_time": "18:00", "volume": 20, "location": "Европа"},
                         svc.new_key(), today=today).shift
    svc.decide_urgent(conn, s["id"], False, 999)
    assert mine["id"] in {u["id"] for u in violations.unmarked(conn, today)}
    r = svc.admin_mark_day(conn, owner, mine["id"], today, "off", None, svc.new_key())
    assert r.changed and r.shift["status"] == "pre_declared_off"
    assert svc.get_shift(conn, s["id"])["status"] == "withdrawn"  # kept in the history


# ---------------------------------------------------------------- §6: «Оштрафовать», one violation — one fine


def test_fine_from_day_uses_the_same_violation_and_never_doubles(conn, make_user):
    owner, _, mine = setup(conn, make_user)
    today = today_local()
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh = headers(*OWNER)
    client.get("/api/me", headers=oh)
    k = key()
    r = client.post(f"/api/admin/day/{today}/fine", json=body(mine["id"], k), headers=oh)
    assert r.status_code == 200 and r.json()["changed"] is True
    v = violations.unmarked_violation(conn, mine["id"], today)
    assert v["status"] == "penalized" and len(fines_of(conn, mine["id"])) == 1
    assert finance.get(conn, v["linked_fine_transaction_id"])["status"] == "active"  # an owner fines directly
    # a double tap / a retried request — the same answer, nothing new
    again = client.post(f"/api/admin/day/{today}/fine", json=body(mine["id"], k), headers=oh)
    assert again.status_code == 200 and again.json()["changed"] is False
    # a new tap — refused with what already happened
    r = client.post(f"/api/admin/day/{today}/fine", json=body(mine["id"]), headers=oh)
    assert r.status_code == 409 and r.json()["code"] == "already_resolved" and "штраф назначен" in r.json()["detail"]
    # the 22:00 check afterwards makes no second violation
    assert mine["id"] not in {v["user_id"] for v in violations.scan_unmarked(conn, today)}
    assert conn.execute("SELECT COUNT(*) FROM attendance_violations WHERE user_id = ?", (mine["id"],)).fetchone()[0] == 1
    assert len(fines_of(conn, mine["id"])) == 1
    # «Дисциплина» sees the same, decided violation
    assert client.post(f"/api/admin/violations/{v['id']}/fine", json={"units": 3, "comment": "ещё", "idempotency_key": key()},
                       headers=oh).status_code == 409
    row = next(p for p in client.get(f"/api/admin/day?day={today}", headers=oh).json()["unmarked"] if p["user_id"] == mine["id"])
    assert row["violation"]["status"] == "penalized" and row["violation"]["fine"]["status"] == "active"


def test_fine_after_the_automatic_violation_and_a_trainer_goes_to_approval(conn, make_user):
    owner, trainer, mine = setup(conn, make_user)
    today = today_local()
    violations.scan_unmarked(conn, today)
    auto = violations.unmarked_violation(conn, mine["id"], today)
    res = violations.fine_unmarked(conn, trainer, mine["id"], today, 3, "Не отметил", None, svc.new_key())
    assert res.changed and res.violation["id"] == auto["id"]  # the automatic one, not a new one
    assert res.tx["status"] == "pending_admin_approval"  # no write-off past the approval step
    # declined → the violation waits again; a new fine only by a new, deliberate action
    finance.decide(conn, owner, res.tx["id"], False, "нет", svc.new_key())
    assert violations.unmarked_violation(conn, mine["id"], today)["status"] == "pending"
    assert len([t for t in fines_of(conn, mine["id"]) if t["status"] not in ("rejected", "cancelled")]) == 0
    again = violations.fine_unmarked(conn, owner, mine["id"], today, 2, "Всё же штраф", None, svc.new_key())
    assert again.changed and again.tx["status"] == "active"


def test_waived_and_forgiven_violations_are_not_fined_again(conn, make_user):
    owner, _, mine = setup(conn, make_user)
    today = today_local()
    violations.scan_unmarked(conn, today)
    v = violations.unmarked_violation(conn, mine["id"], today)
    violations.waive(conn, owner, v["id"], "больничный", None, svc.new_key())
    with pytest.raises(svc.ServiceError) as e:
        violations.fine_unmarked(conn, owner, mine["id"], today, 3, "x", None, svc.new_key())
    assert e.value.code == "already_resolved" and "прощено" in e.value.message


def test_tomorrow_only_after_the_deadline_and_only_the_unmarked(conn, make_user):
    owner, trainer, mine = setup(conn, make_user)
    today = today_local()
    tomorrow = today + timedelta(days=1)
    noon = datetime.combine(today, time(12), tzinfo=settings.tz)
    late = datetime.combine(today, time(22, 30), tzinfo=settings.tz)
    with pytest.raises(svc.ServiceError) as e:
        violations.fine_unmarked(conn, owner, mine["id"], tomorrow, 3, "x", None, svc.new_key(), today=today, now=noon)
    assert e.value.code == "fine_not_yet"
    assert violations.unmarked_violation(conn, mine["id"], tomorrow) is None  # nothing written
    res = violations.fine_unmarked(conn, owner, mine["id"], tomorrow, 3, "x", None, svc.new_key(), today=today, now=late)
    assert res.changed and res.violation["kind"] == "unmarked" and res.violation["week_start_date"] == tomorrow.isoformat()
    # someone who marked the day (and has no violation) can't be fined for it
    svc.admin_mark_day(conn, owner, trainer["id"], tomorrow, "off", None, svc.new_key())
    with pytest.raises(svc.ServiceError) as e:
        violations.fine_unmarked(conn, owner, trainer["id"], tomorrow, 3, "x", None, svc.new_key(), today=today, now=late)
    assert e.value.code == "not_unmarked"
    # other days — through «Дисциплина»
    with pytest.raises(svc.ServiceError) as e:
        violations.fine_unmarked(conn, owner, mine["id"], today - timedelta(days=1), 3, "x", None, svc.new_key())
    assert e.value.code == "fine_not_yet"
    # the API says so before the deadline (tests run at noon)
    client = TestClient(create_app(conn, Notifier(None, conn)))
    day = client.get(f"/api/admin/day?day={tomorrow}", headers=headers(*OWNER)).json()
    assert day["can_fine"] is False and "22:00" in day["fine_note"]


def test_fine_rights_follow_the_network(conn, make_user):
    owner, trainer, mine = setup(conn, make_user)
    stranger = make_user(first="Чужой")
    today = today_local()
    with pytest.raises(svc.ServiceError) as e:
        violations.fine_unmarked(conn, trainer, stranger["id"], today, 3, "x", None, svc.new_key())
    assert e.value.status == 403
    with pytest.raises(svc.ServiceError):
        violations.fine_unmarked(conn, owner, mine["id"], today, 3, "", None, svc.new_key())  # a reason is required
    employee = make_user(first="Рядовой")
    client = TestClient(create_app(conn, Notifier(None, conn)))
    r = client.post(f"/api/admin/day/{today}/fine", json=body(mine["id"]),
                    headers=headers(employee["telegram_id"], "Рядовой", "Петров"))
    assert r.status_code == 403
    assert pending.counts(conn, owner)["unmarked"] >= 1


# ---------------------------------------------------------------- §7: the label above the role


def test_header_title_can_be_changed_hidden_and_restored(conn, make_user):
    svc.ensure_admin(conn, *OWNER)
    u = make_user()
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh, uh = headers(*OWNER), headers(u["telegram_id"])
    assert client.get("/api/me", headers=uh).json()["header"] == "выход"  # the default — what it always was
    r = client.put("/api/admin/header-title", json={"text": "  Иди   работай ", "visible": True}, headers=oh)
    assert r.status_code == 200 and r.json()["header"]["text"] == "Иди работай"
    assert client.get("/api/me", headers=uh).json()["header"] == "Иди работай"
    client.put("/api/admin/header-title", json={"text": "Иди работай", "visible": False}, headers=oh)
    assert client.get("/api/me", headers=uh).json()["header"] is None
    client.put("/api/admin/header-title", json={"text": "Иди работай", "visible": True}, headers=oh)
    assert client.get("/api/me", headers=uh).json()["header"] == "Иди работай"
    assert client.delete("/api/admin/header-title", headers=oh).json()["header"] == {"text": "выход", "visible": True,
                                                                                     "default": True}
    assert client.put("/api/admin/header-title", json={"text": "x" * 40, "visible": True}, headers=oh).status_code == 422
    assert client.put("/api/admin/header-title", json={"text": "", "visible": True}, headers=oh).status_code == 422
    # only who has «settings»
    assert client.put("/api/admin/header-title", json={"text": "Моё", "visible": True}, headers=uh).status_code == 403
    assert conn.execute("SELECT COUNT(*) FROM event_logs WHERE event_type LIKE 'HEADER_TITLE_%'").fetchone()[0] == 4
    assert app_settings.header_title(conn)["text"] == "выход"


# ---------------------------------------------------------------- §8: «Приложение»


def test_menu_button_and_app_button_are_named_for_the_whole_app():
    from app import bot

    assert bot.MENU_BUTTON == "Приложение"
    settings_url = settings.webapp_url
    kb = bot.open_app_keyboard()
    if settings_url.startswith("https://"):
        assert kb.inline_keyboard[0][0].text == "📱 Открыть приложение"
    assert "График" not in bot.WELCOME
