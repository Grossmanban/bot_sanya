"""Telegram notifications. Messages are built from the DB at send time, so totals are current."""

import asyncio
import logging
import sqlite3
from datetime import date
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from . import services as svc
from .timeutil import ddmm, ddmm_wd

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


def who(user: dict) -> str:
    name = escape(svc.full_name(user))
    if user.get("username"):
        return f"@{escape(user['username'])}, {name}"
    return f'<a href="tg://user?id={user["telegram_id"]}">{name}</a> (без username)'


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


def registration_text(user: dict) -> str:
    username = f"@{escape(user['username'])}" if user["username"] else "нет"
    return (
        "📝 <b>Новая заявка на регистрацию</b>\n"
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
        "❌ <b>Отмена смены</b>\n"
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


# ---------------------------------------------------------------- sender


class Notifier:
    def __init__(self, bot: Bot | None, conn: sqlite3.Connection):
        self.bot = bot
        self.conn = conn
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> None:
        """Fire-and-forget: API responses never wait on Telegram."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def send(
        self, chat_id: int, text: str, markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None = None
    ) -> bool:
        if self.bot is None:
            log.info("[notify → %s] %s", chat_id, text)
            return False
        try:
            await self.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML",
                                        disable_web_page_preview=True)
            return True
        except TelegramAPIError as exc:
            # user blocked the bot, chat not found, etc. — keep going for the other recipients
            log.warning("send to %s failed: %s", chat_id, exc)
            return False
        except Exception:  # network errors must not kill the caller
            log.exception("send to %s failed", chat_id)
            return False

    async def to_admins(self, text: str, markup: InlineKeyboardMarkup | None = None, exclude: int | None = None):
        for admin_id in svc.admin_telegram_ids(self.conn):
            if admin_id != exclude:
                await self.send(admin_id, text, markup)

    async def shift_event(self, event: str, shift_id: int, old: dict | None = None) -> None:
        shift = svc.get_shift(self.conn, shift_id)
        if shift is None:
            return
        user = svc.get_user(self.conn, shift["user_id"])
        if event == "SHIFT_CREATED":
            await self.to_admins(shift_created_text(self.conn, user, shift))
        elif event == "SHIFT_URGENT_REQUESTED":
            await self.to_admins(urgent_text(user, shift), urgent_keyboard(shift_id))
        elif event == "SHIFT_CANCELLED":
            await self.to_admins(cancelled_text(self.conn, user, shift))
        elif event == "SHIFT_RESTORED":
            await self.to_admins(restored_text(self.conn, user, shift))
        elif event == "SHIFT_FINAL_CANCELLED":
            await self.to_admins(final_cancel_text(self.conn, user, shift), penalty_keyboard(shift_id))
        elif event == "SHIFT_EDITED_BY_USER" and old:
            await self.to_admins(edited_text(user, shift, old))

    async def admin_edit(self, admin: dict, shift: dict, reason: str) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        await self.to_admins(admin_edited_text(admin, user, shift, reason), exclude=admin["telegram_id"])

    async def urgent_decided(self, shift: dict, approved: bool) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        if approved:
            text = f"✅ Ваша срочная заявка на сегодня ({ddmm(_day(shift))}) одобрена! Ждём вас в {shift['start_time']}."
        else:
            text = f"❌ Ваша срочная заявка на сегодня ({ddmm(_day(shift))}) отклонена администратором."
        await self.send(user["telegram_id"], text)

    async def penalty_assigned(self, shift: dict) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        await self.send(
            user["telegram_id"],
            f"💸 Вам начислен штраф {shift['penalty_amount']} ₽ за окончательную отмену смены {ddmm(_day(shift))}.",
        )

    async def access_changed(self, user: dict, previous_status: str, open_app: InlineKeyboardMarkup | None) -> None:
        if user["status"] == "approved":
            if svc.claim_welcome(self.conn, user["id"]):
                await self.send(user["telegram_id"], "Добро пожаловать в систему!", open_app)
            else:
                await self.send(user["telegram_id"], "Доступ снова открыт.", open_app)
        elif previous_status == "pending":
            await self.send(user["telegram_id"], "В доступе отказано.")
        else:
            await self.send(user["telegram_id"], "Доступ к графику закрыт администратором.", ReplyKeyboardRemove())

    async def role_changed(self, user: dict) -> None:
        if user["role"] == "admin":
            await self.send(
                user["telegram_id"],
                "Вам выданы права администратора. В приложении появилась вкладка «Админ», "
                "сюда будут приходить заявки и уведомления.",
                ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📋 Распред на завтра")]], resize_keyboard=True),
            )
        else:
            await self.send(user["telegram_id"], "Права администратора сняты.", ReplyKeyboardRemove())
