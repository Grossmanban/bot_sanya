"""Grouped staff notifications (19.11): service notices — «встал в график», «взял выходной» and the like —
may be collected into one digest per chat instead of pinging the owner and the managers every time.

The settings (Админ → Настройки → «Служебные уведомления»):
• «Группировать в сводку» — the notices are queued and the scheduler sends one message per chat, its
  items grouped by category (график, отчёты, онбординг…), at most once per `staff_digest_every_min`;
• «Без звука» — the notices come without sound and vibration (works in both modes);
• «Тихие часы» — during the night window nothing is flushed: the digest is only accumulated and comes
  right after they end (in the morning), so a night cancellation never wakes anyone up.

Only informational notices without decision buttons are grouped: cards with buttons («Разрешить/
Запретить», штраф-пресеты, заявки на регистрацию) always go out straight away — they need a decision
now. Personal messages to a person (fines, reminders, broadcasts) are never touched.
"""

import sqlite3
from datetime import datetime, timedelta

from . import app_settings
from . import services as svc  # the clock: tests patch it, so stamps and ageing agree

# the section order and titles inside a digest message (unknown categories fall under «staff»)
CATEGORIES = {
    "schedule": "🗓 График",
    "reports": "📝 Отчёты",
    "money": "💰 Деньги",
    "staff": "👥 Команда",
    "onboarding": "🎓 Онбординг",
    "fact": "📊 Общий факт",
}

MAX_MESSAGE = 3900  # Telegram's cap with some slack; a long digest continues in the next message
MAX_ITEM = 1500  # one notice is capped, so a runaway line can't blow the whole message
AGE = timedelta(days=2)  # queued notices older than this are dropped without sending (stale news)


def on(conn: sqlite3.Connection) -> bool:
    return bool(app_settings.get_all(conn)["staff_digest_enabled"])


def silent(conn: sqlite3.Connection) -> bool:
    return bool(app_settings.get_all(conn)["staff_digest_silent"])


def queue(conn: sqlite3.Connection, chat_id: int, category: str, text: str) -> None:
    """One notice for one chat; the scheduler flushes it together with the chat's other notices."""
    conn.execute("INSERT INTO notify_queue (chat_id, category, text, created_at) VALUES (?, ?, ?, ?)",
                 (int(chat_id), category if category in CATEGORIES else "staff", text, svc.now_iso()))


def text(conn: sqlite3.Connection, full: str, compact: str) -> str:
    """Which wording goes out now: grouping keeps the digest tidy with the compact one-liner, instant mode
    sends the full card exactly as before."""
    return compact if on(conn) else full


def quiet_now(conn: sqlite3.Connection, now: datetime | None = None) -> bool:
    """True inside the night window («Тихие часы»): the digest waits until it ends. Handles a window
    that wraps over midnight (22:00 → 09:00)."""
    cfg = app_settings.get_all(conn)
    if not cfg["staff_digest_quiet"]:
        return False
    now = now or svc.now_local()

    def minutes(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    start, end = minutes(cfg["staff_digest_quiet_from"]), minutes(cfg["staff_digest_quiet_to"])
    now_min = now.hour * 60 + now.minute
    return (now_min >= start or now_min < end) if start > end else start <= now_min < end


async def flush(conn: sqlite3.Connection, notifier, now: datetime | None = None) -> int:
    """Send every chat one digest message with all its queued notices grouped by category. The scheduler
    calls this once per the configured interval, never during the quiet hours. Returns the number of
    chats messaged (for the scheduler's log); the queue is drained either way — a failed send is never
    retried with stale items."""
    if not on(conn):
        conn.execute("DELETE FROM notify_queue")  # grouping was switched off: leftovers are dropped, not dumped later
        return 0
    if quiet_now(conn, now):
        return 0
    now = now or svc.now_local()
    # stale news are dropped without sending (the queue clock, so tests passing a fixed `now` agree with it)
    conn.execute("DELETE FROM notify_queue WHERE created_at < ?", ((svc.now_local() - AGE).isoformat(timespec="seconds"),))
    rows = [dict(r) for r in conn.execute("SELECT * FROM notify_queue ORDER BY id")]
    if not rows:
        return 0
    by_chat: dict[int, dict[str, list[str]]] = {}
    for r in rows:
        by_chat.setdefault(r["chat_id"], {}).setdefault(r["category"], []).append(r["text"])
    stamp = now.strftime("%d.%m %H:%M")
    for chat, sections in by_chat.items():
        # the fixed category order; long digests continue in the next message, the section is named again there
        head = f"🔔 <b>Сводка</b> — {stamp} МСК"
        chunks: list[str] = []
        current, last_category = head, None
        for category in CATEGORIES:
            for raw in sections.get(category, []):
                item = raw if len(raw) <= MAX_ITEM else raw[:MAX_ITEM - 1] + "…"
                title = f"\n{CATEGORIES[category]}:" if category != last_category else ""
                if len(current) + len(title) + len(item) + 4 > MAX_MESSAGE:
                    chunks.append(current)
                    current, last_category = f"🔔 <b>Сводка</b> (продолжение) — {stamp} МСК", None
                    title = f"\n{CATEGORIES[category]}:"
                current += f"{title}\n• {item}"
                last_category = category
        chunks.append(current)
        for chunk in chunks:
            await notifier.send(chat, chunk, silent=silent(conn))
        conn.execute("DELETE FROM notify_queue WHERE chat_id = ?", (chat,))
    return len(by_chat)
