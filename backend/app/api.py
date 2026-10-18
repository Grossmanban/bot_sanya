import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import app_settings, export, pricing
from . import permissions as perms
from . import services as svc
from .auth import InitDataError, validate_init_data
from .config import settings
from .notify import Notifier
from .bot import open_app_keyboard
from .timeutil import today_local


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
    fact_volume: int | None = None


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


class FactBody(BaseModel):
    fact_volume: int
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
    audiences: list[str]
    html: str
    signed: bool = True


class BroadcastTest(BaseModel):
    html: str
    signed: bool = True


class ExportBody(BaseModel):
    date_from: str
    date_to: str
    user_id: int | None = None


class ShortfallBody(BaseModel):
    amount: int | None = None  # None = no fine
    override: bool = True


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

    @api.post("/shifts/{shift_id}/fact")
    async def fact(shift_id: int, body: FactBody, user: dict = Depends(current_user)):
        result = svc.set_fact(conn, user, shift_id, body.fact_volume, body.idempotency_key)
        if result.changed:
            notifier.spawn(notifier.fact_result(result.shift))
        return {"shift": svc.serialize_shift(result.shift, today_local()), "changed": result.changed}

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
        return {"items": [public_user(u) for u in visible(admin, people)], "bot_username": app.state.bot_username}

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
    async def admin_broadcasts(_: dict = Depends(staff("broadcast_all"))):
        people = [
            {"id": u["id"], "working": u["work_status"] == "working", "admin": u["role"] == "admin"}
            for u in svc.list_users(conn) if u["account_state"] != "blocked"
        ]
        return {"items": svc.list_broadcasts(conn), "audiences": svc.AUDIENCES, "people": people}

    @api.post("/admin/broadcasts")
    async def admin_broadcast(body: BroadcastBody, admin: dict = Depends(staff("broadcast_all"))):
        if notifier.bot is None:
            raise svc.ServiceError("Бот не запущен: рассылка недоступна", 503, "no_bot")
        row, recipients = svc.create_broadcast(conn, admin, body.audiences, body.html, body.signed)
        notifier.spawn(notifier.broadcast(row["id"], recipients, row["html"], bool(row["signed"])))
        return {"broadcast": row}

    @api.post("/admin/broadcasts/test")
    async def admin_broadcast_test(body: BroadcastTest, admin: dict = Depends(staff("broadcast_all"))):
        if notifier.bot is None:
            raise svc.ServiceError("Бот не запущен: проверка недоступна", 503, "no_bot")
        html, _ = svc.prepare_message(body.html)
        if not await notifier.send(admin["telegram_id"], notifier.broadcast_body(html, body.signed)):
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
            item = svc.serialize_shift(s, today)
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
        if body.fact_volume is not None:
            require(admin, "review", owner_id)
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
            if before and result.shift["fact_volume"] != before["fact_volume"]:
                notifier.spawn(notifier.fact_result(result.shift, admin["telegram_id"]))
            if (before and result.shift["penalty_amount"] is not None
                    and result.shift["penalty_amount"] != before["penalty_amount"]):
                notifier.spawn(notifier.penalty_assigned(result.shift, svc.full_name(admin)))
        return {"shift": svc.serialize_shift(result.shift, today_local()), "changed": result.changed}

    @api.get("/admin/shortfalls")
    async def admin_shortfalls(pending: bool = True, admin: dict = Depends(admin_user)):
        only = visible_ids(admin)
        items = svc.shortfalls(conn, pending_only=pending)
        return {"items": [i for i in items if only is None or i["user_id"] in only]}

    @api.post("/admin/shifts/{shift_id}/shortfall")
    async def admin_shortfall(shift_id: int, body: ShortfallBody, admin: dict = Depends(admin_user)):
        require(admin, "fine_direct" if body.amount else "review", shift_user(shift_id))
        result = svc.decide_shortfall(conn, admin, shift_id, body.amount, override=body.override)
        if result.changed:
            notifier.spawn(notifier.shortfall_decided(result.shift, svc.full_name(admin), admin["telegram_id"]))
        return {"shift": svc.serialize_shift(result.shift, today_local()), "changed": result.changed}

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
