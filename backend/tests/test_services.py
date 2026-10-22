from datetime import date, timedelta

import pytest

from app import services as svc
from app.config import settings
from app.timeutil import today_local

TODAY = date(2026, 10, 14)  # Wednesday
SAT = date(2026, 10, 17)
PAYLOAD = {"start_time": "16:47", "end_time": "19:00", "volume": 25, "location": "Европа"}


def create(conn, user, day=SAT, payload=PAYLOAD, key=None):
    return svc.create_shift(conn, user, day, dict(payload), key or svc.new_key(), today=TODAY)


def test_registration_is_created_once_and_decided_once(conn):
    user, created = svc.create_registration(conn, 1, None, "Иван", "Петров")
    again, created_again = svc.create_registration(conn, 1, None, "Другой", "Человек")
    assert created and not created_again and again["id"] == user["id"]
    approved, changed = svc.decide_registration(conn, user["id"], True, 999)
    assert changed and approved["status"] == "approved" and approved["work_status"] == "not_working"
    _, changed = svc.decide_registration(conn, user["id"], False, 999)
    assert not changed
    assert svc.claim_welcome(conn, user["id"]) is True
    assert svc.claim_welcome(conn, user["id"]) is False


def test_rejected_cannot_reregister(conn):
    user, _ = svc.create_registration(conn, 2, None, "Иван", "Петров")
    svc.decide_registration(conn, user["id"], False, 999)
    again, created = svc.create_registration(conn, 2, None, "Иван", "Петров")
    assert not created and again["status"] == "rejected"


def test_not_working_blocks_booking(conn, make_user):
    user = make_user(working=False)
    with pytest.raises(svc.ServiceError) as e:
        create(conn, user)
    assert e.value.code == "not_working"
    with pytest.raises(svc.ServiceError):
        svc.pre_declare_off(conn, user, SAT, svc.new_key(), today=TODAY)


@pytest.mark.parametrize(
    "payload,code",
    [
        ({**PAYLOAD, "start_time": "25:00"}, "bad_start_time"),
        ({**PAYLOAD, "end_time": "16:00"}, "bad_time_range"),
        ({**PAYLOAD, "volume": 0}, "bad_volume"),
        ({**PAYLOAD, "volume": -5}, "bad_volume"),
        ({**PAYLOAD, "location": "   "}, "bad_location"),
    ],
)
def test_invalid_payload_rejected(conn, make_user, payload, code):
    user = make_user()
    with pytest.raises(svc.ServiceError) as e:
        create(conn, user, payload=payload)
    assert e.value.code == code


def test_past_and_far_dates_rejected(conn, make_user):
    user = make_user()
    with pytest.raises(svc.ServiceError) as e:
        create(conn, user, day=TODAY - timedelta(days=1))
    assert e.value.code == "past_date"
    with pytest.raises(svc.ServiceError) as e:
        create(conn, user, day=TODAY + timedelta(days=14))
    assert e.value.code == "too_far"
    # next week's Sunday is still bookable
    assert create(conn, user, day=date(2026, 10, 25)).changed


def test_create_is_idempotent_and_unique_per_day(conn, make_user):
    user = make_user()
    key = svc.new_key()
    first = create(conn, user, key=key)
    second = create(conn, user, key=key)
    assert first.changed and not second.changed
    assert svc.day_totals(conn, SAT) == (1, 25)
    with pytest.raises(svc.ServiceError) as e:
        create(conn, user)
    assert e.value.code == "already_exists"


def test_today_is_urgent_until_admin_decides(conn, make_user):
    user = make_user()
    res = create(conn, user, day=TODAY)
    assert res.shift["status"] == "awaiting_approval" and res.event == "SHIFT_URGENT_REQUESTED"
    assert svc.day_totals(conn, TODAY) == (0, 0)
    decided = svc.decide_urgent(conn, res.shift["id"], True, 999)
    assert decided.changed and decided.shift["status"] == "active"
    assert not svc.decide_urgent(conn, res.shift["id"], False, 999).changed
    assert svc.day_totals(conn, TODAY) == (1, 25)


def test_cancel_restore_final_cancel_cycle(conn, make_user):
    user = make_user()
    shift = create(conn, user).shift
    sid = shift["id"]

    # restored shift cannot be first-cancelled; active shift cannot be final-cancelled
    with pytest.raises(svc.ServiceError):
        svc.final_cancel_shift(conn, user, sid, svc.new_key(), today=TODAY)

    key = svc.new_key()
    res = svc.cancel_shift(conn, user, sid, key, today=TODAY)
    assert res.shift["status"] == "cancelled"
    assert not svc.cancel_shift(conn, user, sid, key, today=TODAY).changed  # duplicate click
    assert svc.day_totals(conn, SAT) == (0, 0)
    view = svc.serialize_shift(res.shift, TODAY)
    assert view["can_restore"] and not view["can_cancel"]

    new_payload = {**PAYLOAD, "volume": 30}
    res = svc.restore_shift(conn, user, sid, new_payload, svc.new_key(), today=TODAY)
    assert res.shift["status"] == "restored" and res.shift["volume"] == 30
    assert svc.day_totals(conn, SAT) == (1, 30)

    with pytest.raises(svc.ServiceError) as e:
        svc.cancel_shift(conn, user, sid, svc.new_key(), today=TODAY)
    assert e.value.code == "final_confirm_required"

    res = svc.final_cancel_shift(conn, user, sid, svc.new_key(), today=TODAY)
    assert res.shift["status"] == "finally_cancelled"
    assert svc.day_totals(conn, SAT) == (0, 0)
    view = svc.serialize_shift(res.shift, TODAY)
    assert not any(view[k] for k in ("can_edit", "can_cancel", "can_restore", "can_final_cancel"))
    with pytest.raises(svc.ServiceError):
        svc.restore_shift(conn, user, sid, PAYLOAD, svc.new_key(), today=TODAY)

    events = [r[0] for r in conn.execute("SELECT event_type FROM event_logs WHERE shift_id = ? ORDER BY id", (sid,))]
    assert events == ["SHIFT_CREATED", "SHIFT_CANCELLED", "SHIFT_RESTORED", "SHIFT_FINAL_CANCELLED"]


def test_penalty_applied_once(conn, make_user):
    user = make_user()
    sid = create(conn, user).shift["id"]
    with pytest.raises(svc.ServiceError):
        svc.set_penalty(conn, sid, 1000, 999)
    svc.cancel_shift(conn, user, sid, svc.new_key(), today=TODAY)
    svc.restore_shift(conn, user, sid, PAYLOAD, svc.new_key(), today=TODAY)
    svc.final_cancel_shift(conn, user, sid, svc.new_key(), today=TODAY)
    assert svc.set_penalty(conn, sid, 1000, 999).changed
    second = svc.set_penalty(conn, sid, 2000, 998)
    assert not second.changed and second.shift["penalty_amount"] == 1000
    assert svc.user_penalties(conn, user["id"])[0]["penalty_amount"] == 1000
    log = svc.penalty_log(conn, None, None, None)
    assert len(log) == 1 and log[0]["amount"] == 1000


def test_volume_until_midnight_then_one_change_then_a_request(conn, make_user):
    """Until 00:00 after booking — anything, as often as needed; then the volume is fixed, the time or the point
    change once, and the next change waits for an admin or the network's trainer."""
    from datetime import datetime, time

    user = make_user()
    sid = create(conn, user).shift["id"]
    conn.execute("UPDATE shifts SET created_at = ? WHERE id = ?", (f"{TODAY.isoformat()}T19:30:00+03:00", sid))
    evening = datetime.combine(TODAY, time(23, 50), tzinfo=settings.tz)
    night = datetime.combine(TODAY + timedelta(days=1), time(0, 0), tzinfo=settings.tz)
    shift = svc.get_shift(conn, sid)
    assert svc.volume_until(shift) == night
    view = svc.serialize_shift(shift, TODAY, conn, now=evening)
    assert view["volume_open"] and view["can_edit"] and not view["can_request_change"]
    for volume in (40, 45):  # free while the schedule is being set — not counted
        res = svc.edit_shift(conn, user, sid, {**PAYLOAD, "volume": volume}, svc.new_key(), today=TODAY, now=evening)
    assert res.shift["volume"] == 45 and res.shift["edit_count"] == 0

    view = svc.serialize_shift(res.shift, TODAY, conn, now=night)
    assert not view["volume_open"] and view["can_edit"] and view["volume_until"].startswith(f"{(TODAY + timedelta(days=1)).isoformat()}T00:00")
    with pytest.raises(svc.ServiceError) as e:  # 00:00 — the volume is fixed
        svc.edit_shift(conn, user, sid, {**PAYLOAD, "volume": 60}, svc.new_key(), today=TODAY, now=night)
    assert e.value.code == "volume_locked"
    res = svc.edit_shift(conn, user, sid, {**PAYLOAD, "volume": 45, "start_time": "11:00"}, svc.new_key(),
                         today=TODAY, now=night)
    assert res.shift["start_time"] == "11:00" and res.shift["edit_count"] == 1
    view = svc.serialize_shift(res.shift, TODAY, conn, now=night)
    assert not view["can_edit"] and view["can_request_change"]
    with pytest.raises(svc.ServiceError) as e:
        svc.edit_shift(conn, user, sid, {**PAYLOAD, "volume": 45, "start_time": "12:00"}, svc.new_key(), today=TODAY, now=night)
    assert e.value.code == "edit_limit"

    # the second change is a request; the volume in it stays as it is
    req = svc.request_change(conn, user, sid, {**PAYLOAD, "volume": 999, "start_time": "12:00", "location": "Центр"},
                             svc.new_key(), today=TODAY, now=night)
    assert req.request["status"] == "pending" and req.shift["start_time"] == "11:00"
    view = svc.serialize_shift(req.shift, TODAY, conn, now=night)
    assert view["change_request"]["location"] == "Центр" and not view["can_request_change"]
    with pytest.raises(svc.ServiceError) as e:
        svc.request_change(conn, user, sid, {**PAYLOAD, "start_time": "13:00"}, svc.new_key(), today=TODAY, now=night)
    assert e.value.code == "already_requested"
    stranger = make_user(first="Мария", username="maria")
    with pytest.raises(svc.ServiceError) as e:  # an employee can't decide
        svc.decide_change(conn, stranger, req.request["id"], True, None, svc.new_key(), today=TODAY)
    assert e.value.status == 403
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    done = svc.decide_change(conn, owner, req.request["id"], True, None, svc.new_key(), today=TODAY)
    assert done.request["status"] == "approved"
    assert (done.shift["start_time"], done.shift["location"], done.shift["volume"], done.shift["edit_count"]) == ("12:00", "Центр", 45, 2)

    # a declined one changes nothing; a withdrawn one too; cancelling the shift closes a waiting one
    no = svc.request_change(conn, user, sid, {**PAYLOAD, "start_time": "14:00"}, svc.new_key(), today=TODAY, now=night)
    svc.decide_change(conn, owner, no.request["id"], False, "Точка занята", svc.new_key(), today=TODAY)
    assert svc.get_shift(conn, sid)["start_time"] == "12:00"
    back = svc.request_change(conn, user, sid, {**PAYLOAD, "start_time": "15:00"}, svc.new_key(), today=TODAY, now=night)
    assert svc.withdraw_change(conn, user, back.request["id"], svc.new_key()).request["status"] == "withdrawn"
    last = svc.request_change(conn, user, sid, {**PAYLOAD, "start_time": "16:00"}, svc.new_key(), today=TODAY, now=night)
    svc.cancel_shift(conn, user, sid, svc.new_key(), today=TODAY)
    assert svc._change(conn, last.request["id"])["status"] == "withdrawn"


def test_change_request_goes_to_the_network_trainer(conn, make_user):
    from datetime import datetime, time

    from app import networks

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    north = networks.create(conn, owner, "Север", svc.new_key())
    south = networks.create(conn, owner, "Юг", svc.new_key())
    user, trainer, other = make_user(), make_user(first="Тренер", username="tr"), make_user(first="Чужой", username="ch")
    conn.execute("UPDATE users SET network_id = ? WHERE id IN (?, ?)", (north["id"], user["id"], trainer["id"]))
    conn.execute("UPDATE users SET role = 'trainer', network_id = ? WHERE id = ?", (south["id"], other["id"]))
    conn.execute("UPDATE users SET role = 'trainer' WHERE id = ?", (trainer["id"],))
    sid = create(conn, user).shift["id"]
    conn.execute("UPDATE shifts SET created_at = ?, edit_count = 1 WHERE id = ?", (f"{TODAY.isoformat()}T10:00:00+03:00", sid))
    night = datetime.combine(TODAY + timedelta(days=1), time(1, 0), tzinfo=settings.tz)
    req = svc.request_change(conn, user, sid, {**PAYLOAD, "end_time": "20:00"}, svc.new_key(), today=TODAY, now=night)
    with pytest.raises(svc.ServiceError) as e:  # another network's trainer
        svc.decide_change(conn, svc.get_user(conn, other["id"]), req.request["id"], True, None, svc.new_key(), today=TODAY)
    assert e.value.status == 403
    done = svc.decide_change(conn, svc.get_user(conn, trainer["id"]), req.request["id"], True, None, svc.new_key(), today=TODAY)
    assert done.shift["end_time"] == "20:00"


def test_other_users_shift_is_not_found(conn, make_user):
    a, b = make_user(), make_user(first="Мария", username="maria")
    sid = create(conn, a).shift["id"]
    with pytest.raises(svc.ServiceError) as e:
        svc.cancel_shift(conn, b, sid, svc.new_key(), today=TODAY)
    assert e.value.status == 404


def test_pre_declare_off_and_clear(conn, make_user):
    user = make_user()
    res = svc.pre_declare_off(conn, user, SAT, svc.new_key(), today=TODAY)
    assert res.shift["status"] == "pre_declared_off"
    with pytest.raises(svc.ServiceError):
        create(conn, user)
    svc.clear_pre_declare_off(conn, user, res.shift["id"], svc.new_key(), today=TODAY)
    assert svc.get_user_shift_on(conn, user["id"], SAT) is None
    assert create(conn, user).changed


def test_week_view_shows_colleagues_and_flags(conn, make_user):
    me, vanya, vera = make_user(), make_user("Ваня", "Бургер", None), make_user("Вера", "Иванова", "vera")
    create(conn, vanya, payload={**PAYLOAD, "start_time": "14:00", "end_time": "20:00"})
    svc.pre_declare_off(conn, vera, SAT, svc.new_key(), today=TODAY)
    week = svc.week_view(conn, me, 0, today=TODAY)
    assert week["start"] == "2026-10-12" and week["end"] == "2026-10-18"
    sat = week["days"][5]
    assert sat["date"] == SAT.isoformat() and sat["my_shift"] is None
    assert sat["colleagues"][0]["name"] == "Ваня Бургер" and sat["colleagues"][0]["start_time"] == "14:00"
    assert sat["colleagues_off"][0]["first_name"] == "Вера"
    assert week["days"][0]["is_past"] and week["days"][2]["is_today"]


def test_lazy_tax_rate(conn, make_user):
    user = make_user()
    shift = create(conn, user, payload={**PAYLOAD, "volume": 15}).shift
    view = svc.serialize_shift(shift, TODAY)
    assert view["lazy"] and view["rate"] == 100 and view["expected_pay"] == 1500


def test_admin_edit_locks_user_edit_and_force_cancel_blocks_restore(conn, make_user):
    user = make_user()
    admin = {"telegram_id": 999}
    sid = create(conn, user).shift["id"]
    with pytest.raises(svc.ServiceError) as e:
        svc.admin_edit_shift(conn, admin, sid, {"volume": 50}, svc.new_key())
    assert e.value.code == "reason_required"
    res = svc.admin_edit_shift(conn, admin, sid, {"volume": 50, "reason": "договорились"}, svc.new_key())
    view = svc.serialize_shift(res.shift, TODAY)  # the admin's change closes the free window: next — a request
    assert res.shift["volume"] == 50 and not view["can_edit"] and not view["volume_open"] and view["can_request_change"]
    res = svc.admin_edit_shift(conn, admin, sid, {"action": "force_cancel", "reason": "заболел"}, svc.new_key())
    assert res.shift["status"] == "cancelled" and not svc.serialize_shift(res.shift, TODAY)["can_restore"]
    res = svc.admin_edit_shift(conn, admin, sid, {"action": "force_restore", "reason": "выздоровел"}, svc.new_key())
    assert res.shift["status"] == "active"
    svc.admin_delete_shift(conn, admin, sid, "ошибка", svc.new_key())
    assert svc.get_shift(conn, sid)["status"] == "removed"  # kept as history, never deleted
    assert svc.get_user_shift_on(conn, user["id"], SAT) is None
    assert conn.execute("SELECT COUNT(*) FROM event_logs WHERE event_type = 'SHIFT_REMOVED_BY_ADMIN'").fetchone()[0] == 1


def test_raspred_sorted_and_excludes_cancelled(conn, make_user):
    a = make_user("Вася", "Пупкин", "vasya")
    b = make_user("Мария", "Пупкина", None)
    c = make_user("Иван", "Иванов", "ivan2")
    create(conn, a, payload={**PAYLOAD, "start_time": "17:00", "volume": 30})
    create(conn, b, payload={**PAYLOAD, "start_time": "16:00", "volume": 15, "location": "Центр"})
    sid = create(conn, c, payload={**PAYLOAD, "start_time": "15:00"}).shift["id"]
    svc.cancel_shift(conn, c, sid, svc.new_key(), today=TODAY)
    data = svc.raspred(conn, SAT)
    assert [r["name"] for r in data["rows"]] == ["Мария Пупкина", "Вася Пупкин"]
    assert data["total_count"] == 2 and data["total_volume"] == 45
    assert "16:00–19:00 — Мария Пупкина — 15 шт. — Центр" in data["text"]
    assert "(@vasya)" in data["text"]
    assert "нет сотрудников" in svc.raspred(conn, SAT + timedelta(days=1))["text"]


def test_stats_totals(conn, make_user):
    user = make_user()
    create(conn, user, day=date(2026, 10, 15), payload={**PAYLOAD, "volume": 30})
    sid = create(conn, user, day=date(2026, 10, 16), payload={**PAYLOAD, "volume": 20}).shift["id"]
    create(conn, user, day=SAT, payload={**PAYLOAD, "volume": 10})
    svc.cancel_shift(conn, user, sid, svc.new_key(), today=TODAY)
    s = svc.stats(conn, user["id"], date(2026, 10, 12), date(2026, 10, 18))
    t = s["totals"]
    assert t["shifts"] == 3 and t["active_volume"] == 40 and t["cancelled_volume"] == 20
    assert t["expected_pay"] == 30 * 120 + 10 * 100
    assert "Налог на лень" in s["items"][2]["notes"]


def test_only_owner_grants_admin_and_owner_is_protected(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    worker = make_user()
    helper = make_user("Мария", "Админова", "maria")
    user, changed = svc.set_role(conn, owner, helper["id"], "admin", svc.new_key())
    assert changed and user["role"] == "admin"
    with pytest.raises(svc.ServiceError) as e:
        svc.set_role(conn, user, worker["id"], "admin", svc.new_key())
    assert e.value.code == "owner_only"
    with pytest.raises(svc.ServiceError) as e:
        svc.set_role(conn, owner, owner["id"], "employee", svc.new_key())
    assert e.value.code == "owner"
    assert svc.admin_telegram_ids(conn) == sorted([999, helper["telegram_id"]])


def test_block_is_owner_only_and_pause_needs_reason(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    helper = make_user("Мария", "Админова", "maria")
    helper, _ = svc.set_role(conn, owner, helper["id"], "admin", svc.new_key())
    worker = make_user()

    # an admin pauses an employee (reason required), cannot block, cannot touch owners or themselves
    with pytest.raises(svc.ServiceError) as e:
        svc.set_account_state(conn, helper, worker["id"], "paused", "", svc.new_key())
    assert e.value.code == "reason_required"
    paused, changed = svc.set_account_state(conn, helper, worker["id"], "paused", "отпуск", svc.new_key())
    assert changed and paused["account_state"] == "paused" and paused["work_status"] == "not_working"
    with pytest.raises(svc.ServiceError) as e:
        create(conn, paused)
    assert e.value.code == "paused"
    with pytest.raises(svc.ServiceError) as e:
        svc.set_account_state(conn, helper, worker["id"], "blocked", None, svc.new_key())
    assert e.value.code == "owner_only"
    with pytest.raises(svc.ServiceError):
        svc.set_account_state(conn, helper, owner["id"], "paused", "x", svc.new_key())
    with pytest.raises(svc.ServiceError):
        svc.set_account_state(conn, helper, helper["id"], "paused", "x", svc.new_key())

    # the owner blocks an admin: rights are taken away; unblocking keeps them an employee
    blocked, _ = svc.set_account_state(conn, owner, helper["id"], "blocked", "уволен", svc.new_key())
    assert blocked["account_state"] == "blocked" and blocked["role"] == "employee"
    with pytest.raises(svc.ServiceError) as e:
        svc.set_account_state(conn, helper, worker["id"], "active", None, svc.new_key())  # blocked admin has no rights
    back, _ = svc.set_account_state(conn, owner, helper["id"], "active", None, svc.new_key())
    assert back["account_state"] == "active" and back["state_reason"] is None
    # approved people are not "rejected" any more: access is closed only by block/pause
    with pytest.raises(svc.ServiceError) as e:
        svc.set_access(conn, owner, worker["id"], False, svc.new_key())
    assert e.value.code == "use_block"


def test_four_roles_and_permissions(conn, make_user):
    from app import permissions as perms

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = make_user("Мария", "Админова", "maria")
    trainer = make_user("Тимур", "Тренер", "tim")
    worker = make_user()
    admin, _ = svc.set_role(conn, owner, admin["id"], "admin", svc.new_key())
    trainer, _ = svc.set_role(conn, owner, trainer["id"], "trainer", svc.new_key())
    with pytest.raises(svc.ServiceError):
        svc.set_role(conn, admin, worker["id"], "trainer", svc.new_key())  # only the owner hands out roles
    assert [perms.role_of(u) for u in (owner, admin, trainer, worker)] == ["owner", "admin", "trainer", "employee"]
    assert perms.can(owner, "money") and not perms.can(admin, "money") and not perms.can(trainer, "money")
    assert perms.can(admin, "fine_direct") and not perms.can(trainer, "fine_direct") and perms.can(trainer, "fine_request")
    assert perms.can(owner, "global_actual") and not perms.can(admin, "global_actual")
    # a trainer manages only people of their own network
    assert not perms.manages(trainer, worker)
    conn.execute("UPDATE users SET network_id = 1 WHERE id IN (?, ?)", (trainer["id"], worker["id"]))
    trainer, worker = svc.get_user(conn, trainer["id"]), svc.get_user(conn, worker["id"])
    assert perms.manages(trainer, worker) and perms.can(trainer, "pause", worker)
    assert perms.manages(admin, worker)
    # trainers and employees don't receive admin notifications
    assert set(svc.admin_telegram_ids(conn)) == {999, admin["telegram_id"]}


def test_reminders_locked_while_working(conn, make_user):
    u = make_user()  # working
    with pytest.raises(svc.ServiceError) as e:
        svc.set_notify(conn, u, False)
    assert e.value.code == "reminders_locked"
    u = svc.set_work_status(conn, u, "not_working", svc.new_key())
    assert u["notify_reminders"] == 0  # «Не работаю» switches them off by itself (the customer)
    with pytest.raises(svc.ServiceError) as e:  # and they stay off until «Работаю»
        svc.set_notify(conn, u, True)
    assert e.value.code == "reminders_off"
    assert svc.status_days(u, today_local() + timedelta(days=12)) == 12
    u = svc.set_work_status(conn, u, "working", svc.new_key())
    assert u["notify_reminders"] == 1  # switched back on automatically


def test_pending_request_decided_from_team(conn):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    newbie, _ = svc.create_registration(conn, 77, None, "Новый", "Человек")
    user, changed = svc.set_access(conn, owner, newbie["id"], True, svc.new_key())
    assert changed and user["status"] == "approved"
    assert svc.claim_welcome(conn, user["id"])


def test_change_request_over_http_and_the_bot_message(conn, make_user):
    """The app sends the request; managers get it with buttons; an admin decides in the app."""
    from fastapi.testclient import TestClient

    from app.api import create_app
    from app.auth import sign_init_data

    from .test_features import FakeNotifier, run

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    user = make_user()
    day = today_local() + timedelta(days=1)
    sid = svc.create_shift(conn, user, day, PAYLOAD, svc.new_key()).shift["id"]
    conn.execute("UPDATE shifts SET created_at = '2020-01-01T10:00:00+03:00', edit_count = 1 WHERE id = ?", (sid,))
    n = FakeNotifier(conn)
    client = TestClient(create_app(conn, n))
    h = lambda tg: {"X-Telegram-Init-Data": sign_init_data({"id": tg, "first_name": "X"}, settings.bot_token)}
    body = {**PAYLOAD, "start_time": "12:00", "idempotency_key": svc.new_key()}
    r = client.patch(f"/api/shifts/{sid}", json=body, headers=h(user["telegram_id"]))
    assert r.status_code == 409 and r.json()["code"] == "edit_limit"
    r = client.post(f"/api/shifts/{sid}/change-request", json=body, headers=h(user["telegram_id"]))
    assert r.status_code == 200, r.text
    req = r.json()["shift"]["change_request"]
    assert req["start_time"] == "12:00"
    n.sent.clear()
    run(n.change_requested(svc._change(conn, req["id"])))
    [text] = [t for c, t in n.sent if c == 999]
    assert "Запрос на изменение смены" in text and "Стало: <b>12:00–" in text
    r = client.post(f"/api/admin/change-requests/{req['id']}/decide",
                    json={"approve": True, "idempotency_key": svc.new_key()}, headers=h(999))
    assert r.status_code == 200 and r.json()["shift"]["start_time"] == "12:00"
    r = client.post(f"/api/admin/change-requests/{req['id']}/decide",
                    json={"approve": False, "idempotency_key": svc.new_key()}, headers=h(999))
    assert r.status_code == 409
