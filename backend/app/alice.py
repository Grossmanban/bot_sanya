"""«Яндекс Алиса» (ТЗ 27.10 §30–44): executions of the official promo scenario, voluntary for the person who
sets up Yandex search.

The employee's path, checked step by step on the server:
    start (access to the product, a QR code assigned — else «На вас не назначен QR-код…»)
    → the QR on screen; «QR-код отсканирован» hides it (the QR stays recorded in the execution)
    → three photo stages, each fixed before the next one; a photo may be retaken or removed until its stage is fixed
    → «Завершить выполнение»: the three photos go to the employee's topic in the forum group, then «На модерации».
A failed send is never reported as success: the execution waits as «Не отправлено» and is retried.

Photos live in Telegram (the forum group); the server keeps a file only until it is sent. The photos prove that
a report exists, not that its content is genuine; a photo already used in another execution is flagged.

Statuses: in_progress → sending / send_failed → moderation → rejected | accepted («Принято, ожидает оплаты») → paid.
«Всего завершено» counts everything submitted. The payout (§41–44): Yandex reports a number per QR code; the
owner enters it, the oldest accepted executions of that QR are paid first (a QR may be assigned to several
people), the rest keep waiting. Nothing changes on a preview, only on «Зафиксировать».
"""

import hashlib
import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from . import app_settings, media, products
from . import permissions as perms
from . import services as svc
from .config import settings
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, now_local

PRODUCT = "alice"
STAGES = {
    1: ("Подтверждение участия",
        "Экран акции с номером телефона и кнопками «Подтвердить участие» и «Изменить номер»."),
    2: ("Участие подтверждено",
        "Экран после нажатия «Подтвердить участие»: о начислении на баланс телефона и кнопка «На главную»."),
    3: ("Поисковый запрос выполнен",
        "Поиск Яндекса с введённым запросом точно как в задании — видно, что запрос выполнен."),
}
STATUS = {
    "in_progress": "Выполняется", "sending": "Отправляется", "send_failed": "Не отправлено",
    "moderation": "На модерации", "rejected": "Отклонено", "accepted": "Принято, ожидает оплаты", "paid": "Оплачено",
    "abandoned": "Прервано",
}
SUBMITTED = ("sending", "send_failed", "moderation", "rejected", "accepted", "paid")
MAX_REASON = 300
MAX_QUERY = 120
QUERIES_FILE = Path(__file__).parent / "resources" / "alice_queries.txt"


def _tmp_dir() -> Path:
    path = Path(settings.media_dir) / "alice_tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def number(task_id: int) -> str:
    """ТЗ 28.10 §18: «#152» — the task's own id, through-numbered, never reset, never reused."""
    return f"#{task_id}"


def title(task_id: int) -> str:
    return f"Алиса {number(task_id)}"


def qr_label(task: dict) -> str:
    """A QR code by its own number, or «QR 5» — never «#5», which is how tasks are numbered now (В40)."""
    return task.get("qr_number") or f"QR {task['qr_id']}"


# ---------------------------------------------------------------- QR codes


def get_qr(conn: sqlite3.Connection, qr_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM alice_qr WHERE id = ?", (qr_id,)).fetchone()
    return dict(row) if row else None


def assigned_qr(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """A person has at most one QR (one QR may be given to several people — §32, agreed with the owner)."""
    row = conn.execute(
        """SELECT q.* FROM alice_qr_assign a JOIN alice_qr q ON q.id = a.qr_id
           WHERE a.user_id = ? AND a.removed_at IS NULL AND q.archived_at IS NULL ORDER BY a.id DESC LIMIT 1""",
        (user_id,)).fetchone()
    return dict(row) if row else None


def _staff(conn, actor: dict) -> dict:
    actor = _fresh_actor(conn, actor)
    if not perms.can(actor, "alice"):
        raise ServiceError("QR-коды и модерацию ведут владелец и администраторы", 403, "forbidden")
    return actor


def _clean_number(value: str | None) -> str | None:
    return " ".join((value or "").split())[:30] or None


def create_qr(conn: sqlite3.Connection, actor: dict, image: str, number_: str | None, key: str) -> dict:
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return dict(conn.execute("SELECT * FROM alice_qr ORDER BY id DESC LIMIT 1").fetchone())
        actor = _staff(conn, actor)
        cur = conn.execute("INSERT INTO alice_qr (number, image, created_at, created_by) VALUES (?, ?, ?, ?)",
                           (_clean_number(number_), image, now_iso(), actor["telegram_id"]))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="ALICE_QR_CREATED", key=key,
                  new={"qr": cur.lastrowid, "number": _clean_number(number_)})
    return get_qr(conn, cur.lastrowid)


def update_qr(conn: sqlite3.Connection, actor: dict, qr_id: int, key: str, number_: str | None = None,
              image: str | None = None, archive: bool = False) -> dict:
    """A new number or picture, or the archive. Executions keep the QR they were made with."""
    _check_key(key)
    with transaction(conn):
        qr = get_qr(conn, qr_id)
        if qr is None:
            raise ServiceError("QR-код не найден", 404, "not_found")
        if _seen(conn, key):
            return qr
        actor = _staff(conn, actor)
        fields = {}
        if number_ is not None:
            fields["number"] = _clean_number(number_)
        if image:
            fields["image"] = image
        if archive:
            fields["archived_at"] = now_iso()
            conn.execute("UPDATE alice_qr_assign SET removed_at = ?, removed_by = ? WHERE qr_id = ? AND removed_at IS NULL",
                         (now_iso(), actor["telegram_id"], qr_id))
        if not fields:
            raise ServiceError("Нет изменений", 422, "no_changes")
        conn.execute(f"UPDATE alice_qr SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), qr_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="ALICE_QR_CHANGED", key=key,
                  old={k: qr[k] for k in fields if k != "image"}, new={"qr": qr_id, **{k: v for k, v in fields.items() if k != "image"},
                                                                        "image_changed": bool(image)})
    return get_qr(conn, qr_id)


def assign(conn: sqlite3.Connection, actor: dict, qr_id: int, user_id: int, on: bool, key: str) -> bool:
    """Give the QR to a person (their previous QR, if any, is taken away) or take it back."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return False
        actor = _staff(conn, actor)
        qr, user = get_qr(conn, qr_id), svc.get_user(conn, user_id)
        if qr is None or qr["archived_at"] or user is None:
            raise ServiceError("QR-код или сотрудник не найден", 404, "not_found")
        live = conn.execute("SELECT id FROM alice_qr_assign WHERE qr_id = ? AND user_id = ? AND removed_at IS NULL",
                            (qr_id, user_id)).fetchone()
        if on and not products.has(conn, user, PRODUCT):
            raise ServiceError("У сотрудника нет доступа к «Яндекс Алисе» — сначала выдайте доступ", 409, "no_product_access")
        if bool(live) == on:
            return False
        if on:
            conn.execute("UPDATE alice_qr_assign SET removed_at = ?, removed_by = ? WHERE user_id = ? AND removed_at IS NULL",
                         (now_iso(), actor["telegram_id"], user_id))
            conn.execute("INSERT INTO alice_qr_assign (qr_id, user_id, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
                         (qr_id, user_id, now_iso(), actor["telegram_id"]))
        else:
            conn.execute("UPDATE alice_qr_assign SET removed_at = ?, removed_by = ? WHERE id = ?",
                         (now_iso(), actor["telegram_id"], live["id"]))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], key=key,
                  event_type="ALICE_QR_ASSIGNED" if on else "ALICE_QR_UNASSIGNED", new={"qr": qr_id})
    return True


def qr_list(conn: sqlite3.Connection, archived: bool = False) -> list[dict]:
    names = {r["id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT id, first_name, last_name FROM users")}
    out = []
    for q in conn.execute("SELECT * FROM alice_qr WHERE (archived_at IS NOT NULL) = ? ORDER BY id DESC", (int(archived),)):
        q = dict(q)
        people = [{"user_id": r["user_id"], "name": names.get(r["user_id"]), "since": r["assigned_at"]}
                  for r in conn.execute("SELECT * FROM alice_qr_assign WHERE qr_id = ? AND removed_at IS NULL ORDER BY id",
                                        (q["id"],))]
        used = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM alice_tasks WHERE qr_id = ? GROUP BY status", (q["id"],))}
        out.append({"id": q["id"], "number": q["number"], "created_at": q["created_at"], "archived_at": q["archived_at"],
                    "image_url": media.photo_url(q["id"], kind="aliceqr"), "people": people,
                    "used": {"submitted": sum(used.get(s, 0) for s in SUBMITTED), "accepted": used.get("accepted", 0),
                             "paid": used.get("paid", 0), "rejected": used.get("rejected", 0)}})
    return out


def qr_history(conn: sqlite3.Connection, qr_id: int) -> list[dict]:
    names = {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT telegram_id, first_name, last_name FROM users")}
    people = {r["id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT id, first_name, last_name FROM users")}
    rows = [{"at": r["assigned_at"], "what": f"Назначен: {people.get(r['user_id'])}", "by": names.get(r["assigned_by"])}
            for r in conn.execute("SELECT * FROM alice_qr_assign WHERE qr_id = ?", (qr_id,))]
    rows += [{"at": r["removed_at"], "what": f"Снят: {people.get(r['user_id'])}", "by": names.get(r["removed_by"])}
             for r in conn.execute("SELECT * FROM alice_qr_assign WHERE qr_id = ? AND removed_at IS NOT NULL", (qr_id,))]
    rows += [{"at": r["completed_at"] or r["started_at"],
              "what": f"{title(r['id'])}: {people.get(r['user_id'])} — {STATUS[r['status']]}", "by": None}
             for r in conn.execute("SELECT * FROM alice_tasks WHERE qr_id = ? AND status != 'abandoned'", (qr_id,))]
    return sorted(rows, key=lambda r: r["at"] or "", reverse=True)[:200]


# ---------------------------------------------------------------- search queries


def ensure_queries(conn: sqlite3.Connection) -> None:
    """The list starts from resources/alice_queries.txt; afterwards it is edited in the admin panel."""
    if conn.execute("SELECT 1 FROM alice_queries LIMIT 1").fetchone():
        return
    lines = [ln.strip() for ln in QUERIES_FILE.read_text(encoding="utf-8").splitlines()]
    with transaction(conn):
        conn.executemany("INSERT OR IGNORE INTO alice_queries (text) VALUES (?)",
                         [(ln[:MAX_QUERY],) for ln in lines if ln and not ln.startswith("#")])


def queries(conn: sqlite3.Connection) -> list[str]:
    ensure_queries(conn)
    return [r[0] for r in conn.execute("SELECT text FROM alice_queries WHERE active = 1 ORDER BY id")]


def set_queries(conn: sqlite3.Connection, actor: dict, text: str, key: str) -> list[str]:
    """The whole list at once, one query per line. Removed ones are switched off: executions keep their query."""
    _check_key(key)
    wanted = list(dict.fromkeys(" ".join(ln.split())[:MAX_QUERY] for ln in (text or "").splitlines() if ln.strip()))
    if len(wanted) < 10:
        raise ServiceError("Нужно хотя бы 10 запросов — иначе они будут часто повторяться", 422, "too_few")
    ensure_queries(conn)
    with transaction(conn):
        if _seen(conn, key):
            return queries(conn)
        actor = _staff(conn, actor)
        conn.execute("UPDATE alice_queries SET active = 0")
        for q in wanted:
            conn.execute("INSERT INTO alice_queries (text, active) VALUES (?, 1) ON CONFLICT(text) DO UPDATE SET active = 1", (q,))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="ALICE_QUERIES_SET", key=key,
                  new={"count": len(wanted)})
    return queries(conn)


def pick_query(conn: sqlite3.Connection, user_id: int, rng: random.Random | None = None) -> str:
    """At random, not one of the person's recent ones while the list allows it."""
    pool = queries(conn)
    if not pool:
        raise ServiceError("Список поисковых запросов пуст — обратитесь к администратору", 409, "no_queries")
    recent = {r[0] for r in conn.execute("SELECT query FROM alice_tasks WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                                         (user_id, max(0, min(len(pool) - 1, 50))))}
    fresh = [q for q in pool if q not in recent] or pool
    return (rng or random).choice(fresh)


# ---------------------------------------------------------------- an execution, step by step


def get(conn: sqlite3.Connection, task_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM alice_tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def active(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM alice_tasks WHERE user_id = ? AND status = 'in_progress'", (user_id,)).fetchone()
    return dict(row) if row else None


def _own(conn, user: dict, task_id: int) -> dict:
    products.require(conn, user, PRODUCT)
    task = get(conn, task_id)
    if task is None or task["user_id"] != user["id"]:
        raise ServiceError("Выполнение не найдено", 404, "not_found")
    if task["status"] != "in_progress":
        raise ServiceError("Выполнение уже завершено", 409, "not_in_progress")
    return task


def photos(conn: sqlite3.Connection, task_id: int) -> dict[int, dict]:
    return {r["stage"]: dict(r) for r in conn.execute(
        "SELECT * FROM alice_photos WHERE task_id = ? AND replaced_at IS NULL", (task_id,))}


def start(conn: sqlite3.Connection, user: dict, key: str) -> dict:
    """«Начать выполнение». An execution already going is returned as is (one at a time; a repeated tap
    doesn't create a second one)."""
    _check_key(key)
    products.require(conn, user, PRODUCT)
    ensure_queries(conn)
    with transaction(conn):
        going = active(conn, user["id"])
        if going is not None:
            return going
        if _seen(conn, key):
            return get(conn, conn.execute("SELECT MAX(id) FROM alice_tasks WHERE user_id = ?", (user["id"],)).fetchone()[0])
        # ТЗ 28.10 §9, В27: a new execution needs «Работаю»; one already started can still be finished or stopped
        svc.require_working(svc.get_user(conn, user["id"]), "Новые выполнения недоступны")
        qr = assigned_qr(conn, user["id"])
        if qr is None:
            raise ServiceError("На вас не назначен QR-код. Обратитесь к администратору", 409, "no_qr")
        cur = conn.execute("INSERT INTO alice_tasks (user_id, qr_id, qr_number, query, started_at) VALUES (?, ?, ?, ?, ?)",
                           (user["id"], qr["id"], qr["number"], pick_query(conn, user["id"]), now_iso()))
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="ALICE_STARTED", key=key,
                  new={"task": cur.lastrowid, "qr": qr["id"]})
    return get(conn, cur.lastrowid)


def scanned(conn: sqlite3.Connection, user: dict, task_id: int) -> dict:
    """«QR-код отсканирован»: the picture is hidden from now on; the QR stays in the record. After going back to
    the QR the first scan is kept (В41)."""
    with transaction(conn):
        task = _own(conn, user, task_id)
        if task["step"] == 0:
            conn.execute("UPDATE alice_tasks SET step = 1, scanned_at = COALESCE(scanned_at, ?) WHERE id = ?",
                         (now_iso(), task_id))
    return get(conn, task_id)


def goto(conn: sqlite3.Connection, user: dict, task_id: int, stage: int) -> dict:
    """ТЗ 28.10 §19: back to an earlier stage (0 — the QR code again). Photos, the QR and the scan stay; the stages
    from there on are fixed again with «Далее», each one checking its photo."""
    with transaction(conn):
        task = _own(conn, user, task_id)
        if not 0 <= stage < task["step"]:
            raise ServiceError("На этот этап перейти нельзя — обновите экран", 409, "wrong_stage")
        unfix = ", ".join(f"stage{s}_at = NULL" for s in STAGES if s >= stage)
        conn.execute(f"UPDATE alice_tasks SET step = ?{', ' + unfix if unfix else ''} WHERE id = ?", (stage, task_id))
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="ALICE_STEP_BACK", key=svc.new_key(),
                  old={"step": task["step"]}, new={"step": stage, "task": task_id})
    return get(conn, task_id)


def check_photo(photo: dict | None, stage: int) -> None:
    """§19: a database row is not enough — the file must be there and unchanged. A missing or broken photo stops the
    way forward with the stage named; a failure to read is reported as a retry, never as «no photo»."""
    what = f"Фото этапа {stage} «{STAGES[stage][0]}»"
    if photo is None:
        raise ServiceError(f"Прикрепите фото этапа {stage}", 409, f"photo_missing_{stage}")
    if photo.get("file_id") and not photo.get("temp_path"):
        return  # already in Telegram
    path = Path(photo["temp_path"] or "")
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise ServiceError(f"{what} пропало — загрузите его заново", 409, f"photo_missing_{stage}") from None
    except OSError as exc:
        raise ServiceError("Не удалось проверить фото — повторите через минуту", 503, "photo_check_failed") from exc
    if hashlib.sha256(data).hexdigest() != photo["sha256"]:
        raise ServiceError(f"{what} повреждено — загрузите его заново", 409, f"photo_missing_{stage}")


def put_photo(conn: sqlite3.Connection, user: dict, task_id: int, stage: int, data: bytes, ext: str) -> dict:
    """The photo of the current stage; a new one replaces the previous until the stage is fixed."""
    with transaction(conn):
        task = _own(conn, user, task_id)
        if stage not in STAGES or task["step"] != stage:
            raise ServiceError("Сейчас другой этап — обновите экран", 409, "wrong_stage")
        old = photos(conn, task_id).get(stage)
        digest = hashlib.sha256(data).hexdigest()
        path = _tmp_dir() / f"{task_id}_{stage}_{digest[:12]}.{ext}"
        path.write_bytes(data)
        if old:
            conn.execute("UPDATE alice_photos SET replaced_at = ? WHERE id = ?", (now_iso(), old["id"]))
            _drop(old)
        conn.execute("""INSERT INTO alice_photos (task_id, stage, temp_path, sha256, size, uploaded_at)
                        VALUES (?, ?, ?, ?, ?, ?)""", (task_id, stage, str(path), digest, len(data), now_iso()))
    return get(conn, task_id)


def _drop(photo: dict) -> None:
    if photo.get("temp_path"):
        Path(photo["temp_path"]).unlink(missing_ok=True)


def delete_photo(conn: sqlite3.Connection, user: dict, task_id: int, stage: int) -> dict:
    with transaction(conn):
        task = _own(conn, user, task_id)
        if task["step"] != stage:
            raise ServiceError("Этот этап уже зафиксирован", 409, "wrong_stage")
        old = photos(conn, task_id).get(stage)
        if old:
            conn.execute("UPDATE alice_photos SET replaced_at = ? WHERE id = ?", (now_iso(), old["id"]))
            _drop(old)
    return get(conn, task_id)


def fix_stage(conn: sqlite3.Connection, user: dict, task_id: int) -> dict:
    """«Далее»: the stage's photo is fixed, the next stage opens."""
    with transaction(conn):
        task = _own(conn, user, task_id)
        step = task["step"]
        if step not in STAGES:
            raise ServiceError("Сначала отметьте, что QR-код отсканирован", 409, "wrong_stage")
        check_photo(photos(conn, task_id).get(step), step)
        conn.execute(f"UPDATE alice_tasks SET step = ?, stage{step}_at = ? WHERE id = ?", (step + 1, now_iso(), task_id))
    return get(conn, task_id)


def finish(conn: sqlite3.Connection, user: dict, task_id: int, key: str) -> dict:
    """«Завершить выполнение»: all three stages fixed → «Отправляется» (deliver() sends it to the group)."""
    _check_key(key)
    with transaction(conn):
        task = get(conn, task_id)
        if task is not None and task["user_id"] == user["id"] and task["status"] != "in_progress" and _seen(conn, key):
            return task
        task = _own(conn, user, task_id)
        have = photos(conn, task_id)
        if task["step"] != 4:
            raise ServiceError("Нужны все три фото — пройдите этапы по порядку", 409, "photos_required")
        for s in STAGES:  # §19: every photo really there before sending
            check_photo(have.get(s), s)
        conn.execute("UPDATE alice_tasks SET status = 'sending', completed_at = ? WHERE id = ?", (now_iso(), task_id))
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="ALICE_FINISHED", key=key,
                  new={"task": task_id})
    return get(conn, task_id)


def abandon(conn: sqlite3.Connection, user: dict, task_id: int) -> dict:
    """«⛔ Прервать выполнение» (§20): the photos are deleted, the execution never counts as done. A second tap
    (or a tap on a stale screen) changes nothing and is not an error."""
    with transaction(conn):
        task = get(conn, task_id)
        if task is not None and task["user_id"] == user["id"] and task["status"] == "abandoned":
            return task
        _own(conn, user, task_id)
        conn.execute("UPDATE alice_tasks SET status = 'abandoned' WHERE id = ? AND status = 'in_progress'", (task_id,))
        for p in photos(conn, task_id).values():
            _drop(p)
            conn.execute("UPDATE alice_photos SET temp_path = NULL, replaced_at = ? WHERE id = ?", (now_iso(), p["id"]))
        log_event(conn, user_id=user["id"], actor_id=user["telegram_id"], event_type="ALICE_ABANDONED", key=svc.new_key(),
                  new={"task": task_id})
    return get(conn, task_id)


def duplicates(conn: sqlite3.Connection, task_id: int) -> list[int]:
    """Other executions with exactly the same photo — a hint for the moderator, not a verdict."""
    return [r[0] for r in conn.execute(
        """SELECT DISTINCT o.task_id FROM alice_photos p JOIN alice_photos o ON o.sha256 = p.sha256 AND o.task_id != p.task_id
           JOIN alice_tasks t ON t.id = o.task_id
           WHERE p.task_id = ? AND p.replaced_at IS NULL AND o.replaced_at IS NULL AND t.status != 'abandoned'""", (task_id,))]


def view(conn: sqlite3.Connection, task: dict, staff: bool = False) -> dict:
    have = photos(conn, task["id"])
    qr = get_qr(conn, task["qr_id"])
    names = {} if not staff else {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute(
        "SELECT telegram_id, first_name, last_name FROM users WHERE telegram_id = ?", (task["moderated_by"] or 0,))}
    out = {
        "id": task["id"], "number": number(task["id"]), "title": title(task["id"]), "status": task["status"],
        "status_title": STATUS[task["status"]], "qr_label": qr_label(task),
        "step": task["step"], "query": task["query"], "qr_number": task["qr_number"],
        # the QR picture only before «QR-код отсканирован» (§31.6), for the staff always
        "qr_url": media.photo_url(qr["id"], kind="aliceqr") if qr and (task["step"] == 0 or staff) else None,
        "started_at": task["started_at"], "scanned_at": task["scanned_at"], "completed_at": task["completed_at"],
        "stages": [{"stage": s, "title": t, "hint": h, "fixed_at": task[f"stage{s}_at"],
                    "photo_url": media.photo_url(have[s]["id"], kind="alice") if s in have else None}
                   for s, (t, h) in STAGES.items()],
        "send_error": task["send_error"] if task["status"] == "send_failed" else None,
        "reject_reason": task["reject_reason"], "moderated_at": task["moderated_at"], "paid_at": task["paid_at"],
    }
    if staff:
        user = svc.get_user(conn, task["user_id"])
        out.update(user_id=task["user_id"], name=svc.full_name(user), username=user["username"],
                   telegram_id=user["telegram_id"], qr_id=task["qr_id"], duplicates=duplicates(conn, task["id"]),
                   moderated_by=names.get(task["moderated_by"]), payout_id=task["payout_id"],
                   message_link=_message_link(task))
    return out


def _message_link(task: dict) -> str | None:
    if not task["chat_id"] or not task["message_ids"]:
        return None
    first = json.loads(task["message_ids"])[0]
    return f"https://t.me/c/{str(task['chat_id']).removeprefix('-100')}/{first}"


# ---------------------------------------------------------------- the forum group (§36)


def forum(conn: sqlite3.Connection) -> dict | None:
    chat_id = app_settings.get_raw(conn, "alice_chat_id")
    return {"chat_id": int(chat_id), "title": app_settings.get_raw(conn, "alice_chat_title")} if chat_id else None


def connect_forum(conn: sqlite3.Connection, chat_id: int | None, title: str | None) -> None:
    app_settings.set_raw(conn, "alice_chat_id", str(chat_id) if chat_id is not None else None)
    app_settings.set_raw(conn, "alice_chat_title", title if chat_id is not None else None)


def _person(conn, task: dict) -> str:
    from .notify import person_link  # notify imports services; this module is imported by notify lazily

    return person_link(svc.get_user(conn, task["user_id"]))


def caption(conn: sqlite3.Connection, task: dict, stage: int = 1) -> str:
    """ТЗ 28.10 §22: every photo says which task and which stage it is, the name opens the profile; the common block
    (time, QR, query, status) — once, on the first photo. Telegram shows the captions of an album's photos when a
    photo is opened; under the album it shows the text only of a single captioned photo — so nothing is lost."""
    head = f"📸 <b>{title(task['id'])}</b> · фото {stage}/3 — {STAGES[stage][0]}\n👤 {_person(conn, task)}"
    if stage != 1:
        return head
    at = datetime.fromisoformat(task["completed_at"] or task["started_at"]).strftime("%d.%m.%Y %H:%M")
    mark = {"moderation": "🟡", "accepted": "🟢", "paid": "💰", "rejected": "🔴"}.get(task["status"], "⚪")
    status = STATUS[task["status"]] + (f": {escape(task['reject_reason'])}" if task["status"] == "rejected" and task["reject_reason"] else "")
    return (f"{head}\n"
            f"🕒 {at} (МСК)\n"
            f"🔳 QR: {escape(qr_label(task))}\n"
            f"🔎 Запрос: «{escape(task['query'])}»\n"
            f"Статус: {mark} {status}")


async def _topic(conn: sqlite3.Connection, bot, user: dict, chat_id: int, fresh: bool = False) -> int:
    """A topic per employee: their reports don't mix and are easy to find."""
    row = conn.execute("SELECT * FROM alice_topics WHERE user_id = ? AND chat_id = ?", (user["id"], chat_id)).fetchone()
    if row and not fresh:
        return row["thread_id"]
    topic = await bot.create_forum_topic(chat_id, name=f"{svc.full_name(user)} · {user['telegram_id']}"[:128])
    with transaction(conn):
        conn.execute("""INSERT INTO alice_topics (user_id, chat_id, thread_id, created_at) VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET chat_id = excluded.chat_id, thread_id = excluded.thread_id,
                                                           created_at = excluded.created_at""",
                     (user["id"], chat_id, topic.message_thread_id, now_iso()))
    return topic.message_thread_id


async def deliver(conn: sqlite3.Connection, bot, task_id: int, notifier=None) -> dict:
    """Three photos with the caption into the person's topic. Success → «На модерации», the files on the server
    are removed; failure → «Не отправлено» with the reason, retried later — never a silent success."""
    task = get(conn, task_id)
    if task is None or task["status"] not in ("sending", "send_failed"):
        return task
    # §19: one sender at a time — «Завершить» and the retry job never send the same album twice
    with transaction(conn):
        claimed = conn.execute(
            """UPDATE alice_tasks SET send_claimed_at = ? WHERE id = ? AND status IN ('sending','send_failed')
               AND (send_claimed_at IS NULL OR send_claimed_at < ?)""",
            (now_iso(), task_id, (now_local() - timedelta(minutes=5)).isoformat(timespec="seconds"))).rowcount
    if not claimed:
        return get(conn, task_id)
    try:
        return await _deliver(conn, bot, task, notifier)
    finally:
        with transaction(conn):
            conn.execute("UPDATE alice_tasks SET send_claimed_at = NULL WHERE id = ?", (task_id,))


async def _deliver(conn: sqlite3.Connection, bot, task: dict, notifier=None) -> dict:
    from aiogram.exceptions import TelegramAPIError
    from aiogram.types import BufferedInputFile, InputMediaPhoto

    task_id = task["id"]
    group = forum(conn)
    error = None
    if bot is None:
        error = "Бот не запущен"
    elif group is None:
        error = "Группа для фотоотчётов не подключена"
    if error is None:
        user = svc.get_user(conn, task["user_id"])
        have = photos(conn, task_id)
        files, missing = {}, None
        for s in STAGES:
            try:
                files[s] = Path(have[s]["temp_path"]).read_bytes()
            except (OSError, TypeError, KeyError):
                missing = s
                break
        if missing is not None:
            return await _photo_lost(conn, task, missing, notifier)
        if files:
            album = [InputMediaPhoto(media=BufferedInputFile(files[s], filename=f"{task_id}_{s}.jpg"),
                                     caption=caption(conn, {**task, "status": "moderation"}, s), parse_mode="HTML")
                     for s in STAGES]
            sent = None
            for attempt in range(2):
                try:
                    thread = await _topic(conn, bot, user, group["chat_id"], fresh=attempt == 1)
                    sent = await bot.send_media_group(group["chat_id"], media=album, message_thread_id=thread)
                    if attempt == 1 and notifier is not None:  # В44: the deleted topic is made again — admins know
                        await notifier.to_admins(f"📸 Тема сотрудника {escape(svc.full_name(user))} в группе фотоотчётов "
                                                 "«Алисы» была удалена — бот создал её заново.")
                    break
                except TelegramAPIError as exc:
                    error = str(exc)[:300]
                    if "thread" not in error.lower():  # a deleted topic is recreated once; other errors wait for a retry
                        break
            if sent:
                error = None
                with transaction(conn):
                    for s, msg in zip(STAGES, sent):
                        conn.execute("UPDATE alice_photos SET file_id = ?, message_id = ?, temp_path = NULL WHERE id = ?",
                                     (msg.photo[-1].file_id, msg.message_id, have[s]["id"]))
                    conn.execute("""UPDATE alice_tasks SET status = 'moderation', chat_id = ?, thread_id = ?, message_ids = ?,
                                    send_error = NULL, send_attempts = send_attempts + 1 WHERE id = ?""",
                                 (group["chat_id"], thread, json.dumps([m.message_id for m in sent]), task_id))
                for s in STAGES:
                    _drop(have[s])
    if error is not None:
        with transaction(conn):
            conn.execute("UPDATE alice_tasks SET status = 'send_failed', send_error = ?, send_attempts = send_attempts + 1 "
                         "WHERE id = ?", (error, task_id))
    return get(conn, task_id)


async def _photo_lost(conn: sqlite3.Connection, task: dict, stage: int, notifier=None) -> dict:
    """§19: a photo vanished before sending — the execution goes back to the person at that stage (if they have no
    other one going), with the reason; nothing is sent half-done."""
    reason = f"Фото этапа {stage} «{STAGES[stage][0]}» пропало — загрузите его заново"
    with transaction(conn):
        busy = conn.execute("SELECT 1 FROM alice_tasks WHERE user_id = ? AND status = 'in_progress' AND id != ?",
                            (task["user_id"], task["id"])).fetchone()
        if busy:
            conn.execute("UPDATE alice_tasks SET status = 'send_failed', send_error = ?, send_attempts = send_attempts + 1 "
                         "WHERE id = ?", (reason, task["id"]))
        else:
            unfix = ", ".join(f"stage{s}_at = NULL" for s in STAGES if s >= stage)
            conn.execute(f"""UPDATE alice_tasks SET status = 'in_progress', step = ?, completed_at = NULL, send_error = ?,
                             {unfix} WHERE id = ?""", (stage, reason, task["id"]))
        log_event(conn, user_id=task["user_id"], actor_id=0, event_type="ALICE_PHOTO_LOST", key=svc.new_key(),
                  new={"task": task["id"], "stage": stage})
    if notifier is not None:
        user = svc.get_user(conn, task["user_id"])
        await notifier.send(user["telegram_id"], f"⚠️ {title(task['id'])}: {reason}. Откройте «Работа» → «Алиса».")
    return get(conn, task["id"])


def to_retry(conn: sqlite3.Connection, limit: int = 10) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT id FROM alice_tasks WHERE status IN ('sending','send_failed') AND send_attempts < 20 ORDER BY id LIMIT ?",
        (limit,))]


async def post_status(conn: sqlite3.Connection, bot, task_id: int) -> None:
    """After moderation: the album's caption shows the new status and a reply marks it in the topic."""
    from aiogram.exceptions import TelegramAPIError

    task = get(conn, task_id)
    if bot is None or task is None or not task["message_ids"]:
        return
    first = json.loads(task["message_ids"])[0]
    try:
        await bot.edit_message_caption(chat_id=task["chat_id"], message_id=first, caption=caption(conn, task), parse_mode="HTML")
    except TelegramAPIError:
        pass
    mark = {"accepted": "🟢 Принято, ожидает оплаты", "rejected": "🔴 Отклонено администратором",
            "paid": "💰 Оплачено"}.get(task["status"], STATUS[task["status"]])
    reason = f"\nПричина: {escape(task['reject_reason'])}" if task["status"] == "rejected" and task["reject_reason"] else ""
    try:
        await bot.send_message(task["chat_id"], f"{title(task_id)} — {mark}{reason}",
                               message_thread_id=task["thread_id"], reply_to_message_id=first, parse_mode="HTML")
    except TelegramAPIError:
        pass


# ---------------------------------------------------------------- moderation (§38)


def moderate(conn: sqlite3.Connection, actor: dict, task_id: int, approve: bool, reason: str | None,
             key: str) -> tuple[dict, bool]:
    _check_key(key)
    reason = " ".join((reason or "").split())[:MAX_REASON] or None
    with transaction(conn):
        task = get(conn, task_id)
        if task is None:
            raise ServiceError("Выполнение не найдено", 404, "not_found")
        if _seen(conn, key):
            return task, False
        actor = _staff(conn, actor)
        if task["status"] != "moderation":
            if task["status"] in ("accepted", "rejected", "paid"):
                raise ServiceError(f"Уже решено: {STATUS[task['status']]}", 409, "already_decided")
            raise ServiceError("Выполнение ещё не отправлено на модерацию", 409, "not_ready")
        conn.execute("""UPDATE alice_tasks SET status = ?, moderated_by = ?, moderated_at = ?, reject_reason = ?
                        WHERE id = ? AND status = 'moderation'""",
                     ("accepted" if approve else "rejected", actor["telegram_id"], now_iso(),
                      None if approve else reason, task_id))
        log_event(conn, user_id=task["user_id"], actor_id=actor["telegram_id"], key=key,
                  event_type="ALICE_ACCEPTED" if approve else "ALICE_REJECTED", new={"task": task_id, "reason": reason})
    return get(conn, task_id), True


def queue(conn: sqlite3.Connection, status: str | None = "moderation", user_id: int | None = None, qr_id: int | None = None,
          date_from: date | None = None, date_to: date | None = None, limit: int = 200) -> list[dict]:
    where, args = ["status != 'abandoned'", "status != 'in_progress'"], []
    if status:
        where.append("status = ?")
        args.append(status)
    if user_id:
        where.append("user_id = ?")
        args.append(user_id)
    if qr_id:
        where.append("qr_id = ?")
        args.append(qr_id)
    if date_from:
        where.append("substr(completed_at, 1, 10) >= ?")
        args.append(date_from.isoformat())
    if date_to:
        where.append("substr(completed_at, 1, 10) <= ?")
        args.append(date_to.isoformat())
    rows = conn.execute(f"SELECT * FROM alice_tasks WHERE {' AND '.join(where)} ORDER BY completed_at, id LIMIT ?",
                        (*args, limit))
    return [view(conn, dict(r), staff=True) for r in rows]


# ---------------------------------------------------------------- statistics (§37, §40)


def _counts(rows) -> dict:
    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + r["n"]
    return {"completed": sum(by.get(s, 0) for s in SUBMITTED), "moderation": by.get("moderation", 0)
            + by.get("sending", 0) + by.get("send_failed", 0), "rejected": by.get("rejected", 0),
            "accepted": by.get("accepted", 0), "paid": by.get("paid", 0)}


def my_stats(conn: sqlite3.Connection, user_id: int, today: date, date_from: date | None = None,
             date_to: date | None = None) -> dict:
    """Counted from the executions themselves, by status (not from counters)."""
    def between(a: date | None, b: date | None):
        q = "SELECT status, COUNT(*) AS n FROM alice_tasks WHERE user_id = ? AND status IN ({})".format(
            ",".join("?" * len(SUBMITTED)))
        args = [user_id, *SUBMITTED]
        if a:
            q += " AND substr(completed_at, 1, 10) >= ?"
            args.append(a.isoformat())
        if b:
            q += " AND substr(completed_at, 1, 10) <= ?"
            args.append(b.isoformat())
        return _counts(conn.execute(q + " GROUP BY status", args))

    monday = today - timedelta(days=today.weekday())
    return {"today": between(today, today), "week": between(monday, today),
            "period": between(date_from, date_to) if date_from else None, "all": between(None, None)}


def history(conn: sqlite3.Connection, user_id: int, limit: int = 50) -> list[dict]:
    return [view(conn, dict(r)) for r in conn.execute(
        "SELECT * FROM alice_tasks WHERE user_id = ? AND status != 'abandoned' AND status != 'in_progress' "
        "ORDER BY id DESC LIMIT ?", (user_id, limit))]


def admin_stats(conn: sqlite3.Connection, date_from: date, date_to: date) -> dict:
    args = (date_from.isoformat(), date_to.isoformat(), *SUBMITTED)
    base = f"""FROM alice_tasks WHERE substr(completed_at, 1, 10) BETWEEN ? AND ?
               AND status IN ({','.join('?' * len(SUBMITTED))})"""
    people = {}
    for r in conn.execute(f"SELECT user_id, status, COUNT(*) AS n {base} GROUP BY user_id, status", args):
        people.setdefault(r["user_id"], []).append(r)
    qrs = {}
    for r in conn.execute(f"SELECT qr_id, status, COUNT(*) AS n {base} GROUP BY qr_id, status", args):
        qrs.setdefault(r["qr_id"], []).append(r)
    days = {}
    for r in conn.execute(f"SELECT substr(completed_at, 1, 10) AS d, status, COUNT(*) AS n {base} GROUP BY d, status", args):
        days.setdefault(r["d"], []).append(r)
    all_rows = [r for rows in people.values() for r in rows]
    return {
        "total": _counts(all_rows),
        "people": sorted(({"user_id": uid, "name": svc.full_name(svc.get_user(conn, uid)), **_counts(rows)}
                          for uid, rows in people.items()), key=lambda r: -r["completed"]),
        "qrs": sorted(({"qr_id": qid, "number": (get_qr(conn, qid) or {}).get("number"), **_counts(rows)}
                       for qid, rows in qrs.items()), key=lambda r: -r["completed"]),
        "days": [{"date": d, **_counts(rows)} for d, rows in sorted(days.items())],
    }


# ---------------------------------------------------------------- the payout by Yandex's report (§41–44)


def _unpaid(conn, qr_id: int, date_to: date) -> list[dict]:
    """Accepted, not paid, finished up to the period's end — the oldest first (the agreed order)."""
    return [dict(r) for r in conn.execute(
        """SELECT * FROM alice_tasks WHERE qr_id = ? AND status = 'accepted' AND substr(completed_at, 1, 10) <= ?
           ORDER BY completed_at, id""", (qr_id, date_to.isoformat()))]


def reconcile(conn: sqlite3.Connection, date_from: date, date_to: date, confirmed: dict[int, int] | None = None,
              rate: int | None = None) -> dict:
    """The grid by QR codes: accepted in the period, already paid, waiting; what Yandex confirmed (entered) is spread
    over the oldest waiting executions — and so over people. Nothing is saved."""
    if date_to < date_from:
        raise ServiceError("Период задан неверно", 422, "bad_range")
    rate = app_settings.get_all(conn)["alice_rate"] if rate is None else rate
    confirmed = confirmed or {}
    rows, people = [], {}
    qr_ids = [r[0] for r in conn.execute(
        """SELECT DISTINCT qr_id FROM alice_tasks WHERE status IN ('accepted','paid')
           AND substr(completed_at, 1, 10) <= ?""", (date_to.isoformat(),))]
    for qr_id in qr_ids:
        in_period = conn.execute(
            """SELECT status, COUNT(*) FROM alice_tasks WHERE qr_id = ? AND status IN ('accepted','paid')
               AND substr(completed_at, 1, 10) BETWEEN ? AND ? GROUP BY status""",
            (qr_id, date_from.isoformat(), date_to.isoformat())).fetchall()
        counts = {s: n for s, n in in_period}
        waiting = _unpaid(conn, qr_id, date_to)
        if not waiting and not counts:
            continue
        want = confirmed.get(qr_id, 0)
        if isinstance(want, bool) or not isinstance(want, int) or want < 0:
            raise ServiceError("Количество — целое число от 0", 422, "bad_count")
        if want > len(waiting):
            raise ServiceError(f"QR {(get_qr(conn, qr_id) or {}).get('number') or qr_id}: подтверждено {want}, а ждут оплаты "
                               f"только {len(waiting)}", 422, "too_many")
        paid_now = waiting[:want]
        for t in paid_now:
            p = people.setdefault(t["user_id"], {"user_id": t["user_id"], "count": 0, "task_ids": []})
            p["count"] += 1
            p["task_ids"].append(t["id"])
        qr = get_qr(conn, qr_id) or {}
        rows.append({"qr_id": qr_id, "number": qr.get("number"), "accepted": counts.get("accepted", 0) + counts.get("paid", 0),
                     "paid": counts.get("paid", 0), "waiting": len(waiting), "confirmed": want,
                     "amount": want * rate, "left": len(waiting) - want,
                     "people": sorted({t["user_id"] for t in waiting})})
    names = {uid: svc.full_name(svc.get_user(conn, uid)) for uid in {u for r in rows for u in r["people"]} | set(people)}
    for r in rows:
        r["people"] = [{"user_id": u, "name": names[u]} for u in r["people"]]
    pay = [{**p, "name": names[p["user_id"]], "amount": p["count"] * rate} for p in people.values()]
    return {"rate": rate, "rows": sorted(rows, key=lambda r: (r["number"] or "", r["qr_id"])),
            "pay": sorted(pay, key=lambda p: p["name"]), "total": sum(p["amount"] for p in pay)}


def commit_payout(conn: sqlite3.Connection, actor: dict, title: str, date_from: date, date_to: date,
                  confirmed: dict[int, int], proof: list[str], key: str) -> int:
    """Paid: the chosen executions become «Оплачено», tied to the payout; the rest keep waiting. Returns the batch id."""
    from . import payouts

    _check_key(key)
    title = " ".join((title or "").split())[:100]
    if not title:
        raise ServiceError("Назовите выплату", 422, "title_required")
    with transaction(conn):
        if _seen(conn, key):
            return conn.execute("SELECT MAX(id) FROM payout_batches WHERE product = ?", (PRODUCT,)).fetchone()[0]
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money"):
            raise ServiceError("Выплаты фиксирует только владелец", 403, "owner_only")
        plan = reconcile(conn, date_from, date_to, confirmed)
        if not plan["pay"]:
            raise ServiceError("Укажите, сколько подтвердил Яндекс", 422, "nothing")
        cur = conn.execute(
            """INSERT INTO payout_batches (title, period_from, period_to, network_id, rates, proof_media, deduction_media,
                                           total, created_by, created_at, product) VALUES (?, ?, ?, NULL, ?, ?, '[]', ?, ?, ?, ?)""",
            (title, date_from.isoformat(), date_to.isoformat(), json.dumps({"alice_rate": plan["rate"]}),
             json.dumps(proof[:10]), plan["total"], actor["telegram_id"], now_iso(), PRODUCT))
        batch_id = cur.lastrowid
        for p in plan["pay"]:
            user = svc.get_user(conn, p["user_id"])
            profile = payouts.get_profile(conn, user["id"])
            conn.execute(
                """INSERT INTO payout_items (batch_id, user_id, role, network, phone, bank, recipient, personal_volume,
                       team_volume, base_calc, base, bonuses, fines, deductions, total, carried_debt, pieces_pay, commission,
                       lazy_units, lazy_before, rate_base, rate_lazy)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 0, ?, ?, 0, 0, 0, ?, 0, ?, 0, 0, 0, ?, NULL)""",
                (batch_id, user["id"], perms.role_of(user), payouts.pretty_phone(profile["phone"]) if profile else None,
                 profile["bank"] if profile else None, profile["recipient"] if profile else None, p["count"],
                 p["amount"], p["amount"], p["amount"], p["amount"], plan["rate"]))
            marked = conn.execute(
                f"""UPDATE alice_tasks SET status = 'paid', payout_id = ?, paid_at = ?
                    WHERE id IN ({','.join('?' * len(p['task_ids']))}) AND status = 'accepted'""",
                (batch_id, now_iso(), *p["task_ids"])).rowcount
            if marked != len(p["task_ids"]):  # someone else paid them a moment ago: nothing is saved
                raise ServiceError("Выполнения изменились — обновите расчёт", 409, "changed")
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="ALICE_PAYOUT", key=key,
                  new={"batch": batch_id, "total": plan["total"], "confirmed": {str(k): v for k, v in confirmed.items()}})
    return batch_id
