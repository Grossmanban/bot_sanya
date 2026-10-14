import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import pricing
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


class RoleBody(BaseModel):
    role: str
    idempotency_key: str


class AccessBody(BaseModel):
    allow: bool
    idempotency_key: str


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
        "role": user["role"],
        "work_status": user["work_status"],
        "is_owner": svc.is_owner(user),
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
        return user

    async def admin_user(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "admin":
            raise svc.ServiceError("Только для администраторов", 403, "forbidden")
        return user

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
            "pricing": pricing.pricing_info(),
            "bot_username": app.state.bot_username,
        }

    @api.patch("/me/work-status")
    async def work_status(body: WorkStatus, user: dict = Depends(current_user)):
        return {"user": public_user(svc.set_work_status(conn, user, body.work_status, body.idempotency_key))}

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

    @api.post("/shifts/pre-declare-off")
    async def pre_declare(body: DateKey, user: dict = Depends(current_user)):
        return respond(svc.pre_declare_off(conn, user, svc.parse_date(body.shift_date), body.idempotency_key))

    @api.post("/shifts/{shift_id}/clear-off")
    async def clear_off(shift_id: int, body: KeyOnly, user: dict = Depends(current_user)):
        return respond(svc.clear_pre_declare_off(conn, user, shift_id, body.idempotency_key))

    # ------------------------------------------------------------ admin

    @api.get("/admin/users")
    async def admin_users(_: dict = Depends(admin_user)):
        return {"items": [public_user(u) for u in svc.list_users(conn)]}

    @api.get("/admin/team")
    async def admin_team(_: dict = Depends(admin_user)):
        return {"items": [public_user(u) for u in svc.team(conn)], "bot_username": app.state.bot_username}

    @api.post("/admin/team/{user_id}/role")
    async def admin_set_role(user_id: int, body: RoleBody, admin: dict = Depends(admin_user)):
        user, changed = svc.set_role(conn, admin, user_id, body.role, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.role_changed(user))
        return {"user": public_user(user), "changed": changed}

    @api.post("/admin/team/{user_id}/access")
    async def admin_set_access(user_id: int, body: AccessBody, admin: dict = Depends(admin_user)):
        before = svc.get_user(conn, user_id)
        user, changed = svc.set_access(conn, admin, user_id, body.allow, body.idempotency_key)
        if changed:
            notifier.spawn(notifier.access_changed(user, before["status"], open_app_keyboard()))
        return {"user": public_user(user), "changed": changed}

    @api.get("/admin/raspred/tomorrow")
    async def admin_raspred(_: dict = Depends(admin_user)):
        return svc.raspred(conn, today_local() + timedelta(days=1))

    @api.get("/admin/day")
    async def admin_day(day: str = Query(...), _: dict = Depends(admin_user)):
        d = svc.parse_date(day)
        today = today_local()
        items = []
        for s in svc.shifts_on(conn, d):
            item = svc.serialize_shift(s, today)
            item.update(user_id=s["user_id"], name=f"{s['first_name']} {s['last_name']}", username=s["username"])
            items.append(item)
        count, volume = svc.day_totals(conn, d)
        return {"date": d.isoformat(), "items": items, "total_count": count, "total_volume": volume}

    @api.get("/admin/stats")
    async def admin_stats(user_id: int, date_from: str = Query(..., alias="from"), date_to: str = Query(..., alias="to"),
                    _: dict = Depends(admin_user)):
        return svc.stats(conn, user_id, svc.parse_date(date_from), svc.parse_date(date_to))

    @api.get("/admin/penalties")
    async def admin_penalties(user_id: int | None = None, date_from: str | None = Query(None, alias="from"),
                        date_to: str | None = Query(None, alias="to"), _: dict = Depends(admin_user)):
        return {
            "items": svc.penalty_log(
                conn,
                user_id,
                svc.parse_date(date_from) if date_from else None,
                svc.parse_date(date_to) if date_to else None,
            )
        }

    @api.patch("/admin/shifts/{shift_id}")
    async def admin_edit(shift_id: int, body: AdminEdit, admin: dict = Depends(admin_user)):
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
                notifier.spawn(notifier.penalty_assigned(result.shift))
        return {"shift": svc.serialize_shift(result.shift, today_local()), "changed": result.changed}

    @api.post("/admin/shifts/{shift_id}/delete")
    async def admin_delete(shift_id: int, body: AdminDelete, admin: dict = Depends(admin_user)):
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
