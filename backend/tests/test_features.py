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
    assert run(n.broadcast(row["id"], recipients, row["html"])) == 3
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
    from app.db import MIGRATIONS

    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert conn.execute("SELECT notify_reminders FROM users").fetchone()[0] == 1
    connect(str(path))  # idempotent


def test_broadcast_audience_toggles_union(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")  # admin, not working
    worker = make_user()
    idle = make_user("Ваня", "Бургер", None, working=False)
    helper = make_user("Мария", "Админова", "m")  # working
    svc.set_role(conn, owner, helper["id"], "admin", svc.new_key())
    ids = lambda groups: {u["id"] for u in svc.broadcast_recipients(conn, groups)}
    assert ids(["working"]) == {worker["id"], helper["id"]}
    assert ids(["not_working"]) == {owner["id"], idle["id"]}
    assert ids(["admins"]) == {owner["id"], helper["id"]}
    assert ids(["working", "admins"]) == {worker["id"], helper["id"], owner["id"]}  # helper counted once
    with pytest.raises(svc.ServiceError):
        svc.broadcast_recipients(conn, [])
    row, rec = svc.create_broadcast(conn, owner, ["working", "admins"], "<b>Важно</b>: завтра <i>сбор</i> в 9:00", signed=False)
    assert row["html"] == "<b>Важно</b>: завтра <i>сбор</i> в 9:00" and row["text"].startswith("Важно")
    assert row["signed"] == 0 and len(rec) == 3


def test_sanitizer_keeps_telegram_tags_and_drops_the_rest():
    from app.richtext import sanitize

    html, text = sanitize(
        '<div><strong>Жирный</strong> <em>курсив</em> <u>под</u> <strike>зач</strike></div>'
        '<div><span class="tg-spoiler">секрет</span> <code>код <b>без</b> формата</code></div>'
        '<blockquote>цитата <blockquote>вложенная</blockquote></blockquote>'
        '<a href="https://t.me/x">ссылка</a> <a href="javascript:alert(1)">плохая</a>'
        '<script>alert(1)</script><img src=x onerror=alert(1)><span style="color:red">цвет</span> 5 < 6 & 7'
    )
    assert "<b>Жирный</b>" in html and "<i>курсив</i>" in html and "<u>под</u>" in html and "<s>зач</s>" in html
    assert "<tg-spoiler>секрет</tg-spoiler>" in html
    assert "<code>код без формата</code>" in html
    assert html.count("<blockquote>") == 1
    assert '<a href="https://t.me/x">ссылка</a>' in html and "javascript" not in html and "плохая" in html
    assert "<script" not in html and "<img" not in html and "style" not in html
    assert "5 &lt; 6 &amp; 7" in html
    assert "Жирный" in text and "<" not in text.replace("5 < 6", "")


def test_sanitizer_closes_open_tags_and_rejects_empty():
    from app.richtext import sanitize

    html, _ = sanitize("<b>начало <i>середина")
    assert html == "<b>начало <i>середина</i></b>"
    html, _ = sanitize("<b>a<i>b</b>c</i>")
    assert html == "<b>a<i>b</i></b>c"
    with pytest.raises(svc.ServiceError):
        svc.prepare_message("<b> </b><br>")
    with pytest.raises(svc.ServiceError):
        svc.prepare_message("x" * 4001)


# ---------------------------------------------------------------- plan vs fact shortfall


def test_shortfall_flow_notifies_and_fines(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user()
    s = shift(conn, u, payload={**P, "volume": 20})
    res = svc.set_fact(conn, u, s["id"], 10, svc.new_key(), today=THU)
    assert svc.shortfall(res.shift) == 10 and svc.shortfall_percent(res.shift) == 50
    view = svc.serialize_shift(res.shift, THU)
    assert view["shortfall"] == 10 and view["shortfall_status"] is None

    n = FakeNotifier(conn)
    run(n.fact_result(res.shift))
    to_user = [t for chat, t in n.sent if chat == u["telegram_id"]]
    to_admin = [t for chat, t in n.sent if chat == 999]
    assert to_user and "Не по норме" in to_user[0] and "План 20, факт 10 шт. — 50% (−10 шт.)" in to_user[0]
    assert to_admin and "Назначить штраф" in to_admin[0]

    assert svc.shortfalls(conn)[0]["shift_id"] == s["id"]
    fined = svc.decide_shortfall(conn, owner, s["id"], 700)
    assert fined.changed and fined.shift["shortfall_penalty"] == 700
    assert not svc.decide_shortfall(conn, owner, s["id"], 2000).changed  # first decision wins in the bot
    assert svc.shortfalls(conn) == []
    # the employee can no longer rewrite the fact to dodge the fine
    with pytest.raises(svc.ServiceError) as e:
        svc.set_fact(conn, u, s["id"], 20, svc.new_key(), today=THU)
    assert e.value.code == "fact_locked"

    penalties = svc.user_penalties(conn, u["id"])
    assert penalties[0]["kind"] == "shortfall" and penalties[0]["penalty_amount"] == 700
    st = svc.stats(conn, u["id"], TODAY, THU)
    assert st["totals"]["penalties"] == 700 and st["totals"]["shortfall_volume"] == 10
    log = svc.penalty_log(conn, None, None, None)
    assert log[0]["kind"] == "shortfall" and log[0]["amount"] == 700

    run(n.shortfall_decided(fined.shift, "Саня", 999))
    assert any("Штраф 700 ₽" in t for chat, t in n.sent if chat == u["telegram_id"])

    wb = load_workbook(io.BytesIO(export.build_xlsx(conn, TODAY, THU)))
    header = list(wb["Итоги"].iter_rows(values_only=True))[0]
    row = list(wb["Итоги"].iter_rows(values_only=True))[1]
    assert row[header.index("Недобор, шт.")] == 10 and row[header.index("Штрафы, ₽")] == 700


def test_shortfall_waive_and_admin_correction(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user()
    s = shift(conn, u, payload={**P, "volume": 20})
    svc.set_fact(conn, u, s["id"], 20, svc.new_key(), today=THU)
    with pytest.raises(svc.ServiceError) as e:
        svc.decide_shortfall(conn, owner, s["id"], 500)
    assert e.value.code == "no_shortfall"
    n = FakeNotifier(conn)
    run(n.fact_result(svc.get_shift(conn, s["id"])))
    assert n.sent == []  # plan met: nobody is bothered

    svc.admin_edit_shift(conn, owner, s["id"], {"fact_volume": 15, "reason": "пересчитали"}, svc.new_key())
    waived = svc.decide_shortfall(conn, owner, s["id"], None)
    assert waived.shift["shortfall_status"] == "waived"
    assert svc.penalty_log(conn, None, None, None) == []
    # admin raises the fact back to the plan: the decision is cleared
    fixed = svc.admin_edit_shift(conn, owner, s["id"], {"fact_volume": 20, "reason": "нашли ещё"}, svc.new_key())
    assert fixed.shift["shortfall_status"] is None
