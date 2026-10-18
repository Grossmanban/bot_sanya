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
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from . import app_settings
from . import permissions as perms
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


def person_tag(user: dict) -> str:
    """#Иван_Петров — lets the archive be filtered by person."""
    return "#" + re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "_", svc.full_name(user)).strip("_")


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


def shortfall_keyboard(shift_id: int) -> InlineKeyboardMarkup:
    presets = [InlineKeyboardButton(text=f"{a} ₽", callback_data=f"sf:{shift_id}:{a}") for a in PENALTY_PRESETS]
    return InlineKeyboardMarkup(inline_keyboard=[
        presets,
        [InlineKeyboardButton(text="Своя сумма…", callback_data=f"sf:{shift_id}:custom"),
         InlineKeyboardButton(text="Без штрафа", callback_data=f"sf:{shift_id}:0")],
    ])


def _shortfall_line(shift: dict) -> str:
    short = svc.shortfall(shift)
    return f"План {shift['volume']}, факт {shift['fact_volume']} шт. — {svc.shortfall_percent(shift)}% (−{short} шт.)"


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

    async def broadcast(self, broadcast_id: int, recipients: list[dict], html: str, signed: bool = True) -> int:
        """html must already be sanitized (richtext.sanitize)."""
        body = self.broadcast_body(html, signed)
        delivered = 0
        for i, user in enumerate(recipients):
            delivered += await self.send(user["telegram_id"], body)
            if i % 10 == 9:
                self.conn.execute("UPDATE broadcasts SET delivered = ? WHERE id = ?", (delivered, broadcast_id))
            await asyncio.sleep(0.05)  # stay well under Telegram's ~30 msg/s limit
        self.conn.execute("UPDATE broadcasts SET delivered = ? WHERE id = ?", (delivered, broadcast_id))
        return delivered

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
                await self.send(user["telegram_id"], "Добро пожаловать в систему!", open_app)
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

    async def fact_result(self, shift: dict, actor_tg: int | None = None) -> None:
        """After a fact is saved: warn the employee and ask admins for a decision if the plan was missed."""
        if not svc.shortfall(shift) or shift["shortfall_status"]:
            return
        user = svc.get_user(self.conn, shift["user_id"])
        day = _day(shift)
        place = f"{ddmm_wd(day)} {shift['start_time']}–{shift['end_time']}, «{escape(shift['location'] or '')}»"
        await self.send(
            user["telegram_id"],
            f"⚠️ <b>Не по норме</b>\n{place}\n{_shortfall_line(shift)}\n\nАдминистраторы это видят и могут назначить штраф.",
        )
        await self.to_admins(
            f"📉 <b>Не по норме</b>\n{who(user)}\n{place}\n{_shortfall_line(shift)}\n\nНазначить штраф?",
            shortfall_keyboard(shift["id"]),
            exclude=actor_tg,
        )


    async def shortfall_decided(self, shift: dict, admin_name: str, admin_tg: int) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        day = ddmm(_day(shift))
        if shift["shortfall_status"] == "fined":
            await self.send(user["telegram_id"],
                            f"💸 Штраф {shift['shortfall_penalty']} ₽: не выполнена норма {day} "
                            f"(план {shift['volume']}, факт {shift['fact_volume']} шт.).")
            verdict = f"штраф {shift['shortfall_penalty']} ₽"
            await self.archive(
                ["штраф", "недобор"],
                f"💸 Штраф {shift['shortfall_penalty']} ₽ — {who(user)}\nНедобор {day}: план {shift['volume']}, "
                f"факт {shift['fact_volume']} шт.\nНазначил: {escape(admin_name)}",
                user,
            )
        else:
            await self.send(user["telegram_id"], f"По смене {day} недобор учтён без штрафа.")
            verdict = "без штрафа"
        await self.to_admins(f"Решено по недобору {who(user)} за {day}: {verdict} — {escape(admin_name)}", exclude=admin_tg)
