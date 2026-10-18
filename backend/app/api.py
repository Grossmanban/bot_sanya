import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import app_settings, export, media, pricing
from . import permissions as perms
from . import reports
from . import services as svc
from .auth import InitDataError, validate_init_data
from .config import settings
from .notify import Notifier
from .bot import open_app_keyboard
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
        if not perms.can(admin, "review"):
            return {"review": 0, "no_show": 0}
        return reports.counts(conn, visible_ids(admin))

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
