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

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import app_settings, export, notify
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
    await daily("backup", cfg["backup_enabled"], cfg["backup_time"], lambda: backup(conn, notifier))
    if cfg["fact_request_enabled"]:
        if await fact_requests(conn, notifier, now, cfg["fact_request_delay"]):
            ran.append("fact_request")
    return ran


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


def fact_keyboard(shift: dict) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"✅ {shift['volume']} шт., как план", callback_data=f"fact:{shift['id']}:{shift['volume']}")],
            [InlineKeyboardButton(text="✏️ Другое число", callback_data=f"factc:{shift['id']}")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def fact_requests(conn, notifier: Notifier, now: datetime, delay_min: int) -> int:
    today = now.date()
    rows = [
        dict(r)
        for r in conn.execute(
            f"""SELECT s.*, u.telegram_id FROM shifts s JOIN users u ON u.id = s.user_id
                WHERE s.status IN {svc.ACTIVE} AND s.fact_volume IS NULL AND s.fact_asked_at IS NULL
                  AND s.shift_date BETWEEN ? AND ? AND u.status = 'approved'""",
            ((today - timedelta(days=1)).isoformat(), today.isoformat()),
        )
    ]
    sent = 0
    for s in rows:
        day = date.fromisoformat(s["shift_date"])
        end = datetime.combine(day, time.fromisoformat(s["end_time"]), tzinfo=now.tzinfo) + timedelta(minutes=delay_min)
        if now < end:
            continue
        conn.execute("UPDATE shifts SET fact_asked_at = ? WHERE id = ?", (now_iso(), s["id"]))
        sent += await notifier.send(
            s["telegram_id"],
            f"Смена {ddmm_wd(day)} {s['start_time']}–{s['end_time']} на точке «{escape(s['location'])}» закончилась.\n"
            f"Сколько сделал? План был {s['volume']} шт.",
            fact_keyboard(s),
        )
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
