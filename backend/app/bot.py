import asyncio
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
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from . import app_settings, notify
from . import permissions as perms
from . import alice, broadcasts, day_actual, finance, groups, invites, pending, recruiting, reports, templates, training
from . import services as svc
from .config import settings
from .notify import Notifier
from .timeutil import now_local, today_local

log = logging.getLogger(__name__)

RASPRED_BUTTON = "📋 Распред на завтра"


class Registration(StatesGroup):
    name = State()
    confirm = State()


class PenaltyInput(StatesGroup):
    amount = State()


class ReportInput(StatesGroup):
    volume = State()
    photo = State()


class EventReject(StatesGroup):
    reason = State()


class BroadcastReply(StatesGroup):
    collecting = State()


class TrainingInput(StatesGroup):
    reschedule = State()   # the candidate's «Прошу перенести»: a reason and a convenient time
    reject_reason = State()  # an admin rejects a request: the reason is required


class DecisionInput(StatesGroup):
    amount = State()
    reason = State()


# quick reasons offered as buttons when a decision needs a comment — the editable templates (ТЗ 28.10 §2, В2)
REASON_KIND = {"return": None, "fine": "report_fine", "ns_fine": "no_show_fine", "ns_excuse": "no_show_excuse"}


WELCOME = ("Добро пожаловать в систему!\n\nСовет: в приложении → «Профиль» → «Быстрый доступ» можно добавить "
           "иконку на главный экран телефона и открывать график, не заходя в чат.")


def open_app_keyboard() -> InlineKeyboardMarkup | None:
    if not settings.webapp_url.startswith("https://"):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📅 Открыть график", web_app=WebAppInfo(url=settings.webapp_url))
    ]])


def admin_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=RASPRED_BUTTON)]], resize_keyboard=True)


def make_block_guard(conn: sqlite3.Connection):
    """The bot's middleware for blocked people (ТЗ 28.10 §16)."""

    async def block_guard(handler, event, data):
        """ТЗ 28.10 §16: a blocked person can do nothing in the bot — no buttons (old ones included), no commands,
        no dialogs. One clear answer instead. Group chats (the owners' commands) are not touched."""
        tg = getattr(event, "from_user", None)
        chat = event.chat if isinstance(event, Message) else None
        if tg is not None and (chat is None or chat.type == "private") and tg.id not in settings.admin_ids:
            user = svc.get_user_by_tg(conn, tg.id)
            if user is not None and user["account_state"] == "blocked":
                state = data.get("state")
                if state is not None:
                    await state.clear()
                if isinstance(event, CallbackQuery):
                    await event.answer(notify.BLOCKED_TEXT, show_alert=True)
                else:
                    await event.answer(notify.BLOCKED_TEXT)
                return None
        return await handler(event, data)

    return block_guard


def build_router(conn: sqlite3.Connection, notifier: Notifier) -> Router:
    router = Router()

    block_guard = make_block_guard(conn)
    router.message.outer_middleware(block_guard)
    router.callback_query.outer_middleware(block_guard)

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
            await message.answer(f"{notify.STAFF_TAB.get(perms.role_of(user), 'Админ')}: распред на завтра — кнопкой ниже "
                                 "или /raspred_zavtra",
                                 reply_markup=admin_reply_keyboard())

    # ------------------------------------------------------------ /start and registration

    def questionnaire_keyboard() -> InlineKeyboardMarkup:
        """The questionnaire lives in the Mini App (the customer's module 3); without HTTPS — the old chat flow."""
        if settings.webapp_url.startswith("https://"):
            return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="📝 Заполнить анкету", web_app=WebAppInfo(url=settings.webapp_url))]])
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Регистрация", callback_data="register")]])

    def tg_dict(tg) -> dict:
        return {"id": tg.id, "username": tg.username, "first_name": tg.first_name, "last_name": tg.last_name}

    async def stranger(message: Message, state: FSMContext, param: str | None) -> None:
        """ТЗ 27.10 §5–7: no link — silence; an outdated link — one answer; a live one — the way in."""
        tg = message.from_user
        await state.clear()
        link, st = invites.resolve(conn, param)
        if st == "none":
            if invites.entry(conn, tg.id) is None:
                invites.log_attempt(conn, tg_dict(tg), "no_link")
                return
        elif st != "active" or not invites.use(conn, tg.id, link):
            attempt = invites.log_attempt(conn, tg_dict(tg), "bad_link", param)
            if invites.should_answer_stale(conn, attempt):
                await message.answer(invites.STALE)
            return
        kind = link["kind"] if link else invites.entry(conn, tg.id)
        if kind == "owner":
            # an owner's invitation: just a surname and a name, then the admins decide (no training)
            await message.answer("👋 Вас пригласили в команду. Чтобы отправить заявку, укажите фамилию и имя.",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                     InlineKeyboardButton(text="Отправить заявку", callback_data="register")]]))
            return
        await message.answer("👋 Добро пожаловать! Чтобы присоединиться к команде, заполните короткую анкету.",
                             reply_markup=questionnaire_keyboard())

    @router.message(CommandStart(), F.chat.type == "private")
    async def start(message: Message, state: FSMContext):
        tg = message.from_user
        param = ((message.text or "").split(maxsplit=1)[1:] or [None])[0]  # /start ref_<token> / inv_<token>
        user = svc.ensure_admin(conn, tg.id, tg.first_name or "", tg.last_name or "", tg.username)
        if user is None:
            await stranger(message, state, param)
            return
        await set_menu(message.bot, tg.id, user)
        svc.touch_username(conn, user, tg.username)
        if user["status"] == "pending":
            application = recruiting.current(conn, user["id"])
            if application is None:  # returned to the reserve, or an old chat request
                await message.answer("Заполните анкету, чтобы мы могли рассмотреть вашу кандидатуру.",
                                     reply_markup=questionnaire_keyboard())
            else:
                await message.answer("Ваша анкета на рассмотрении. Не выключайте уведомления — бот напишет, когда "
                                     "согласует с тренерами день, время и место обучения.")
        elif user["status"] == "rejected":
            await message.answer("В доступе отказано.")
        elif user["account_state"] == "blocked":
            await message.answer(notify.BLOCKED_TEXT)
        else:
            if svc.claim_welcome(conn, user["id"]):
                await message.answer(WELCOME)
            if user["account_state"] == "paused":
                reason = f"\nПричина: {escape(user['state_reason'])}" if user["state_reason"] else ""
                await message.answer(f"⏸ Аккаунт на паузе — новые смены пока недоступны.{reason}", parse_mode="HTML")
            await show_menu(message, user)

    @router.callback_query(F.data == "register")
    async def register(call: CallbackQuery, state: FSMContext):
        if svc.get_user_by_tg(conn, call.from_user.id) is not None:
            await call.answer("Заявка уже существует", show_alert=True)
            return
        if invites.entry(conn, call.from_user.id) is None:  # checked on the server, not only by the button
            await call.answer(invites.STALE, show_alert=True)
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
        way = invites.entry(conn, tg.id)
        if way is None and svc.get_user_by_tg(conn, tg.id) is None:
            await call.message.edit_reply_markup(reply_markup=None)
            await call.answer(invites.STALE, show_alert=True)
            return
        user, created = svc.create_registration(conn, tg.id, tg.username, data["first_name"], data["last_name"])
        await call.message.edit_reply_markup(reply_markup=None)
        if not created:
            await call.answer("Заявка уже отправлена")
            return
        user = invites.registered(conn, tg.id, user)
        await call.message.answer("Заявка отправлена. Ожидайте решения администратора.")
        await call.answer()
        await notifier.to_admins(notify.registration_text(user, invites.owner_invited(conn, tg.id)),
                                 notify.registration_keyboard(user["id"]))

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
        await set_menu(call.bot, user["telegram_id"], user)
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

    # ------------------------------------------------------------ reports: fact + screenshot (ТЗ 3.2)

    async def start_report(tg_id: int, shift_id: int, state: FSMContext, volume: int | None = None) -> str | None:
        """Begin the report dialog; returns an error text if the report can't be submitted now."""
        user = svc.get_user_by_tg(conn, tg_id)
        if user is None or user["status"] != "approved" or user["account_state"] == "blocked":
            return "Нет доступа"
        try:
            shift = reports.check_submittable(svc.get_shift(conn, shift_id), user, now_local())
        except svc.ServiceError as exc:
            return exc.message
        await state.clear()
        await state.update_data(shift_id=shift_id)
        if volume is None:
            await state.set_state(ReportInput.volume)
            await notifier.send(tg_id, f"Сколько штук сделал за смену {notify.shift_place(shift)}? Напиши число.",
                                InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                                    text=f"✅ {shift['volume']} шт., как план", callback_data=f"repv:{shift_id}:{shift['volume']}")]]))
        else:
            await state.update_data(volume=volume)
            await state.set_state(ReportInput.photo)
            await notifier.send(tg_id, f"Факт: {volume} шт. Теперь пришли скриншот результата одним фото.")
        return None

    @router.callback_query(F.data.startswith("rep:"))
    async def report_begin(call: CallbackQuery, state: FSMContext):
        error = await start_report(call.from_user.id, int(call.data.split(":")[1]), state)
        await call.answer(error or "", show_alert=bool(error))

    @router.callback_query(F.data.startswith("repv:") | F.data.startswith("fact:"))
    async def report_quick_volume(call: CallbackQuery, state: FSMContext):
        # fact:… are buttons from messages sent before reports needed a screenshot
        _, shift_id, value = call.data.split(":")
        error = await start_report(call.from_user.id, int(shift_id), state, int(value))
        await call.answer(error or "", show_alert=bool(error))

    @router.callback_query(F.data.startswith("factc:"))
    async def report_legacy_custom(call: CallbackQuery, state: FSMContext):
        error = await start_report(call.from_user.id, int(call.data.split(":")[1]), state)
        await call.answer(error or "", show_alert=bool(error))

    @router.message(StateFilter(ReportInput.volume), F.text)
    async def report_volume(message: Message, state: FSMContext):
        raw = message.text.strip().replace(" ", "")
        if not raw.isdigit() or int(raw) > svc.MAX_VOLUME:
            await message.answer("Нужно целое число, например 23.")
            return
        await state.update_data(volume=int(raw))
        await state.set_state(ReportInput.photo)
        await message.answer("Теперь пришли скриншот результата одним фото.")

    @router.message(StateFilter(ReportInput.photo), F.photo)
    async def report_photo(message: Message, state: FSMContext):
        data = await state.get_data()
        user = svc.get_user_by_tg(conn, message.from_user.id)
        try:
            result = reports.submit(conn, user, data["shift_id"], data["volume"], message.photo[-1].file_id, "bot",
                                    svc.new_key())
        except svc.ServiceError as exc:
            await state.clear()
            await message.answer(exc.message)
            return
        await state.clear()
        await notifier.report_submitted(result.shift, result.report)

    @router.message(StateFilter(ReportInput.photo))
    async def report_photo_missing(message: Message):
        await message.answer("Нужен скриншот как фото (не файлом). Нажми 📎 → Фото и отправь одно изображение.")

    # ------------------------------------------------------------ reports: review and no-shows (ТЗ 3.3–3.4)

    async def mark_done(call: CallbackQuery, text: str) -> None:
        line = f"\n\n<b>{escape(text)}</b> — {escape(call.from_user.full_name)}"
        try:
            if call.message.photo:
                await call.message.edit_caption(caption=(call.message.html_text or "") + line, parse_mode="HTML")
            else:
                await call.message.edit_text(call.message.html_text + line, parse_mode="HTML")
        except Exception:
            log.debug("could not mark decision on the message", exc_info=True)

    async def decide(tg_id: int, kind: str, shift_id: int, amount: int | None = None,
                     comment: str | None = None) -> tuple[bool, str]:
        actor = svc.get_user_by_tg(conn, tg_id)
        if actor is None:
            return False, "Нет доступа"
        try:
            if kind in ("approve", "return", "fine"):
                result = reports.review(conn, actor, shift_id, kind, svc.new_key(), amount, comment)
                if result.changed:
                    await notifier.report_reviewed(result.shift, result.report, actor)
            else:
                result = reports.resolve_no_show(conn, actor, shift_id, kind.removeprefix("ns_"), svc.new_key(),
                                                 amount, comment)
                if result.changed:
                    await notifier.no_show_resolved(result.shift, actor)
        except svc.ServiceError as exc:
            return False, exc.message
        if not result.changed:
            return False, "Уже решено"
        return True, {"approve": "✅ Принят", "return": "🔄 Возвращён на исправление", "fine": f"⚠️ Штраф {amount} ед.",
                      "ns_fine": f"💸 Штраф {amount} ед.", "ns_excuse": "🤝 Без штрафа"}[kind]

    async def ask_reason(message: Message, state: FSMContext, kind: str) -> None:
        presets = templates.reasons(conn, REASON_KIND[kind])[:8] if REASON_KIND[kind] else []
        await state.update_data(presets=presets)  # the button keeps meaning what it showed, even if edited meanwhile
        await state.set_state(DecisionInput.reason)
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=r, callback_data=f"rsn:{i}")]
                                                       for i, r in enumerate(presets)]) if presets else None
        prompt = "Что нужно исправить? Напиши комментарий." if kind == "return" else "Причина? Выбери или напиши своими словами."
        await message.answer(prompt, reply_markup=markup)

    @router.callback_query(F.data.startswith("rv:") | F.data.startswith("ns:"))
    async def decision_pick(call: CallbackQuery, state: FSMContext):
        parts = call.data.split(":")
        family, action, shift_id = parts[0], parts[1], int(parts[2])
        kind = {("rv", "a"): "approve", ("rv", "r"): "return", ("rv", "f"): "fine",
                ("ns", "f"): "ns_fine", ("ns", "x"): "ns_excuse"}[(family, action)]
        if kind == "approve":
            ok, text = await decide(call.from_user.id, kind, shift_id)
            if ok:
                await mark_done(call, text)
            await call.answer(text, show_alert=not ok)
            return
        await state.clear()
        await state.update_data(kind=kind, shift_id=shift_id, origin_chat=call.message.chat.id,
                                origin_msg=call.message.message_id)
        if kind in ("fine", "ns_fine"):
            if parts[3] == "c":
                await state.set_state(DecisionInput.amount)
                await call.message.answer("Сколько штрафных единиц? (целое число; единица = ставка сотрудника за штуку)")
                await call.answer()
                return
            await state.update_data(amount=int(parts[3]))
        await ask_reason(call.message, state, kind)
        await call.answer()

    @router.message(StateFilter(DecisionInput.amount), F.text)
    async def decision_amount(message: Message, state: FSMContext):
        raw = message.text.strip().replace(" ", "")
        if not raw.isdigit() or not 0 < int(raw) <= finance.MAX_UNITS:
            await message.answer("Нужно целое число единиц больше нуля, например 3.")
            return
        await state.update_data(amount=int(raw))
        await ask_reason(message, state, (await state.get_data())["kind"])

    async def finish_decision(tg_id: int, state: FSMContext, comment: str) -> str:
        data = await state.get_data()
        await state.clear()
        ok, text = await decide(tg_id, data["kind"], data["shift_id"], data.get("amount"), comment)
        if ok and notifier.bot is not None:
            # mark the original card so other reviewers' copies are not the only trace
            try:
                await notifier.bot.edit_message_reply_markup(chat_id=data["origin_chat"], message_id=data["origin_msg"])
            except Exception:
                pass
        return text if ok else f"Не получилось: {text}"

    @router.callback_query(StateFilter(DecisionInput.reason), F.data.startswith("rsn:"))
    async def decision_reason_preset(call: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        try:
            reason = data.get("presets", [])[int(call.data.split(":")[1])]
        except (IndexError, ValueError):
            await call.answer("Кнопка устарела — напишите причину текстом", show_alert=True)
            return
        text = await finish_decision(call.from_user.id, state, reason)
        await call.message.edit_text(f"{escape(reason)}\n\n<b>{escape(text)}</b>", parse_mode="HTML")
        await call.answer()

    @router.message(StateFilter(DecisionInput.reason), F.text)
    async def decision_reason_text(message: Message, state: FSMContext):
        await message.answer(await finish_decision(message.from_user.id, state, message.text.strip()))

    @router.callback_query(F.data.startswith("sf:") | F.data.startswith("pen_old:"))
    async def legacy_shortfall(call: CallbackQuery):
        await call.answer("Эта кнопка устарела — решение по отчётам теперь в приложении, раздел «Отчёты».", show_alert=True)

    # ------------------------------------------------------------ admin: raspred

    @router.message(Command("raspred_zavtra"))
    @router.message(F.text == RASPRED_BUTTON)
    async def raspred(message: Message):
        actor = allowed(message.from_user.id, "raspred")
        if actor is None:
            return
        data = svc.raspred(conn, today_local() + timedelta(days=1), actor)
        # plain text so it copies cleanly into the work chat
        await message.answer(data["text"], parse_mode=None)

    # ------------------------------------------------------------ money (ТЗ модуль 4)

    @router.message(Command("finances"), F.chat.type == "private")
    async def finances_cmd(message: Message):
        """Debtors and people with bonuses: owners and admins see everyone, a trainer — their network."""
        actor = allowed(message.from_user.id, "review")
        if actor is None:
            return
        only = None
        if not perms.sees_everyone(actor):
            only = {u["id"] for u in svc.list_users(conn) if perms.manages(actor, u)}
        people = finance.summary(conn, only)
        if not people:
            await message.answer("💰 Долгов и начислений нет.")
            return
        lines = ["💰 <b>Финансы</b>"]
        for p in people:
            link = notify.person_link({"telegram_id": p["telegram_id"], "username": p.get("username")}, p["name"])
            parts = []
            if p["fines"]:
                parts.append(f"штрафы {p['fines']} ₽")
            if p["bonuses"]:
                parts.append(f"бонусы {p['bonuses']} ₽")
            if p["deductions"]:
                parts.append(f"вычеты {p['deductions']} ₽")
            if p["pending"]:
                parts.append(f"на согласовании {p['pending']} ₽")
            if p["drafts"]:
                parts.append(f"без суммы: {p['drafts']}")
            sign = "−" if p["balance"] < 0 else "+" if p["balance"] > 0 else ""
            lines.append(f"{'🔴' if p['balance'] < 0 else '🟢' if p['balance'] > 0 else '⚪'} {link}: "
                         f"<b>{sign}{abs(p['balance'])} ₽</b> ({', '.join(parts)})")
        await message.answer("\n".join(lines[:80]), parse_mode="HTML", reply_markup=open_app_keyboard())

    @router.message(Command("pending"), F.chat.type == "private")
    async def pending_cmd(message: Message):
        """ТЗ 28.10 §4, §14 (В10): what waits for this person's decision — the same counts as the app's badges."""
        actor = svc.get_user_by_tg(conn, message.from_user.id)
        if actor is None or not perms.is_staff(actor):
            return
        await message.answer(pending.summary_text(pending.counts(conn, actor)), parse_mode="HTML",
                             reply_markup=open_app_keyboard())

    @router.callback_query(F.data.startswith("fx:"))
    async def fine_decision(call: CallbackQuery):
        _, verdict, tx_id = call.data.split(":")
        actor = svc.get_user_by_tg(conn, call.from_user.id)
        try:
            result = finance.decide(conn, actor or {"id": None}, int(tx_id), verdict == "a", None, svc.new_key())
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        if not result.changed:
            await call.answer(f"Уже решено: {finance.STATUS_TITLES[result.tx['status']]}", show_alert=True)
            return
        await notifier.tx_decided(result.tx, actor)
        text = "✅ Одобрен" if verdict == "a" else "❌ Отклонён"
        await mark_done(call, text)
        await call.answer(text)

    # ------------------------------------------------------------ live events (ТЗ модуль 7)

    async def event_decide(tg_id: int, entry_id: int, approve: bool, reason: str | None) -> tuple[bool, str]:
        from . import events

        actor = svc.get_user_by_tg(conn, tg_id)
        if actor is None:
            return False, "Нет доступа"
        try:
            result = events.review(conn, actor, entry_id, approve, reason, svc.new_key())
        except svc.ServiceError as exc:
            return False, exc.message
        if not result.changed:
            return False, "Уже решено"
        await notifier.event_reviewed(result, actor)
        return True, "✅ Подтверждено" if approve else "❌ Отклонено"

    @router.callback_query(F.data.startswith("ev:"))
    async def event_pick(call: CallbackQuery, state: FSMContext):
        _, verdict, entry_id = call.data.split(":")
        if verdict == "ok":
            ok, text = await event_decide(call.from_user.id, int(entry_id), True, None)
            if ok:
                await mark_done(call, text)
            await call.answer(text, show_alert=not ok)
            return
        await state.clear()
        await state.set_state(EventReject.reason)
        await state.update_data(entry_id=int(entry_id))
        await call.message.answer("Причина отклонения заявки? Напишите — участник увидит её.")
        await call.answer()

    @router.message(StateFilter(EventReject.reason), F.text)
    async def event_reject_reason(message: Message, state: FSMContext):
        data = await state.get_data()
        await state.clear()
        _, text = await event_decide(message.from_user.id, data["entry_id"], False, message.text.strip())
        await message.answer(text)

    # ------------------------------------------------------------ goals (ТЗ модуль 5)

    @router.callback_query(F.data.startswith("goal:"))
    async def goal_join(call: CallbackQuery):
        from . import goals

        user = svc.get_user_by_tg(conn, call.from_user.id)
        if user is None or user["status"] != "approved":
            await call.answer("Нет доступа", show_alert=True)
            return
        try:
            goal, joined = goals.join(conn, user, int(call.data.split(":")[1]))
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        if joined:
            _, pieces = goals.counted_so_far(conn, goal, [user["id"]])
            note = f" Уже учтено ваших принятых отчётов с начала цели: {pieces} шт." if pieces else ""
            await call.answer(f"Вы участвуете!{note} Прогресс — в приложении", show_alert=bool(pieces))
        else:
            await call.answer("Вы уже участвуете")
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    # ------------------------------------------------------------ read receipts (ТЗ 7.2)

    @router.callback_query(F.data.startswith("ack:"))
    async def acknowledge(call: CallbackQuery):
        broadcast_id = int(call.data.split(":")[1])
        if broadcast_id == 0:
            await call.answer("Это проверочное сообщение — у сотрудников кнопка отметит прочтение.", show_alert=True)
            return
        svc.acknowledge(conn, broadcast_id, call.from_user.id)
        mark = "\n\n✅ <i>Прочитано</i>"
        b = broadcasts.get(conn, broadcast_id)
        keep = notify.broadcast_keyboard(broadcast_id, False, bool(b and b["allow_reply"]))  # «Ответить» stays
        try:
            if call.message.photo or call.message.video:
                await call.message.edit_caption(caption=(call.message.html_text or "") + mark, parse_mode="HTML",
                                                reply_markup=keep)
            else:
                await call.message.edit_text(call.message.html_text + mark, parse_mode="HTML", disable_web_page_preview=True,
                                             reply_markup=keep)
        except Exception:
            log.debug("could not mark the broadcast as read", exc_info=True)
        await call.answer("Учтено")

    # ------------------------------------------------------------ recruiting: the candidate's buttons

    @router.callback_query(F.data.startswith("trc:"))
    async def training_confirm(call: CallbackQuery):
        try:
            application, changed = recruiting.confirm_training(conn, call.from_user.id, int(call.data.split(":")[1]))
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            log.debug("could not drop the confirm button", exc_info=True)
        await call.answer("Вы записаны" if changed else "Запись уже подтверждена")
        if changed:
            await notifier.training_confirmed(application)
            await notifier.invite_pre_if_agreed(application)  # §6: «Предзакреп» only after the confirmation

    @router.callback_query(F.data.startswith("ttc:"))
    async def trainer_confirm(call: CallbackQuery):
        """The trainer confirms the agreed date, time and place (В9); an old button of a changed training is refused."""
        _, app_id, version = call.data.split(":")
        user = svc.get_user_by_tg(conn, call.from_user.id)
        try:
            application, changed = training.confirm_by_trainer(conn, user or {"id": None}, int(app_id), int(version))
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            log.debug("could not drop the confirm button", exc_info=True)
        await call.answer("Подтверждено" if changed else "Уже подтверждено")
        if changed:
            await notifier.invite_pre_if_agreed(application)

    @router.callback_query(F.data.startswith("tcw:") | F.data.startswith("tcc:"))
    async def candidate_contacted(call: CallbackQuery):
        kind, app_id = call.data.split(":")
        try:
            application, changed = training.candidate_answer(conn, call.from_user.id, int(app_id),
                                                             "wrote" if kind == "tcw" else "cant_write")
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        await call.answer("Спасибо, тренер получил сообщение" if kind == "tcw" else "Тренер напишет вам сам")
        if changed:
            await notifier.candidate_answered(application)

    @router.callback_query(F.data.startswith("trr:"))
    async def reschedule_begin(call: CallbackQuery, state: FSMContext):
        await state.clear()
        await state.set_state(TrainingInput.reschedule)
        await state.update_data(app_id=int(call.data.split(":")[1]))
        await call.message.answer("🔁 Напишите одним сообщением, почему нужно перенести обучение и когда вам удобно.")
        await call.answer()

    @router.message(StateFilter(TrainingInput.reschedule), F.text)
    async def reschedule_text(message: Message, state: FSMContext):
        data = await state.get_data()
        await state.clear()
        user = svc.get_user_by_tg(conn, message.from_user.id)
        text = message.text.strip()
        try:
            req, created = training.create_request(conn, user or {"id": None}, data["app_id"], "change", text,
                                                   {"note": text}, svc.new_key())
        except svc.ServiceError as exc:
            await message.answer(exc.message)
            return
        await message.answer("Запрос отправлен администратору. До решения действует прежняя запись." if created
                             else "Запрос на перенос уже отправлен — ждите ответа.")
        if created:
            await notifier.training_request_created(req)

    @router.callback_query(F.data.startswith("trq:"))
    async def request_decide(call: CallbackQuery, state: FSMContext):
        """«Ожидает согласования» in the bot: approve at once, reject — with a reason (§4)."""
        _, verdict, request_id = call.data.split(":")
        actor = allowed(call.from_user.id, "registrations")
        if actor is None:
            return await deny(call)
        if verdict == "r":
            await state.clear()
            await state.set_state(TrainingInput.reject_reason)
            await state.update_data(request_id=int(request_id))
            await call.message.answer("Причина отклонения? Напишите — инициатор её увидит.")
            await call.answer()
            return
        await finish_request(call.from_user.id, int(request_id), True, None, call)

    @router.message(StateFilter(TrainingInput.reject_reason), F.text)
    async def request_reject_reason(message: Message, state: FSMContext):
        data = await state.get_data()
        await state.clear()
        await finish_request(message.from_user.id, data["request_id"], False, message.text.strip(), None, message)

    async def finish_request(tg_id: int, request_id: int, approve: bool, reason: str | None,
                             call: CallbackQuery | None, message: Message | None = None) -> None:
        actor = allowed(tg_id, "registrations")
        reply = (lambda t: call.answer(t, show_alert=True)) if call else (lambda t: message.answer(t))
        if actor is None:
            await reply("Недостаточно прав")
            return
        try:
            req, changed, effect = training.decide(conn, actor, request_id, approve, reason, svc.new_key())
        except svc.ServiceError as exc:
            await reply(exc.message)
            return
        if not changed:
            await reply("Уже решено")
            return
        await notifier.training_request_decided(req, actor, effect)
        if effect and effect["kind"] == "refuse":
            await groups.kick_blocked(conn, notifier.bot, notifier, svc.get_user(conn, effect["app"]["user_id"]), actor)
        if call:
            await call.answer("Одобрено" if approve else "Отклонено")
        else:
            await message.answer("Отклонено, инициатор получил причину.")

    @router.callback_query(F.data.startswith("ctw:") | F.data.startswith("ctc:"))
    async def contact_reply(call: CallbackQuery):
        kind, app_id = call.data.split(":")
        try:
            application, changed = recruiting.contact_answer(conn, call.from_user.id, int(app_id),
                                                             "wrote" if kind == "ctw" else "cant_write")
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        await call.answer("Спасибо, менеджер получил сообщение" if kind == "ctw" else "Менеджер напишет вам сам")
        if changed:
            await notifier.contact_answered(application)

    # ------------------------------------------------------------ «Ответить» on a broadcast

    REPLY_DONE = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📨 Отправить", callback_data="brs:send"),
        InlineKeyboardButton(text="Отмена", callback_data="brs:cancel")]])

    @router.callback_query(F.data.startswith("brr:"))
    async def reply_begin(call: CallbackQuery, state: FSMContext):
        broadcast_id = int(call.data.split(":")[1])
        if broadcast_id == 0:
            await call.answer("Это проверочное сообщение — у сотрудников кнопка откроет ответ.", show_alert=True)
            return
        try:
            broadcasts.can_reply(conn, broadcast_id, svc.get_user_by_tg(conn, call.from_user.id))
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        await state.clear()
        await state.set_state(BroadcastReply.collecting)
        await state.update_data(broadcast_id=broadcast_id, text=[], media=[], groups=[])
        await call.message.answer("✍️ Напишите ответ. Можно приложить фото и видео (до 10).\n"
                                  "Когда всё готово — нажмите «📨 Отправить».", reply_markup=REPLY_DONE)
        await call.answer()

    # an album comes as several updates handled at once: one lock per person keeps every photo
    reply_locks: dict[int, asyncio.Lock] = {}

    @router.message(StateFilter(BroadcastReply.collecting))
    async def reply_collect(message: Message, state: FSMContext):
        async with reply_locks.setdefault(message.from_user.id, asyncio.Lock()):
            await collect_reply_part(message, state)

    async def collect_reply_part(message: Message, state: FSMContext):
        data = await state.get_data()
        text, media, groups = data["text"], data["media"], data["groups"]
        part = message.text or message.caption
        if part:
            text.append(part)
        if message.photo:
            media.append({"kind": "photo", "file_id": message.photo[-1].file_id})
        elif message.video:
            media.append({"kind": "video", "file_id": message.video.file_id})
        elif not part:
            await message.answer("Можно текст, фото или видео.", reply_markup=REPLY_DONE)
            return
        if len(media) > broadcasts.MAX_REPLY_MEDIA:
            media = media[: broadcasts.MAX_REPLY_MEDIA]
            await message.answer(f"Не больше {broadcasts.MAX_REPLY_MEDIA} фото и видео — лишние не отправятся.")
        await state.update_data(text=text, media=media, groups=groups + ([message.media_group_id] if message.media_group_id else []))
        # an album arrives as several messages: one confirmation for it, not one per photo
        if message.media_group_id and message.media_group_id in groups:
            return
        added = f"{len(media)} влож." if media else ""
        await message.answer(f"Добавлено{f' ({added})' if added else ''}. Можно дописать ещё или нажать «📨 Отправить».",
                             reply_markup=REPLY_DONE)

    @router.callback_query(F.data.startswith("brs:"))
    async def reply_finish(call: CallbackQuery, state: FSMContext):
        if await state.get_state() != BroadcastReply.collecting.state:
            await call.answer("Ответ уже отправлен или отменён", show_alert=True)
            return
        if call.data == "brs:cancel":
            await state.clear()
            await call.message.edit_text("Ответ отменён.")
            await call.answer()
            return
        async with reply_locks.setdefault(call.from_user.id, asyncio.Lock()):  # the album's last photos are in
            data = await state.get_data()
        user = svc.get_user_by_tg(conn, call.from_user.id)
        try:
            b, reply = broadcasts.add_reply(conn, data["broadcast_id"], user, "\n\n".join(data["text"]), data["media"])
        except svc.ServiceError as exc:
            await call.answer(exc.message, show_alert=True)
            return
        await state.clear()
        delivered = await notifier.broadcast_reply(b, user, reply)
        await call.message.edit_text("✅ Ответ отправлен." if delivered or notifier.bot is None
                                     else "Ответ сохранён, но автору не удалось его доставить — администраторы увидят его в приложении.")
        await call.answer()

    # ------------------------------------------------------------ the day's global actual (owners)

    @router.message(Command("fakt"), F.chat.type == "private")
    async def global_actual_cmd(message: Message):
        """/fakt 480 — today's global actual; /fakt 480 17.10 — for another day."""
        actor = allowed(message.from_user.id, "global_actual")
        if actor is None:
            return
        parts = (message.text or "").split()[1:]
        if not parts or not parts[0].isdigit():
            await message.answer("Общий факт дня: <code>/fakt 480</code> — за сегодня, "
                                 "<code>/fakt 480 17.10</code> — за другой день.", parse_mode="HTML")
            return
        day = today_local()
        if len(parts) > 1:
            try:
                dd, mm = parts[1].split(".")[:2]
                day = day.replace(month=int(mm), day=int(dd))
                if day > today_local():
                    day = day.replace(year=day.year - 1)
            except ValueError:
                await message.answer("Дата в формате ДД.ММ, например 17.10")
                return
        try:
            result = day_actual.set_actual(conn, actor, day, int(parts[0]), svc.new_key())
        except svc.ServiceError as exc:
            await message.answer(exc.message)
            return
        row = result.row
        text = (f"📊 Общий факт за {day:%d.%m}: {row['actual']} из плана {row['plan']} шт. — "
                + ("команда выполнила план." if row["met"] else "план не выполнен, недоборы на ручной проверке."))
        if result.approved:
            text += f"\nПринято автоматически: {len(result.approved)}."
        if result.fined:
            text += f"\nЕсть штрафы за недобор ({len(result.fined)}) — их можно аннулировать в приложении, раздел «Факт дня»."
        await message.answer(text)
        if result.changed:
            await notifier.global_actual_set(day, result, actor)

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

    # ------------------------------------------------------------ groups by work days (ТЗ 27.10 §26–27)

    @router.message(Command("group_pre", "group_main"))
    async def group_here(message: Message):
        if message.from_user.id not in settings.admin_ids:
            return
        kind = "pre" if message.text.split()[0].split("@")[0] == "/group_pre" else "main"
        if message.chat.type not in ("group", "supergroup"):
            await message.answer(f"Отправьте /group_{kind} внутри группы «{groups.KINDS[kind]}», куда бот добавлен.")
            return
        try:
            groups.connect(conn, kind, message.chat.id, message.chat.title or groups.KINDS[kind], message.from_user.id)
        except svc.ServiceError as exc:
            await message.answer(exc.message)
            return
        rights = await groups.bot_rights(message.bot, message.chat.id)
        missing = [] if rights["invite"] and rights["remove"] else [
            t for t, ok in (("приглашать пользователей", rights["invite"]), ("банить пользователей", rights["remove"])) if not ok]
        await message.answer(
            f"👥 Группа подключена как «{groups.KINDS[kind]}». Приглашения сюда — личные: по ссылке войдёт только тот, "
            "кому её отправил бот." + (f"\n\n⚠️ Сделайте бота администратором с правами: {', '.join(missing)}." if missing else ""))

    @router.message(Command("alice_here"))
    async def alice_here(message: Message):
        """ТЗ 27.10 §36: the forum group for «Яндекс Алиса» photo reports — a topic per employee."""
        if message.from_user.id not in settings.admin_ids:
            return
        if message.chat.type != "supergroup" or not message.chat.is_forum:
            await message.answer("Нужна группа с включёнными темами (форум). Включите темы в настройках группы и "
                                 "отправьте /alice_here ещё раз.")
            return
        alice.connect_forum(conn, message.chat.id, message.chat.title)
        rights = await groups.bot_rights(message.bot, message.chat.id)
        me = await message.bot.get_chat_member(message.chat.id, message.bot.id)
        topics = rights["admin"] and (me.status == "creator" or bool(getattr(me, "can_manage_topics", False)))
        await message.answer("📸 Группа подключена для фотоотчётов «Яндекс Алисы»: у каждого сотрудника будет своя тема."
                             + ("" if topics else "\n\n⚠️ Сделайте бота администратором с правом «Управление темами»."))

    @router.message(Command("group_off"))
    async def group_off(message: Message):
        if message.from_user.id not in settings.admin_ids:
            return
        for kind in groups.KINDS:
            if (groups.chat(conn, kind) or {}).get("chat_id") == message.chat.id:
                groups.connect(conn, kind, None, None, message.from_user.id)
                await message.answer(f"Группа «{groups.KINDS[kind]}» отключена.")
                return
        await message.answer("Эта группа не подключена.")

    @router.chat_join_request()
    async def join_request(request: ChatJoinRequest):
        link = request.invite_link.invite_link if request.invite_link else None
        ok, user = await groups.join_request(conn, request.bot, request.chat.id, request.from_user.id, link)
        if ok and user is not None:
            if ((groups.chat(conn, "main") or {}).get("chat_id") == request.chat.id and groups.chat(conn, "pre")
                    and not groups.keeps_both(conn, user)):  # В13: unless the admin allowed both groups
                try:  # moved up: out of «Предзакреп», if the bot may (ТЗ 27.10 §27)
                    if await groups.member_state(request.bot, groups.chat(conn, "pre")["chat_id"], user["telegram_id"]) == "member":
                        await groups.remove(conn, request.bot, user, "pre", None)
                except svc.ServiceError:
                    await notifier.to_admins(f"👥 {escape(svc.full_name(user))} вступил в «Основную команду», но остался в "
                                             "«Предзакрепе»: у бота нет права удалять участников — уберите вручную.")

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
            if invites.entry(conn, message.from_user.id) is None:  # a stranger: silence, only the log (§5)
                invites.log_attempt(conn, tg_dict(message.from_user), "no_link")
                return
            await message.answer("Чтобы продолжить, нажмите /start")
        elif user["status"] == "pending":
            await message.answer("Ваша заявка на рассмотрении.")
        elif user["status"] == "approved" and user["account_state"] != "blocked":
            await show_menu(message, user)
        # rejected: no access, stay silent

    return router


async def set_menu(bot: Bot, telegram_id: int, user: dict | None) -> None:
    """The «График» button only in the chats of people who have access (ТЗ 27.10 §5: strangers get no menu)."""
    from aiogram.types import MenuButtonDefault, MenuButtonWebApp

    if not settings.webapp_url.startswith("https://"):
        return
    has_access = user is not None and user["status"] != "rejected" and user["account_state"] != "blocked"
    try:
        await bot.set_chat_menu_button(
            chat_id=telegram_id,
            menu_button=MenuButtonWebApp(text="График", web_app=WebAppInfo(url=settings.webapp_url))
            if has_access else MenuButtonDefault())
    except Exception:
        log.warning("could not set the menu button for %s", telegram_id)


async def setup_menu_button(bot: Bot, conn: sqlite3.Connection | None = None) -> None:
    """The default menu has no Mini App; people with access get it in their own chat."""
    from aiogram.types import MenuButtonDefault

    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    except Exception:
        log.exception("could not set menu button")
    if conn is None:
        return
    for row in conn.execute("SELECT * FROM users WHERE status != 'rejected' AND account_state != 'blocked'").fetchall():
        await set_menu(bot, row["telegram_id"], dict(row))
        await asyncio.sleep(0.05)  # well under Telegram's limits
