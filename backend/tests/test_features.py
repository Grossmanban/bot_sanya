import asyncio
import io
from datetime import date, datetime, timedelta

import pytest
from openpyxl import load_workbook

from app import app_settings, export, jobs
from app import services as svc
from app.config import settings
from app.db import connect
from app.notify import Notifier

TODAY = date(2026, 10, 14)  # Wednesday
THU = TODAY + timedelta(days=1)
P = {"start_time": "16:00", "end_time": "19:00", "volume": 25, "location": "Европа"}


class FakeNotifier(Notifier):
    def __init__(self, conn):
        super().__init__(None, conn)
        self.sent: list[tuple[int, str]] = []
        self.docs: list[tuple[int, str]] = []

    async def send(self, chat_id, text, markup=None):
        self.sent.append((chat_id, text))
        return True

    async def send_document(self, chat_id, data, filename, caption=None):
        self.docs.append((chat_id, filename))
        return True


def at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=settings.tz)


def run(coro):
    return asyncio.run(coro)


def shift(conn, user, day=THU, payload=P):
    return svc.create_shift(conn, user, day, dict(payload), svc.new_key(), today=TODAY).shift


# ---------------------------------------------------------------- points


def test_points_restrict_location_and_capacity(conn, make_user):
    admin = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    a, b, c = make_user(), make_user("Ваня", "Бургер", None), make_user("Вера", "Котова", "vera")
    # no points configured: free text is fine
    assert shift(conn, a, payload={**P, "location": "Где угодно"})["location"] == "Где угодно"
    svc.create_point(conn, admin, "Европа", 2)
    with pytest.raises(svc.ServiceError) as e:
        svc.create_point(conn, admin, "европа", None)
    assert e.value.code == "point_exists"
    with pytest.raises(svc.ServiceError) as e:
        shift(conn, b, payload={**P, "location": "Неизвестная"})
    assert e.value.code == "unknown_point"
    assert shift(conn, b, payload={**P, "location": "европа"})["location"] == "Европа"  # canonical name
    shift(conn, c, day=THU + timedelta(days=1))
    d = make_user("Лена", "Морозова", "lena")
    shift(conn, d)  # second place on Thursday
    e_user = make_user("Костя", "Новиков", "k")
    with pytest.raises(svc.ServiceError) as e:
        shift(conn, e_user)
    assert e.value.code == "point_full"
    week = svc.week_view(conn, b, 0, today=TODAY)
    assert week["days"][3]["points_load"]["Европа"] == 2


def test_point_rename_moves_upcoming_shifts(conn, make_user):
    admin = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    point = svc.create_point(conn, admin, "Европа", None)
    u = make_user()
    sid = shift(conn, u)["id"]
    svc.update_point(conn, admin, point["id"], {"name": "ТЦ Европа"})
    assert svc.get_shift(conn, sid)["location"] == "ТЦ Европа"
    svc.update_point(conn, admin, point["id"], {"active": False})
    assert svc.list_points(conn) == []


# ---------------------------------------------------------------- fact


def test_fact_window_and_stats(conn, make_user):
    u = make_user()
    s = shift(conn, u)
    with pytest.raises(svc.ServiceError) as e:
        svc.set_fact(conn, u, s["id"], 20, svc.new_key(), today=TODAY)
    assert e.value.code == "too_early"
    res = svc.set_fact(conn, u, s["id"], 30, svc.new_key(), today=THU)
    assert res.changed and res.shift["fact_volume"] == 30
    view = svc.serialize_shift(res.shift, THU)
    assert view["fact_pay"] == 30 * 120 and view["can_set_fact"]
    with pytest.raises(svc.ServiceError) as e:
        svc.set_fact(conn, u, s["id"], 31, svc.new_key(), today=THU + timedelta(days=4))
    assert e.value.code == "too_late"
    st = svc.stats(conn, u["id"], TODAY, THU)
    assert st["totals"]["fact_volume"] == 30 and st["totals"]["fact_pay"] == 3600


# ---------------------------------------------------------------- scheduled jobs


def test_eve_reminder_auto_raspred_once(conn, make_user):
    svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    a, b = make_user(), make_user("Ваня", "Бургер", None)
    shift(conn, a)
    shift(conn, b, payload={**P, "start_time": "14:00"})
    svc.set_notify(conn, b, False)
    n = FakeNotifier(conn)
    ran = run(jobs.tick(conn, n, at(TODAY, "21:05")))
    assert "eve_reminder" in ran and "auto_raspred" in ran
    reminders = [t for chat, t in n.sent if "Завтра" in t]
    assert len(reminders) == 1 and "На точке также: Ваня 14:00–19:00" in reminders[0]
    assert any(chat == 999 and "Распред на" in t for chat, t in n.sent)
    # second tick the same evening sends nothing again
    n.sent.clear()
    assert run(jobs.tick(conn, n, at(TODAY, "21:30"))) == []
    assert n.sent == []


def test_jobs_respect_window_and_settings(conn, make_user):
    u = make_user()
    shift(conn, u)
    n = FakeNotifier(conn)
    # too early and too late (window is 3h)
    assert "eve_reminder" not in run(jobs.tick(conn, n, at(TODAY, "19:59")))
    app_settings.update(conn, {"eve_reminder_enabled": False})
    assert "eve_reminder" not in run(jobs.tick(conn, n, at(TODAY, "20:10")))
    with pytest.raises(svc.ServiceError):
        app_settings.update(conn, {"eve_reminder_time": "25:00"})
    with pytest.raises(svc.ServiceError):
        app_settings.update(conn, {"nope": True})


def test_week_reminder_only_for_empty_next_week(conn, make_user):
    a, b = make_user(), make_user("Ваня", "Бургер", None)
    next_mon = date(2026, 10, 19)
    shift(conn, b, day=next_mon)
    n = FakeNotifier(conn)
    ran = run(jobs.tick(conn, n, at(date(2026, 10, 15), "12:01")))  # Thursday
    assert "week_reminder" in ran
    targets = {chat for chat, t in n.sent if "следующую неделю" in t}
    assert targets == {a["telegram_id"]}


def test_fact_request_after_shift_end(conn, make_user):
    u = make_user()
    s = shift(conn, u)
    n = FakeNotifier(conn)
    run(jobs.tick(conn, n, at(THU, "19:10")))
    assert not [t for _, t in n.sent if "Сколько сделал" in t]
    run(jobs.tick(conn, n, at(THU, "19:16")))
    asks = [t for _, t in n.sent if "Сколько сделал" in t]
    assert len(asks) == 1 and "План был 25" in asks[0]
    run(jobs.tick(conn, n, at(THU, "19:30")))
    assert len([t for _, t in n.sent if "Сколько сделал" in t]) == 1
    assert svc.get_shift(conn, s["id"])["fact_asked_at"]


def test_weekly_report_with_excel(conn, make_user):
    svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user()
    s = shift(conn, u)
    svc.set_fact(conn, u, s["id"], 22, svc.new_key(), today=THU)
    n = FakeNotifier(conn)
    monday = date(2026, 10, 19)
    ran = run(jobs.tick(conn, n, at(monday, "10:00")))
    assert "weekly_report" in ran
    report = next(t for _, t in n.sent if "Итоги недели" in t)
    assert "план 25, факт 22 шт." in report
    assert n.docs and n.docs[0][1].endswith(".xlsx")


# ---------------------------------------------------------------- export, broadcasts, migration


def test_excel_export_contents(conn, make_user):
    u = make_user()
    s = shift(conn, u)
    svc.set_fact(conn, u, s["id"], 20, svc.new_key(), today=THU)
    wb = load_workbook(io.BytesIO(export.build_xlsx(conn, TODAY, THU)))
    assert wb.sheetnames == ["Итоги", "Смены", "Штрафы"]
    totals = list(wb["Итоги"].iter_rows(values_only=True))
    assert totals[1][0] == "Иван Петров" and totals[1][3] == 25 and totals[1][4] == 20
    assert totals[-1][0] == "Итого"
    assert list(wb["Смены"].iter_rows(values_only=True))[1][5] == "Европа"


def test_broadcast_audiences(conn, make_user):
    admin = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    make_user()
    make_user("Ваня", "Бургер", None, working=False)
    assert len(svc.broadcast_recipients(conn, "working")) == 1
    assert len(svc.broadcast_recipients(conn, "all")) == 3  # owner included
    row, recipients = svc.create_broadcast(conn, admin, "all", "  Привет всем  ")
    assert row["text"] == "Привет всем" and row["recipients"] == 3
    n = FakeNotifier(conn)
    assert run(n.broadcast(row["id"], recipients, row["text"])) == 3
    assert svc.list_broadcasts(conn)[0]["delivered"] == 3
    with pytest.raises(svc.ServiceError):
        svc.create_broadcast(conn, admin, "all", "   ")


def test_migration_from_first_schema(tmp_path):
    import sqlite3

    from app.db import SCHEMA

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA)
    old.execute("INSERT INTO users (telegram_id, first_name, last_name, created_at) VALUES (1, 'А', 'Б', 'x')")
    old.commit()
    old.close()
    conn = connect(str(path))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT notify_reminders FROM users").fetchone()[0] == 1
    connect(str(path))  # idempotent
