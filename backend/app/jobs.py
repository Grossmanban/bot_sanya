"""Scheduled messages: reminders, auto roster, fact requests, weekly report, DB backup.

Runs inside the main process. Every daily/weekly job is claimed in job_runs before it
sends anything, so a restart never sends it twice; a job only fires within a few hours
after its time, so a server that was down overnight doesn't spam stale messages.
"""

import asyncio
import logging
import sqlite3
import tempfile
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path

from aiogram.types import InlineKeyboardMarkup

from . import alice, app_settings, events, export, goals, groups, notify, recruiting, reports, violations
from . import services as svc
from .config import settings
from .db import transaction
from .notify import Notifier
from .timeutil import ddmm_wd, now_iso, now_local, week_dates

log = logging.getLogger(__name__)
WINDOW = timedelta(hours=3)


def _due(now: datetime, hhmm: str) -> bool:
    t = time.fromisoformat(hhmm)
    start = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    return start <= now < start + WINDOW


def claim(conn: sqlite3.Connection, job: str, run_date: date) -> bool:
    with transaction(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO job_runs (job, run_date, ran_at) VALUES (?, ?, ?)", (job, run_date.isoformat(), now_iso())
        )
    return cur.rowcount == 1


def _open_app() -> InlineKeyboardMarkup | None:
    from .bot import open_app_keyboard  # bot imports notify; import lazily to keep modules independent

    return open_app_keyboard()


async def tick(conn: sqlite3.Connection, notifier: Notifier, now: datetime | None = None) -> list[str]:
    """Run whatever is due. Returns the names of jobs that ran (for logs and tests)."""
    now = now or now_local()
    today = now.date()
    cfg = app_settings.get_all(conn)
    ran: list[str] = []

    async def daily(name: str, enabled: bool, at: str, fn, weekday: int | None = None):
        if not enabled or (weekday is not None and now.weekday() != weekday) or not _due(now, at):
            return
        if claim(conn, name, today):
            ran.append(name)
            try:
                await fn()
            except Exception:
                log.exception("job %s failed", name)

    await daily("eve_reminder", cfg["eve_reminder_enabled"], cfg["eve_reminder_time"],
                lambda: eve_reminders(conn, notifier, today + timedelta(days=1)))
    await daily("auto_raspred", cfg["auto_raspred_enabled"], cfg["auto_raspred_time"],
                lambda: auto_raspred(conn, notifier, today + timedelta(days=1)))
    await daily("week_reminder", cfg["week_reminder_enabled"], cfg["week_reminder_time"],
                lambda: week_reminders(conn, notifier, today), weekday=cfg["week_reminder_weekday"])
    await daily("weekly_report", cfg["weekly_report_enabled"], cfg["weekly_report_time"],
                lambda: weekly_report(conn, notifier, today), weekday=0)
    common = cfg["weekly_attendance_quota"] if cfg["attendance_check_enabled"] else 0
    await daily("attendance_check", common > 0 or violations.has_personal_norms(conn),
                cfg["attendance_check_time"], lambda: attendance_check(conn, notifier, today, common), weekday=0)
    await daily("daily_mark_reminder", cfg["daily_mark_enabled"], cfg["daily_mark_reminder_time"],
                lambda: notifier.mark_reminders(violations.unmarked(conn, today + timedelta(days=1)), today + timedelta(days=1),
                                                cfg["daily_mark_deadline"]))
    await daily("daily_mark_check", cfg["daily_mark_enabled"], cfg["daily_mark_deadline"],
                lambda: unmarked_check(conn, notifier, today + timedelta(days=1)))
    await daily("backup", cfg["backup_enabled"], cfg["backup_time"], lambda: backup(conn, notifier))
    await daily("global_actual_evening", cfg["global_actual_evening_enabled"], cfg["global_actual_evening_time"],
                lambda: notifier.global_actual_reminder(today, morning=False))
    await daily("global_actual_morning", cfg["global_actual_morning_enabled"], cfg["global_actual_morning_time"],
                lambda: notifier.global_actual_reminder(today - timedelta(days=1), morning=True))
    await daily("report_digest", cfg["report_digest_enabled"], cfg["report_digest_time"],
                lambda: report_digest(conn, notifier, today, cfg["report_digest_to"]))
    since = reports_since(conn, today)
    if cfg["fact_request_enabled"] and await fact_requests(conn, notifier, now, cfg["fact_request_delay"], since):
        ran.append("fact_request")
    if cfg["report_reminder_enabled"] and await report_reminders(conn, notifier, now, cfg["report_reminder_lead"], since):
        ran.append("report_reminder")
    if await no_shows(conn, notifier, now, since):
        ran.append("no_show")
    # ТЗ 28.10 §14: an urgent request for a day that has passed can't be decided any more — it closes by itself
    for shift in svc.expire_urgent(conn, today):
        ran.append("urgent_expired")
        await notifier.urgent_expired(shift)
    for event in events.finish_due(conn, now):
        ran.append("event_finished")
        if event["visibility"] != "hidden":  # a hidden event finishes quietly
            await notifier.event_finished(event)
    # recruiting: training reminders and trainees who reached a stage (each once)
    for application, kind in recruiting.training_reminders(conn, now):
        ran.append("training_reminder")
        await notifier.training_reminder(application, kind)
    for application in recruiting.reached(conn):
        ran.append("stage_reached")
        await notifier.stage_reached(application)
    # ТЗ 27.10 §26–27: the daily check of both groups, and personal invitations when someone reaches a group
    if notifier.bot is not None and any(groups.chat(conn, k) for k in groups.KINDS):
        await daily("groups_check", True, cfg["groups_check_time"], lambda: groups.check(conn, notifier.bot))
        if _every(conn, "groups_auto", now, minutes=10):
            for _ in await groups.auto_invites(conn, notifier.bot, notifier.group_invited):
                ran.append("group_invite")
    # ТЗ 27.10 §36: an execution whose photos didn't reach the group is sent again (never left as «sent»)
    if notifier.bot is not None and _every(conn, "alice_retry", now, minutes=10):
        for task_id in alice.to_retry(conn):
            task = await alice.deliver(conn, notifier.bot, task_id, notifier)
            if task and task["status"] == "moderation":
                ran.append("alice_sent")
    done = goals.check_completions(conn, today, now)
    if done:
        ran.append("goals")
        await notifier.goals_completed(done)
    return ran


def _every(conn, name: str, now: datetime, minutes: int) -> bool:
    """A job that runs at most every N minutes (kept in the database, so restarts don't rerun it)."""
    last = app_settings.get_raw(conn, f"last:{name}")
    if last and datetime.fromisoformat(last) > now - timedelta(minutes=minutes):
        return False
    app_settings.set_raw(conn, f"last:{name}", now.isoformat(timespec="seconds"))
    return True


def reports_since(conn, today: date) -> date:
    """Reports became mandatory on the day this version first ran: older shifts never turn into no-shows."""
    raw = app_settings.get_raw(conn, "reports_since")
    if raw is None:
        app_settings.set_raw(conn, "reports_since", today.isoformat())
        return today
    return date.fromisoformat(raw)


async def run_scheduler(conn: sqlite3.Connection, notifier: Notifier, interval: int = 30) -> None:
    log.info("scheduler started")
    while True:
        try:
            ran = await tick(conn, notifier)
            if ran:
                log.info("jobs ran: %s", ", ".join(ran))
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------- jobs


async def eve_reminders(conn, notifier: Notifier, day: date) -> int:
    rows = [
        dict(r)
        for r in conn.execute(
            f"""SELECT s.*, u.telegram_id, u.first_name, u.last_name FROM shifts s JOIN users u ON u.id = s.user_id
                WHERE s.shift_date = ? AND s.status IN {svc.ACTIVE} AND s.reminded_at IS NULL
                  AND u.status = 'approved' AND u.notify_reminders = 1""",
            (day.isoformat(),),
        )
    ]
    everyone = svc.active_shifts_on(conn, day)
    sent = 0
    for s in rows:
        conn.execute("UPDATE shifts SET reminded_at = ? WHERE id = ?", (now_iso(), s["id"]))
        mates = [m for m in everyone if m["id"] != s["id"] and (m["location"] or "").casefold() == (s["location"] or "").casefold()]
        text = (
            f"🔔 Завтра, {ddmm_wd(day)}, у тебя выход\n"
            f"<b>{s['start_time']}–{s['end_time']}</b>, точка «{escape(s['location'])}», план {s['volume']} шт."
        )
        if mates:
            text += "\nНа точке также: " + ", ".join(f"{escape(m['first_name'])} {m['start_time']}–{m['end_time']}" for m in mates)
        sent += await notifier.send(s["telegram_id"], text, _open_app())
    return sent


async def auto_raspred(conn, notifier: Notifier, day: date) -> None:
    data = svc.raspred(conn, day)
    await notifier.to_admins(escape(data["text"]))


async def week_reminders(conn, notifier: Notifier, today: date) -> int:
    nxt = week_dates(1, today)
    users = [
        dict(r)
        for r in conn.execute(
            """SELECT u.* FROM users u
               WHERE u.status = 'approved' AND u.work_status = 'working' AND u.notify_reminders = 1
                 AND NOT EXISTS (SELECT 1 FROM shifts s WHERE s.user_id = u.id AND s.shift_date BETWEEN ? AND ?
                                 AND s.status NOT IN ('withdrawn','removed'))""",
            (nxt[0].isoformat(), nxt[-1].isoformat()),
        )
    ]
    sent = 0
    for u in users:
        sent += await notifier.send(
            u["telegram_id"],
            f"🗓 Отметь график на следующую неделю ({nxt[0]:%d.%m}–{nxt[-1]:%d.%m}): когда выходишь и когда выходной.",
            _open_app(),
        )
    return sent


def _open_shifts(conn, since: date, extra: str) -> list[dict]:
    """Held shifts since `since` that still have no report, joined with the employee."""
    return [
        dict(r)
        for r in conn.execute(
            f"""SELECT s.*, u.telegram_id FROM shifts s JOIN users u ON u.id = s.user_id
                WHERE s.status IN {svc.ACTIVE} AND s.start_time IS NOT NULL AND s.report_state IS NULL
                  AND s.shift_date >= ? AND u.status = 'approved' AND u.account_state != 'blocked' {extra}""",
            (since.isoformat(),),
        )
    ]


async def fact_requests(conn, notifier: Notifier, now: datetime, delay_min: int, since: date) -> int:
    """ТЗ 3.2: after the shift the bot asks for the report (fact + screenshot)."""
    sent = 0
    for s in _open_shifts(conn, max(since, now.date() - timedelta(days=1)), "AND s.fact_asked_at IS NULL"):
        if now < reports.shift_end(s) + timedelta(minutes=delay_min) or now >= reports.deadline(s):
            continue
        conn.execute("UPDATE shifts SET fact_asked_at = ? WHERE id = ?", (now_iso(), s["id"]))
        sent += await notifier.send(
            s["telegram_id"],
            f"Смена {notify.shift_place(s)} закончилась.\nНапиши фактический объём и пришли скриншот результата. "
            f"План был {s['volume']} шт. Сдать до {reports.deadline(s):%H:%M %d.%m}.",
            notify.report_keyboard(s),
        )
    return sent


async def report_reminders(conn, notifier: Notifier, now: datetime, lead_min: int, since: date) -> int:
    """ТЗ 3.3 step 1: a warning some time before the deadline (lead set by admins)."""
    sent = 0
    for s in _open_shifts(conn, since, "AND s.report_reminded_at IS NULL"):
        due = reports.deadline(s)
        if not (due - timedelta(minutes=lead_min) <= now < due) or now < reports.shift_end(s):
            continue
        conn.execute("UPDATE shifts SET report_reminded_at = ? WHERE id = ?", (now_iso(), s["id"]))
        sent += await notifier.report_reminder(s, f"{due:%H:%M}")
    return sent


async def no_shows(conn, notifier: Notifier, now: datetime, since: date) -> int:
    """ТЗ 3.3 step 2: no report by the deadline → no_show, to manual review with notifications."""
    marked = 0
    for s in _open_shifts(conn, since, ""):
        if now < reports.deadline(s):
            continue
        shift = reports.mark_no_show(conn, s["id"], now)
        if shift:
            marked += 1
            await notifier.no_show(shift)
    return marked


async def report_digest(conn, notifier: Notifier, today: date, audience: str) -> int:
    """Admins learn who hasn't reported for today yet and what waits for review.
    audience: owners | admins (owners + admins) | staff (+ trainers, each about their network)."""
    missing = reports.queue(conn, "missing", today)
    waiting = reports.queue(conn, "review")
    no_show = reports.queue(conn, "no_show")
    if not (missing or waiting or no_show):
        return 0
    recipients = set(svc.owner_telegram_ids()) if audience == "owners" else set(svc.admin_telegram_ids(conn))
    trainers = []
    if audience == "staff":
        trainers = [dict(r) for r in conn.execute(
            """SELECT * FROM users WHERE role IN ('trainer', 'instructor') AND status = 'approved'
               AND account_state != 'blocked' AND network_id IS NOT NULL""")]

    def text(only: set[int] | None) -> str | None:
        m = [x for x in missing if only is None or x["user_id"] in only]
        w = [x for x in waiting if only is None or x["user_id"] in only]
        n = [x for x in no_show if only is None or x["user_id"] in only]
        if not (m or w or n):
            return None
        lines = [f"📋 <b>Отчёты за {ddmm_wd(today)}</b>"]
        if m:
            lines.append(f"\nНе сдали ({len(m)}):")
            lines += [f"• {escape(x['name'])} — {x['start_time']}–{x['end_time']}, до {x['deadline'][11:16]}" for x in m]
        if w:
            lines.append(f"\n📝 Ждут проверки: {len(w)}")
        if n:
            lines.append(f"🚫 Неявки без решения: {len(n)}")
        return "\n".join(lines)

    sent = 0
    full = text(None)
    for chat in recipients:
        sent += await notifier.send(chat, full, _open_app())
    for t in trainers:
        own = {r[0] for r in conn.execute(
            """SELECT u.id FROM users u JOIN networks n ON n.id = u.network_id
               WHERE n.trainer_id = ? AND n.status = 'active' AND u.id != ?""", (t["id"], t["id"]))
               } if t["role"] == "trainer" else {r[0] for r in conn.execute("SELECT id FROM users WHERE instructor_id = ?", (t["id"],))}
        body = text(own)
        if body and t["telegram_id"] not in recipients:
            sent += await notifier.send(t["telegram_id"], body, _open_app())
    return sent


def weekly_summary(conn, today: date) -> tuple[str, date, date]:
    days = week_dates(-1, today)
    start, end = days[0], days[-1]
    lines, plan_total, fact_total = [], 0, 0
    users = conn.execute(
        "SELECT DISTINCT user_id FROM shifts WHERE shift_date BETWEEN ? AND ? AND status NOT IN ('withdrawn','removed')", (start.isoformat(), end.isoformat())
    ).fetchall()
    for (user_id,) in users:
        st = svc.stats(conn, user_id, start, end)
        t = st["totals"]
        if not t["active_shifts"] and not t["penalties"]:
            continue
        plan_total += t["active_volume"]
        fact_total += t["fact_volume"]
        line = f"{escape(st['user']['name'])}: {t['active_shifts']} {notify.plural(t['active_shifts'], 'выход', 'выхода', 'выходов')}, план {t['active_volume']}, факт {t['fact_volume']} шт."
        if t["fact_missing"]:
            line += f" (без факта: {t['fact_missing']})"
        if t["shortfall_shifts"]:
            line += f", ⚠️ не по норме: {t['shortfall_shifts']} (−{t['shortfall_volume']} шт.)"
        if t["penalties"]:
            line += f", штрафы {t['penalties']} ₽"
        lines.append(line)
    head = f"📊 <b>Итоги недели {start:%d.%m}–{end:%d.%m}</b>\n"
    if not lines:
        return head + "Выходов не было.", start, end
    return head + "\n".join(lines) + f"\n\nВсего: план {plan_total}, факт {fact_total} шт.", start, end


async def weekly_report(conn, notifier: Notifier, today: date) -> None:
    text, start, end = weekly_summary(conn, today)
    data = export.build_xlsx(conn, start, end)
    for admin_id in svc.admin_telegram_ids(conn):
        await notifier.send(admin_id, text)
        await notifier.send_document(admin_id, data, export.export_filename(start, end))


async def attendance_check(conn, notifier: Notifier, today: date, common: int) -> int:
    """ТЗ 12.4: Monday — people below their weekly norm of work days get a violation to decide on."""
    created = violations.scan(conn, common, today)
    if created:
        await notifier.violations_found(created)
    return len(created)


async def unmarked_check(conn, notifier: Notifier, day: date) -> int:
    """At the deadline: «Работаю» without a shift or a day off tomorrow → a violation to decide on."""
    created = violations.scan_unmarked(conn, day)
    if created:
        await notifier.violations_found(created)
    return len(created)


async def backup(conn, notifier: Notifier) -> None:
    if settings.db_path == ":memory:" or not settings.admin_ids:
        return
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / f"bot-{now_local():%Y%m%d}.db"
        dst = sqlite3.connect(target)
        with dst:
            conn.backup(dst)
        dst.close()
        data = target.read_bytes()
    for owner in sorted(settings.admin_ids):
        await notifier.send_document(owner, data, target.name, "💾 Резервная копия базы. Храните этот файл в надёжном месте.")
