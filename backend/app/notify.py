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
    WebAppInfo,
)

from . import app_settings, day_actual, finance, media
from . import permissions as perms
from . import services as svc
from .config import settings
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


def review_keyboard(shift_id: int, can_fine: bool = True) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✅ Принять", callback_data=f"rv:a:{shift_id}"),
             InlineKeyboardButton(text="🔄 На исправление", callback_data=f"rv:r:{shift_id}")]]
    if can_fine:
        rows.append([InlineKeyboardButton(text=f"⚠️ {a} ₽", callback_data=f"rv:f:{shift_id}:{a}") for a in PENALTY_PRESETS])
        rows.append([InlineKeyboardButton(text="⚠️ Штраф — своя сумма", callback_data=f"rv:f:{shift_id}:c")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ack_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Я ознакомился", callback_data=f"ack:{broadcast_id}")]])


def fine_decision_keyboard(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"fx:a:{tx_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"fx:r:{tx_id}"),
    ]])


def no_show_keyboard(shift_id: int, can_fine: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if can_fine:
        rows.append([InlineKeyboardButton(text=f"💸 {a} ₽", callback_data=f"ns:f:{shift_id}:{a}") for a in PENALTY_PRESETS])
        rows.append([InlineKeyboardButton(text="💸 Своя сумма", callback_data=f"ns:f:{shift_id}:c")])
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

    async def _deliver(self, chat_id: int, body: str, media: str | None, markup) -> int | None:
        """Send one broadcast message; returns the message id of the part carrying the buttons."""
        if self.bot is None:
            log.info("[broadcast → %s] %s", chat_id, body[:80])
            return None
        try:
            if media and not media.startswith("local:"):
                if len(body) <= 1000:
                    msg = await self._retry(lambda: self.bot.send_photo(chat_id, media, caption=body, parse_mode="HTML",
                                                                        reply_markup=markup))
                    return msg.message_id
                await self._retry(lambda: self.bot.send_photo(chat_id, media))
            msg = await self._retry(lambda: self.bot.send_message(chat_id, body, parse_mode="HTML", reply_markup=markup,
                                                                  disable_web_page_preview=True))
            return msg.message_id
        except Exception as exc:
            log.warning("broadcast to %s failed: %s", chat_id, exc)
            return None

    async def broadcast(self, broadcast_id: int, recipients: list[dict], html: str, signed: bool = True,
                        media: str | None = None, require_ack: bool = False) -> int:
        """html must already be sanitized (richtext.sanitize). Every delivery is recorded for read receipts."""
        body = self.broadcast_body(html, signed)
        markup = ack_keyboard(broadcast_id) if require_ack else None
        delivered = 0
        for i, user in enumerate(recipients):
            message_id = await self._deliver(user["telegram_id"], body, media, markup)
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
            message_id = await self._deliver(r["telegram_id"], body, b["media_file_id"], ack_keyboard(broadcast_id))
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
        """Owners, admins and the trainer of the person's network (ТЗ модуль 8)."""
        from . import networks

        await self.to_admins(text, markup, exclude)
        trainer = networks.trainer_of(self.conn, user)
        if trainer and trainer["telegram_id"] != exclude and trainer["telegram_id"] not in svc.admin_telegram_ids(self.conn):
            await self.send(trainer["telegram_id"], text, markup if trainer_buttons else None)

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
            pending = next((t for t in finance.linked(self.conn, shift["id"], ("report",))
                            if t["status"] == "pending_admin_approval"), None)
            if pending:
                await self.send(user["telegram_id"], f"❌ Отчёт отклонён: {place}{note}")
                tags, line = ["отчёт", "штраф", "согласование"], f"❌ ОТЧЁТ ОТКЛОНЁН | ШТРАФ {report['penalty_amount']} ₽ НА СОГЛАСОВАНИИ"
                await self.fine_requested(pending, actor)
            else:
                await self.send(user["telegram_id"], f"❌ Отчёт отклонён, штраф {report['penalty_amount']} ₽: {place}{note}")
                tags, line = ["отчёт", "штраф"], f"❌ ОТЧЁТ ОТКЛОНЁН | ШТРАФ {report['penalty_amount']} ₽"
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
            tags, line = ["неявка", "штраф", "согласование"], f"💸 НЕЯВКА | ШТРАФ {pending['amount']} ₽ НА СОГЛАСОВАНИИ"
            await self.fine_requested(pending, actor)
        elif shift["shortfall_status"] == "fined":
            await self.send(user["telegram_id"], f"💸 Штраф {shift['shortfall_penalty']} ₽ за неявку: {place}{note}")
            tags, line = ["неявка", "штраф"], f"💸 НЕЯВКА | ШТРАФ {shift['shortfall_penalty']} ₽"
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
        text = (f"🧾 <b>Заявка на штраф</b> от тренера {who(trainer)}\n{who(user)}: <b>{tx['amount']} ₽</b>\n"
                f"Причина: {escape(tx['reason'])}")
        for chat in svc.owner_telegram_ids():
            await self._photo_or_text(chat, tx["media_file_id"], text, fine_decision_keyboard(tx["id"]))

    async def tx_created(self, tx: dict, actor: dict) -> None:
        user = svc.get_user(self.conn, tx["user_id"])
        amount, reason = tx["amount"], escape(tx["reason"])
        if tx["status"] == "pending_admin_approval":
            await self.archive(["штраф", "согласование"], f"🧾 ЗАЯВКА НА ШТРАФ {amount} ₽ — {who(user)}\n"
                               f"Причина: {reason}\nТренер: {who(actor)}", user, tx["media_file_id"])
            await self.fine_requested(tx, actor)
            return
        if tx["type"] == "fine":
            await self.send(user["telegram_id"], f"💸 Вам начислен штраф {amount} ₽.\nПричина: {reason}")
            tags, line = ["штраф"], f"💸 ШТРАФ {amount} ₽"
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
        user = svc.get_user(self.conn, tx["user_id"])
        trainer = svc.get_user_by_tg(self.conn, tx["created_by"])
        comment = f"\nКомментарий: {escape(tx['decision_comment'])}" if tx["decision_comment"] else ""
        if tx["status"] == "active":
            await self.send(user["telegram_id"], f"💸 Вам начислен штраф {tx['amount']} ₽.\nПричина: {escape(tx['reason'])}")
            verdict, tags = "✅ ОДОБРЕН", ["штраф"]
        else:
            verdict, tags = "❌ ОТКЛОНЁН", ["штраф", "отклонён"]  # the employee is not told (ТЗ 5.3)
        if trainer:
            await self.send(trainer["telegram_id"], f"Заявка на штраф {tx['amount']} ₽ ({who(user)}): "
                            f"{'одобрена' if tx['status'] == 'active' else 'отклонена'}.{comment}")
        await self.archive(tags, f"{verdict} ШТРАФ {tx['amount']} ₽ — {who(user)}\nПричина: {escape(tx['reason'])}"
                           f"{comment}\nРешил: {who(actor)}", user)

    async def tx_cancelled(self, tx: dict, actor: dict, was: str) -> None:
        user = svc.get_user(self.conn, tx["user_id"])
        title = finance.TYPE_TITLES[tx["type"]]
        amount = f" {tx['amount']} ₽" if tx["amount"] else ""
        if was != "pending_admin_approval":
            await self.send(user["telegram_id"], f"↩️ {title}{amount} аннулирован.\nПричина: {escape(tx['decision_comment'] or '')}")
        await self.archive(["аннулирование", {"fine": "штраф", "bonus": "бонус"}.get(tx["type"], "вычет")],
                           f"↩️ АННУЛИРОВАН {title.upper()}{amount} — {who(user)}\nБыло: {escape(tx['reason'])}\n"
                           f"Причина: {escape(tx['decision_comment'] or '')}\nАннулировал: {who(actor)}", user)

    async def fines_paid(self, user_id: int, rows: list[dict], actor: dict) -> None:
        user = svc.get_user(self.conn, user_id)
        total = sum(r["amount"] for r in rows)
        await self.send(user["telegram_id"], f"✅ Штрафы погашены: {len(rows)} на {total} ₽. Долг по штрафам — 0 ₽.")
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
                            f"↩️ Штраф {amount} ₽ за смену {shift_place(s)} аннулирован: команда справилась с общим планом.")
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
