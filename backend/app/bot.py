import logging
import sqlite3
from datetime import timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from . import app_settings, notify
from . import permissions as perms
from . import services as svc
from .config import settings
from .notify import Notifier
from .timeutil import today_local

log = logging.getLogger(__name__)

RASPRED_BUTTON = "📋 Распред на завтра"


class Registration(StatesGroup):
    name = State()
    confirm = State()


class PenaltyInput(StatesGroup):
    amount = State()


class FactInput(StatesGroup):
    amount = State()


class ShortfallInput(StatesGroup):
    amount = State()


def open_app_keyboard() -> InlineKeyboardMarkup | None:
    if not settings.webapp_url.startswith("https://"):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📅 Открыть график", web_app=WebAppInfo(url=settings.webapp_url))
    ]])


def admin_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=RASPRED_BUTTON)]], resize_keyboard=True)


def build_router(conn: sqlite3.Connection, notifier: Notifier) -> Router:
    router = Router()

    def allowed(telegram_id: int, capability: str, target_user_id: int | None = None) -> dict | None:
        """The acting user if they hold the capability (for that employee), else None."""
        actor = svc.get_user_by_tg(conn, telegram_id)
        target = svc.get_user(conn, target_user_id) if target_user_id else None
        if target_user_id and target is None:
            return None
        return actor if perms.can(actor, capability, target) else None

    def shift_owner(shift_id: int) -> int | None:
        shift = svc.get_shift(conn, shift_id)
        return shift["user_id"] if shift else None

    async def deny(call: CallbackQuery) -> None:
        await call.answer("Недостаточно прав", show_alert=True)

    async def show_menu(message: Message, user: dict) -> None:
        markup = open_app_keyboard()
        text = "Главное меню. График, профиль и история — в приложении."
        if markup is None:
            text += "\n\n⚠️ Ссылка на приложение ещё не настроена (WEBAPP_URL)."
        await message.answer(text, reply_markup=markup)
        if perms.can(user, "raspred"):
            await message.answer("Админ: распред на завтра — кнопкой ниже или /raspred_zavtra",
                                 reply_markup=admin_reply_keyboard())

    # ------------------------------------------------------------ /start and registration

    @router.message(CommandStart(), F.chat.type == "private")
    async def start(message: Message, state: FSMContext):
        tg = message.from_user
        user = svc.ensure_admin(conn, tg.id, tg.first_name or "", tg.last_name or "", tg.username)
        if user is None:
            await state.clear()
            await message.answer(
                "Вы ещё не зарегистрированы в системе.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Регистрация", callback_data="register")
                ]]),
            )
            return
        svc.touch_username(conn, user, tg.username)
        if user["status"] == "pending":
            await message.answer("Ваша заявка уже на рассмотрении. Мы сообщим, когда администратор её рассмотрит.")
        elif user["status"] == "rejected":
            await message.answer("В доступе отказано.")
        elif user["account_state"] == "blocked":
            await message.answer("⛔ Аккаунт заблокирован. Если это ошибка — свяжитесь с руководителем.")
        else:
            if svc.claim_welcome(conn, user["id"]):
                await message.answer("Добро пожаловать в систему!")
            if user["account_state"] == "paused":
                reason = f"\nПричина: {escape(user['state_reason'])}" if user["state_reason"] else ""
                await message.answer(f"⏸ Аккаунт на паузе — новые смены пока недоступны.{reason}", parse_mode="HTML")
            await show_menu(message, user)

    @router.callback_query(F.data == "register")
    async def register(call: CallbackQuery, state: FSMContext):
        if svc.get_user_by_tg(conn, call.from_user.id) is not None:
            await call.answer("Заявка уже существует", show_alert=True)
            return
        await state.set_state(Registration.name)
        await call.message.answer("Напишите фамилию и имя через пробел.\nНапример: <b>Иванов Иван</b>", parse_mode="HTML")
        await call.answer()

    @router.message(StateFilter(Registration.name), F.text)
    async def registration_name(message: Message, state: FSMContext):
        parts = message.text.split()
        if len(parts) < 2 or any(len(p) > 50 for p in parts) or message.text.startswith("/"):
            await message.answer("Нужно две части: фамилия и имя. Например: Иванов Иван")
            return
        last_name, first_name = parts[0], " ".join(parts[1:])
        await state.update_data(first_name=first_name, last_name=last_name)
        await state.set_state(Registration.confirm)
        await message.answer(
            f"Фамилия: <b>{escape(last_name)}</b>\nИмя: <b>{escape(first_name)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📨 Отправить заявку", callback_data="reg_send")],
                [InlineKeyboardButton(text="✏️ Изменить", callback_data="register")],
            ]),
        )

    @router.callback_query(F.data == "reg_send", StateFilter(Registration.confirm))
    async def registration_send(call: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        await state.clear()
        tg = call.from_user
        user, created = svc.create_registration(conn, tg.id, tg.username, data["first_name"], data["last_name"])
        await call.message.edit_reply_markup(reply_markup=None)
        if not created:
            await call.answer("Заявка уже отправлена")
            return
        await call.message.answer("Заявка отправлена. Ожидайте решения администратора.")
        await call.answer()
        await notifier.to_admins(notify.registration_text(user), notify.registration_keyboard(user["id"]))

    @router.callback_query(F.data == "reg_send")
    async def registration_send_stale(call: CallbackQuery):
        await call.answer("Начните регистрацию заново: /start", show_alert=True)

    @router.callback_query(F.data.startswith("reg:"))
    async def registration_decide(call: CallbackQuery):
        actor = allowed(call.from_user.id, "registrations")
        if actor is None:
            return await deny(call)
        _, verdict, user_id = call.data.split(":")
        approve = verdict == "ok"
        try:
            user, changed = svc.decide_registration(conn, int(user_id), approve, call.from_user.id)
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        if not changed:
            status = {"approved": "уже одобрена", "rejected": "уже отклонена"}.get(user["status"], user["status"])
            await call.answer(f"Заявка {status}", show_alert=True)
            return
        verdict_text = "✅ Принята" if approve else "❌ Отклонена"
        await call.message.edit_text(
            f"{call.message.html_text}\n\n<b>{verdict_text}</b> — {escape(call.from_user.full_name)}",
            parse_mode="HTML",
        )
        await call.answer(verdict_text)
        await notifier.access_changed(user, "pending", open_app_keyboard(), actor)

    # ------------------------------------------------------------ urgent requests

    @router.callback_query(F.data.startswith("urg:"))
    async def urgent_decide(call: CallbackQuery):
        _, verdict, shift_id = call.data.split(":")
        if allowed(call.from_user.id, "schedule", shift_owner(int(shift_id))) is None:
            return await deny(call)
        approve = verdict == "ok"
        try:
            result = svc.decide_urgent(conn, int(shift_id), approve, call.from_user.id)
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        if not result.changed:
            await call.answer(f"Уже решено: {notify.svc_status_ru(result.shift['status'])}", show_alert=True)
            return
        verdict_text = "✅ Разрешено" if approve else "⛔ Запрещено"
        await call.message.edit_text(
            f"{call.message.html_text}\n\n<b>{verdict_text}</b> — {escape(call.from_user.full_name)}",
            parse_mode="HTML",
        )
        await call.answer(verdict_text)
        await notifier.urgent_decided(result.shift, approve)

    # ------------------------------------------------------------ penalties

    async def apply_penalty(admin_tg: int, admin_name: str, shift_id: int, amount: int) -> tuple[bool, str]:
        if allowed(admin_tg, "fine_direct", shift_owner(shift_id)) is None:
            return False, "Недостаточно прав"
        try:
            result = svc.set_penalty(conn, shift_id, amount, admin_tg)
        except svc.ServiceError as exc:
            return False, exc.message
        if not result.changed:
            return False, f"Штраф уже назначен: {result.shift['penalty_amount']} ₽"
        await notifier.penalty_assigned(result.shift, admin_name)
        user = svc.get_user(conn, result.shift["user_id"])
        await notifier.to_admins(
            f"💸 Штраф {amount} ₽ назначен сотруднику {notify.who(user)} "
            f"(смена {result.shift['shift_date'][8:10]}.{result.shift['shift_date'][5:7]}) — {escape(admin_name)}",
            exclude=admin_tg,
        )
        return True, f"Штраф {amount} ₽ назначен"

    @router.callback_query(F.data.startswith("pen:"))
    async def penalty_pick(call: CallbackQuery, state: FSMContext):
        _, shift_id, amount = call.data.split(":")
        if allowed(call.from_user.id, "fine_direct", shift_owner(int(shift_id))) is None:
            return await deny(call)
        shift = svc.get_shift(conn, int(shift_id))
        if shift and shift["penalty_amount"] is not None:
            await call.answer(f"Штраф уже назначен: {shift['penalty_amount']} ₽", show_alert=True)
            return
        if amount == "custom":
            await state.set_state(PenaltyInput.amount)
            await state.update_data(shift_id=int(shift_id))
            await call.message.answer("Введите сумму штрафа в рублях (целое число):")
            await call.answer()
            return
        ok, text = await apply_penalty(call.from_user.id, call.from_user.full_name, int(shift_id), int(amount))
        if ok:
            await call.message.edit_text(f"{call.message.html_text}\n\n<b>{escape(text)}</b>", parse_mode="HTML")
        await call.answer(text, show_alert=True)

    @router.message(StateFilter(PenaltyInput.amount), F.text)
    async def penalty_custom(message: Message, state: FSMContext):
        raw = message.text.strip().replace(" ", "").replace("₽", "")
        if not raw.isdigit() or int(raw) > svc.MAX_PENALTY:
            await message.answer("Нужно целое число, например 1500. Попробуйте ещё раз.")
            return
        data = await state.get_data()
        await state.clear()
        _, text = await apply_penalty(message.from_user.id, message.from_user.full_name, data["shift_id"], int(raw))
        await message.answer(text)

    # ------------------------------------------------------------ fact after the shift

    async def save_fact(tg_id: int, shift_id: int, value: int) -> str:
        user = svc.get_user_by_tg(conn, tg_id)
        if user is None or user["status"] != "approved":
            return "Нет доступа"
        try:
            result = svc.set_fact(conn, user, shift_id, value, svc.new_key())
        except svc.ServiceError as exc:
            return exc.message
        if result.changed:
            await notifier.fact_result(result.shift)
        return f"Записал: {value} шт. Спасибо!"

    @router.callback_query(F.data.startswith("fact:"))
    async def fact_quick(call: CallbackQuery):
        _, shift_id, value = call.data.split(":")
        text = await save_fact(call.from_user.id, int(shift_id), int(value))
        if text.startswith("Записал"):
            await call.message.edit_text(f"{call.message.html_text}\n\n<b>✅ {escape(text)}</b>", parse_mode="HTML")
        await call.answer(text)

    @router.callback_query(F.data.startswith("factc:"))
    async def fact_custom_ask(call: CallbackQuery, state: FSMContext):
        await state.set_state(FactInput.amount)
        await state.update_data(shift_id=int(call.data.split(":")[1]))
        await call.message.answer("Сколько штук сделал? Напиши число.")
        await call.answer()

    @router.message(StateFilter(FactInput.amount), F.text)
    async def fact_custom(message: Message, state: FSMContext):
        raw = message.text.strip().replace(" ", "")
        if not raw.isdigit():
            await message.answer("Нужно целое число, например 23.")
            return
        data = await state.get_data()
        await state.clear()
        await message.answer(await save_fact(message.from_user.id, data["shift_id"], int(raw)))

    # ------------------------------------------------------------ shortfall decisions

    async def decide_shortfall(tg, admin_name: str, shift_id: int, amount: int | None) -> tuple[bool, str]:
        admin = allowed(tg.id, "fine_direct" if amount else "review", shift_owner(shift_id))
        if admin is None:
            return False, "Недостаточно прав"
        try:
            result = svc.decide_shortfall(conn, admin, shift_id, amount)
        except svc.ServiceError as exc:
            return False, exc.message
        if not result.changed:
            done = result.shift
            already = f"штраф {done['shortfall_penalty']} ₽" if done["shortfall_status"] == "fined" else "без штрафа"
            return False, f"Уже решено: {already}"
        await notifier.shortfall_decided(result.shift, admin_name, tg.id)
        return True, f"Штраф {amount} ₽ назначен" if amount else "Решено без штрафа"

    @router.callback_query(F.data.startswith("sf:"))
    async def shortfall_pick(call: CallbackQuery, state: FSMContext):
        _, shift_id, amount = call.data.split(":")
        need = "review" if amount == "0" else "fine_direct"
        if allowed(call.from_user.id, need, shift_owner(int(shift_id))) is None:
            return await deny(call)
        if amount == "custom":
            await state.set_state(ShortfallInput.amount)
            await state.update_data(shift_id=int(shift_id))
            await call.message.answer("Введите сумму штрафа за недобор в рублях (целое число):")
            await call.answer()
            return
        ok, text = await decide_shortfall(call.from_user, call.from_user.full_name, int(shift_id), int(amount) or None)
        if ok:
            await call.message.edit_text(f"{call.message.html_text}\n\n<b>{escape(text)}</b> — {escape(call.from_user.full_name)}",
                                         parse_mode="HTML")
        await call.answer(text, show_alert=not ok)

    @router.message(StateFilter(ShortfallInput.amount), F.text)
    async def shortfall_custom(message: Message, state: FSMContext):
        raw = message.text.strip().replace(" ", "").replace("₽", "")
        if not raw.isdigit() or not 0 < int(raw) <= svc.MAX_PENALTY:
            await message.answer("Нужно целое число больше нуля, например 700.")
            return
        data = await state.get_data()
        await state.clear()
        _, text = await decide_shortfall(message.from_user, message.from_user.full_name, data["shift_id"], int(raw))
        await message.answer(text)

    # ------------------------------------------------------------ admin: raspred

    @router.message(Command("raspred_zavtra"))
    @router.message(F.text == RASPRED_BUTTON)
    async def raspred(message: Message):
        if allowed(message.from_user.id, "raspred") is None:
            return
        data = svc.raspred(conn, today_local() + timedelta(days=1))
        # plain text so it copies cleanly into the work chat
        await message.answer(data["text"], parse_mode=None)

    # ------------------------------------------------------------ archive group (owner, inside the group)

    @router.message(Command("archive_here"))
    async def archive_here(message: Message):
        if message.chat.type not in ("group", "supergroup"):
            await message.answer("Отправьте /archive_here внутри закрытой группы, куда бот добавлен.")
            return
        if message.from_user.id not in settings.admin_ids:
            return
        app_settings.set_raw(conn, "archive_chat_id", str(message.chat.id))
        app_settings.set_raw(conn, "archive_chat_title", message.chat.title or "")
        await message.answer(
            "🗂 Архив подключён. Сюда будут приходить штрафы, бонусы, блокировки, решения по отчётам и выплаты "
            "с хештегами — ищите по #штраф, #блок, #отчёт или по имени сотрудника."
        )
        await notifier.archive(["архив"], f"Архив подключён — {escape(message.from_user.full_name)}")

    @router.message(Command("archive_off"))
    async def archive_off(message: Message):
        if message.from_user.id not in settings.admin_ids:
            return
        if app_settings.get_raw(conn, "archive_chat_id") != str(message.chat.id) and message.chat.type != "private":
            await message.answer("Архив подключён к другому чату.")
            return
        app_settings.set_raw(conn, "archive_chat_id", None)
        app_settings.set_raw(conn, "archive_chat_title", None)
        await message.answer("Архив отключён.")

    # ------------------------------------------------------------ fallback

    @router.message(StateFilter(None), F.chat.type == "private")
    async def fallback(message: Message):
        user = svc.get_user_by_tg(conn, message.from_user.id)
        if user is None:
            await message.answer("Вы ещё не зарегистрированы. Нажмите /start")
        elif user["status"] == "pending":
            await message.answer("Ваша заявка на рассмотрении.")
        elif user["status"] == "approved" and user["account_state"] != "blocked":
            await show_menu(message, user)
        # rejected: no access, stay silent

    return router


async def setup_menu_button(bot: Bot) -> None:
    from aiogram.types import MenuButtonWebApp

    if settings.webapp_url.startswith("https://"):
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="График", web_app=WebAppInfo(url=settings.webapp_url))
            )
        except Exception:
            log.exception("could not set menu button")
