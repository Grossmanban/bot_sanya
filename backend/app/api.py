import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import app_settings, export, media, pricing
from . import permissions as perms
from . import day_actual, events, finance, goals, networks, payouts, reports
from . import services as svc
from .auth import InitDataError, validate_init_data
from .config import settings
from .notify import Notifier
from .bot import open_app_keyboard
from .notify import ack_keyboard as notify_ack
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
    idempotency_key: str


class NetworkBody(BaseModel):
    name: str
    idempotency_key: str


class NetworkPatch(BaseModel):
    name: str | None = None
    trainer_id: int | None = None
    clear_trainer: bool = False
    archive: bool = False
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
    idempotency_key: str


class TxDecision(BaseModel):
    approve: bool
    comment: str | None = None
    idempotency_key: str


class TxCancel(BaseModel):
    reason: str
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


class BroadcastTest(BaseModel):
    html: str
    signed: bool = True
    require_ack: bool = False
    media_file_id: str | None = None


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
        "created_at": user["created_at"],
    }


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
        return {
            "registered": True,
            "user": public_user(user),
            "role_title": perms.ROLE_TITLES[perms.role_of(user)],
            "events": len(events.visible(conn, user)),
            "network": (networks.get(conn, user["network_id"]) or {}).get("name") if user["network_id"] else None,
            "capabilities": perms.capabilities(user),
            "pricing": pricing.pricing_info(),
            "bot_username": app.state.bot_username,
        }

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
        event = events.get(conn, event_id)
        if event is None or event["id"] not in {e["id"] for e in events.visible(conn, user, days=30)}:
            raise svc.ServiceError("Событие не найдено", 404, "not_found")
        return {"event": events.view(conn, event, user)}

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
        return svc.week_view(conn, user, offset)

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
        items = [{**public_user(u), "network": nets.get(u["network_id"])} for u in visible(admin, people)]
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

    def settings_view() -> dict:
        archive = app_settings.get_raw(conn, "archive_chat_id")
        return {
            "settings": app_settings.get_all(conn),
            "timezone": settings.timezone,
            "archive": {"connected": bool(archive), "title": app_settings.get_raw(conn, "archive_chat_title")},
        }

    @api.get("/admin/settings")
    async def admin_settings_get(_: dict = Depends(staff("settings"))):
        return settings_view()

    @api.patch("/admin/settings")
    async def admin_settings_set(body: dict, _: dict = Depends(staff("settings"))):
        app_settings.update(conn, body)
        return settings_view()

    @api.get("/admin/broadcasts")
    async def admin_broadcasts(admin: dict = Depends(staff("broadcast_network"))):
        nets = networks.names(conn)
        people = [
            {"id": u["id"], "name": svc.full_name(u), "working": u["work_status"] == "working", "admin": u["role"] == "admin",
             "network_id": u["network_id"], "network": nets.get(u["network_id"])}
            for u in visible(admin, svc.list_users(conn)) if u["account_state"] != "blocked"
        ]
        everyone = perms.can(admin, "broadcast_all")
        return {
            "items": svc.list_broadcasts(conn, author_tg=None if everyone else admin["telegram_id"]),
            "audiences": svc.AUDIENCES,
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
                                               body.user_ids, body.require_ack, body.media_file_id)
        notifier.spawn(notifier.broadcast(row["id"], recipients, row["html"], bool(row["signed"]), row["media_file_id"],
                                          bool(row["require_ack"])))
        return {"broadcast": row}

    @api.get("/admin/broadcasts/{broadcast_id}")
    async def admin_broadcast_receipts(broadcast_id: int, admin: dict = Depends(staff("broadcast_network"))):
        row = conn.execute("SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)).fetchone()
        if row is None or (not perms.can(admin, "broadcast_all") and row["author_id"] != admin["telegram_id"]):
            raise svc.ServiceError("Рассылка не найдена", 404, "not_found")
        items = [{"user_id": d["user_id"], "name": d["name"], "username": d["username"], "telegram_id": d["telegram_id"],
                  "delivered": bool(d["delivered"]), "read_at": d["read_at"], "reminders": d["reminders"],
                  "reminded_at": d["reminded_at"]} for d in svc.broadcast_deliveries(conn, broadcast_id)]
        return {"broadcast": dict(row), "items": items}

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
        markup = notify_ack(0) if body.require_ack else None
        if await notifier._deliver(admin["telegram_id"], notifier.broadcast_body(html, body.signed), body.media_file_id,
                                   markup) is None:
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
    async def admin_raspred(_: dict = Depends(staff("raspred"))):
        return svc.raspred(conn, today_local() + timedelta(days=1))

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
        return out

    # ------------------------------------------------------------ live events (ТЗ модуль 7)

    @api.get("/admin/events")
    async def admin_events(admin: dict = Depends(staff("review"))):
        rows = events.visible(conn, admin, days=14)
        if not perms.sees_everyone(admin):
            rows = [e for e in rows if e["network_id"] == admin["network_id"]]
        return {"items": [events.view(conn, e) for e in rows], "pending": events.pending_entries(conn, admin)}

    @api.post("/admin/events")
    async def admin_event_create(body: EventBody, admin: dict = Depends(staff("review"))):
        def when(value: str):
            try:
                dt = datetime.fromisoformat(value)
            except ValueError as exc:
                raise svc.ServiceError("Некорректное время", 422, "bad_time") from exc
            return dt if dt.tzinfo else dt.replace(tzinfo=settings.tz)
        event = events.create(conn, admin, title=body.title, mode=body.mode, starts_at=when(body.starts_at),
                              ends_at=when(body.ends_at), key=body.idempotency_key, description=body.description,
                              prize=body.prize, target=body.target, max_winners=body.max_winners,
                              max_attempts=body.max_attempts, network_id=body.network_id)
        notifier.spawn(notifier.event_created(event))
        return {"event": events.view(conn, event)}

    @api.post("/admin/events/{event_id}/cancel")
    async def admin_event_cancel(event_id: int, body: KeyOnly, admin: dict = Depends(staff("review"))):
        return {"event": events.view(conn, events.cancel(conn, admin, event_id, body.idempotency_key))}

    @api.post("/admin/event-entries/{entry_id}/review")
    async def admin_event_review(entry_id: int, body: EntryReview, admin: dict = Depends(staff("review"))):
        result = events.review(conn, admin, entry_id, body.approve, body.reason, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.event_reviewed(result, admin))
        return {"event": events.view(conn, result.event), "changed": result.changed}

    # ------------------------------------------------------------ goals (ТЗ модуль 5)

    @api.get("/admin/goals")
    async def admin_goals(admin: dict = Depends(admin_user)):
        return {"items": goals.listing(conn, admin)}

    @api.post("/admin/goals")
    async def admin_goal_create(body: GoalBody, admin: dict = Depends(admin_user)):
        goal = goals.create(conn, admin, title=body.title, kind=body.kind, target=body.target,
                            date_from=svc.parse_date(body.date_from), date_to=svc.parse_date(body.date_to),
                            key=body.idempotency_key, description=body.description, user_id=body.user_id,
                            network_id=body.network_id, mode=body.mode)
        notifier.spawn(notifier.goal_created(goal))
        return {"goal": goals.view(conn, goal)}

    @api.post("/admin/goals/{goal_id}/archive")
    async def admin_goal_archive(goal_id: int, body: KeyOnly, admin: dict = Depends(admin_user)):
        return {"goal": goals.view(conn, goals.archive(conn, admin, goal_id, body.idempotency_key))}

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
        if net is None:
            raise svc.ServiceError("Нет изменений", 422, "no_changes")
        return {"network": net}

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
        result = finance.create(conn, admin, body.user_id, body.type, body.amount, body.reason, body.idempotency_key,
                                svc.parse_date(body.event_date) if body.event_date else None)
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

    @api.post("/admin/media")
    async def admin_upload_media(request: Request, admin: dict = Depends(admin_user)):
        """An image for a payout or a fine (proof); kept in Telegram, the id goes back to the client."""
        data, ext = await read_photo(request, required=True)
        file_id = await notifier.store_photo(admin["telegram_id"], data, ext, "📎 Документ загружен в приложение")
        return {"file_id": file_id}

    @api.post("/admin/payouts/preview")
    async def admin_payout_preview(body: PayoutPreview, _: dict = Depends(staff("money"))):
        rates = payouts.Rates.from_dict(body.rates.model_dump())
        rows = payouts.calculate(conn, svc.parse_date(body.date_from), svc.parse_date(body.date_to), rates, body.network_id)
        return {"rows": [{k: v for k, v in r.items() if k != "tx_ids"} for r in rows], "rates": rates.as_dict()}

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
