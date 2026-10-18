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

from . import app_settings, media
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
        await self.send(
            user["telegram_id"],
            f"📝 Отчёт отправлен на проверку: {shift_place(shift)}\n{_shortfall_line(shift)}\n"
            "Пока его не проверили, можно заменить его в приложении.",
        )
        attempt = f" (попытка {report['attempt']})" if report["attempt"] > 1 else ""
        caption = f"📝 <b>Отчёт на проверку</b>{attempt}\n{who(user)}\n{shift_place(shift)}\n{_shortfall_line(shift)}"
        for chat in reports.reviewers(self.conn, user):
            reviewer = svc.get_user_by_tg(self.conn, chat)
            fine = reviewer is not None and perms.can(reviewer, "fine_direct", user) or chat in settings.admin_ids
            await self._photo_or_text(chat, report["photo_file_id"], caption, review_keyboard(shift["id"], fine))

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
            fine = reviewer is not None and perms.can(reviewer, "fine_direct", user) or chat in settings.admin_ids
            await self.send(chat, f"🚫 <b>Неявка</b>: нет отчёта\n{who(user)}\n{place}, план {shift['volume']} шт.\n\n"
                            "Назначьте штраф, отпустите без штрафа или внесите результат за сотрудника.",
                            no_show_keyboard(shift["id"], fine))

    async def no_show_resolved(self, shift: dict, actor: dict) -> None:
        user = svc.get_user(self.conn, shift["user_id"])
        place = shift_place(shift)
        note = f"\nПричина: {escape(shift['report_note'])}" if shift["report_note"] else ""
        if shift["shortfall_status"] == "fined":
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

    async def report_reminder(self, shift: dict, deadline_text: str) -> bool:
        user = svc.get_user(self.conn, shift["user_id"])
        return await self.send(
            user["telegram_id"],
            f"⚠️ Вы не сдали отчёт за смену {shift_place(shift)}.\n"
            f"Напоминаем: за отсутствие отчёта может быть начислен штраф. У вас есть время до {deadline_text}, "
            "чтобы исправить ситуацию.",
            report_keyboard(shift),
        )
