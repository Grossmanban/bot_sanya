"""Telegram notifications. Messages are built from the DB at send time, so totals are current."""

import asyncio
import logging
import re
import sqlite3
from datetime import date
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from . import app_settings, day_actual, finance, media
from . import permissions as perms
from . import services as svc
from .config import settings
from .timeutil import ddmm, ddmm_wd, now_local

log = logging.getLogger(__name__)

PENALTY_PRESETS = (500, 1000, 2000)


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def employees(n: int) -> str:
    return f"{n} {plural(n, 'сотрудник', 'сотрудника', 'сотрудников')}"


def person_tag(user: dict) -> str:
    """#Иван_Петров — lets the archive be filtered by person."""
    return "#" + re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "_", svc.full_name(user)).strip("_")


def who(user: dict) -> str:
    name = escape(svc.full_name(user))
    if user.get("username"):
        return f"@{escape(user['username'])}, {name}"
    # ТЗ 27.10 §11: the Telegram ID is the identifier; the name opens the profile wherever the client allows it
    return f'<a href="tg://user?id={user["telegram_id"]}">{name}</a> (без username, ID <code>{user["telegram_id"]}</code>)'


def prize_line(item: dict) -> str:
    """«Подарок — Наушники» / just one of them."""
    return " — ".join(x for x in (item.get("prize_type"), item.get("prize")) if x)


CHANGE_LABELS = {"starts_at": "Старт", "ends_at": "Финиш", "date_from": "Начало", "date_to": "Конец",
                 "target": "Цель", "max_winners": "Победителей", "max_attempts": "Попыток",
                 "prize": "Приз", "prize_type": "Тип приза"}


def _shown(field: str, value) -> str:
    if value is None or value == "":
        return "—"
    if field in ("starts_at", "ends_at"):
        return f"{value[8:10]}.{value[5:7]} {value[11:16]}"
    if field in ("date_from", "date_to"):
        return f"{value[8:10]}.{value[5:7]}"
    if field == "target":
        return f"{value} шт."
    return str(value)


def change_lines(old: dict | None, new: dict, fields: list[str]) -> list[str]:
    """ТЗ №20: what exactly changed, «Финиш: 20.10 18:00 → 21.10 18:00»."""
    return [f"• {CHANGE_LABELS.get(f, f)}: {escape(_shown(f, (old or {}).get(f)))} → <b>{escape(_shown(f, new.get(f)))}</b>"
            for f in fields]


def handle(user: dict) -> str:
    if user.get("username"):
        return f"@{escape(user['username'])}"
    return f'<a href="tg://user?id={user["telegram_id"]}">{escape(svc.full_name(user))}</a>'


def _day(shift: dict) -> date:
    return date.fromisoformat(shift["shift_date"])


def _totals(conn, day: date) -> str:
    count, volume = svc.day_totals(conn, day)
    return f"{employees(count)}, {volume} шт."


# ---------------------------------------------------------------- message builders


def registration_text(user: dict, invite: dict | None = None) -> str:
    username = f"@{escape(user['username'])}" if user["username"] else "нет"
    via = ""
    if invite is not None:  # ТЗ 27.10 §6: came with an owner's invitation
        via = "\nПо приглашению владельца" + (f" «{escape(invite['label'])}»" if invite["label"] else "")
    return (
        "📝 <b>Новая заявка на регистрацию</b>" + via + "\n"
        f"ФИО: {escape(svc.full_name(user))}\n"
        f"Username: {username}\n"
        f"Telegram ID: <code>{user['telegram_id']}</code>\n"
        f'Профиль: <a href="tg://user?id={user["telegram_id"]}">открыть</a>\n'
        f"Подана: {user['created_at'][:16].replace('T', ' ')}"
    )


def registration_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"reg:ok:{user_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reg:no:{user_id}"),
    ]])


def shift_created_text(conn, user: dict, shift: dict) -> str:
    return (
        "🆕 <b>Новая запись в график</b>\n"
        f"{who(user)}\n"
        f"Дата: {ddmm_wd(_day(shift))}\n"
        f"Время: {shift['start_time']}–{shift['end_time']}\n"
        f"Объём: {shift['volume']} шт.\n"
        f"Точка: {escape(shift['location'])}\n"
        f"Итого на день: {_totals(conn, _day(shift))}"
    )


def urgent_text(user: dict, shift: dict) -> str:
    return (
        f"⚡ <b>Срочно!</b> {handle(user)} просит выйти СЕГОДНЯ:\n"
        f"ФИО: {escape(svc.full_name(user))}\n"
        f"Время: {shift['start_time']}–{shift['end_time']}\n"
        f"Объём: {shift['volume']} шт.\n"
        f"Точка: {escape(shift['location'])}"
    )


def urgent_keyboard(shift_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Разрешить", callback_data=f"urg:ok:{shift_id}"),
        InlineKeyboardButton(text="⛔ Запретить", callback_data=f"urg:no:{shift_id}"),
    ]])


def cancelled_text(conn, user: dict, shift: dict) -> str:
    return (
        "❌ <b>Отмена смены — взял(а) выходной</b>\n"
        f"{who(user)}\n"
        f"Дата: {ddmm_wd(_day(shift))}\n"
        f"Было: {shift['start_time']}–{shift['end_time']}, {shift['volume']} шт., {escape(shift['location'] or '')}\n"
        f"Осталось на день: {_totals(conn, _day(shift))}"
    )


def restored_text(conn, user: dict, shift: dict) -> str:
    return (
        "♻️ <b>Восстановление</b>\n"
        f"{who(user)} вернулся в график на {ddmm_wd(_day(shift))}\n"
        f"Время: {shift['start_time']}–{shift['end_time']}\n"
        f"Новый объём: {shift['volume']} шт.\n"
        f"Точка: {escape(shift['location'])}\n"
        f"Итого на день: {_totals(conn, _day(shift))}"
    )


def final_cancel_text(conn, user: dict, shift: dict) -> str:
    return (
        "🔒 <b>ФИНАЛЬНАЯ ОТМЕНА!</b>\n"
        f"{who(user)}\n"
        f"Дата: {ddmm_wd(_day(shift))}\n"
        f"Было: {shift['start_time']}–{shift['end_time']}, {shift['volume']} шт.\n"
        f"Осталось на день: {_totals(conn, _day(shift))}\n\n"
        f"Укажите сумму штрафа для сотрудника {escape(svc.full_name(user))}:"
    )


def penalty_keyboard(shift_id: int) -> InlineKeyboardMarkup:
    presets = [InlineKeyboardButton(text=f"{a} ₽", callback_data=f"pen:{shift_id}:{a}") for a in PENALTY_PRESETS]
    return InlineKeyboardMarkup(inline_keyboard=[
        presets,
        [InlineKeyboardButton(text="Своя сумма…", callback_data=f"pen:{shift_id}:custom")],
    ])


def edited_text(user: dict, shift: dict, old: dict) -> str:
    return (
        f"✏️ {handle(user)} изменил смену на {ddmm_wd(_day(shift))}:\n"
        f"Было: {old['start_time']}–{old['end_time']}, {old['volume']} шт., {escape(old['location'])}\n"
        f"Стало: {shift['start_time']}–{shift['end_time']}, {shift['volume']} шт., {escape(shift['location'])}"
    )


def admin_edited_text(admin: dict, user: dict, shift: dict, reason: str) -> str:
    if shift.get("status") == "removed":
        what = "удалил запись"
    else:
        what = f"изменил смену ({svc_status_ru(shift['status'])})"
    return (
        f"⚙️ Админ {handle(admin)} {what} сотрудника {who(user)} на {ddmm_wd(_day(shift))}\n"
        f"Причина: {escape(reason)}"
    )


STATUS_RU = {
    "active": "выходит",
    "restored": "выходит (восстановлена)",
    "cancelled": "отменена",
    "finally_cancelled": "окончательная отмена",
    "awaiting_approval": "ожидает решения",
    "rejected_by_admin": "отклонена админом",
    "pre_declared_off": "не выходит",
    "removed": "удалена",
}


def svc_status_ru(status: str) -> str:
    return STATUS_RU.get(status, status)


def _open_admin() -> InlineKeyboardMarkup | None:
    if not settings.webapp_url.startswith("https://"):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚖️ Открыть «Дисциплину»", web_app=WebAppInfo(url=settings.webapp_url))
    ]])


def _open_schedule() -> InlineKeyboardMarkup | None:
    if not settings.webapp_url.startswith("https://"):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗓 Отметить график", web_app=WebAppInfo(url=settings.webapp_url))
    ]])


def name_handle(user: dict) -> str:
    """Иван Петров (@ivan_p) — for the archive templates of ТЗ 12.4."""
    name = escape(svc.full_name(user))
    return f"{name} (@{escape(user['username'])})" if user.get("username") else f'<a href="tg://user?id={user["telegram_id"]}">{name}</a>'


def stamp() -> str:
    now = now_local()
    zone = "МСК" if settings.timezone == "Europe/Moscow" else now.strftime("%Z")
    return f"{now:%d.%m.%Y %H:%M} {zone}"


def files_word(n: int) -> str:
    return f"{n} {plural(n, 'файл', 'файла', 'файлов')}"


def violation_text(v: dict, user: dict, tx: dict | None) -> str:
    """The employee's message (ТЗ 12.4 Б): fine or waived."""
    from . import violations

    files = violations.media_ids(v)
    req, act = v["required_days"], v["actual_days"]
    unmarked = v.get("kind") == "unmarked"
    stats = (f"📊 <b>Причина:</b> {escape(violations.what(v))}" if unmarked else
             f"📊 <b>Статистика:</b>\n• Требование: {req} {violations.days_word(req)}\n"
             f"• Факт: {act} {violations.days_word(act)}")
    head = (f"👤 Сотрудник: {escape(svc.full_name(user))}\n📅 Период: {violations.period(v)}\n\n")
    comment = f"📝 Комментарий администратора: «{escape(v['resolution_comment'])}»" if v["resolution_comment"] else ""
    if v["status"] == "penalized":
        missed = max(0, req - act)
        return ("⚠️ <b>НАРУШЕНИЕ ГРАФИКА | ШТРАФ НАЗНАЧЕН</b>\n\n" + head + stats +
                ("\n\n" if unmarked else f"\n• Пропущено: {missed} {violations.days_word(missed)}\n\n") +
                f"💰 Штраф: <b>{finance.units_text(tx) or 'назначен'}</b>\n{comment}\n"
                f"📎 Доказательства: {files_word(len(files)) + ' — ниже' if files else 'нет'}\n\n"
                "ℹ️ Ваш текущий баланс обновлён. Пожалуйста, ознакомьтесь с правилами выхода на работу.")
    return ("✅ <b>НАРУШЕНИЕ ГРАФИКА | СНЯТО АДМИНИСТРАЦИЕЙ</b>\n\n" + head + stats +
            f"\n\n💬 Решение: штраф не начислен.\n" + (f"{comment}\n" if comment else "") +
            f"📎 Документы: {files_word(len(files)) + ' — ниже' if files else 'нет'}\n\n"
            "ℹ️ Спасибо за своевременное информирование. Соблюдайте график в будущем.")


def violation_archive_text(v: dict, user: dict, resolver: dict, tx: dict | None, approver: dict | None = None) -> str:
    """The audit copy for the archive group (ТЗ 12.4 В)."""
    from . import violations

    files = violations.media_ids(v)
    role = perms.ROLE_TITLES[perms.role_of(resolver)]
    reason = violations.what(v)
    media_line = f"📎 Медиа: {files_word(len(files)) + ' — ниже' if files else 'нет'}"
    if v["status"] == "penalized":
        approved = f"\n✅ Одобрил: {name_handle(approver)}" if approver else ""
        return (f"❌ <b>ШТРАФ ЗА ПРОПУСК ГРАФИКА</b>\n👤 Кто: {name_handle(user)}\n"
                f"👮‍♂️ Назначил: {role} {name_handle(resolver)}{approved}\n💰 Сумма: -{finance.staff_text(tx)}\n"
                f"📝 Причина: {escape(reason)}. {escape(v['resolution_comment'] or '')}\n"
                f"{media_line}\n⏱ Время: {stamp()}")
    return (f"☑️ <b>НАРУШЕНИЕ ПОГАШЕНО (БЕЗ ШТРАФА)</b>\n👤 Кто: {name_handle(user)}\n"
            f"👮‍♂️ Решил: {role} {name_handle(resolver)}\n📊 Нарушение: {escape(reason)}\n"
            f"💬 Основание: {escape(v['resolution_comment'] or 'без указания причины')}\n{media_line}\n⏱ Время: {stamp()}")


# ---------------------------------------------------------------- sender


def review_keyboard(shift_id: int, can_fine: bool = True) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✅ Принять", callback_data=f"rv:a:{shift_id}"),
             InlineKeyboardButton(text="🔄 На исправление", callback_data=f"rv:r:{shift_id}")]]
    if can_fine:
        rows.append([InlineKeyboardButton(text=f"⚠️ {a} ед.", callback_data=f"rv:f:{shift_id}:{a}") for a in finance.UNIT_PRESETS])
        rows.append([InlineKeyboardButton(text="⚠️ Штраф — своё число единиц", callback_data=f"rv:f:{shift_id}:c")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ack_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Я ознакомился", callback_data=f"ack:{broadcast_id}")]])


def broadcast_keyboard(broadcast_id: int, ack: bool, reply: bool) -> InlineKeyboardMarkup | None:
    """«✅ Я ознакомился» and/or «✍️ Ответить» under a broadcast (none — no buttons)."""
    row = []
    if ack:
        row.append(InlineKeyboardButton(text="✅ Я ознакомился", callback_data=f"ack:{broadcast_id}"))
    if reply:
        row.append(InlineKeyboardButton(text="✍️ Ответить", callback_data=f"brr:{broadcast_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[row]) if row else None


def reply_text(broadcast: dict, user: dict, text: str | None, network: str | None) -> str:
    """What the author gets: whose reply, to which broadcast, a link to the person's Telegram."""
    quote = (broadcast.get("text") or "").replace("\n", " ").strip()
    quote = quote[:80] + ("…" if len(quote) > 80 else "")
    sent = broadcast["created_at"]
    contact = ", ".join(x for x in (f"@{user['username']}" if user.get("username") else None,
                                    f"ID {user['telegram_id']}", f"сеть «{network}»" if network else None) if x)
    head = (f"💬 <b>Ответ на рассылку</b> от {sent[8:10]}.{sent[5:7]} {sent[11:16]}\n<i>«{escape(quote)}»</i>\n\n"
            f"👤 <a href=\"tg://user?id={user['telegram_id']}\">{escape(svc.full_name(user))}</a> ({escape(contact)})")
    return head + (f"\n\n{escape(text)}" if text else "")


def fine_decision_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"fx:a:{tx_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"fx:r:{tx_id}"),
    ]])


def no_show_keyboard(shift_id: int, can_fine: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if can_fine:
        rows.append([InlineKeyboardButton(text=f"💸 {a} ед.", callback_data=f"ns:f:{shift_id}:{a}") for a in finance.UNIT_PRESETS])
        rows.append([InlineKeyboardButton(text="💸 Своё число единиц", callback_data=f"ns:f:{shift_id}:c")])
    rows.append([InlineKeyboardButton(text="🤝 Без штрафа", callback_data=f"ns:x:{shift_id}")])
    if settings.webapp_url.startswith("https://"):
        rows.append([InlineKeyboardButton(text="✍️ Внести результат в приложении", web_app=WebAppInfo(url=settings.webapp_url))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_keyboard(shift: dict) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="📸 Сдать отчёт", callback_data=f"rep:{shift['id']}")]]
    if settings.webapp_url.startswith("https://"):
        rows.append([InlineKeyboardButton(text="Открыть в приложении", web_app=WebAppInfo(url=settings.webapp_url))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shift_place(shift: dict) -> str:
    return f"{ddmm_wd(_day(shift))} {shift['start_time']}–{shift['end_time']}, «{escape(shift['location'] or '')}»"


def _shortfall_line(shift: dict) -> str:
    short = svc.shortfall(shift)
    return f"План {shift['volume']}, факт {shift['fact_volume']} шт. — {svc.shortfall_percent(shift)}% (−{short} шт.)"


class Notifier:
    def __init__(self, bot: Bot | None, conn: sqlite3.Connection):
        self.bot = bot
        self.conn = conn
        self._tasks: set[asyncio.Task] = set()
        self._photo_cache: dict[str, tuple[bytes, str]] = {}

    def spawn(self, coro) -> None:
        """Fire-and-forget: API responses never wait on Telegram."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _retry(call, attempts: int = 3):
        """Retry flood limits and network/server hiccups (ТЗ 12.3); other API errors fail fast."""
        for attempt in range(1, attempts + 1):
            try:
                return await call()
            except TelegramRetryAfter as exc:
                if attempt == attempts:
                    raise
                await asyncio.sleep(min(exc.retry_after, 30))
            except (TelegramNetworkError, TelegramServerError):
                if attempt == attempts:
                    raise
                await asyncio.sleep(attempt)

    async def send(
        self, chat_id: int, text: str, markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None = None
    ) -> bool:
        if self.bot is None:
            log.info("[notify → %s] %s", chat_id, text)
            return False
        try:
            await self._retry(lambda: self.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML",
                                                            disable_web_page_preview=True))
            return True
        except TelegramAPIError as exc:
            # user blocked the bot, chat not found, etc. — keep going for the other recipients
            log.warning("send to %s failed: %s", chat_id, exc)
            return False
        except Exception:  # network errors must not kill the caller
            log.exception("send to %s failed", chat_id)
            return False

    async def send_document(self, chat_id: int, data: bytes, filename: str, caption: str | None = None) -> bool:
        if self.bot is None:
            log.info("[document → %s] %s (%d bytes)", chat_id, filename, len(data))
            return False
        try:
            await self._retry(lambda: self.bot.send_document(chat_id, BufferedInputFile(data, filename), caption=caption,
                                                             parse_mode="HTML"))
            return True
        except TelegramAPIError as exc:
            log.warning("document to %s failed: %s", chat_id, exc)
            return False
        except Exception:
            log.exception("document to %s failed", chat_id)
            return False

    @staticmethod
    def broadcast_body(html: str, signed: bool) -> str:
        return f"📣 <b>Сообщение от администратора</b>\n\n{html}" if signed else html

    async def _deliver(self, chat_id: int, body: str, media: str | None, markup, kind: str = "photo") -> int | None:
        """Send one broadcast message (with a photo or a video); returns the message id of the part carrying
        the buttons."""
        if self.bot is None:
            log.info("[broadcast → %s] %s", chat_id, body[:80])
            return None
        try:
            if media and not media.startswith("local:"):
                send = self.bot.send_video if kind == "video" else self.bot.send_photo
                if len(body) <= 1000:
                    msg = await self._retry(lambda: send(chat_id, media, caption=body, parse_mode="HTML", reply_markup=markup))
                    return msg.message_id
                await self._retry(lambda: send(chat_id, media))
            msg = await self._retry(lambda: self.bot.send_message(chat_id, body, parse_mode="HTML", reply_markup=markup,
                                                                  disable_web_page_preview=True))
            return msg.message_id
        except Exception as exc:
            log.warning("broadcast to %s failed: %s", chat_id, exc)
            return None

    async def broadcast(self, broadcast_id: int, recipients: list[dict], html: str, signed: bool = True,
                        media: str | None = None, require_ack: bool = False, media_kind: str = "photo",
                        allow_reply: bool = False) -> int:
        """html must already be sanitized (richtext.sanitize). Every delivery is recorded for read receipts."""
        body = self.broadcast_body(html, signed)
        markup = broadcast_keyboard(broadcast_id, require_ack, allow_reply)
        delivered = 0
        for i, user in enumerate(recipients):
            message_id = await self._deliver(user["telegram_id"], body, media, markup, media_kind)
            ok = message_id is not None
            delivered += ok
            self.conn.execute(
                """INSERT INTO broadcast_deliveries (broadcast_id, user_id, message_id, delivered, sent_at)
                   VALUES (?, ?, ?, ?, ?) ON CONFLICT(broadcast_id, user_id) DO NOTHING""",
                (broadcast_id, user["id"], message_id, int(ok), svc.now_iso()))
            if i % 10 == 9:
                self.conn.execute("UPDATE broadcasts SET delivered = ? WHERE id = ?", (delivered, broadcast_id))
            await asyncio.sleep(0.05)  # stay well under Telegram's ~30 msg/s limit
        self.conn.execute("UPDATE broadcasts SET delivered = ? WHERE id = ?", (delivered, broadcast_id))
        return delivered

    async def broadcast_remind(self, broadcast_id: int, user_ids: list[int] | None = None) -> int:
        """«Напомнить» / «Напомнить всем»: the message again, with the button, to those who haven't confirmed."""
        b = self.conn.execute("SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        if b is None:
            return 0
        rows = [r for r in svc.broadcast_deliveries(self.conn, broadcast_id)
                if r["read_at"] is None and (user_ids is None or r["user_id"] in user_ids)]
        body = "⏰ <b>Напоминание:</b> подтвердите, что ознакомились с сообщением.\n\n" + self.broadcast_body(b["html"] or escape(b["text"]), bool(b["signed"]))
        sent = 0
        for r in rows:
            message_id = await self._deliver(r["telegram_id"], body, b["media_file_id"],
                                             broadcast_keyboard(broadcast_id, True, bool(b["allow_reply"])), b["media_kind"])
            sent += message_id is not None
            self.conn.execute("UPDATE broadcast_deliveries SET reminded_at = ?, reminders = reminders + 1 WHERE id = ?",
                              (svc.now_iso(), r["id"]))
            await asyncio.sleep(0.05)
        return sent

    async def send_photo(self, chat_id: int | str, photo: str, caption: str, markup=None) -> bool:
        if self.bot is None:
            log.info("[photo → %s] %s", chat_id, caption)
            return False
        try:
            await self._retry(lambda: self.bot.send_photo(chat_id, photo, caption=caption[:1024], parse_mode="HTML",
                                                          reply_markup=markup))
            return True
        except Exception as exc:
            log.warning("photo to %s failed: %s", chat_id, exc)
            return False

    async def store_photo(self, chat_id: int, data: bytes, ext: str, caption: str) -> str:
        """Keep an uploaded screenshot in Telegram (ТЗ 1.2): send it to the person's own chat as a receipt
        and return the file_id. Without a bot (local dev) the file is saved to MEDIA_DIR."""
        if self.bot is None:
            return media.save_local(data, ext)
        try:
            msg = await self._retry(lambda: self.bot.send_photo(
                chat_id, BufferedInputFile(data, f"report.{ext}"), caption=caption[:1024], parse_mode="HTML"))
        except Exception as exc:
            log.warning("photo upload for %s failed: %s", chat_id, exc)
            raise svc.ServiceError("Не удалось загрузить скриншот. Напишите боту /start и попробуйте снова",
                                   502, "upload_failed") from exc
        return msg.photo[-1].file_id

    async def store_video(self, chat_id: int, data: bytes, ext: str, caption: str) -> str:
        """A video for a broadcast: uploaded once to the author's own chat, then sent to everyone by file_id."""
        if self.bot is None:
            return media.save_local(data, ext)
        try:
            msg = await self._retry(lambda: self.bot.send_video(
                chat_id, BufferedInputFile(data, f"video.{ext}"), caption=caption[:1024], parse_mode="HTML",
                supports_streaming=True))
        except Exception as exc:
            log.warning("video upload for %s failed: %s", chat_id, exc)
            raise svc.ServiceError("Не удалось загрузить видео. Напишите боту /start и попробуйте снова",
                                   502, "upload_failed") from exc
        if msg.video is None:
            raise svc.ServiceError("Telegram не принял файл как видео — нужен MP4 или MOV", 415, "bad_video")
        try:
            media.keep_video(msg.video.file_id, data, ext)
        except OSError as exc:  # the broadcast still goes out; only the preview in the app is lost
            log.warning("keeping a video copy failed: %s", exc)
        return msg.video.file_id

    async def broadcast_reply(self, broadcast: dict, user: dict, reply: dict) -> bool:
        """Hand a reply to the broadcast's author: the text with who sent it, then their photos and videos."""
        from . import networks

        network = (networks.get(self.conn, user["network_id"]) or {}).get("name") if user.get("network_id") else None
        text = reply_text(broadcast, user, reply["text"], network)
        author = broadcast["author_id"]
        items = reply["media"]
        if self.bot is None:
            log.info("[reply → %s] %s (+%d media)", author, text[:80], len(items))
            return False
        try:
            if not items:
                await self._retry(lambda: self.bot.send_message(author, text, parse_mode="HTML", disable_web_page_preview=True))
            elif len(items) == 1 and len(text) <= 1000:
                send = self.bot.send_video if items[0]["kind"] == "video" else self.bot.send_photo
                await self._retry(lambda: send(author, items[0]["file_id"], caption=text, parse_mode="HTML"))
            else:
                caption = text if len(text) <= 1000 else None
                album = [(InputMediaVideo if m["kind"] == "video" else InputMediaPhoto)(
                    media=m["file_id"], caption=caption if i == 0 else None, parse_mode="HTML" if i == 0 and caption else None)
                    for i, m in enumerate(items)]
                if caption is None:
                    await self._retry(lambda: self.bot.send_message(author, text, parse_mode="HTML", disable_web_page_preview=True))
                await self._retry(lambda: self.bot.send_media_group(author, album))
            return True
        except Exception as exc:
            log.warning("reply to the broadcast author %s failed: %s", author, exc)
            return False

    async def fetch_photo(self, file_id: str) -> tuple[bytes, str] | None:
        """Bytes for the signed preview link; a small in-memory cache saves repeated downloads."""
        if file_id.startswith("local:"):
            path = media.local_path(file_id)
            if path is None:
                return None
            return path.read_bytes(), media.TYPES.get(path.suffix.lstrip("."), "image/jpeg")
        if file_id in self._photo_cache:
            return self._photo_cache[file_id]
        if self.bot is None:
            return None
        try:
            file = await self._retry(lambda: self.bot.get_file(file_id))
            buf = await self.bot.download_file(file.file_path)
            data = buf.read()
        except Exception as exc:
            log.warning("photo download failed: %s", exc)
            return None
        item = (data, media.TYPES.get(media.sniff(data) or "jpg", "image/jpeg"))
        if len(self._photo_cache) >= 60:
            self._photo_cache.pop(next(iter(self._photo_cache)))
        self._photo_cache[file_id] = item
        return item

    async def fetch_video(self, file_id: str):
        """A sent video for the Mini App: the kept copy, else downloaded once from Telegram (up to 20 MB)."""
        path = media.kept_video(file_id)
        if path is not None or self.bot is None:
            return path
        try:
            file = await self._retry(lambda: self.bot.get_file(file_id))
            buf = await self.bot.download_file(file.file_path)
            data = buf.read()
        except Exception as exc:  # over 20 MB, or gone
            log.warning("video download failed: %s", exc)
            return None
        try:
            media.keep_video(file_id, data, media.sniff_video(data) or "mp4")
        except OSError as exc:
            log.warning("keeping a video copy failed: %s", exc)
            return None
        return media.kept_video(file_id)

    async def fetch_avatar(self, telegram_id: int) -> tuple[bytes, str] | None:
        """A person's current Telegram profile photo (the onboarding card), None if hidden or absent."""
        if self.bot is None:
            return None
        try:
            photos = await self._retry(lambda: self.bot.get_user_profile_photos(telegram_id, limit=1))
        except Exception as exc:
            log.warning("profile photo of %s: %s", telegram_id, exc)
            return None
        if not photos.photos:
            return None
        return await self.fetch_photo(photos.photos[0][-1].file_id)

    # ------------------------------------------------------------ recruiting (the customer's module 3)

    def _application_who(self, app: dict) -> str:
        from . import recruiting

        user = svc.get_user(self.conn, app["user_id"])
        ref = svc.get_user(self.conn, app["referrer_id"]) if app["referrer_id"] else None
        return (f"👤 <a href=\"tg://user?id={user['telegram_id']}\">{escape(app['full_name'])}</a>"
                f"{' @' + escape(user['username']) if user['username'] else ''}, {app['age']} лет, м. {escape(app['metro'])}\n"
                f"Дней в неделю: от {app['min_days']}, обучение: {recruiting.days_text(app)}, общение: {app['communication']}/10"
                + (f"\nПригласил(а): {escape(svc.full_name(ref))}" if ref else ""))

    async def application_submitted(self, app: dict) -> None:
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="🧑‍🎓 Открыть «Онбординг»", web_app=WebAppInfo(url=settings.webapp_url))]]) \
            if settings.webapp_url.startswith("https://") else None
        user = svc.get_user(self.conn, app["user_id"])
        # the candidate hears back in the chat; when the bot can't write, the staff know to reach out themselves
        reached = await self.send(user["telegram_id"], "📝 Анкета получена! Бот напишет сюда, когда назначат обучение. "
                                                       "Не выключайте уведомления от бота.")
        warn = "" if reached or self.bot is None else ("\n⚠️ Бот не может написать кандидату: он не открыл чат с ботом. "
                                                       "Свяжитесь с ним в Telegram сами.")
        await self.to_admins(f"📝 <b>Новая анкета кандидата</b>\n{self._application_who(app)}{warn}", markup)

    def _training_text(self, app: dict) -> str:
        from datetime import date as _date

        trainer = svc.get_user(self.conn, app["trainer_id"])
        handle = f"@{trainer['username']} ({escape(svc.full_name(trainer))})" if trainer["username"] else escape(svc.full_name(trainer))
        day = _date.fromisoformat(app["training_date"])
        return (f"📅 <b>Ваше обучение запланировано!</b>\n🗓 Дата: {ddmm_wd(day)}\n⏰ Время: {app['training_time']}\n"
                f"📍 Место: {escape(app['training_location'])}\n👨‍🏫 Ваш тренер: {handle}\n\n"
                "‼️ <b>ВАЖНО:</b> в случае неявки без предупреждения тренера дальнейшее сотрудничество может быть прекращено.\n"
                "✅ Пожалуйста, нажмите кнопку ниже, чтобы подтвердить запись.")

    async def training_assigned(self, app: dict) -> None:
        user = svc.get_user(self.conn, app["user_id"])
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="✅ Подтвердить запись", callback_data=f"trc:{app['id']}")]])
        await self.send(user["telegram_id"], self._training_text(app), markup)
        trainer = svc.get_user(self.conn, app["trainer_id"])
        await self.send(trainer["telegram_id"], f"🧑‍🎓 <b>Вам назначен кандидат на обучение</b>\n"
                                                f"{app['training_date'][8:10]}.{app['training_date'][5:7]} в {app['training_time']}, "
                                                f"{escape(app['training_location'])}\n{self._application_who(app)}\n"
                                                "Сообщу, когда кандидат подтвердит запись.")

    async def training_confirmed(self, app: dict) -> None:
        user = svc.get_user(self.conn, app["user_id"])
        await self.send(user["telegram_id"], "✅ Вы успешно записаны. Ждём вас!")
        trainer = svc.get_user(self.conn, app["trainer_id"])
        text = (f"✅ Кандидат {escape(app['full_name'])} подтвердил запись на обучение "
                f"{app['training_date'][8:10]}.{app['training_date'][5:7]} в {app['training_time']}, {escape(app['training_location'])}.")
        await self.send(trainer["telegram_id"], text)

    async def training_reminder(self, app: dict, kind: str) -> None:
        user = svc.get_user(self.conn, app["user_id"])
        head = "⏰ <b>Напоминание: завтра обучение</b>" if kind == "eve" else "⏰ <b>Сегодня ваше обучение</b>"
        markup = None if app["training_confirmed_at"] else InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="✅ Подтвердить запись", callback_data=f"trc:{app['id']}")]])
        await self.send(user["telegram_id"], head + "\n\n" + self._training_text(app).split("\n\n")[0].split("\n", 1)[1], markup)

    async def training_cancelled(self, app: dict) -> None:
        """The candidate is back among those waiting; the trainer is told the session is off."""
        user = svc.get_user(self.conn, app["user_id"])
        day = app.get("cancelled_date")
        when = f" {day[8:10]}.{day[5:7]}" if day else ""
        await self.send(user["telegram_id"], f"ℹ️ Обучение{when} отменено. Мы свяжемся с вами, когда назначим новую дату.")
        trainer = svc.get_user(self.conn, app["cancelled_trainer_id"]) if app.get("cancelled_trainer_id") else None
        if trainer:
            await self.send(trainer["telegram_id"], f"ℹ️ Обучение{when} отменено\n{self._application_who(app)}")

    async def application_rejected(self, app: dict) -> None:
        user = svc.get_user(self.conn, app["user_id"])
        text = "К сожалению, мы вынуждены отказать вам в сотрудничестве."
        if app["reject_reason"]:
            text += f"\nПричина: {escape(app['reject_reason'])}"
        await self.send(user["telegram_id"], text)

    async def application_returned(self, app: dict) -> None:
        user = svc.get_user(self.conn, app["user_id"])
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="📝 Заполнить анкету", web_app=WebAppInfo(url=settings.webapp_url))]]) \
            if settings.webapp_url.startswith("https://") else None
        await self.send(user["telegram_id"], "Ваш отказ был пересмотрен. Пожалуйста, заполните анкету заново.", markup)

    async def application_contact(self, app: dict) -> None:
        user = svc.get_user(self.conn, app["user_id"])
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Я написал менеджеру", callback_data=f"ctw:{app['id']}")],
            [InlineKeyboardButton(text="🙈 Не могу написать первым", callback_data=f"ctc:{app['id']}")]])
        await self.send(user["telegram_id"], "🎉 <b>Хорошие новости!</b> Мы готовы вас рассмотреть. Давайте обсудим детали "
                                             f"обучения. Свяжитесь с нашим представителем: @{escape(app['contact_manager'])}",
                        markup)

    async def contact_answered(self, app: dict) -> None:
        name = escape(app["full_name"])
        user = svc.get_user(self.conn, app["user_id"])
        link = f"<a href=\"tg://user?id={user['telegram_id']}\">{name}</a>" + (f" @{escape(user['username'])}" if user["username"] else "")
        text = (f"✍️ Кандидат {link} сообщил, что связался с вами (@{escape(app['contact_manager'])})."
                if app["contact_answer"] == "wrote" else
                f"⚠️ Кандидат {link} просит написать ему сам. Причина: ограничения аккаунта или стеснение. "
                f"Менеджер: @{escape(app['contact_manager'])}")
        manager = self.conn.execute("SELECT telegram_id FROM users WHERE lower(username) = lower(?)",
                                    (app["contact_manager"],)).fetchone()
        if manager:
            await self.send(manager[0], text)
        await self.to_admins(text, exclude=manager[0] if manager else None)

    async def internship_started(self, app: dict) -> None:
        from . import recruiting

        user = svc.get_user(self.conn, app["user_id"])
        units = recruiting.progress(self.conn, app, user)["target"]
        await self.send(user["telegram_id"],
                        f"🎓 <b>Добро пожаловать на стажировку!</b>\nЦель стажировки: {units} единиц. "
                        "Эти единицы учебные и не оплачиваются.\nЗаписывайтесь в график и сдавайте отчёты в приложении, "
                        "как все сотрудники — прогресс виден на главном экране.", _open_schedule())

    async def stage_reached(self, app: dict) -> None:
        what = "набрал(а) стажировку" if app["stage"] == 1 else "отработал(а) второй этап"
        await self.to_admins(f"🎯 <b>{escape(app['full_name'])}</b> {what}: {app['units']} из {app['target']} шт. "
                             "Проверьте и подтвердите в «Онбординг».", _open_admin())

    async def stage_confirmed(self, app: dict, stage: int, tx: dict | None, reached: int | None = None) -> None:
        """`reached` — what the person had when staff checked; a different `counted` is explained to them."""
        user = svc.get_user(self.conn, app["user_id"])
        counted = app[f"stage{stage}_counted"]
        fixed = f"\nАдминистратор скорректировал результат: {reached} → {counted} шт." \
            if reached is not None and reached != counted else ""
        if stage == 1:
            await self.send(user["telegram_id"], f"✅ Стажировка засчитана: {counted} шт. Теперь вы в команде — "
                                                 f"дальше смены оплачиваются как обычно.{fixed}")
        elif fixed:
            await self.send(user["telegram_id"], f"✅ Второй этап засчитан: {counted} шт.{fixed}")
        if app["referrer_id"]:
            ref = svc.get_user(self.conn, app["referrer_id"])
            step = "прошёл стажировку" if stage == 1 else "отработал второй этап"
            text = f"🤝 Ваш приглашённый {escape(app['full_name'])} {step}: засчитано {counted} шт."
            if tx:
                text += f"\n💰 Бонус {tx['amount']} ₽ начислен."
            await self.send(ref["telegram_id"], text)

    async def archive(self, tags: list[str], text: str, person: dict | None = None, photo: str | None = None) -> bool:
        """Copy an important action to the private archive group with hashtags for search.
        Never raises: the business action already happened; a failed copy is logged in archive_log."""
        head = " ".join(f"#{t}" for t in tags)
        if person:
            head += f" {person_tag(person)}"
        body = f"{head}\n{text}"
        cur = self.conn.execute(
            "INSERT INTO archive_log (tags, text, created_at) VALUES (?, ?, ?)", (",".join(tags), body, svc.now_iso())
        )
        chat = app_settings.get_raw(self.conn, "archive_chat_id")
        if not chat:
            self.conn.execute("UPDATE archive_log SET error = 'archive group is not set' WHERE id = ?", (cur.lastrowid,))
            return False
        ok = await (self.send_photo(int(chat), photo, body) if photo else self.send(int(chat), body))
        self.conn.execute("UPDATE archive_log SET sent = ?, error = ? WHERE id = ?",
                          (int(ok), None if ok else "send failed", cur.lastrowid))
        return ok

    async def to_admins(self, text: str, markup: InlineKeyboardMarkup | None = None, exclude: int | None = None):
        for admin_id in svc.admin_telegram_ids(self.conn):
            if admin_id != exclude:
                await self.send(admin_id, text, markup)

    async def to_managers(self, user: dict, text: str, markup: InlineKeyboardMarkup | None = None,
                          exclude: int | None = None, trainer_buttons: bool = True) -> None:
        """Owners, admins, the trainer of the person's network (ТЗ модуль 8) and their instructor (ТЗ 27.10 §23)."""
        from . import networks

        await self.to_admins(text, markup, exclude)
        admins = svc.admin_telegram_ids(self.conn)
        for lead in (networks.trainer_of(self.conn, user), networks.instructor_of(self.conn, user)):
            if lead and lead["telegram_id"] != exclude and lead["telegram_id"] not in admins:
                await self.send(lead["telegram_id"], text, markup if trainer_buttons else None)

    async def shift_event(self, event: str, shift_id: int, old: dict | None = None) -> None:
        shift = svc.get_shift(self.conn, shift_id)
        if shift is None:
            return
        user = svc.get_user(self.conn, shift["user_id"])
        if event == "SHIFT_CREATED":
            await self.to_managers(user, shift_created_text(self.conn, user, shift))
        elif event == "SHIFT_URGENT_REQUESTED":
            await self.to_managers(user, urgent_text(user, shift), urgent_keyboard(shift_id))
        elif event == "SHIFT_CANCELLED":
            await self.to_managers(user, cancelled_text(self.conn, user, shift))
        elif event == "SHIFT_RESTORED":
            await self.to_managers(user, restored_text(self.conn, user, shift))
        elif event == "SHIFT_FINAL_CANCELLED":
            # trainers can't fine directly: no penalty buttons for them
            await self.to_managers(user, final_cancel_text(self.conn, user, shift), penalty_keyboard(shift_id),
                                   trainer_buttons=False)
        elif event == "SHIFT_EDITED_BY_USER" and old:
            await self.to_managers(user, edited_text(user, shift, old))

    async def admin_edit(self, admin: dict, shift: dict, reason: str) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        await self.to_managers(user, admin_edited_text(admin, user, shift, reason), exclude=admin["telegram_id"])

    async def network_changed(self, user: dict, network_name: str | None, actor: dict, trainer: bool = False) -> None:
        if trainer:
            text = f"🧭 Вы назначены тренером сети «{escape(network_name or '')}». В приложении во вкладке «Админ» — ваша команда."
        elif network_name:
            text = f"🧭 Вы в сети «{escape(network_name)}»."
        else:
            text = "🧭 Вы больше не состоите в сети."
        await self.send(user["telegram_id"], text)
        await self.archive(["сеть"], f"🧭 {who(user)}: {'тренер сети' if trainer else 'сеть'} "
                           f"«{escape(network_name or '—')}»\nИзменил: {who(actor)}", user)

    async def profile_card(self, chat_id: int, person: dict) -> bool:
        """ТЗ 27.10 §11: a person without a username — the bot sends a card where the name opens the profile
        (a text mention works for people who started the bot), with a button when Telegram allows it."""
        from aiogram.exceptions import TelegramBadRequest

        name = escape(" ".join(x for x in (person.get("first_name"), person.get("last_name")) if x) or "Без имени")
        text = (f'👤 <a href="tg://user?id={person["telegram_id"]}">{name}</a>\n'
                + (f"@{escape(person['username'])}\n" if person.get("username") else "Username нет\n")
                + f"Telegram ID: <code>{person['telegram_id']}</code>\n\nНажмите на имя, чтобы открыть профиль.")
        if self.bot is None:
            return False
        button = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="Открыть профиль", url=f"tg://user?id={person['telegram_id']}")]])
        try:
            await self._retry(lambda: self.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=button))
            return True
        except TelegramBadRequest:  # the person's privacy settings forbid the button: the name still works
            return await self.send(chat_id, text)
        except Exception as exc:
            log.warning("profile card to %s failed: %s", chat_id, exc)
            return False

    async def product_access(self, user: dict, product: str, allow: bool, actor: dict) -> None:
        from . import products

        title = products.PRODUCTS[product]
        await self.send(user["telegram_id"], f"🧰 Вам открыт доступ к «{title}» — раздел «Работа» в приложении."
                        if allow else f"🧰 Доступ к «{title}» закрыт. История выполнений и выплат сохранена.")
        await self.archive(["доступ"], f"🧰 {who(user)}: «{title}» — {'доступ выдан' if allow else 'доступ закрыт'}"
                                       f"\nИзменил: {who(actor)}", user)

    async def alice_qr_changed(self, user: dict, on: bool) -> None:
        await self.send(user["telegram_id"], "🔳 Вам назначен QR-код для «Яндекс Алисы» — можно начинать выполнения."
                        if on else "🔳 QR-код для «Яндекс Алисы» с вас снят. Новый назначит администратор.")

    async def alice_decided(self, task: dict) -> None:
        """§39: the person learns the moderator's decision; the money comes after Yandex confirms it."""
        from . import alice

        user = svc.get_user(self.conn, task["user_id"])
        if task["status"] == "accepted":
            text = f"✅ Выполнение {alice.number(task['id'])} принято и ждёт оплаты — оплата после подтверждения Яндекса."
        else:
            reason = f"\nПричина: {escape(task['reject_reason'])}" if task["reject_reason"] else ""
            text = f"❌ Выполнение {alice.number(task['id'])} отклонено и не оплачивается.{reason}"
        await self.send(user["telegram_id"], text)

    async def alice_paid(self, batch: dict) -> None:
        for it in batch["items"]:
            user = svc.get_user(self.conn, it["user_id"])
            await self.send(user["telegram_id"], f"💰 «Яндекс Алиса»: оплачено выполнений — {it['personal_volume']}.")
        await self.archive(["выплата", "алиса"], f"💰 Выплата «{escape(batch['title'])}» по «Яндекс Алисе»: "
                                                 f"{len(batch['items'])} чел., {batch['total']} ₽")

    async def group_invited(self, user: dict, kind: str, inv: dict) -> bool:
        """ТЗ 27.10 §27: the personal link goes to the person; the archive keeps the fact (not the link)."""
        from . import groups

        ok = await self.send(user["telegram_id"], groups.invite_text(kind, inv))
        await self.archive(["группа"], f"👥 {who(user)}: приглашение в «{groups.KINDS[kind]}»"
                                       + ("" if ok else " — бот не смог написать человеку"), user)
        return ok

    async def instructor_changed(self, user: dict, actor: dict) -> None:
        """ТЗ 27.10 §22: the person, their new instructor and the archive learn about the assignment."""
        from . import networks

        lead = networks.instructor_of(self.conn, user)
        if lead:
            await self.send(user["telegram_id"], f"🧭 Ваш инструктор — {who(lead)}.")
            await self.send(lead["telegram_id"], f"🧭 За вами закреплён сотрудник {who(user)}.")
        else:
            await self.send(user["telegram_id"], "🧭 Вы работаете напрямую с тренером сети.")
        await self.archive(["сеть"], f"🧭 {who(user)}: инструктор — {who(lead) if lead else 'нет (напрямую тренеру)'}"
                                     f"\nИзменил: {who(actor)}", user)

    async def admin_contacted(self, shift: dict, text: str) -> None:
        """«Связаться с администратором» after the deadline: who, which shift and why, with a link to answer."""
        user = svc.get_user(self.conn, shift["user_id"])
        if shift["status"] in svc.DAY_OFF:
            plan = "выходной"
        else:
            plan = f"{shift['start_time']}–{shift['end_time']}, {escape(shift['location'] or '')}, {shift['volume']} шт."
        await self.to_managers(user, f"📨 <b>Сотрудник просит изменить смену</b>\n{who(user)}\n"
                                     f"Смена {ddmm_wd(_day(shift))}: {plan}\n💬 {escape(text)}\n"
                                     "Смена не изменена — если согласны, поправьте её в «Админ → День».")

    async def urgent_decided(self, shift: dict, approved: bool) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        if approved:
            text = f"✅ Ваша срочная заявка на сегодня ({ddmm(_day(shift))}) одобрена! Ждём вас в {shift['start_time']}."
        else:
            text = f"❌ Ваша срочная заявка на сегодня ({ddmm(_day(shift))}) отклонена администратором."
        await self.send(user["telegram_id"], text)

    async def penalty_assigned(self, shift: dict, actor_name: str | None = None) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        await self.send(
            user["telegram_id"],
            f"💸 Вам начислен штраф {shift['penalty_amount']} ₽ за окончательную отмену смены {ddmm(_day(shift))}.",
        )
        await self.archive(
            ["штраф", "отмена"],
            f"💸 Штраф {shift['penalty_amount']} ₽ — {who(user)}\nОкончательная отмена смены {ddmm_wd(_day(shift))}"
            + (f"\nНазначил: {escape(actor_name)}" if actor_name else ""),
            user,
        )

    async def access_changed(self, user: dict, previous_status: str, open_app: InlineKeyboardMarkup | None,
                             actor: dict | None = None) -> None:
        verdict = "✅ Заявка принята" if user["status"] == "approved" else "❌ Заявка отклонена"
        await self.archive(["доступ"], f"{verdict} — {who(user)}" + (f"\nРешил: {who(actor)}" if actor else ""), user)
        if user["status"] == "approved":
            if svc.claim_welcome(self.conn, user["id"]):
                from .bot import WELCOME  # bot imports notify; import lazily

                await self.send(user["telegram_id"], WELCOME, open_app)
            else:
                await self.send(user["telegram_id"], "Доступ снова открыт.", open_app)
        elif previous_status == "pending":
            await self.send(user["telegram_id"], "В доступе отказано.")
        else:
            await self.send(user["telegram_id"], "Доступ к графику закрыт администратором.", ReplyKeyboardRemove())

    async def role_changed(self, user: dict, actor: dict | None = None, previous: str | None = None) -> None:
        title = perms.ROLE_TITLES[perms.role_of(user)]
        if previous:
            await self.archive(
                ["роль"],
                f"👤 {who(user)}: {perms.ROLE_TITLES.get(previous, previous)} → {title}"
                + (f"\nИзменил: {who(actor)}" if actor else ""),
                user,
            )
        if user["role"] == "admin":
            await self.send(
                user["telegram_id"],
                "Вам выданы права администратора. В приложении появилась вкладка «Админ», "
                "сюда будут приходить заявки и уведомления.",
                ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📋 Распред на завтра")]], resize_keyboard=True),
            )
        elif user["role"] == "trainer":
            await self.send(
                user["telegram_id"],
                "Вы назначены тренером. В приложении появилась вкладка «Админ» — там ваша команда, "
                "её смены и отчёты.",
                ReplyKeyboardRemove(),
            )
        elif user["role"] == "instructor":
            await self.send(
                user["telegram_id"],
                "Вы назначены инструктором. В приложении появилась вкладка «Админ» — там закреплённые за вами "
                "сотрудники, их смены и отчёты.",
                ReplyKeyboardRemove(),
            )
        else:
            await self.send(user["telegram_id"], "Расширенные права сняты, вы снова сотрудник.", ReplyKeyboardRemove())

    async def account_state_changed(self, user: dict, actor: dict, previous: str) -> None:
        state, reason = user["account_state"], user["state_reason"]
        why = f"\nПричина: {escape(reason)}" if reason else ""
        if state == "paused":
            tags, line = ["пауза"], "⏸ На паузе"
            personal = "⏸ Ваш аккаунт поставлен на паузу: новые смены пока недоступны." + why
        elif state == "blocked":
            tags, line = ["блок"], "⛔ Заблокирован"
            personal = "⛔ Ваш аккаунт заблокирован." + why
        else:
            tags = ["блок"] if previous == "blocked" else ["пауза"]
            line = "✅ Разблокирован" if previous == "blocked" else "▶️ Пауза снята"
            personal = "✅ Доступ восстановлен. Можно снова записываться на смены."
        await self.send(user["telegram_id"], personal,
                        ReplyKeyboardRemove() if state == "blocked" else None)
        await self.archive(tags, f"{line} — {who(user)}{why}\nИзменил: {who(actor)}", user)

    # ------------------------------------------------------------ reports (ТЗ 3.2–3.4)

    async def _photo_or_text(self, chat_id: int | str, photo: str | None, text: str, markup=None) -> bool:
        if photo and not photo.startswith("local:"):
            return await self.send_photo(chat_id, photo, text, markup)
        return await self.send(int(chat_id), text, markup)

    async def _archive_report(self, tags: list[str], text: str, user: dict, report: dict | None) -> None:
        photo = report["photo_file_id"] if report and report["photo_file_id"] and not report["photo_file_id"].startswith("local:") else None
        await self.archive(tags, text, user, photo)

    async def report_submitted(self, shift: dict, report: dict) -> None:
        from . import reports

        user = svc.get_user(self.conn, shift["user_id"])
        plan_fact = f"План {shift['volume']}, факт {report['fact_volume']} шт."
        if report["status"] == "auto_approved":
            await self.send(user["telegram_id"], f"✅ Отчёт принят: {shift_place(shift)}\n{plan_fact}")
            await self._archive_report(["отчёт", "принят"], f"✅ ОТЧЁТ ПРИНЯТ (план выполнен) — {who(user)}\n"
                                       f"{shift_place(shift)}\n{plan_fact}", user, report)
            return
        if report["status"] == "approved_team":
            await self.team_approved(shift, report)
            return
        await self.send(
            user["telegram_id"],
            f"📝 Отчёт отправлен на проверку: {shift_place(shift)}\n{_shortfall_line(shift)}\n"
            "Пока его не проверили, можно заменить его в приложении.",
        )
        attempt = f" (попытка {report['attempt']})" if report["attempt"] > 1 else ""
        caption = f"📝 <b>Отчёт на проверку</b>{attempt}\n{who(user)}\n{shift_place(shift)}\n{_shortfall_line(shift)}"
        block = day_actual.shortfall_fine_block(self.conn, shift)
        if block:
            caption += f"\n\nℹ️ {block}."
        for chat in reports.reviewers(self.conn, user):
            reviewer = svc.get_user_by_tg(self.conn, chat)
            fine = chat in settings.admin_ids or reviewer is not None and (
                perms.can(reviewer, "fine_direct", user) or perms.can(reviewer, "fine_request", user))
            await self._photo_or_text(chat, report["photo_file_id"], caption,
                                      review_keyboard(shift["id"], fine and not block))

    async def report_reviewed(self, shift: dict, report: dict, actor: dict) -> None:
        from . import reports

        user = svc.get_user(self.conn, shift["user_id"])
        place = shift_place(shift)
        note = f"\nКомментарий: {escape(shift['report_note'])}" if shift["report_note"] else ""
        plan_fact = f"План {shift['volume']}, факт {report['fact_volume']} шт."
        if report["status"] == "approved":
            await self.send(user["telegram_id"], f"✅ Отчёт принят: {place}\n{plan_fact}{note}")
            tags, line = ["отчёт", "принят"], "✅ ОТЧЁТ ПРИНЯТ"
        elif report["status"] == "rejected_retry":
            await self.send(user["telegram_id"], f"🔄 Отчёт вернули на исправление: {place}{note}\n\n"
                            "Сдайте отчёт заново — в приложении или кнопкой ниже.", report_keyboard(shift))
            tags, line = ["отчёт", "исправление"], "🔄 ТРЕБУЕТСЯ ИСПРАВЛЕНИЕ"
        else:
            fines = finance.linked(self.conn, shift["id"], ("report",))
            pending = next((t for t in fines if t["status"] == "pending_admin_approval"), None)
            if pending:
                await self.send(user["telegram_id"], f"❌ Отчёт отклонён: {place}{note}")
                tags, line = ["отчёт", "штраф", "согласование"], f"❌ ОТЧЁТ ОТКЛОНЁН | ШТРАФ {finance.staff_text(pending)} НА СОГЛАСОВАНИИ"
                await self.fine_requested(pending, actor)
            else:
                fine = next((t for t in fines if t["status"] == "active"), None)
                units = finance.units_text(fine)
                await self.send(user["telegram_id"], f"❌ Отчёт отклонён{', штраф ' + units if units else ''}: {place}{note}")
                tags, line = ["отчёт", "штраф"], f"❌ ОТЧЁТ ОТКЛОНЁН | ШТРАФ {finance.staff_text(fine) if fine else report['penalty_amount']}"
        await self._archive_report(tags, f"{line} — {who(user)}\n{place}\n{plan_fact}{note}\nПроверил: {who(actor)}",
                                   user, report)
        for chat in reports.reviewers(self.conn, user):
            if chat != actor["telegram_id"]:
                await self.send(chat, f"{line}: {who(user)}, {ddmm(_day(shift))} — {who(actor)}")

    async def no_show(self, shift: dict) -> None:
        from . import reports

        user = svc.get_user(self.conn, shift["user_id"])
        place = shift_place(shift)
        await self.send(user["telegram_id"],
                        f"🚫 Отчёт по смене {place} не сдан вовремя — смена отмечена как неявка. "
                        "Решение примет администратор.")
        await self.archive(["неявка"], f"🚫 НЕЯВКА — {who(user)}\n{place}, план {shift['volume']} шт.", user)
        for chat in reports.reviewers(self.conn, user):
            reviewer = svc.get_user_by_tg(self.conn, chat)
            fine = chat in settings.admin_ids or reviewer is not None and (
                perms.can(reviewer, "fine_direct", user) or perms.can(reviewer, "fine_request", user))
            await self.send(chat, f"🚫 <b>Неявка</b>: нет отчёта\n{who(user)}\n{place}, план {shift['volume']} шт.\n\n"
                            "Назначьте штраф, отпустите без штрафа или внесите результат за сотрудника.",
                            no_show_keyboard(shift["id"], fine))

    async def no_show_resolved(self, shift: dict, actor: dict) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        place = shift_place(shift)
        note = f"\nПричина: {escape(shift['report_note'])}" if shift["report_note"] else ""
        pending = next((t for t in finance.linked(self.conn, shift["id"], ("no_show",))
                        if t["status"] == "pending_admin_approval"), None)
        if pending:
            tags, line = ["неявка", "штраф", "согласование"], f"💸 НЕЯВКА | ШТРАФ {finance.staff_text(pending)} НА СОГЛАСОВАНИИ"
            await self.fine_requested(pending, actor)
        elif shift["shortfall_status"] == "fined":
            fine = next((t for t in finance.linked(self.conn, shift["id"], ("no_show",)) if t["status"] == "active"), None)
            units = finance.units_text(fine)
            await self.send(user["telegram_id"], f"💸 Штраф{' ' + units if units else ''} за неявку: {place}{note}")
            tags, line = ["неявка", "штраф"], f"💸 НЕЯВКА | ШТРАФ {finance.staff_text(fine) if fine else shift['shortfall_penalty']}"
        else:
            await self.send(user["telegram_id"], f"🤝 Неявка {place} закрыта без штрафа.{note}")
            tags, line = ["неявка"], "🤝 НЕЯВКА БЕЗ ШТРАФА"
        await self.archive(tags, f"{line} — {who(user)}\n{place}{note}\nРешил: {who(actor)}", user)

    async def manual_result(self, shift: dict, report: dict, actor: dict) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        place = shift_place(shift)
        note = f"\nКомментарий: {escape(shift['report_note'])}" if shift["report_note"] else ""
        text = f"План {shift['volume']}, факт {report['fact_volume']} шт."
        await self.send(user["telegram_id"], f"✍️ Администратор внёс результат: {place}\n{text}{note}")
        await self._archive_report(["отчёт", "ручной"], f"✍️ РЕЗУЛЬТАТ ВНЕСЁН ВРУЧНУЮ — {who(user)}\n{place}\n{text}{note}\n"
                                   f"Внёс: {who(actor)}", user, report)

    # ------------------------------------------------------------ money (ТЗ модуль 4)

    async def fine_requested(self, tx: dict, trainer: dict) -> None:
        """A trainer's fine: owners decide, the balance doesn't change until then (ТЗ 5.3)."""
        user = svc.get_user(self.conn, tx["user_id"])
        text = (f"🧾 <b>Заявка на штраф</b> от тренера {who(trainer)}\n{who(user)}: <b>{finance.staff_text(tx)}</b>\n"
                f"Причина: {escape(tx['reason'])}")
        for chat in svc.admin_telegram_ids(self.conn):  # owners and admins decide
            await self._photo_or_text(chat, tx["media_file_id"], text, fine_decision_keyboard(tx["id"]))

    async def tx_created(self, tx: dict, actor: dict) -> None:
        user = svc.get_user(self.conn, tx["user_id"])
        amount, reason = tx["amount"], escape(tx["reason"])
        if tx["status"] == "pending_admin_approval":
            await self.archive(["штраф", "согласование"], f"🧾 ЗАЯВКА НА ШТРАФ {finance.staff_text(tx)} — {who(user)}\n"
                               f"Причина: {reason}\nТренер: {who(actor)}", user, tx["media_file_id"])
            await self.fine_requested(tx, actor)
            return
        if tx["type"] == "fine":
            await self.send(user["telegram_id"], f"💸 Вам назначен штраф: {finance.units_text(tx)}.\nПричина: {reason}")
            tags, line = ["штраф"], f"💸 ШТРАФ {finance.staff_text(tx)}"
        elif tx["type"] == "bonus":
            await self.send(user["telegram_id"], f"🎁 Вам начислен бонус {amount} ₽.\nЗа что: {reason}")
            tags, line = ["бонус"], f"🎁 БОНУС {amount} ₽"
        else:
            await self.send(user["telegram_id"], f"➖ Вычет {amount} ₽ из выплаты.\nПричина: {reason}")
            tags, line = ["вычет"], f"➖ ВЫЧЕТ {amount} ₽"
        role = perms.ROLE_TITLES[perms.role_of(actor)].lower()
        await self.archive(tags, f"{line} — {who(user)}\nПричина: {reason}\nНачислил: {who(actor)} ({role})",
                           user, tx["media_file_id"])

    async def tx_decided(self, tx: dict, actor: dict) -> None:
        from . import violations

        user = svc.get_user(self.conn, tx["user_id"])
        v = violations.by_tx(self.conn, tx["id"]) if tx["source"] == "attendance" else None
        if v and tx["status"] == "active":
            trainer = svc.get_user(self.conn, v["resolution_admin_id"])
            await self._violation_out(v, tx, trainer, approver=actor)
            await self.send(trainer["telegram_id"], f"Заявка на штраф {finance.staff_text(tx)} за пропуск графика "
                            f"({who(user)}): одобрена.")
            return
        trainer = svc.get_user_by_tg(self.conn, tx["created_by"])
        comment = f"\nКомментарий: {escape(tx['decision_comment'])}" if tx["decision_comment"] else ""
        if tx["status"] == "active":
            units = finance.units_text(tx)
            await self.send(user["telegram_id"], f"💸 Вам назначен штраф{': ' + units if units else ''}.\nПричина: {escape(tx['reason'])}")
            verdict, tags = "✅ ОДОБРЕН", ["штраф"]
        else:
            verdict, tags = "❌ ОТКЛОНЁН", ["штраф", "отклонён"]  # the employee is not told (ТЗ 5.3)
        if tx["source"] == "attendance" and tx["status"] == "rejected":
            comment += "\nНарушение графика снова ждёт решения в «Дисциплине»."
        if trainer:
            await self.send(trainer["telegram_id"], f"Заявка на штраф {finance.staff_text(tx)} ({who(user)}): "
                            f"{'одобрена' if tx['status'] == 'active' else 'отклонена'}.{comment}")
        await self.archive(tags, f"{verdict} ШТРАФ {finance.staff_text(tx)} — {who(user)}\nПричина: {escape(tx['reason'])}"
                           f"{comment}\nРешил: {who(actor)}", user)

    async def tx_forgiven(self, tx: dict, actor: dict, was: str, reason: str | None) -> None:
        user = svc.get_user(self.conn, tx["user_id"])
        amount = f" {finance.staff_text(tx)}" if tx["amount"] else ""
        units = finance.units_text(tx)
        why = f"\nПричина: {escape(reason)}" if reason else ""
        if was != "pending_admin_approval":  # a request nobody approved was never shown to the employee
            await self.send(user["telegram_id"], f"🤝 Штраф{' (' + units + ')' if units else ''} прощён.{why}")
        role = perms.ROLE_TITLES[perms.role_of(actor)].lower()
        await self.archive(["штраф", "прощён"], f"🤝 ШТРАФ{amount} ПРОЩЁН — {who(user)}\nБыло: {escape(tx['reason'])}"
                           f"{why}\nПростил: {who(actor)} ({role})", user)
        creator = svc.get_user_by_tg(self.conn, tx["created_by"]) if tx["created_by"] else None
        if creator and creator["telegram_id"] != actor["telegram_id"] and perms.is_staff(creator):
            await self.send(creator["telegram_id"], f"🤝 Ваш штраф{amount} ({who(user)}) простил {who(actor)}.{why}")

    async def tx_cancelled(self, tx: dict, actor: dict, was: str) -> None:
        user = svc.get_user(self.conn, tx["user_id"])
        title = finance.TYPE_TITLES[tx["type"]]
        amount = f" {finance.staff_text(tx)}" if tx["amount"] else ""
        mine = f" ({finance.units_text(tx)})" if tx["type"] == "fine" and tx.get("pieces") else "" if tx["type"] == "fine" else amount
        if was != "pending_admin_approval":
            await self.send(user["telegram_id"], f"↩️ {title}{mine} аннулирован.\nПричина: {escape(tx['decision_comment'] or '')}")
        await self.archive(["аннулирование", {"fine": "штраф", "bonus": "бонус"}.get(tx["type"], "вычет")],
                           f"↩️ АННУЛИРОВАН {title.upper()}{amount} — {who(user)}\nБыло: {escape(tx['reason'])}\n"
                           f"Причина: {escape(tx['decision_comment'] or '')}\nАннулировал: {who(actor)}", user)

    # ------------------------------------------------------------ schedule violations (ТЗ 12.4)

    async def send_album(self, chat_id: int, files: list[str]) -> int:
        """Proof photos after a message: one photo, or an album of up to 10. Local dev files are skipped."""
        real = [f for f in files if not f.startswith("local:")]
        if not real or self.bot is None:
            if files:
                log.info("[album → %s] %d file(s)", chat_id, len(files))
            return 0
        try:
            if len(real) == 1:
                await self._retry(lambda: self.bot.send_photo(chat_id, real[0]))
            else:
                await self._retry(lambda: self.bot.send_media_group(chat_id, [InputMediaPhoto(media=f) for f in real[:10]]))
            return len(real)
        except Exception as exc:
            log.warning("album to %s failed: %s", chat_id, exc)
            return 0

    async def mark_reminders(self, people: list[dict], day: date, deadline: str) -> int:
        """«Отметь график на завтра» to those with «Работаю» who have neither a shift nor a day off."""
        text = (f"🗓 <b>Отметь график на завтра</b> ({ddmm_wd(day)}) до {deadline}: выход или выходной.\n"
                "Если не отметить, будет нарушение графика.")
        sent = 0
        for u in people:
            sent += await self.send(u["telegram_id"], text, _open_schedule())
            await asyncio.sleep(0.05)
        return sent

    async def violations_found(self, created: list[dict]) -> None:
        """Monday check: managers learn who fell below the norm; each decides in «Дисциплина»."""
        from . import networks, violations

        if not created:
            return
        v0 = created[0]
        users = {v["id"]: svc.get_user(self.conn, v["user_id"]) for v in created}

        unmarked = v0.get("kind") == "unmarked"

        def body(items: list[dict]) -> str:
            lines = [f"📋 <b>Нарушения графика</b> — {violations.period(v0)}",
                     (f"Не отметили график на этот день: {len(items)}. Нужно решение по каждому: штраф или простить."
                      if unmarked else
                      f"Не выполнили норму выходов: {len(items)}. Нужно решение по каждому: штраф или оправдание."), ""]
            lines += [f"• {escape(svc.full_name(users[v['id']]))}" + ("" if unmarked else f": {v['actual_days']} из {v['required_days']}")
                      for v in items]
            lines += ["", "Раздел «Дисциплина» в приложении."]
            return "\n".join(lines)[:3900]

        markup = _open_admin()
        await self.to_admins(body(created), markup)
        admins = set(svc.admin_telegram_ids(self.conn))
        by_trainer: dict[int, tuple[dict, list[dict]]] = {}
        for v in created:
            trainer = networks.trainer_of(self.conn, users[v["id"]])
            if trainer and trainer["telegram_id"] not in admins:
                by_trainer.setdefault(trainer["id"], (trainer, []))[1].append(v)
        for trainer, items in by_trainer.values():
            await self.send(trainer["telegram_id"], body(items), markup)

    async def _violation_out(self, v: dict, tx: dict | None, resolver: dict, approver: dict | None = None) -> None:
        """The employee's message and the archive copy, each followed by the proof photos."""
        from . import violations

        user = svc.get_user(self.conn, v["user_id"])
        files = violations.media_ids(v)
        if await self.send(user["telegram_id"], violation_text(v, user, tx)):
            await self.send_album(user["telegram_id"], files)
        tags = ["нарушение", "штраф" if v["status"] == "penalized" else "оправдание"]
        if await self.archive(tags, violation_archive_text(v, user, resolver, tx, approver), user):
            await self.send_album(int(app_settings.get_raw(self.conn, "archive_chat_id")), files)

    async def violation_resolved(self, v: dict, tx: dict | None, actor: dict) -> None:
        if v["status"] == "penalized" and tx and tx["status"] == "pending_admin_approval":
            # a trainer's fine: owners approve it first; the employee hears about it only then (ТЗ 5.3)
            user = svc.get_user(self.conn, v["user_id"])
            await self.archive(["нарушение", "штраф", "согласование"],
                               f"🧾 ЗАЯВКА НА ШТРАФ ЗА ПРОПУСК ГРАФИКА {finance.staff_text(tx)} — {name_handle(user)}\n"
                               f"Норма выходов: {v['actual_days']}/{v['required_days']}\n"
                               f"Причина: {escape(tx['reason'])}\nТренер: {name_handle(actor)}", user, tx["media_file_id"])
            await self.fine_requested(tx, actor)
            return
        await self._violation_out(v, tx, actor)

    async def fines_paid(self, user_id: int, rows: list[dict], actor: dict) -> None:
        user = svc.get_user(self.conn, user_id)
        total = sum(r["amount"] for r in rows)
        await self.send(user["telegram_id"], f"✅ Штрафы погашены: {len(rows)}. Долга по штрафам нет.")
        await self.archive(["погашение", "штраф"], f"✅ ПОГАШЕНО {total} ₽ ({len(rows)} шт.) — {who(user)}\n"
                           f"Погасил: {who(actor)}", user)

    # ------------------------------------------------------------ payouts (ТЗ модуль 10)

    async def payout_committed(self, batch: dict, actor: dict) -> None:
        from . import export, payouts

        period = f"{batch['period_from'][8:10]}.{batch['period_from'][5:7]}–{batch['period_to'][8:10]}.{batch['period_to'][5:7]}"
        lines = [f"💸 <b>ВЕДОМОСТЬ ЗАФИКСИРОВАНА: {escape(batch['title'])}</b>", f"Период {period}, итого {batch['total']} ₽",
                 f"Зафиксировал: {who(actor)}", "", "<b>📂 ДОКУМЕНТЫ ОСНОВАНИЯ:</b>",
                 f"1️⃣ Подтверждение перевода денег: {len(batch['proof_media'])} файл(ов)",
                 f"2️⃣ Документы по вычетам: {len(batch['deduction_media'])} файл(ов)", "", "<b>ДЕТАЛИЗАЦИЯ ПО СОТРУДНИКАМ:</b>"]
        for it in batch["items"]:
            parts = [f"База {it['base']}"]
            if it["bonuses"]:
                parts.append(f"Бонус +{it['bonuses']}")
            if it["fines"]:
                parts.append(f"Штраф -{it['fines']}")
            if it["deductions"]:
                parts.append(f"<b>Вычет -{it['deductions']}</b>")
            debt = f" (долг остаётся {it['carried_debt']} ₽)" if it["carried_debt"] else ""
            lines.append(f"• {escape(it['name'])}: {' | '.join(parts)} | <b>Итого {it['total']} ₽</b>{debt}")
        sm = batch.get("summary")
        if sm and sm.get("revenue_per_piece"):
            lines += ["", "<b>ВЛАДЕЛЕЦ:</b>",
                      f"Выручка: {sm['pieces']} шт. × {sm['revenue_per_piece']} ₽ = {sm['revenue']} ₽",
                      f"Выплачено людям: {sm['paid']} ₽ (за штуки {sm['pieces_pay']}, тренерские {sm['commission']}, "
                      f"бонусы {sm['bonuses']}, удержано {sm['withheld']})",
                      f"<b>Заработок владельца: {sm['owner']} ₽</b>"]
        await self.archive(["выплата"], "\n".join(lines)[:3900])
        chat = app_settings.get_raw(self.conn, "archive_chat_id")
        if chat:
            for i, fid in enumerate(batch["proof_media"]):
                await self._photo_or_text(int(chat), fid, f"#выплата 1️⃣ Подтверждение перевода — {escape(batch['title'])} ({i + 1})")
            for i, fid in enumerate(batch["deduction_media"]):
                await self._photo_or_text(int(chat), fid, f"#выплата #вычет 2️⃣ Документы по вычетам — {escape(batch['title'])} ({i + 1})")
        await self.send_document(actor["telegram_id"], export.payout_xlsx(batch), f"vyplata_{batch['id']}.xlsx",
                                 f"Реестр выплаты «{escape(batch['title'])}»")
        await self.send(actor["telegram_id"], f"<pre>{escape(payouts.bank_text(batch))}</pre>")
        for it in batch["items"]:
            user = svc.get_user(self.conn, it["user_id"])
            if it["total"] > 0:
                await self.send(user["telegram_id"], f"💸 Начислена выплата {it['total']} ₽ за {period}.")

    # ------------------------------------------------------------ goals (ТЗ модуль 5)

    async def goal_created(self, goal: dict) -> int:
        from . import goals

        period = f"{goal['date_from'][8:10]}.{goal['date_from'][5:7]}–{goal['date_to'][8:10]}.{goal['date_to'][5:7]}"
        # ТЗ №18: the reports already approved inside the period count from the start
        pool = [u for u in svc.list_users(self.conn) if goals.eligible(self.conn, goal, u)]
        reports, pieces = goals.counted_so_far(self.conn, goal, [u["id"] for u in pool])
        creator = svc.get_user_by_tg(self.conn, goal["created_by"])
        kind = {"personal": "личная", "team": "командная", "network": "сетевая", "group": "для группы",
                "point": f"точки «{goal['location']}» на день"}[goal["kind"]]
        await self.archive(["цель"], (
            f"🎯 <b>ЦЕЛЬ СОЗДАНА: {escape(goal['title'])}</b>\nТип: {kind}, норма {goal['target']} шт., период {period}\n"
            f"Принятых отчётов за период уже: {reports} ({pieces} шт.) — учитываются автоматически"
            + (f"\nСоздал: {who(creator)}" if creator else "")))
        if goal["kind"] == "point":
            every = "каждый день" if goal["date_to"] == goals.FOREVER else period
            text = (f"🎯 <b>Цель точки «{escape(goal['location'])}»: {escape(goal['title'])}</b>\n"
                    f"Общая норма на день: {goal['target']} шт., {every}. Считается всё, что сделали на точке за день; "
                    f"день начинается в {goal['reset_time']}.")
        else:
            text = (f"🎯 <b>Новая цель: {escape(goal['title'])}</b>\n"
                    f"{'Норма' if goal['mode'] == 'each' or goal['kind'] == 'personal' else 'Общая норма'}: {goal['target']} шт., {period}")
        if goal["description"]:
            text += f"\n{escape(goal['description'])}"
        if goal["prize"] or goal["prize_type"]:
            text += f"\n🏆 Приз: {escape(prize_line(goal))}"
        if goal["kind"] == "personal":
            user = svc.get_user(self.conn, goal["user_id"])
            _, mine = goals.counted_so_far(self.conn, goal, [user["id"]])
            already = f"\nУже учтены ваши принятые отчёты с начала периода: {mine} шт." if mine else ""
            return await self._photo_or_text(user["telegram_id"], goal["prize_photo"],
                                             text + "\n\nПрогресс считается по принятым отчётам." + already)
        # «Участвую» — for team and network goals; a group and a point's people take part by themselves
        join = (InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🙋 Участвую", callback_data=f"goal:{goal['id']}")]])
                if goal["kind"] in ("team", "network") else None)
        sent = 0
        for user in svc.list_users(self.conn):
            if user["account_state"] == "active" and goals.eligible(self.conn, goal, user):
                sent += await self._photo_or_text(user["telegram_id"], goal["prize_photo"], text, join)
                await asyncio.sleep(0.05)
        return sent

    async def goals_completed(self, done: list) -> None:
        for goal, people in done:
            for p in people:
                await self.send(p["telegram_id"], (
                    f"✅ Цель точки «{escape(goal['location'])}» на сегодня выполнена: <b>{escape(goal['title'])}</b>! "
                    f"Вместе сделали {goal['target']} шт. и больше." if goal["kind"] == "point" else
                    f"✅ Цель выполнена: <b>{escape(goal['title'])}</b>! Норма {goal['target']} шт."))
            creator = svc.get_user_by_tg(self.conn, goal["created_by"])
            if creator:
                names = ", ".join(escape(svc.full_name(p)) for p in people)
                await self.send(creator["telegram_id"], f"🎯 Цель «{escape(goal['title'])}» выполнена: {names}")

    async def goal_updated(self, update) -> int:
        """ТЗ №20: a critical change, the editor chose «уведомить» — every participant learns what changed."""
        from . import goals

        goal = update.goal
        text = (f"⚠️ <b>Важное обновление по цели «{escape(goal['title'])}»</b>: изменены условия или сроки. "
                f"Проверьте детали.\n" + "\n".join(change_lines(update.old, goal, update.critical)))
        sent = 0
        for p in goals.participants(self.conn, goal["id"]):
            sent += await self.send(p["telegram_id"], text, self._event_markup())
            await asyncio.sleep(0.05)
        return sent

    async def goal_revoked(self, goal: dict, people: list[dict]) -> None:
        from . import goals

        for p in people:
            _, mine = goals.counted_so_far(self.conn, goal, [p["user_id"]])
            total = goals.view(self.conn, goal)["total"] if goal["mode"] == "shared" else mine
            await self.send(p["telegram_id"],
                            f"📈 Норму цели «{escape(goal['title'])}» подняли до {goal['target']} шт. "
                            f"Отметка «выполнено» снята — до цели осталось {max(0, goal['target'] - total)} шт.")

    async def goal_annulled(self, goal: dict, user_id: int, actor: dict) -> None:
        user = svc.get_user(self.conn, user_id)
        reason = self.conn.execute("SELECT annul_reason FROM goal_participants WHERE goal_id = ? AND user_id = ?",
                                   (goal["id"], user_id)).fetchone()[0]
        await self.send(user["telegram_id"], f"🚫 Ваш результат в цели «{escape(goal['title'])}» аннулирован.\n"
                                             f"Причина: {escape(reason or '')}")
        await self.archive(["цель", "аннулирование"], (
            f"🚫 <b>РЕЗУЛЬТАТ В ЦЕЛИ АННУЛИРОВАН</b>\nЦель: {escape(goal['title'])}\nСотрудник: {who(user)}\n"
            f"Причина: {escape(reason or '')}\nРешение: {who(actor)}"))

    # ------------------------------------------------------------ live events (ТЗ модуль 7)

    def _event_markup(self) -> InlineKeyboardMarkup | None:
        if not settings.webapp_url.startswith("https://"):
            return None
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏁 Открыть событие",
                                                                           web_app=WebAppInfo(url=settings.webapp_url))]])

    async def event_created(self, event: dict) -> int:
        from . import events

        text = (f"🏁 <b>Событие: {escape(event['title'])}</b>\n{escape(events.rule(event))}\n"
                f"👑 {escape(events.how_to_win(event))}\n"
                f"⏱ {_shown('starts_at', event['starts_at'])} – {_shown('ends_at', event['ends_at'])}")
        if event["prize"] or event["prize_type"]:
            text += f"\n🏆 Приз: {escape(prize_line(event))}"
        if event["description_html"] or event["description"]:
            text += "\n\n" + (event["description_html"] or escape(event["description"]))
        sent = 0
        for user in svc.list_users(self.conn):
            if events.eligible(event, user, self.conn):
                # the prize photo or video carries the text (or goes first when the text is too long for a caption)
                sent += await self._deliver(user["telegram_id"], text, event["prize_photo"], self._event_markup(),
                                            event["prize_media_kind"]) is not None
                await asyncio.sleep(0.05)
        return sent

    async def event_entry(self, entry: dict) -> None:
        from . import events, reports

        event = events.get(self.conn, entry["event_id"])
        user = svc.get_user(self.conn, entry["user_id"])
        caption = (f"🏁 <b>Заявка в событии «{escape(event['title'])}»</b>\n{who(user)}: <b>{entry['value']} шт.</b>"
                   f" (попытка {entry['attempt']})")
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"ev:ok:{entry['id']}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ev:no:{entry['id']}")]])
        for chat in reports.reviewers(self.conn, user):
            await self._photo_or_text(chat, entry["photo_file_id"], caption, markup)

    async def event_reviewed(self, result, actor: dict) -> None:
        from . import events

        entry, event = result.entry, result.event
        user = svc.get_user(self.conn, entry["user_id"])
        if entry["status"] == "confirmed":
            text = (f"👑 Вы среди победителей события «{escape(event['title'])}»!" if event["mode"] == "first_to_reach"
                    else f"✅ Засчитано {entry['value']} шт. в событии «{escape(event['title'])}».")
        else:
            left = events.view(self.conn, event, user)["my"]["attempts_left"]
            text = (f"❌ Заявка в событии «{escape(event['title'])}» отклонена.\nПричина: {escape(entry['reason'] or '')}\n"
                    + (f"Осталось попыток: {left}. Исправьте и отправьте снова." if left else "Попытки закончились."))
        await self.send(user["telegram_id"], text, self._event_markup())
        if result.finished:
            await self.event_finished(event)

    def _event_people(self, event: dict) -> list[int]:
        """Everyone who may take part, plus those already on the board — each once."""
        from . import events

        on_board = {r["user_id"] for r in events.leaderboard(self.conn, event)}
        chats = [u["telegram_id"] for u in svc.list_users(self.conn) if u["id"] in on_board or events.eligible(event, u, self.conn)]
        return list(dict.fromkeys(chats))

    async def event_updated(self, update) -> int:
        """ТЗ №20: a critical change confirmed with «уведомить»."""
        event = update.event
        head = (f"🔄 <b>Событие «{escape(event['title'])}» перезапущено</b>" if update.restarted
                else f"⚠️ <b>Важное обновление по событию «{escape(event['title'])}»</b>: изменены условия или сроки. "
                     f"Проверьте детали.")
        text = head + "\n" + "\n".join(change_lines(update.old, event, update.critical))
        sent = 0
        for chat in self._event_people(event):
            sent += await self.send(chat, text, self._event_markup())
            await asyncio.sleep(0.05)
        return sent

    async def entry_annulled(self, result, actor: dict) -> None:
        entry, event = result.entry, result.event
        user = svc.get_user(self.conn, entry["user_id"])
        text = (f"↩️ Ваш результат в событии «{escape(event['title'])}» ({entry['value']} шт.) отменён.\n"
                f"Причина: {escape(entry['reason'] or '')}")
        if event["status"] == "active":
            text += "\nПопытка не сгорела — можно отправить заявку снова."
        await self.send(user["telegram_id"], text, self._event_markup())
        await self.archive(["событие", "аннулирование"], (
            f"↩️ <b>РЕЗУЛЬТАТ В СОБЫТИИ ОТМЕНЁН</b>\nСобытие: {escape(event['title'])}\nСотрудник: {who(user)}, "
            f"{entry['value']} шт.\nПричина: {escape(entry['reason'] or '')}\nРешение: {who(actor)}"))

    async def event_finished(self, event: dict) -> None:
        from . import events

        board = events.leaderboard(self.conn, event)
        winners = [r for r in board if r["winner"]]
        lines = [f"🏁 <b>Событие «{escape(event['title'])}» завершено</b>"]
        if winners:
            lines.append("👑 Победители: " + ", ".join(f"{escape(r['name'])} ({r['confirmed']} шт.)" for r in winners))
        else:
            lines.append("Победителей нет.")
        if event["prize"] or event["prize_type"]:
            lines.append(f"🏆 Приз: {escape(prize_line(event))}")
        text = "\n".join(lines)
        on_board = {r["user_id"] for r in board}
        chats = [u["telegram_id"] for u in svc.list_users(self.conn) if u["id"] in on_board or events.eligible(event, u, self.conn)]
        chats.append(event["created_by"])
        for chat in dict.fromkeys(chats):  # each person once, even the creator who is also a participant
            await self.send(chat, text)
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------ the day's global actual (ТЗ 3.5)

    async def team_approved(self, shift: dict, report: dict | None = None) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        await self.send(user["telegram_id"],
                        f"✅ Ваш отчет принят автоматически. Общая цель команды выполнена!\n{shift_place(shift)}")
        await self._archive_report(
            ["отчёт", "общий_факт"],
            f"🟢 АВТОМАТИЧЕСКОЕ ОДОБРЕНИЕ: {who(user)}. Причина: Общий план выполнен\n{shift_place(shift)}\n"
            f"План {shift['volume']}, факт {shift['fact_volume']} шт.", user, report)

    async def global_actual_set(self, day: date, result, actor: dict) -> None:
        row = result.row
        verdict = "✅ команда выполнила план" if row["met"] else "⚠️ план не выполнен — недоборы на ручной проверке"
        was = f" (было {result.old_value})" if result.old_value is not None else ""
        text = (f"📊 Общий факт за {ddmm_wd(day)}: <b>{row['actual']}</b>{was} из плана {row['plan']} шт.\n{verdict}"
                + (f"\nПринято автоматически: {len(result.approved)}" if result.approved else ""))
        await self.archive(["общий_факт"], f"{text}\nВвёл: {who(actor)}")
        for chat in svc.owner_telegram_ids():
            if chat != actor["telegram_id"]:
                await self.send(chat, f"{text}\nВвёл: {who(actor)}")
        for shift in result.approved:
            await self.team_approved(shift, reports_latest(self.conn, shift["id"]))

    async def fines_annulled(self, shifts: list[dict], actor: dict) -> None:
        for s in shifts:
            user = svc.get_user(self.conn, s["user_id"])
            amount = s.get("annulled_amount")
            await self.send(user["telegram_id"],
                            f"↩️ Штраф за смену {shift_place(s)} аннулирован: команда справилась с общим планом.")
            await self.archive(["аннулирование", "штраф"],
                               f"↩️ ШТРАФ АННУЛИРОВАН {amount} ₽ — {who(user)}\n{shift_place(s)}\n"
                               f"Причина: команда справилась\nАннулировал: {who(actor)}", user)

    async def global_actual_reminder(self, day: date, morning: bool) -> int:
        plan = day_actual.day_plan(self.conn, day)
        if not plan or day_actual.get(self.conn, day):
            return 0
        when = "вчера" if morning else "сегодня"
        markup = None
        if settings.webapp_url.startswith("https://"):
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="Ввести общий факт", web_app=WebAppInfo(url=settings.webapp_url))]])
        sent = 0
        for chat in svc.owner_telegram_ids():
            sent += await self.send(chat, f"📊 Не введён общий факт за {when}, {ddmm_wd(day)}. План команды {plan} шт.\n"
                                          "Пока его нет, отчёты с недобором ждут, а штрафовать за недобор нельзя.", markup)
        return sent

    async def report_reminder(self, shift: dict, deadline_text: str) -> bool:
        user = svc.get_user(self.conn, shift["user_id"])
        return await self.send(
            user["telegram_id"],
            f"⚠️ Вы не сдали отчёт за смену {shift_place(shift)}.\n"
            f"Напоминаем: за отсутствие отчёта может быть начислен штраф. У вас есть время до {deadline_text}, "
            "чтобы исправить ситуацию.",
            report_keyboard(shift),
        )


def reports_latest(conn, shift_id: int) -> dict | None:
    from . import reports

    return reports.latest(conn, shift_id)
