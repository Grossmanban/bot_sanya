import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import app_settings, export, media, pricing
from . import permissions as perms
from . import broadcasts, day_actual, events, finance, goals, lifecycle, networks, payouts, rates, recruiting, reports, violations
from . import services as svc
from .auth import InitDataError, validate_init_data
from .config import settings
from .notify import Notifier
from .bot import open_app_keyboard
from .notify import broadcast_keyboard as notify_keyboard
from .timeutil import now_local, today_local


class ShiftPayload(BaseModel):
    start_time: str
    end_time: str
    volume: int
    location: str
    idempotency_key: str


class CreateShift(ShiftPayload):
    shift_date: str


class KeyOnly(BaseModel):
    idempotency_key: str


class DateKey(BaseModel):
    shift_date: str
    idempotency_key: str


class WorkStatus(BaseModel):
    work_status: str
    idempotency_key: str


class AdminEdit(BaseModel):
    idempotency_key: str
    reason: str
    start_time: str | None = None
    end_time: str | None = None
    volume: int | None = None
    location: str | None = None
    action: str | None = None
    penalty_amount: int | None = None


class RoleBody(BaseModel):
    role: str
    idempotency_key: str


class AccessBody(BaseModel):
    allow: bool
    idempotency_key: str


class StateBody(BaseModel):
    state: str
    reason: str | None = None
    idempotency_key: str


class ProfileBody(BaseModel):
    phone: str
    bank: str
    recipient: str
    idempotency_key: str


class PayoutRates(BaseModel):
    rate_base: int | None = None
    rate_lazy: int | None = None
    lazy_threshold: int | None = None
    trainer_commission: int | None = None


class PayoutPreview(BaseModel):
    date_from: str
    date_to: str
    network_id: int | None = None
    rates: PayoutRates = PayoutRates()


class PayoutChoice(BaseModel):
    user_id: int
    include: bool = True
    base_override: int | None = None


class PayoutCommit(PayoutPreview):
    title: str
    choices: list[PayoutChoice]
    proof: list[str] = []
    deduction_proof: list[str] = []
    idempotency_key: str


class EventBody(BaseModel):
    title: str
    mode: str
    starts_at: str
    ends_at: str
    description: str | None = None
    prize: str | None = None
    target: int | None = None
    max_winners: int = 1
    max_attempts: int = 3
    network_id: int | None = None
    prize_type: str | None = None
    prize_photo: str | None = None
    prize_media_kind: str | None = None
    idempotency_key: str


class EventPatch(BaseModel):
    """ТЗ №20: only the fields sent are changed; `version` — what the editor saw (optimistic locking)."""
    version: int
    notify: bool = False
    title: str | None = None
    description: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    target: int | None = None
    max_winners: int | None = None
    max_attempts: int | None = None
    prize: str | None = None
    prize_type: str | None = None
    prize_photo: str | None = None
    prize_media_kind: str | None = None
    idempotency_key: str


class GoalPatch(BaseModel):
    version: int
    notify: bool = False
    reset_time: str | None = None
    location: str | None = None
    forever: bool | None = None
    title: str | None = None
    description: str | None = None
    target: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    prize: str | None = None
    prize_type: str | None = None
    prize_photo: str | None = None
    idempotency_key: str


class Visibility(BaseModel):
    hidden: bool
    idempotency_key: str


class ReasonBody(BaseModel):
    reason: str
    idempotency_key: str


class GoalAnnul(BaseModel):
    user_id: int
    reason: str
    idempotency_key: str


class EntryReview(BaseModel):
    approve: bool
    reason: str | None = None
    idempotency_key: str


class GoalBody(BaseModel):
    title: str
    kind: str
    target: int
    date_from: str
    date_to: str
    description: str | None = None
    user_id: int | None = None
    network_id: int | None = None
    mode: str = "shared"
    prize: str | None = None
    prize_type: str | None = None
    prize_photo: str | None = None
    user_ids: list[int] | None = None   # a group goal: the people ticked
    location: str | None = None         # a point goal
    reset_time: str | None = None
    forever: bool = False               # a point goal «каждый день, бессрочно»
    idempotency_key: str


class NetworkBody(BaseModel):
    name: str
    idempotency_key: str


class LadderStep(BaseModel):
    model_config = {"populate_by_name": True}
    from_: int = Field(alias="from")
    rate: int


class NetworkPatch(BaseModel):
    name: str | None = None
    trainer_id: int | None = None
    clear_trainer: bool = False
    archive: bool = False
    # owner: rates of the network (customer round B); rate_base None with clear_rate = the common rate
    rate_base: int | None = None
    clear_rate: bool = False
    ladder: list[LadderStep] | None = None
    ladder_period: str | None = None
    ladder_counts_trainer: bool | None = None
    idempotency_key: str


class UserRates(BaseModel):
    personal_rate: int | None = None
    clear_personal_rate: bool = False
    lazy_exempt: bool | None = None
    idempotency_key: str


class PieceDeduction(BaseModel):
    user_id: int
    day: str
    pieces: int
    reason: str
    media_file_id: str | None = None
    idempotency_key: str


class MemberNetwork(BaseModel):
    network_id: int | None = None
    idempotency_key: str


class TxBody(BaseModel):
    user_id: int
    type: str
    amount: int
    reason: str
    event_date: str | None = None
    media_file_id: str | None = None
    idempotency_key: str


class TxDecision(BaseModel):
    approve: bool
    comment: str | None = None
    idempotency_key: str


class TxCancel(BaseModel):
    reason: str
    idempotency_key: str


class TxForgive(BaseModel):
    reason: str | None = None
    idempotency_key: str


class TxAmount(BaseModel):
    amount: int
    idempotency_key: str


class PayAll(BaseModel):
    user_id: int
    idempotency_key: str


class ActualBody(BaseModel):
    actual: int
    idempotency_key: str


class AnnulBody(BaseModel):
    shift_ids: list[int] | None = None  # None = every shortfall fine of the day
    idempotency_key: str


class NormBody(BaseModel):
    norm: int | None = None  # None = the common norm
    idempotency_key: str


class ViolationFine(BaseModel):
    amount: int
    comment: str
    media_files: list[str] = []
    idempotency_key: str


class ViolationWaive(BaseModel):
    comment: str
    media_files: list[str] = []
    idempotency_key: str


class ReviewBody(BaseModel):
    action: str  # approve | return | fine  (no-show: fine | excuse)
    amount: int | None = None
    comment: str | None = None
    idempotency_key: str


class NotifyBody(BaseModel):
    enabled: bool


class PointBody(BaseModel):
    name: str
    capacity: int | None = None


class PointPatch(BaseModel):
    name: str | None = None
    capacity: int | None = None
    clear_capacity: bool = False
    active: bool | None = None


class BroadcastBody(BaseModel):
    audiences: list[str] = []
    html: str
    signed: bool = True
    network_id: int | None = None
    user_ids: list[int] | None = None
    require_ack: bool = False
    media_file_id: str | None = None
    # ТЗ: «отправить только тем, кто не отметил график на завтра»
    only_unmarked_tomorrow: bool = False
    media_kind: str = "photo"
    allow_reply: bool = False


class BroadcastTest(BaseModel):
    html: str
    signed: bool = True
    require_ack: bool = False
    media_file_id: str | None = None
    media_kind: str = "photo"
    allow_reply: bool = False


class ApplicationBody(BaseModel):
    """The questionnaire (the customer's module 3)."""
    full_name: str
    age: int
    metro: str
    min_days: int
    training_days: list[int]
    communication: int
    truthful: bool = False
    consent: bool = False


class TrainingBody(BaseModel):
    date: str
    time: str
    location: str
    trainer_id: int
    idempotency_key: str


class RejectBody(BaseModel):
    reason: str | None = None
    idempotency_key: str


class ContactBody(BaseModel):
    manager: str | None = None


class StageBody(BaseModel):
    stage: int
    counted: int
    bonus: int = 0
    idempotency_key: str


class TemplateBody(BaseModel):
    title: str
    html: str


class RemindBody(BaseModel):
    user_ids: list[int] | None = None


class ExportBody(BaseModel):
    date_from: str
    date_to: str
    user_id: int | None = None


class AdminDelete(BaseModel):
    idempotency_key: str
    reason: str


def _payload(body: ShiftPayload) -> dict:
    return {"start_time": body.start_time, "end_time": body.end_time, "volume": body.volume, "location": body.location}


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "telegram_id": user["telegram_id"],
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "username": user["username"],
        "status": user["status"],
        "role": perms.role_of(user),
        "work_status": user["work_status"],
        "account_state": user["account_state"],
        "state_reason": user["state_reason"],
        "network_id": user["network_id"],
        "is_owner": svc.is_owner(user),
        "notify_reminders": bool(user.get("notify_reminders", 1)),
        # «не работает 12 дн.»: seen by the person and by admins and trainers
        "work_status_at": user.get("work_status_at"),
        "status_days": svc.status_days(user),
        "min_weekly_shifts": user.get("min_weekly_shifts"),
        "created_at": user["created_at"],
    }


def money_fields(user: dict) -> dict:
    """A person's rates — shown to owners only (customer round B)."""
    return {"personal_rate": user.get("personal_rate"), "lazy_exempt": bool(user.get("lazy_exempt"))}


# All handlers and dependencies are `async def` on purpose: they then run on the event loop,
# so the synchronous service calls never overlap (plain `def` would go to a thread pool).
def create_app(conn: sqlite3.Connection, notifier: Notifier, bot_username: str | None = None) -> FastAPI:
    app = FastAPI(title="Schedule Mini App API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.bot_username = bot_username
    app_settings.load_pricing(conn)  # rates from settings (ТЗ 11.1)

    @app.exception_handler(svc.ServiceError)
    async def service_error(_: Request, exc: svc.ServiceError):
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status)

    async def tg_user(x_telegram_init_data: str = Header(default="")) -> dict:
        try:
            return validate_init_data(x_telegram_init_data, settings.bot_token, settings.init_data_max_age)
        except InitDataError as exc:
            raise svc.ServiceError("Откройте приложение из Telegram", 401, "unauthorized") from exc

    async def current_user(tg: dict = Depends(tg_user)) -> dict:
        user = svc.ensure_admin(conn, tg["id"], tg.get("first_name", ""), tg.get("last_name", ""), tg.get("username"))
        if user is None:
            raise svc.ServiceError("Вы не зарегистрированы", 403, "not_registered")
        user = svc.touch_username(conn, user, tg.get("username"))
        if user["status"] != "approved":
            raise svc.ServiceError("Нет доступа", 403, user["status"])
        if user["account_state"] == "blocked":
            raise svc.ServiceError("Аккаунт заблокирован", 403, "blocked")
        return user

    def staff(capability: str | None = None):
        """Dependency: the admin panel, optionally a specific capability (see permissions.CAN)."""
        async def dependency(user: dict = Depends(current_user)) -> dict:
            if not perms.is_staff(user) or (capability and not perms.can(user, capability)):
                raise svc.ServiceError("Недостаточно прав", 403, "forbidden")
            return user
        return dependency

    admin_user = staff()

    def require(actor: dict, capability: str, user_id: int | None) -> dict:
        """The actor holds the capability for this particular employee (trainers: own network only)."""
        target = svc.get_user(conn, user_id) if user_id is not None else None
        if user_id is not None and target is None:
            raise svc.ServiceError("Пользователь не найден", 404, "not_found")
        if not perms.can(actor, capability, target):
            raise svc.ServiceError("Недостаточно прав", 403, "forbidden")
        return target

    def shift_user(shift_id: int) -> int:
        shift = svc.get_shift(conn, shift_id)
        if shift is None:
            raise svc.ServiceError("Смена не найдена", 404, "not_found")
        return shift["user_id"]

    def visible(actor: dict, users: list[dict]) -> list[dict]:
        """Owners and admins see everyone, trainers their network (and themselves)."""
        if perms.sees_everyone(actor):
            return users
        return [u for u in users if u["id"] == actor["id"] or perms.manages(actor, u)]

    def visible_ids(actor: dict) -> set[int] | None:
        if perms.sees_everyone(actor):
            return None
        return {u["id"] for u in visible(actor, svc.list_users(conn))}

    async def read_photo(request: Request, required: bool) -> tuple[bytes, str] | None:
        """Raw image body (the Mini App sends a compressed JPEG); jpg/png/webp up to 10 MB."""
        data = await request.body()
        if not data:
            if required:
                raise svc.ServiceError("Прикрепите скриншот результата", 422, "photo_required")
            return None
        if len(data) > media.MAX_PHOTO:
            raise svc.ServiceError("Файл больше 10 МБ", 413, "too_large")
        ext = media.sniff(data)
        if ext is None:
            raise svc.ServiceError("Нужна картинка: JPG, PNG или WebP", 415, "bad_image")
        return data, ext

    def shift_out(shift: dict | None) -> dict | None:
        return svc.serialize_shift(shift, today_local(), conn)

    def respond(result: svc.Result, old: dict | None = None) -> dict:
        if result.changed and result.event and result.shift:
            notifier.spawn(notifier.shift_event(result.event, result.shift["id"], old))
        return {"shift": svc.serialize_shift(result.shift, today_local()), "changed": result.changed}

    api = APIRouter(prefix="/api")

    # ------------------------------------------------------------ profile

    @api.get("/me")
    async def me(tg: dict = Depends(tg_user)):
        user = svc.ensure_admin(conn, tg["id"], tg.get("first_name", ""), tg.get("last_name", ""), tg.get("username"))
        if user is None:
            return {"registered": False, "bot_username": app.state.bot_username}
        application = recruiting.current(conn, user["id"])
        if user["status"] != "approved":
            # a candidate: the questionnaire (none yet / returned to the reserve) or where it stands
            return {"registered": True, "user": public_user(user), "bot_username": app.state.bot_username,
                    "application": {"status": application["status"], "training": {
                        k: application[k] for k in ("training_date", "training_time", "training_location")}}
                    if application else None}
        return {
            "registered": True,
            # the customer's module 3: a trainee sees the internship's progress
            "internship": recruiting.progress(conn, application, user)
            if application and recruiting.is_trainee(user) else None,
            "user": public_user(user),
            "role_title": perms.ROLE_TITLES[perms.role_of(user)],
            "events": len(events.visible(conn, user)),
            "network": (networks.get(conn, user["network_id"]) or {}).get("name") if user["network_id"] else None,
            "capabilities": perms.capabilities(user),
            # a trainee's shifts are training ones and not paid
            "pricing": {**pricing.pricing_info(rates.for_user(conn, user["id"])), "training": recruiting.is_trainee(user)},
            "bot_username": app.state.bot_username,
        }

    @api.post("/register")
    async def register(body: ApplicationBody, request: Request, tg: dict = Depends(tg_user)):
        """The questionnaire from the Mini App. A referral comes as start_param=ref_<code> in the signed initData."""
        params = parse_qs(request.headers.get("x-telegram-init-data", ""))
        start_param = (params.get("start_param") or [None])[0]
        user, application = recruiting.submit(conn, tg, body.model_dump(), start_param)
        notifier.spawn(notifier.application_submitted(application))
        return {"application": {"status": application["status"]}}

    @api.get("/me/referrals")
    async def my_referrals(user: dict = Depends(current_user)):
        code = recruiting.ref_code(conn, user)
        bot = app.state.bot_username
        return {"code": code, "link": f"https://t.me/{bot}?start=ref_{code}" if bot else None,
                "items": recruiting.referrals(conn, user),
                "targets": {k: app_settings.get_all(conn)[k] for k in ("internship_units", "second_stage_units")}}

    @api.patch("/me/work-status")
    async def work_status(body: WorkStatus, user: dict = Depends(current_user)):
        return {"user": public_user(svc.set_work_status(conn, user, body.work_status, body.idempotency_key))}

    @api.patch("/me/notifications")
    async def my_notifications(body: NotifyBody, user: dict = Depends(current_user)):
        return {"user": public_user(svc.set_notify(conn, user, body.enabled))}

    @api.get("/points")
    async def points(_: dict = Depends(current_user)):
        return {"items": svc.list_points(conn)}

    @api.get("/me/penalties")
    async def my_penalties(user: dict = Depends(current_user)):
        return {"items": svc.user_penalties(conn, user["id"])}

    @api.get("/me/payment-profile")
    async def my_payment_profile(user: dict = Depends(current_user)):
        return {"profile": payouts.public_profile(payouts.get_profile(conn, user["id"]), True), "banks": payouts.BANKS}

    @api.put("/me/payment-profile")
    async def save_payment_profile(body: ProfileBody, user: dict = Depends(current_user)):
        profile = payouts.save_profile(conn, user, user["id"], body.phone, body.bank, body.recipient, body.idempotency_key)
        return {"profile": payouts.public_profile(profile, True), "banks": payouts.BANKS}

    @api.get("/events")
    async def my_events(user: dict = Depends(current_user)):
        return {"items": [events.view(conn, e, user) for e in events.visible(conn, user)]}

    @api.get("/events/{event_id}")
    async def my_event(event_id: int, user: dict = Depends(current_user)):
        return {"event": events.view(conn, events.readable(conn, user, event_id), user)}

    @api.get("/archive")
    async def my_archive(user: dict = Depends(current_user)):
        """ТЗ №22: hidden and long finished goals and events, with their results."""
        return {"events": [events.view(conn, e, user) for e in events.archived(conn, user)],
                "goals": goals.archived(conn, user)}

    @api.get("/media/prize_{kind}/{item_id}")
    async def prize_photo(kind: str, item_id: int, exp: int, sig: str):
        if kind not in lifecycle.TABLES or not media.verify(item_id, exp, sig, kind=f"prize_{kind}"):
            raise svc.ServiceError("Ссылка устарела", 403, "bad_link")
        row = dict(conn.execute(f"SELECT * FROM {lifecycle.TABLES[kind]} WHERE id = ?", (item_id,)).fetchone() or {})
        return await media_response(row.get("prize_photo"), row.get("prize_media_kind"))

    @api.post("/events/{event_id}/entries")
    async def event_entry(event_id: int, request: Request, value: int = Query(...), idempotency_key: str = Query(...),
                          user: dict = Depends(current_user)):
        if svc._seen(conn, idempotency_key):
            return {"event": events.view(conn, events.get(conn, event_id), user), "changed": False}
        event = events.check_entry(conn, events.get(conn, event_id), user, now_local())
        if event["mode"] == "first_to_reach" and value < event["target"]:
            raise svc.ServiceError(f"Цель гонки — {event['target']} шт.", 409, "below_target")
        data, ext = await read_photo(request, required=True)
        file_id = await notifier.store_photo(user["telegram_id"], data, ext,
                                             f"🏁 Ваша заявка в событии «{event['title']}»: {value} шт.")
        entry, changed = events.submit(conn, user, event_id, value, file_id, idempotency_key)
        if changed:
            notifier.spawn(notifier.event_entry(entry))
        return {"event": events.view(conn, events.get(conn, event_id), user), "changed": changed}

    @api.get("/media/event/{entry_id}")
    async def event_photo(entry_id: int, exp: int, sig: str):
        if not media.verify(entry_id, exp, sig, kind="event"):
            raise svc.ServiceError("Ссылка устарела", 403, "bad_link")
        entry = events.get_entry(conn, entry_id)
        found = await notifier.fetch_photo(entry["photo_file_id"]) if entry else None
        if found is None:
            raise svc.ServiceError("Фото недоступно", 404, "not_found")
        return Response(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=3600"})

    @api.get("/goals")
    async def my_goals(user: dict = Depends(current_user)):
        return {"items": goals.for_user(conn, user)}

    @api.post("/goals/{goal_id}/join")
    async def join_goal(goal_id: int, user: dict = Depends(current_user)):
        goals.join(conn, user, goal_id)
        return {"goal": goals.view(conn, goals.get(conn, goal_id), user)}

    @api.get("/me/finances")
    async def my_finances(user: dict = Depends(current_user)):
        # a trainer's fine is invisible to the employee until an owner approves it (ТЗ 5.3)
        items = finance.journal(conn, user_id=user["id"],
                                statuses=("draft", "active", "paid", "offset", "cancelled"), limit=200)
        return {"balance": {k: v for k, v in finance.balance(conn, user["id"]).items() if k != "pending"}, "items": items}

    # ------------------------------------------------------------ schedule

    @api.get("/schedule/week")
    async def week(offset: int = Query(0, ge=-52, le=svc.BOOKING_WEEKS_AHEAD), user: dict = Depends(current_user)):
        hide = not perms.is_staff(user) and not app_settings.get_all(conn)["show_colleague_names"]
        return svc.week_view(conn, user, offset, hide_names=hide)

    @api.get("/history")
    async def history(weeks_back: int = Query(8, ge=1, le=52), user: dict = Depends(current_user)):
        return {"weeks": svc.history(conn, user, weeks_back)}

    @api.post("/shifts/create")
    async def create(body: CreateShift, user: dict = Depends(current_user)):
        day = svc.parse_date(body.shift_date)
        return respond(svc.create_shift(conn, user, day, _payload(body), body.idempotency_key))

    @api.patch("/shifts/{shift_id}")
    async def edit(shift_id: int, body: ShiftPayload, user: dict = Depends(current_user)):
        before = svc.get_shift(conn, shift_id)
        return respond(svc.edit_shift(conn, user, shift_id, _payload(body), body.idempotency_key), before)

    @api.post("/shifts/{shift_id}/change-request")
    async def change_request(shift_id: int, body: ShiftPayload, user: dict = Depends(current_user)):
        """The second change of the time or the point: an admin or the network's trainer decides."""
        result = svc.request_change(conn, user, shift_id, _payload(body), body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.change_requested(result.request))
        return {"shift": svc.serialize_shift(result.shift, today_local(), conn), "changed": result.changed}

    @api.post("/change-requests/{request_id}/withdraw")
    async def change_withdraw(request_id: int, body: KeyOnly, user: dict = Depends(current_user)):
        result = svc.withdraw_change(conn, user, request_id, body.idempotency_key)
        return {"shift": svc.serialize_shift(result.shift, today_local(), conn), "changed": result.changed}

    @api.post("/shifts/{shift_id}/cancel")
    async def cancel(shift_id: int, body: KeyOnly, user: dict = Depends(current_user)):
        return respond(svc.cancel_shift(conn, user, shift_id, body.idempotency_key))

    @api.post("/shifts/{shift_id}/restore")
    async def restore(shift_id: int, body: ShiftPayload, user: dict = Depends(current_user)):
        return respond(svc.restore_shift(conn, user, shift_id, _payload(body), body.idempotency_key))

    @api.post("/shifts/{shift_id}/final-cancel-confirm")
    async def final_cancel(shift_id: int, body: KeyOnly, user: dict = Depends(current_user)):
        return respond(svc.final_cancel_shift(conn, user, shift_id, body.idempotency_key))

    @api.post("/shifts/{shift_id}/report")
    async def submit_report(shift_id: int, request: Request, fact_volume: int = Query(...),
                            idempotency_key: str = Query(...), user: dict = Depends(current_user)):
        if svc._seen(conn, idempotency_key):  # a retry of a report that already went through: don't upload again
            return {"shift": shift_out(svc.get_shift(conn, shift_id)), "changed": False}
        shift = reports.check_submittable(svc.get_shift(conn, shift_id), user, now_local())
        reports._check_value(fact_volume)
        data, ext = await read_photo(request, required=True)
        file_id = await notifier.store_photo(
            user["telegram_id"], data, ext,
            f"📸 Ваш отчёт за {shift['shift_date'][8:10]}.{shift['shift_date'][5:7]}: {fact_volume} шт.",
        )
        result = reports.submit(conn, user, shift_id, fact_volume, file_id, "app", idempotency_key)
        if result.changed:
            notifier.spawn(notifier.report_submitted(result.shift, result.report))
        return {"shift": shift_out(result.shift), "changed": result.changed}

    @api.get("/media/report/{report_id}")
    async def report_photo(report_id: int, exp: int, sig: str):
        if not media.verify(report_id, exp, sig):
            raise svc.ServiceError("Ссылка устарела", 403, "bad_link")
        report = reports.get_report(conn, report_id)
        found = await notifier.fetch_photo(report["photo_file_id"]) if report and report["photo_file_id"] else None
        if found is None:
            raise svc.ServiceError("Фото недоступно", 404, "not_found")
        data, mime = found
        return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})

    @api.post("/shifts/pre-declare-off")
    async def pre_declare(body: DateKey, user: dict = Depends(current_user)):
        return respond(svc.pre_declare_off(conn, user, svc.parse_date(body.shift_date), body.idempotency_key))

    @api.post("/shifts/{shift_id}/clear-off")
    async def clear_off(shift_id: int, body: KeyOnly, user: dict = Depends(current_user)):
        return respond(svc.clear_pre_declare_off(conn, user, shift_id, body.idempotency_key))

    # ------------------------------------------------------------ admin

    @api.get("/admin/users")
    async def admin_users(admin: dict = Depends(admin_user)):
        return {"items": [public_user(u) for u in visible(admin, svc.list_users(conn))]}

    @api.get("/admin/team")
    async def admin_team(admin: dict = Depends(admin_user)):
        people = svc.team(conn)
        if not perms.can(admin, "registrations"):
            people = [u for u in people if u["status"] == "approved"]
        nets = networks.names(conn)
        money = perms.can(admin, "money")
        items = [{**public_user(u), "network": nets.get(u["network_id"]), **(money_fields(u) if money else {})}
                 for u in visible(admin, people)]
        return {"items": items, "bot_username": app.state.bot_username}

    @api.post("/admin/team/{user_id}/role")
    async def admin_set_role(user_id: int, body: RoleBody, admin: dict = Depends(staff("roles"))):
        before = svc.get_user(conn, user_id)
        user, changed = svc.set_role(conn, admin, user_id, body.role, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.role_changed(user, admin, before["role"]))
        return {"user": public_user(user), "changed": changed}

    @api.post("/admin/team/{user_id}/access")
    async def admin_set_access(user_id: int, body: AccessBody, admin: dict = Depends(staff("registrations"))):
        before = svc.get_user(conn, user_id)
        user, changed = svc.set_access(conn, admin, user_id, body.allow, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.access_changed(user, before["status"], open_app_keyboard(), admin))
        return {"user": public_user(user), "changed": changed}

    @api.post("/admin/team/{user_id}/state")
    async def admin_set_state(user_id: int, body: StateBody, admin: dict = Depends(staff("pause"))):
        before = svc.get_user(conn, user_id)
        user, changed = svc.set_account_state(conn, admin, user_id, body.state, body.reason, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.account_state_changed(user, admin, before["account_state"]))
        return {"user": public_user(user), "changed": changed}

    @api.get("/admin/points")
    async def admin_points(_: dict = Depends(admin_user)):
        return {"items": svc.list_points(conn, include_inactive=True)}

    @api.post("/admin/points")
    async def admin_point_create(body: PointBody, admin: dict = Depends(staff("points"))):
        return {"point": svc.create_point(conn, admin, body.name, body.capacity)}

    @api.patch("/admin/points/{point_id}")
    async def admin_point_update(point_id: int, body: PointPatch, admin: dict = Depends(staff("points"))):
        changes = body.model_dump(exclude_unset=True, exclude={"clear_capacity"})
        if body.clear_capacity:
            changes["capacity"] = None
        return {"point": svc.update_point(conn, admin, point_id, changes)}

    # money settings: only owners see and change them (customer round B)
    OWNER_SETTINGS = set(pricing.DEFAULTS) | {"revenue_per_piece"}

    def settings_view(viewer: dict) -> dict:
        archive = app_settings.get_raw(conn, "archive_chat_id")
        values = app_settings.get_all(conn)
        if not perms.can(viewer, "money"):
            values = {k: v for k, v in values.items() if k not in OWNER_SETTINGS}
        return {
            "settings": values,
            "timezone": settings.timezone,
            "archive": {"connected": bool(archive), "title": app_settings.get_raw(conn, "archive_chat_title")},
        }

    @api.get("/admin/settings")
    async def admin_settings_get(admin: dict = Depends(staff("settings"))):
        return settings_view(admin)

    @api.patch("/admin/settings")
    async def admin_settings_set(body: dict, admin: dict = Depends(staff("settings"))):
        # rates are money: only owners change them (admins keep the schedule settings)
        if any(k in OWNER_SETTINGS and body[k] != app_settings.get_all(conn).get(k) for k in body) \
                and not perms.can(admin, "money"):
            raise svc.ServiceError("Ставки меняет только владелец", 403, "owner_only")
        app_settings.update(conn, body)
        return settings_view(admin)

    @api.get("/admin/broadcasts")
    async def admin_broadcasts(admin: dict = Depends(staff("broadcast_network"))):
        nets = networks.names(conn)
        marked = svc.marked_on(conn, today_local() + timedelta(days=1))
        people = [
            {"id": u["id"], "name": svc.full_name(u), "working": u["work_status"] == "working", "admin": u["role"] == "admin",
             "network_id": u["network_id"], "network": nets.get(u["network_id"]), "marked_tomorrow": u["id"] in marked}
            for u in visible(admin, svc.list_users(conn)) if u["account_state"] != "blocked"
        ]
        everyone = perms.can(admin, "broadcast_all")
        return {
            "items": [{**b, "media_url": media.photo_url(b["id"], kind="broadcast") if b["media_file_id"] else None}
                      for b in svc.list_broadcasts(conn, author_tg=None if everyone else admin["telegram_id"])],
            "audiences": svc.AUDIENCE_LABELS,
            "people": people,
            "networks": [{"id": n["id"], "name": n["name"]} for n in networks.listing(conn, admin)],
            "scope": "all" if everyone else "network",
        }

    @api.post("/admin/broadcasts")
    async def admin_broadcast(body: BroadcastBody, admin: dict = Depends(staff("broadcast_network"))):
        if notifier.bot is None:
            raise svc.ServiceError("Бот не запущен: рассылка недоступна", 503, "no_bot")
        if not perms.can(admin, "broadcast_all") and body.network_id not in (None, admin["network_id"]):
            raise svc.ServiceError("Тренер пишет только своей сети", 403, "forbidden")
        row, recipients = svc.create_broadcast(conn, admin, body.audiences, body.html, body.signed, body.network_id,
                                               body.user_ids, body.require_ack, body.media_file_id,
                                               today_local() + timedelta(days=1) if body.only_unmarked_tomorrow else None,
                                               body.media_kind, body.allow_reply)
        notifier.spawn(notifier.broadcast(row["id"], recipients, row["html"], bool(row["signed"]), row["media_file_id"],
                                          bool(row["require_ack"]), row["media_kind"], bool(row["allow_reply"])))
        return {"broadcast": row}

    @api.get("/admin/broadcasts/{broadcast_id}")
    async def admin_broadcast_receipts(broadcast_id: int, admin: dict = Depends(staff("broadcast_network"))):
        row = conn.execute("SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        if row is None or (not perms.can(admin, "broadcast_all") and row["author_id"] != admin["telegram_id"]):
            raise svc.ServiceError("Рассылка не найдена", 404, "not_found")
        replies = broadcasts.replies_by_user(conn, broadcast_id)
        items = [{"user_id": d["user_id"], "name": d["name"], "username": d["username"], "telegram_id": d["telegram_id"],
                  "delivered": bool(d["delivered"]), "read_at": d["read_at"], "reminders": d["reminders"],
                  "reminded_at": d["reminded_at"], "replies": replies.get(d["user_id"], [])}
                 for d in svc.broadcast_deliveries(conn, broadcast_id)]
        return {"broadcast": dict(row), "items": items}

    @api.get("/media/broadcast/{broadcast_id}")
    async def broadcast_media(broadcast_id: int, exp: int, sig: str):
        """The photo or video of a sent broadcast — to see it in full in «Отправленные»."""
        if not media.verify(broadcast_id, exp, sig, kind="broadcast"):
            raise svc.ServiceError("Ссылка устарела", 403, "bad_link")
        row = conn.execute("SELECT media_file_id, media_kind FROM broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        return await media_response(row["media_file_id"] if row else None, row["media_kind"] if row else None)

    async def media_response(file_id: str | None, kind: str | None):
        if file_id and kind == "video":
            path = await notifier.fetch_video(file_id)
            if path is None:
                raise svc.ServiceError("Видео недоступно в приложении — оно есть в чате с ботом", 404, "not_found")
            return FileResponse(path, media_type=media.VIDEO_TYPES.get(path.suffix.lstrip("."), "video/mp4"),
                                headers={"Cache-Control": "private, max-age=3600"})
        found = await notifier.fetch_photo(file_id) if file_id else None
        if found is None:
            raise svc.ServiceError("Фото недоступно", 404, "not_found")
        return Response(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=3600"})

    @api.post("/admin/broadcasts/{broadcast_id}/remind")
    async def admin_broadcast_remind(broadcast_id: int, body: RemindBody, admin: dict = Depends(staff("broadcast_network"))):
        row = conn.execute("SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        if row is None or (not perms.can(admin, "broadcast_all") and row["author_id"] != admin["telegram_id"]):
            raise svc.ServiceError("Рассылка не найдена", 404, "not_found")
        if not row["require_ack"]:
            raise svc.ServiceError("В этой рассылке не просили подтверждать прочтение", 409, "no_ack")
        if notifier.bot is None:
            raise svc.ServiceError("Бот не запущен", 503, "no_bot")
        sent = await notifier.broadcast_remind(broadcast_id, body.user_ids)
        return {"sent": sent}

    @api.post("/admin/broadcasts/test")
    async def admin_broadcast_test(body: BroadcastTest, admin: dict = Depends(staff("broadcast_network"))):
        if notifier.bot is None:
            raise svc.ServiceError("Бот не запущен: проверка недоступна", 503, "no_bot")
        html, _ = svc.prepare_message(body.html)
        markup = notify_keyboard(0, body.require_ack, body.allow_reply)
        if await notifier._deliver(admin["telegram_id"], notifier.broadcast_body(html, body.signed), body.media_file_id,
                                   markup, body.media_kind) is None:
            raise svc.ServiceError("Telegram не принял сообщение. Проверьте ссылки и форматирование", 502, "send_failed")
        return {"ok": True}

    @api.post("/admin/export")
    async def admin_export(body: ExportBody, admin: dict = Depends(staff("export"))):
        if body.user_id is not None:
            require(admin, "export", body.user_id)
        date_from, date_to = svc.parse_date(body.date_from), svc.parse_date(body.date_to)
        if date_to < date_from or (date_to - date_from).days > 370:
            raise svc.ServiceError("Период от 1 дня до года", 422, "bad_range")
        if notifier.bot is None:
            raise svc.ServiceError("Бот не запущен: файл некуда отправить", 503, "no_bot")
        data = export.build_xlsx(conn, date_from, date_to, body.user_id, visible_ids(admin))
        ok = await notifier.send_document(admin["telegram_id"], data, export.export_filename(date_from, date_to),
                                          "Выгрузка графика: итоги, смены и штрафы")
        if not ok:
            raise svc.ServiceError("Не удалось отправить файл. Напишите боту /start и попробуйте снова", 502, "send_failed")
        return {"ok": True}

    @api.get("/admin/raspred/tomorrow")
    async def admin_raspred(admin: dict = Depends(staff("raspred"))):
        return svc.raspred(conn, today_local() + timedelta(days=1), admin)

    @api.get("/admin/raspred/today")
    async def admin_raspred_today(admin: dict = Depends(staff("raspred"))):
        return svc.raspred(conn, today_local(), admin)

    @api.get("/admin/raspred")
    async def admin_raspred_day(date: str, admin: dict = Depends(staff("raspred"))):
        """ТЗ №21: the same text for any day, past or future; a trainer — their network only."""
        return svc.raspred(conn, svc.parse_date(date), admin)

    @api.get("/admin/day")
    async def admin_day(day: str = Query(...), admin: dict = Depends(admin_user)):
        d = svc.parse_date(day)
        today = today_local()
        items = []
        only = visible_ids(admin)
        for s in svc.shifts_on(conn, d):
            if only is not None and s["user_id"] not in only:
                continue
            item = svc.serialize_shift(s, today, conn)
            item.update(user_id=s["user_id"], name=f"{s['first_name']} {s['last_name']}", username=s["username"])
            items.append(item)
        count, volume = svc.day_totals(conn, d)
        if only is not None:
            counted = [i for i in items if i["status"] in svc.ACTIVE]
            count, volume = len(counted), sum(i["volume"] or 0 for i in counted)
        return {"date": d.isoformat(), "items": items, "total_count": count, "total_volume": volume}

    @api.post("/admin/change-requests/{request_id}/decide")
    async def admin_change_decide(request_id: int, body: TxDecision, admin: dict = Depends(staff("schedule"))):
        result = svc.decide_change(conn, admin, request_id, body.approve, body.comment, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.change_decided(result.request, admin))
        elif result.request["status"] != "pending":
            raise svc.ServiceError("Запрос уже рассмотрен", 409, "already_decided")
        return {"shift": svc.serialize_shift(result.shift, today_local(), conn), "changed": result.changed}

    @api.get("/admin/stats")
    async def admin_stats(user_id: int, date_from: str = Query(..., alias="from"), date_to: str = Query(..., alias="to"),
                    admin: dict = Depends(admin_user)):
        require(admin, "review", user_id)
        return svc.stats(conn, user_id, svc.parse_date(date_from), svc.parse_date(date_to))

    @api.get("/admin/penalties")
    async def admin_penalties(user_id: int | None = None, date_from: str | None = Query(None, alias="from"),
                        date_to: str | None = Query(None, alias="to"), admin: dict = Depends(admin_user)):
        if user_id is not None:
            require(admin, "review", user_id)
        items = svc.penalty_log(
            conn,
            user_id,
            svc.parse_date(date_from) if date_from else None,
            svc.parse_date(date_to) if date_to else None,
        )
        only = visible_ids(admin)
        return {"items": [i for i in items if only is None or i["user_id"] in only]}

    @api.patch("/admin/shifts/{shift_id}")
    async def admin_edit(shift_id: int, body: AdminEdit, admin: dict = Depends(admin_user)):
        owner_id = shift_user(shift_id)
        require(admin, "schedule", owner_id)
        if body.penalty_amount is not None:
            require(admin, "fine_direct", owner_id)
        if body.action in ("approve_urgent", "reject_urgent"):
            # same path as the bot's [Разрешить]/[Запретить] buttons, incl. the push to the employee
            approve = body.action == "approve_urgent"
            result = svc.decide_urgent(conn, shift_id, approve, admin["telegram_id"])
            if result.changed:
                notifier.spawn(notifier.urgent_decided(result.shift, approve))
            elif result.shift["status"] != "awaiting_approval":
                raise svc.ServiceError("Заявка уже рассмотрена", 409, "already_decided")
            return {"shift": svc.serialize_shift(result.shift, today_local()), "changed": result.changed}
        before = svc.get_shift(conn, shift_id)
        result = svc.admin_edit_shift(conn, admin, shift_id, body.model_dump(exclude={"idempotency_key"}),
                                      body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.admin_edit(admin, result.shift, body.reason))
            if (before and result.shift["penalty_amount"] is not None
                    and result.shift["penalty_amount"] != before["penalty_amount"]):
                notifier.spawn(notifier.penalty_assigned(result.shift, svc.full_name(admin)))
        return {"shift": svc.serialize_shift(result.shift, today_local()), "changed": result.changed}

    # ------------------------------------------------------------ reports (ТЗ 3.3–3.4)

    @api.get("/admin/reports")
    async def admin_reports(kind: str = "review", day: str | None = None, admin: dict = Depends(staff("review"))):
        only = visible_ids(admin)
        items = reports.queue(conn, kind, svc.parse_date(day) if day else None)
        items = [i for i in items if (only is None or i["user_id"] in only) and i["user_id"] != admin["id"]
                 or perms.is_owner(admin)]
        return {"items": items, "counts": reports.counts(conn, only)}

    @api.get("/admin/reports/counts")
    async def admin_report_counts(admin: dict = Depends(admin_user)):
        out = reports.counts(conn, visible_ids(admin)) if perms.can(admin, "review") else {"review": 0, "no_show": 0}
        out["actual_missing"] = len(day_actual.missing_days(conn)) if perms.can(admin, "global_actual") else 0
        only = visible_ids(admin)
        out["fines_pending"] = (len(finance.journal(conn, only=only, statuses=("pending_admin_approval",)))
                                if perms.can(admin, "approve_fines") else 0)
        out["fines_draft"] = len(finance.journal(conn, only=only, statuses=("draft",))) if perms.can(admin, "fine_direct") else 0
        out["violations"] = violations.pending_count(conn, admin) if perms.can(admin, "review") else 0
        return out

    # ------------------------------------------------------------ schedule violations (ТЗ 12.4)

    def with_media(item: dict) -> dict:
        item["media"] = [media.photo_url(item["id"], kind=f"violation{i}") for i in range(item["media_count"])]
        return item

    def violation_out(v: dict) -> dict:
        return with_media(violations.public(conn, v))

    @api.get("/admin/violations")
    async def admin_violations(status: str = "pending", admin: dict = Depends(staff("review"))):
        items = [with_media(i) for i in violations.listing(conn, admin, status)]
        cfg = app_settings.get_all(conn)
        quota = cfg["weekly_attendance_quota"] if cfg["attendance_check_enabled"] else 0
        return {"items": items, "pending": violations.pending_count(conn, admin), "quota": quota,
                "personal": violations.has_personal_norms(conn)}

    @api.post("/admin/team/{user_id}/norm")
    async def admin_set_norm(user_id: int, body: NormBody, admin: dict = Depends(staff("review"))):
        return {"user": public_user(violations.set_norm(conn, admin, user_id, body.norm, body.idempotency_key))}

    @api.post("/admin/violations/scan")
    async def admin_violations_scan(admin: dict = Depends(staff("settings"))):
        """Check the last finished week right now (the Monday job does the same; repeats create nothing)."""
        cfg = app_settings.get_all(conn)
        created = violations.scan(conn, cfg["weekly_attendance_quota"] if cfg["attendance_check_enabled"] else 0)
        if created:
            notifier.spawn(notifier.violations_found(created))
        return {"created": len(created)}

    @api.post("/admin/violations/{violation_id}/fine")
    async def admin_violation_fine(violation_id: int, body: ViolationFine, admin: dict = Depends(staff("review"))):
        result = violations.fine(conn, admin, violation_id, body.amount, body.comment, body.media_files,
                                 body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.violation_resolved(result.violation, result.tx, admin))
        return {"violation": violation_out(result.violation), "changed": result.changed}

    @api.post("/admin/violations/{violation_id}/waive")
    async def admin_violation_waive(violation_id: int, body: ViolationWaive, admin: dict = Depends(staff("review"))):
        result = violations.waive(conn, admin, violation_id, body.comment, body.media_files, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.violation_resolved(result.violation, None, admin))
        return {"violation": violation_out(result.violation), "changed": result.changed}

    @api.get("/media/tx/{tx_id}")
    async def tx_photo(tx_id: int, exp: int, sig: str):
        if not media.verify(tx_id, exp, sig, kind="tx"):
            raise svc.ServiceError("Ссылка устарела", 403, "bad_link")
        tx = finance.get(conn, tx_id)
        found = await notifier.fetch_photo(tx["media_file_id"]) if tx and tx["media_file_id"] else None
        if found is None:
            raise svc.ServiceError("Файл не найден", 404, "not_found")
        return Response(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=3600"})

    @api.get("/media/violation/{violation_id}/{index}")
    async def violation_photo(violation_id: int, index: int, exp: int, sig: str):
        if not media.verify(violation_id, exp, sig, kind=f"violation{index}"):
            raise svc.ServiceError("Ссылка устарела", 403, "bad_link")
        v = violations.get(conn, violation_id)
        files = violations.media_ids(v) if v else []
        found = await notifier.fetch_photo(files[index]) if 0 <= index < len(files) else None
        if found is None:
            raise svc.ServiceError("Файл не найден", 404, "not_found")
        return Response(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=3600"})

    # ------------------------------------------------------------ live events (ТЗ модуль 7)

    def event_admin_view(e: dict, admin: dict) -> dict:
        return {**events.view(conn, e), "in_feed": events.in_feed(conn, e),
                "can_manage": lifecycle.can_manage(conn, admin, "event", e),
                "entries": [{"id": x["id"], "user_id": x["user_id"], "value": x["value"], "status": x["status"],
                             "annulled": bool(x["annulled_at"]), "reason": x["reason"]}
                            for x in events.entries(conn, e["id"]) if x["status"] == "confirmed"]}

    def when(value: str | None):
        if value is None:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise svc.ServiceError("Некорректное время", 422, "bad_time") from exc
        return dt if dt.tzinfo else dt.replace(tzinfo=settings.tz)

    @api.get("/admin/events")
    async def admin_events(admin: dict = Depends(staff("review"))):
        return {"items": [event_admin_view(e, admin) for e in events.managed(conn, admin)],
                "pending": events.pending_entries(conn, admin), "prize_types": lifecycle.PRIZE_TYPES,
                "hide_after_days": lifecycle.hide_after_days(conn)}

    @api.post("/admin/events")
    async def admin_event_create(body: EventBody, admin: dict = Depends(staff("review"))):
        event = events.create(conn, admin, title=body.title, mode=body.mode, starts_at=when(body.starts_at),
                              ends_at=when(body.ends_at), key=body.idempotency_key, description=body.description,
                              prize=body.prize, target=body.target, max_winners=body.max_winners,
                              max_attempts=body.max_attempts, network_id=body.network_id, prize_type=body.prize_type,
                              prize_photo=body.prize_photo, prize_media_kind=body.prize_media_kind)
        notifier.spawn(notifier.event_created(event))
        return {"event": events.view(conn, event)}

    @api.patch("/admin/events/{event_id}")
    async def admin_event_update(event_id: int, body: EventPatch, admin: dict = Depends(staff("review"))):
        """ТЗ №20: critical changes are announced only when the editor confirmed «уведомить»."""
        sent = body.model_fields_set - {"version", "notify", "idempotency_key"}
        changes = {k: getattr(body, k) for k in sent}
        if "prize_media_kind" in changes and "prize_photo" not in changes:
            raise svc.ServiceError("Видео или фото приза меняется вместе с файлом", 422, "bad_media")
        for k in ("starts_at", "ends_at"):
            if k in changes:
                changes[k] = when(changes[k])
        result = events.update(conn, admin, event_id, body.version, changes, body.idempotency_key)
        if result.finished:
            notifier.spawn(notifier.event_finished(result.event))
        elif result.critical and body.notify:
            notifier.spawn(notifier.event_updated(result))
        return {"event": event_admin_view(result.event, admin), "changed": result.changed, "critical": result.critical,
                "finished": result.finished, "restarted": result.restarted}

    @api.post("/admin/events/{event_id}/visibility")
    async def admin_event_visibility(event_id: int, body: Visibility, admin: dict = Depends(staff("review"))):
        event = lifecycle.set_visibility(conn, admin, "event", event_id, body.hidden, body.idempotency_key)
        return {"event": event_admin_view(event, admin)}

    @api.post("/admin/events/{event_id}/delete")
    async def admin_event_delete(event_id: int, body: KeyOnly, admin: dict = Depends(staff("review"))):
        lifecycle.delete(conn, admin, "event", event_id, body.idempotency_key)
        return {"deleted": True}

    @api.post("/admin/events/{event_id}/cancel")
    async def admin_event_cancel(event_id: int, body: KeyOnly, admin: dict = Depends(staff("review"))):
        return {"event": event_admin_view(events.cancel(conn, admin, event_id, body.idempotency_key), admin)}

    @api.post("/admin/event-entries/{entry_id}/annul")
    async def admin_entry_annul(entry_id: int, body: ReasonBody, admin: dict = Depends(staff("review"))):
        result = events.annul_entry(conn, admin, entry_id, body.reason, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.entry_annulled(result, admin))
        return {"event": event_admin_view(result.event, admin), "changed": result.changed, "reopened": result.reopened}

    @api.post("/admin/event-entries/{entry_id}/review")
    async def admin_event_review(entry_id: int, body: EntryReview, admin: dict = Depends(staff("review"))):
        result = events.review(conn, admin, entry_id, body.approve, body.reason, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.event_reviewed(result, admin))
        return {"event": event_admin_view(result.event, admin), "changed": result.changed}

    # ------------------------------------------------------------ goals (ТЗ модуль 5)

    @api.get("/admin/goals")
    async def admin_goals(admin: dict = Depends(admin_user)):
        return {"items": goals.listing(conn, admin), "prize_types": lifecycle.PRIZE_TYPES,
                "hide_after_days": lifecycle.hide_after_days(conn)}

    def goal_admin_view(goal: dict, admin: dict) -> dict:
        return {**goals.view(conn, goal), "in_feed": goals.in_feed(conn, goal),
                "can_manage": lifecycle.can_manage(conn, admin, "goal", goal)}

    @api.post("/admin/goals")
    async def admin_goal_create(body: GoalBody, admin: dict = Depends(admin_user)):
        date_to = date.fromisoformat(goals.FOREVER) if body.forever else svc.parse_date(body.date_to)
        goal = goals.create(conn, admin, title=body.title, kind=body.kind, target=body.target,
                            date_from=svc.parse_date(body.date_from), date_to=date_to, user_ids=body.user_ids,
                            location=body.location, reset_time=body.reset_time,
                            key=body.idempotency_key, description=body.description, user_id=body.user_id,
                            network_id=body.network_id, mode=body.mode, prize=body.prize, prize_type=body.prize_type,
                            prize_photo=body.prize_photo)
        notifier.spawn(notifier.goal_created(goal))
        return {"goal": goal_admin_view(goal, admin)}

    @api.patch("/admin/goals/{goal_id}")
    async def admin_goal_update(goal_id: int, body: GoalPatch, admin: dict = Depends(admin_user)):
        sent = body.model_fields_set - {"version", "notify", "idempotency_key", "forever"}
        changes = {k: getattr(body, k) for k in sent}
        for k in ("date_from", "date_to"):
            if changes.get(k) is not None:
                changes[k] = svc.parse_date(changes[k])
        if body.forever:
            changes["date_to"] = date.fromisoformat(goals.FOREVER)
        result = goals.update(conn, admin, goal_id, body.version, changes, body.idempotency_key)
        if result.critical and body.notify:
            notifier.spawn(notifier.goal_updated(result))
        # a lowered target: those who reach it now hear about it at once, not on the next tick
        done = goals.check_completions(conn)
        if done:
            notifier.spawn(notifier.goals_completed(done))
        return {"goal": goal_admin_view(goals.get(conn, goal_id), admin), "changed": result.changed,
                "critical": result.critical}

    @api.post("/admin/goals/{goal_id}/visibility")
    async def admin_goal_visibility(goal_id: int, body: Visibility, admin: dict = Depends(admin_user)):
        goal = lifecycle.set_visibility(conn, admin, "goal", goal_id, body.hidden, body.idempotency_key)
        return {"goal": goal_admin_view(goal, admin)}

    @api.post("/admin/goals/{goal_id}/archive")
    async def admin_goal_archive(goal_id: int, body: KeyOnly, admin: dict = Depends(admin_user)):
        """Older clients: «В архив» is hiding now (ТЗ №22)."""
        goal = lifecycle.set_visibility(conn, admin, "goal", goal_id, True, body.idempotency_key)
        return {"goal": goal_admin_view(goal, admin)}

    @api.post("/admin/goals/{goal_id}/delete")
    async def admin_goal_delete(goal_id: int, body: KeyOnly, admin: dict = Depends(admin_user)):
        lifecycle.delete(conn, admin, "goal", goal_id, body.idempotency_key)
        return {"deleted": True}

    @api.post("/admin/goals/{goal_id}/revoke")
    async def admin_goal_revoke(goal_id: int, body: KeyOnly, admin: dict = Depends(admin_user)):
        goal, lost = goals.revoke(conn, admin, goal_id, body.idempotency_key)
        if lost:
            notifier.spawn(notifier.goal_revoked(goal, lost))
        return {"goal": goal_admin_view(goal, admin), "revoked": len(lost)}

    @api.post("/admin/goals/{goal_id}/annul")
    async def admin_goal_annul(goal_id: int, body: GoalAnnul, admin: dict = Depends(admin_user)):
        goal, changed = goals.annul(conn, admin, goal_id, body.user_id, body.reason, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.goal_annulled(goal, body.user_id, admin))
        return {"goal": goal_admin_view(goal, admin), "changed": changed}

    # ------------------------------------------------------------ networks (ТЗ модуль 8)

    @api.get("/admin/networks")
    async def admin_networks(archived: bool = False, admin: dict = Depends(admin_user)):
        return {"items": networks.listing(conn, admin, archived)}

    @api.post("/admin/networks")
    async def admin_network_create(body: NetworkBody, owner: dict = Depends(staff("networks"))):
        return {"network": networks.create(conn, owner, body.name, body.idempotency_key)}

    @api.patch("/admin/networks/{network_id}")
    async def admin_network_update(network_id: int, body: NetworkPatch, owner: dict = Depends(staff("networks"))):
        if body.archive:
            return {"network": networks.archive(conn, owner, network_id, body.idempotency_key)}
        net = None
        if body.name is not None:
            net = networks.rename(conn, owner, network_id, body.name, body.idempotency_key)
        if body.trainer_id is not None or body.clear_trainer:
            key = body.idempotency_key + (":trainer" if body.name is not None else "")
            net, previous = networks.set_trainer(conn, owner, network_id, None if body.clear_trainer else body.trainer_id, key)
            if body.trainer_id is not None:
                notifier.spawn(notifier.network_changed(svc.get_user(conn, body.trainer_id), net["name"], owner, trainer=True))
            if previous:
                notifier.spawn(notifier.role_changed(svc.get_user(conn, previous["id"]), owner, "trainer"))
        money = {}
        if body.rate_base is not None or body.clear_rate:
            money["rate_base"] = None if body.clear_rate else body.rate_base
        if body.ladder is not None:
            money["ladder"] = [{"from": s.from_, "rate": s.rate} for s in body.ladder]
        if body.ladder_period is not None:
            money["ladder_period"] = body.ladder_period
        if body.ladder_counts_trainer is not None:
            money["ladder_counts_trainer"] = body.ladder_counts_trainer
        if money:
            if not perms.can(owner, "money"):
                raise svc.ServiceError("Ставки меняет только владелец", 403, "owner_only")
            net = networks.set_rates(conn, owner, network_id, money, body.idempotency_key + ":rates")
        if net is None:
            raise svc.ServiceError("Нет изменений", 422, "no_changes")
        return {"network": net}

    @api.post("/admin/team/{user_id}/rates")
    async def admin_user_rates(user_id: int, body: UserRates, owner: dict = Depends(staff("money"))):
        changes = {}
        if body.personal_rate is not None or body.clear_personal_rate:
            changes["personal_rate"] = None if body.clear_personal_rate else body.personal_rate
        if body.lazy_exempt is not None:
            changes["lazy_exempt"] = body.lazy_exempt
        user = rates.set_user_rates(conn, owner, user_id, changes, body.idempotency_key)
        return {"user": {**public_user(user), **money_fields(user)}}

    @api.post("/admin/finances/pieces")
    async def admin_piece_deduction(body: PieceDeduction, owner: dict = Depends(staff("money"))):
        if body.media_file_id and len(body.media_file_id) > 300:
            raise svc.ServiceError("Некорректный файл", 422, "bad_media")
        txs = finance.piece_deduction(conn, owner, body.user_id, svc.parse_date(body.day), body.pieces, body.reason,
                                      body.idempotency_key, body.media_file_id or None)
        for tx in txs:
            notifier.spawn(notifier.tx_created(tx, owner))
        return {"items": [finance.public(t) for t in txs]}

    @api.post("/admin/team/{user_id}/network")
    async def admin_member_network(user_id: int, body: MemberNetwork, owner: dict = Depends(staff("networks"))):
        user, changed = networks.set_member(conn, owner, user_id, body.network_id, body.idempotency_key)
        if changed:
            name = networks.get(conn, body.network_id)["name"] if body.network_id else None
            notifier.spawn(notifier.network_changed(user, name, owner))
        return {"user": public_user(user), "changed": changed}

    # ------------------------------------------------------------ money (ТЗ модуль 4)

    @api.get("/admin/finances")
    async def admin_finances(user_id: int | None = None, status: str | None = None, type: str | None = None,
                             date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                             admin: dict = Depends(staff("review"))):
        if user_id is not None:
            require(admin, "review", user_id)
        items = finance.journal(
            conn, user_id=user_id, only=visible_ids(admin), types=(type,) if type else None,
            statuses=(status,) if status else None,
            date_from=svc.parse_date(date_from) if date_from else None, date_to=svc.parse_date(date_to) if date_to else None,
        )
        balance = finance.balance(conn, user_id) if user_id is not None else None
        return {"items": items, "balance": balance}

    @api.get("/admin/finances/summary")
    async def admin_finance_summary(admin: dict = Depends(staff("review"))):
        only = visible_ids(admin)
        pending = finance.journal(conn, only=only, statuses=("pending_admin_approval",)) if perms.can(admin, "approve_fines") else []
        drafts = finance.journal(conn, only=only, statuses=("draft",))
        return {"people": finance.summary(conn, only), "pending": pending, "drafts": drafts}

    @api.post("/admin/finances")
    async def admin_tx_create(body: TxBody, admin: dict = Depends(admin_user)):
        require(admin, "review", body.user_id)
        if body.media_file_id and len(body.media_file_id) > 300:
            raise svc.ServiceError("Некорректный файл", 422, "bad_media")
        result = finance.create(conn, admin, body.user_id, body.type, body.amount, body.reason, body.idempotency_key,
                                svc.parse_date(body.event_date) if body.event_date else None, body.media_file_id or None)
        if result.changed:
            notifier.spawn(notifier.tx_created(result.tx, admin))
        return {"tx": finance.public(result.tx), "changed": result.changed}

    @api.post("/admin/finances/{tx_id}/decide")
    async def admin_tx_decide(tx_id: int, body: TxDecision, owner: dict = Depends(staff("approve_fines"))):
        result = finance.decide(conn, owner, tx_id, body.approve, body.comment, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.tx_decided(result.tx, owner))
        return {"tx": finance.public(result.tx), "changed": result.changed}

    @api.post("/admin/finances/{tx_id}/cancel")
    async def admin_tx_cancel(tx_id: int, body: TxCancel, admin: dict = Depends(admin_user)):
        before = finance.get(conn, tx_id)
        result = finance.cancel(conn, admin, tx_id, body.reason, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.tx_cancelled(result.tx, admin, before["status"]))
            # the trainer's part of a deduction in pieces is annulled together with it
            for row in conn.execute("SELECT * FROM financial_transactions WHERE linked_tx_id = ? AND status = 'cancelled'",
                                    (tx_id,)):
                notifier.spawn(notifier.tx_cancelled(dict(row), admin, "active"))
        return {"tx": finance.public(result.tx), "changed": result.changed}

    @api.post("/admin/finances/{tx_id}/forgive")
    async def admin_tx_forgive(tx_id: int, body: TxForgive, admin: dict = Depends(staff("forgive_fines"))):
        """«Простить» a fine: owners and admins — anyone's, a trainer — their network's; the reason is optional."""
        before = finance.get(conn, tx_id)
        result = finance.forgive(conn, admin, tx_id, body.reason, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.tx_forgiven(result.tx, admin, before["status"], (body.reason or "").strip() or None))
        return {"tx": finance.public(result.tx), "changed": result.changed}

    @api.post("/admin/finances/{tx_id}/amount")
    async def admin_tx_amount(tx_id: int, body: TxAmount, admin: dict = Depends(admin_user)):
        result = finance.fill_draft(conn, admin, tx_id, body.amount, body.idempotency_key)
        if result.changed and result.tx["shift_id"]:
            notifier.spawn(notifier.penalty_assigned(svc.get_shift(conn, result.tx["shift_id"]), svc.full_name(admin)))
        elif result.changed:
            notifier.spawn(notifier.tx_created(result.tx, admin))
        return {"tx": finance.public(result.tx), "changed": result.changed}

    @api.post("/admin/finances/pay-all")
    async def admin_pay_all(body: PayAll, owner: dict = Depends(staff("money"))):
        rows = finance.pay_all(conn, owner, body.user_id, body.idempotency_key)
        if rows:
            notifier.spawn(notifier.fines_paid(body.user_id, rows, owner))
        return {"paid": len(rows), "total": sum(r["amount"] for r in rows)}

    # ------------------------------------------------------------ payouts and SBP requisites (ТЗ модули 9–10, owners)

    @api.get("/admin/users/{user_id}/payment-profile")
    async def admin_payment_profile(user_id: int, admin: dict = Depends(admin_user)):
        target = svc.get_user(conn, user_id)
        if target is None:
            raise svc.ServiceError("Пользователь не найден", 404, "not_found")
        full = payouts.can_see(admin, target)
        history = payouts.profile_history(conn, user_id)[:10] if full else []
        return {"profile": payouts.public_profile(payouts.get_profile(conn, user_id), full), "full": full,
                "banks": payouts.BANKS, "history": history}

    @api.put("/admin/users/{user_id}/payment-profile")
    async def admin_save_payment_profile(user_id: int, body: ProfileBody, owner: dict = Depends(staff("money"))):
        profile = payouts.save_profile(conn, owner, user_id, body.phone, body.bank, body.recipient, body.idempotency_key)
        return {"profile": payouts.public_profile(profile, True)}

    @api.post("/admin/media/video")
    async def admin_upload_video(request: Request, admin: dict = Depends(admin_user)):
        """A video for a broadcast or an event's prize (MP4/MOV up to 50 MB — Telegram's limit for bots)."""
        data = await request.body()
        if not data:
            raise svc.ServiceError("Выберите видео", 422, "video_required")
        if len(data) > media.MAX_VIDEO:
            raise svc.ServiceError("Видео больше 50 МБ — Telegram не примет его от бота", 413, "too_large")
        ext = media.sniff_video(data)
        if ext is None:
            raise svc.ServiceError("Нужно видео MP4 или MOV", 415, "bad_video")
        file_id = await notifier.store_video(admin["telegram_id"], data, ext, "🎬 Видео загружено в приложение")
        return {"file_id": file_id}

    # ------------------------------------------------------------ broadcast templates

    # ------------------------------------------------------------ onboarding (the customer's module 3)

    def application_out(a: dict) -> dict:
        return {**recruiting.view(conn, a), "avatar_url": media.photo_url(a["user_id"], kind="avatar")}

    @api.get("/admin/onboarding")
    async def admin_onboarding(tab: str = "new", _: dict = Depends(staff("registrations"))):
        people = [{"id": u["id"], "name": svc.full_name(u), "username": u["username"]}
                  for u in svc.list_users(conn) if u["account_state"] == "active"]
        cfg = app_settings.get_all(conn)
        return {"items": [application_out(a) for a in recruiting.listing(conn, tab)], "counts": recruiting.counts(conn),
                "trainers": people, "points": [p["name"] for p in svc.list_points(conn)],
                "targets": {k: cfg[k] for k in ("internship_units", "second_stage_units")}}

    @api.post("/admin/onboarding/{app_id}/training")
    async def admin_training(app_id: int, body: TrainingBody, admin: dict = Depends(staff("registrations"))):
        a, changed = recruiting.assign_training(conn, admin, app_id, svc.parse_date(body.date), body.time, body.location,
                                                body.trainer_id, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.training_assigned(a))
        return {"application": application_out(a)}

    @api.post("/admin/onboarding/{app_id}/reject")
    async def admin_application_reject(app_id: int, body: RejectBody, admin: dict = Depends(staff("registrations"))):
        a, changed = recruiting.reject(conn, admin, app_id, body.reason, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.application_rejected(a))
        return {"application": application_out(a)}

    @api.post("/admin/onboarding/{app_id}/cancel-training")
    async def admin_training_cancel(app_id: int, body: KeyOnly, admin: dict = Depends(staff("registrations"))):
        a, changed = recruiting.cancel_training(conn, admin, app_id, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.training_cancelled(a))
        return {"application": application_out(recruiting.get(conn, app_id))}

    @api.post("/admin/onboarding/{app_id}/return")
    async def admin_application_return(app_id: int, body: KeyOnly, admin: dict = Depends(staff("registrations"))):
        a, changed = recruiting.return_to_reserve(conn, admin, app_id, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.application_returned(a))
        return {"application": application_out(a)}

    @api.post("/admin/onboarding/{app_id}/contact")
    async def admin_application_contact(app_id: int, body: ContactBody, admin: dict = Depends(staff("registrations"))):
        a = recruiting.contact(conn, admin, app_id, body.manager)
        notifier.spawn(notifier.application_contact(a))
        return {"application": application_out(a)}

    @api.post("/admin/onboarding/{app_id}/internship")
    async def admin_internship(app_id: int, body: KeyOnly, admin: dict = Depends(staff("registrations"))):
        a, changed = recruiting.start_internship(conn, admin, app_id, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.internship_started(a))
        return {"application": application_out(a)}

    @api.post("/admin/onboarding/{app_id}/stage")
    async def admin_stage(app_id: int, body: StageBody, admin: dict = Depends(staff("registrations"))):
        before = recruiting.progress(conn, recruiting.get(conn, app_id) or {"status": None})
        a, tx, changed = recruiting.confirm_stage(conn, admin, app_id, body.stage, body.counted, body.bonus,
                                                  body.idempotency_key)
        if changed:
            notifier.spawn(notifier.stage_confirmed(a, body.stage, tx, before["units"] if before else None))
        return {"application": application_out(a)}

    @api.get("/media/avatar/{user_id}")
    async def avatar(user_id: int, exp: int, sig: str):
        """The candidate's Telegram profile photo for the onboarding card."""
        if not media.verify(user_id, exp, sig, kind="avatar"):
            raise svc.ServiceError("Ссылка устарела", 403, "bad_link")
        user = svc.get_user(conn, user_id)
        found = await notifier.fetch_avatar(user["telegram_id"]) if user else None
        if found is None:
            raise svc.ServiceError("Фото нет", 404, "not_found")
        return Response(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=3600"})

    @api.get("/admin/broadcast-templates")
    async def admin_templates(admin: dict = Depends(staff("broadcast_network"))):
        return {"items": [{**t, "can_edit": perms.sees_everyone(admin) or t["created_by"] == admin["telegram_id"],
                           "author": svc.full_name(t) if t["first_name"] else None} for t in broadcasts.templates(conn)]}

    @api.post("/admin/broadcast-templates")
    async def admin_template_create(body: TemplateBody, admin: dict = Depends(staff("broadcast_network"))):
        return {"template": broadcasts.save_template(conn, admin, body.title, body.html)}

    @api.patch("/admin/broadcast-templates/{template_id}")
    async def admin_template_update(template_id: int, body: TemplateBody, admin: dict = Depends(staff("broadcast_network"))):
        return {"template": broadcasts.save_template(conn, admin, body.title, body.html, template_id)}

    @api.post("/admin/broadcast-templates/{template_id}/delete")
    async def admin_template_delete(template_id: int, admin: dict = Depends(staff("broadcast_network"))):
        broadcasts.delete_template(conn, admin, template_id)
        return {"deleted": True}

    @api.post("/admin/media")
    async def admin_upload_media(request: Request, admin: dict = Depends(admin_user)):
        """An image for a payout or a fine (proof); kept in Telegram, the id goes back to the client."""
        data, ext = await read_photo(request, required=True)
        file_id = await notifier.store_photo(admin["telegram_id"], data, ext, "📎 Документ загружен в приложение")
        return {"file_id": file_id}

    @api.post("/admin/payouts/preview")
    async def admin_payout_preview(body: PayoutPreview, _: dict = Depends(staff("money"))):
        rates = payouts.Rates.from_dict(body.rates.model_dump())
        d_from, d_to = svc.parse_date(body.date_from), svc.parse_date(body.date_to)
        rows = payouts.calculate(conn, d_from, d_to, rates, body.network_id)
        return {"rows": [{k: v for k, v in r.items() if k != "tx_ids"} for r in rows], "rates": rates.as_dict(),
                "summary": payouts.summary(conn, rows, d_from, d_to, body.network_id)}

    @api.post("/admin/payouts")
    async def admin_payout_commit(body: PayoutCommit, owner: dict = Depends(staff("money"))):
        batch = payouts.commit(conn, owner, body.title, svc.parse_date(body.date_from), svc.parse_date(body.date_to),
                               payouts.Rates.from_dict(body.rates.model_dump()), body.network_id,
                               [c.model_dump() for c in body.choices], body.proof, body.deduction_proof,
                               body.idempotency_key)
        notifier.spawn(notifier.payout_committed(batch, owner))
        return {"batch": batch, "bank_text": payouts.bank_text(batch)}

    @api.get("/admin/payouts")
    async def admin_payouts(_: dict = Depends(staff("money"))):
        return {"items": payouts.history(conn), "rates": pricing.current()}

    @api.get("/admin/payouts/{batch_id}")
    async def admin_payout(batch_id: int, _: dict = Depends(staff("money"))):
        batch = payouts.batch(conn, batch_id)
        if batch is None:
            raise svc.ServiceError("Выплата не найдена", 404, "not_found")
        return {"batch": batch, "bank_text": payouts.bank_text(batch)}

    @api.post("/admin/payouts/{batch_id}/excel")
    async def admin_payout_excel(batch_id: int, owner: dict = Depends(staff("money"))):
        batch = payouts.batch(conn, batch_id)
        if batch is None:
            raise svc.ServiceError("Выплата не найдена", 404, "not_found")
        if notifier.bot is None:
            raise svc.ServiceError("Бот не запущен: файл некуда отправить", 503, "no_bot")
        ok = await notifier.send_document(owner["telegram_id"], export.payout_xlsx(batch), f"vyplata_{batch_id}.xlsx",
                                          f"Реестр выплаты «{batch['title']}»")
        if not ok:
            raise svc.ServiceError("Не удалось отправить файл. Напишите боту /start", 502, "send_failed")
        return {"ok": True}

    # ------------------------------------------------------------ the day's global actual (ТЗ 3.5, owners)

    @api.get("/admin/day-actuals")
    async def admin_day_actuals(_: dict = Depends(staff("global_actual"))):
        return {"items": day_actual.overview(conn), "missing": [d.isoformat() for d in day_actual.missing_days(conn)]}

    @api.post("/admin/day-actuals/{day}")
    async def admin_set_day_actual(day: str, body: ActualBody, owner: dict = Depends(staff("global_actual"))):
        d = svc.parse_date(day)
        result = day_actual.set_actual(conn, owner, d, body.actual, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.global_actual_set(d, result, owner))
        return {"changed": result.changed, "met": bool(result.row["met"]), "approved": len(result.approved),
                "fined": [{"shift_id": s["id"], "amount": s["shortfall_penalty"]} for s in result.fined]}

    @api.post("/admin/day-actuals/{day}/annul")
    async def admin_annul_day_fines(day: str, body: AnnulBody, owner: dict = Depends(staff("global_actual"))):
        done = day_actual.annul_fines(conn, owner, svc.parse_date(day), body.shift_ids, body.idempotency_key)
        if done:
            notifier.spawn(notifier.fines_annulled(done, owner))
        return {"annulled": len(done)}

    @api.post("/admin/shifts/{shift_id}/review")
    async def admin_review(shift_id: int, body: ReviewBody, admin: dict = Depends(staff("review"))):
        result = reports.review(conn, admin, shift_id, body.action, body.idempotency_key, body.amount, body.comment)
        if result.changed:
            notifier.spawn(notifier.report_reviewed(result.shift, result.report, admin))
        return {"shift": shift_out(result.shift), "changed": result.changed}

    @api.post("/admin/shifts/{shift_id}/no-show")
    async def admin_no_show(shift_id: int, body: ReviewBody, admin: dict = Depends(staff("review"))):
        result = reports.resolve_no_show(conn, admin, shift_id, body.action, body.idempotency_key, body.amount,
                                         body.comment)
        if result.changed:
            notifier.spawn(notifier.no_show_resolved(result.shift, admin))
        return {"shift": shift_out(result.shift), "changed": result.changed}

    @api.post("/admin/shifts/{shift_id}/result")
    async def admin_result(shift_id: int, request: Request, fact_volume: int = Query(...),
                           idempotency_key: str = Query(...), comment: str | None = None,
                           admin: dict = Depends(staff("review"))):
        if svc._seen(conn, idempotency_key):
            return {"shift": shift_out(svc.get_shift(conn, shift_id)), "changed": False}
        require(admin, "review", shift_user(shift_id))
        photo = await read_photo(request, required=False)
        file_id = None
        if photo:
            file_id = await notifier.store_photo(admin["telegram_id"], photo[0], photo[1],
                                                 "📎 Подтверждение к результату, внесённому вручную")
        result = reports.manual_result(conn, admin, shift_id, fact_volume, idempotency_key, file_id, comment)
        if result.changed:
            notifier.spawn(notifier.manual_result(result.shift, result.report, admin))
        return {"shift": shift_out(result.shift), "changed": result.changed}

    @api.post("/admin/shifts/{shift_id}/delete")
    async def admin_delete(shift_id: int, body: AdminDelete, admin: dict = Depends(admin_user)):
        require(admin, "schedule", shift_user(shift_id))
        result = svc.admin_delete_shift(conn, admin, shift_id, body.reason, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.admin_edit(admin, result.shift, body.reason))
        return {"changed": result.changed}

    app.include_router(api)

    @app.get("/healthz")
    async def healthz():
        conn.execute("SELECT 1")
        return {"ok": True, "date": date.today().isoformat()}

    dist = Path(settings.frontend_dist).resolve()
    if (dist / "index.html").exists():
        # Next.js static export: real files (/_next/..., icons) or the single-page index.html
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            file = (dist / path).resolve()
            if path and file.is_file() and dist in file.parents:
                cache = "public, max-age=31536000, immutable" if path.startswith("_next/static/") else "no-cache"
                return FileResponse(file, headers={"Cache-Control": cache})
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app
