"""Money ledger: fines, bonuses, deductions (ТЗ модуль 4).

financial_transactions is the source of truth for every ruble. Shifts keep penalty_amount /
shortfall_penalty only as a display cache for their cards; this module keeps them in sync.

Statuses:
    draft                  — a fine created by a final cancellation, waiting for its amount
    pending_admin_approval — a trainer's fine, waits for an owner or an admin; the balance doesn't change
    active                 — counts in the balance
    paid / offset          — settled (by «погасить» or by a payout); history only
    cancelled              — annulled with a reason, or forgiven («Простить», the reason is optional)
    rejected               — a trainer's fine an owner or admin declined (the employee never sees it)

Balance = active bonuses − active fines − active deductions.

Fines are counted in fine units (ТЗ 27.10 §19): units × the person's own rate at that moment (personal, else the
common one), kept in `pieces` and `rate`; `amount` holds the rubles for the payouts. The employee sees units only;
staff see both. Fines from before the change have no units and keep their rubles (nothing is guessed).
"""

import sqlite3
from dataclasses import dataclass
from datetime import date

from . import media
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import MAX_PENALTY, ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, today_local

TYPES = ("fine", "bonus", "deduction")
LIVE = ("draft", "pending_admin_approval", "active", "paid", "offset")
HAPPENED = ("active", "paid", "offset")  # real money events: stats, Excel
TYPE_TITLES = {"fine": "Штраф", "bonus": "Бонус", "deduction": "Вычет", "payout_offset": "Зачёт в выплате"}
STATUS_TITLES = {
    "draft": "ждёт суммы",
    "pending_admin_approval": "на согласовании",
    "active": "активен",
    "paid": "погашен",
    "offset": "учтён в выплате",
    "cancelled": "аннулирован",
    "rejected": "отклонён",
}
MAX_REASON = 500
MAX_UNITS = 1000
UNIT_PRESETS = (1, 3, 5)


@dataclass
class TxResult:
    tx: dict
    changed: bool
    event: str | None = None


# ---------------------------------------------------------------- reads


def get(conn: sqlite3.Connection, tx_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM financial_transactions WHERE id = ?", (tx_id,)).fetchone()
    return dict(row) if row else None


def balance(conn: sqlite3.Connection, user_id: int) -> dict:
    rows = conn.execute(
        """SELECT type, status, COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n FROM financial_transactions
           WHERE user_id = ? GROUP BY type, status""", (user_id,)).fetchall()
    get_ = lambda t, st: sum(r["total"] for r in rows if r["type"] == t and r["status"] == st)  # noqa: E731
    count = lambda t, st: sum(r["n"] for r in rows if r["type"] == t and r["status"] == st)  # noqa: E731
    fines, bonuses, deductions = get_("fine", "active"), get_("bonus", "active"), get_("deduction", "active")
    return {
        "fines": fines,
        "bonuses": bonuses,
        "deductions": deductions,
        "balance": bonuses - fines - deductions,
        "pending": get_("fine", "pending_admin_approval"),
        "drafts": count("fine", "draft"),
    }


def employee_summary(conn: sqlite3.Connection, user_id: int) -> dict:
    """What the employee sees instead of a rubles balance (ТЗ 27.10 §19): fine units, bonuses and deductions."""
    rows = [dict(r) for r in conn.execute(
        "SELECT type, amount, pieces FROM financial_transactions WHERE user_id = ? AND status = 'active'", (user_id,))]
    fines = [r for r in rows if r["type"] == "fine"]
    return {
        "fine_units": sum(r["pieces"] or 0 for r in fines),
        "old_fines": sum(1 for r in fines if not r["pieces"]),  # from before units: their rubles stay with the staff
        "bonuses": sum(r["amount"] or 0 for r in rows if r["type"] == "bonus"),
        "deductions": sum(r["amount"] or 0 for r in rows if r["type"] == "deduction"),
    }


def _names(conn) -> dict[int, str]:
    return {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT telegram_id, first_name, last_name FROM users")}


def public(tx: dict, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    return {
        "id": tx["id"],
        "user_id": tx["user_id"],
        "type": tx["type"],
        "amount": tx["amount"],
        "status": tx["status"],
        "reason": tx["reason"],
        "source": tx["source"],
        "shift_id": tx["shift_id"],
        "event_date": tx["event_date"],
        "created_at": tx["created_at"],
        "created_by": names.get(tx["created_by"]),
        "decided_at": tx["decided_at"],
        "decided_by": names.get(tx["decided_by"]) if tx["decided_by"] else None,
        "decision_comment": tx["decision_comment"],
        "has_media": bool(tx["media_file_id"]),
        "media_url": media.photo_url(tx["id"], kind="tx") if tx["media_file_id"] else None,
        # a deduction entered in pieces: shown in rubles with «N шт. × R ₽» (customer round B)
        "pieces": tx.get("pieces"),
        "rate": tx.get("rate"),
        "linked_tx_id": tx.get("linked_tx_id"),
        # ТЗ 28.10 §24: a fine for «Алиса» (and its task), and which payout took it
        "product": tx.get("product"),
        "alice_task_id": tx.get("alice_task_id"),
        "payout_id": tx.get("payout_id"),
    }


def journal(conn: sqlite3.Connection, *, user_id: int | None = None, only: set[int] | None = None,
            types: tuple[str, ...] | None = None, statuses: tuple[str, ...] | None = None,
            date_from: date | None = None, date_to: date | None = None, limit: int = 500) -> list[dict]:
    sql = """SELECT t.*, u.first_name, u.last_name, u.username, u.telegram_id AS user_tg
             FROM financial_transactions t JOIN users u ON u.id = t.user_id WHERE 1 = 1"""
    params: list = []
    if user_id is not None:
        sql += " AND t.user_id = ?"
        params.append(user_id)
    if types:
        sql += f" AND t.type IN ({','.join('?' * len(types))})"
        params += list(types)
    if statuses:
        sql += f" AND t.status IN ({','.join('?' * len(statuses))})"
        params += list(statuses)
    if date_from:
        sql += " AND t.event_date >= ?"
        params.append(date_from.isoformat())
    if date_to:
        sql += " AND t.event_date <= ?"
        params.append(date_to.isoformat())
    sql += " ORDER BY t.event_date DESC, t.id DESC LIMIT ?"
    params.append(limit)
    names = _names(conn)
    out = []
    for r in conn.execute(sql, params):
        r = dict(r)
        if only is not None and r["user_id"] not in only:
            continue
        out.append({**public(r, names), "name": svc.full_name(r), "username": r["username"], "telegram_id": r["user_tg"]})
    return out


def summary(conn: sqlite3.Connection, only: set[int] | None = None) -> list[dict]:
    """/finances: people with a non-zero balance or something waiting (debtors first)."""
    ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT user_id FROM financial_transactions WHERE status IN ('active','pending_admin_approval','draft')")]
    out = []
    for uid in ids:
        if only is not None and uid not in only:
            continue
        user = svc.get_user(conn, uid)
        b = balance(conn, uid)
        if not (b["balance"] or b["pending"] or b["drafts"]):
            continue
        out.append({"user_id": uid, "name": svc.full_name(user), "username": user["username"],
                    "telegram_id": user["telegram_id"], **b})
    return sorted(out, key=lambda x: (x["balance"], x["name"]))


def fines_total(conn: sqlite3.Connection, user_id: int, date_from: date, date_to: date) -> int:
    row = conn.execute(
        f"""SELECT COALESCE(SUM(amount), 0) FROM financial_transactions WHERE user_id = ? AND type = 'fine'
            AND status IN {HAPPENED} AND event_date BETWEEN ? AND ?""",
        (user_id, date_from.isoformat(), date_to.isoformat())).fetchone()
    return row[0]


# ---------------------------------------------------------------- internal (callers hold the transaction)


def record(conn: sqlite3.Connection, *, user_id: int, type: str, amount: int | None, reason: str, actor_tg: int,
           status: str = "active", source: str = "manual", shift_id: int | None = None,
           event_date: str | None = None, media: str | None = None, pieces: int | None = None,
           rate: int | None = None, linked_tx_id: int | None = None) -> dict:
    cur = conn.execute(
        """INSERT INTO financial_transactions (user_id, type, amount, status, reason, media_file_id, source, shift_id,
                                               event_date, created_by, created_at, decided_by, decided_at, pieces, rate,
                                               linked_tx_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, type, amount, status, reason[:MAX_REASON], media, source, shift_id,
         event_date or today_local().isoformat(), actor_tg, now_iso(),
         actor_tg if status == "active" else None, now_iso() if status == "active" else None, pieces, rate, linked_tx_id),
    )
    return get(conn, cur.lastrowid)


def linked(conn: sqlite3.Connection, shift_id: int, sources: tuple[str, ...]) -> list[dict]:
    return [dict(r) for r in conn.execute(
        f"""SELECT * FROM financial_transactions WHERE shift_id = ? AND source IN ({','.join('?' * len(sources))})
            AND status NOT IN ('cancelled','rejected')""", (shift_id, *sources))]


def cancel_linked(conn: sqlite3.Connection, shift_id: int, sources: tuple[str, ...], actor_tg: int, reason: str) -> list[dict]:
    """Annul live fines tied to a shift (fine cleared by a decision). Settled ones (paid/offset) stay as history."""
    done = []
    for tx in linked(conn, shift_id, sources):
        if tx["status"] in ("paid", "offset"):
            continue
        conn.execute("UPDATE financial_transactions SET status = 'cancelled', decided_by = ?, decided_at = ?, "
                     "decision_comment = ? WHERE id = ?", (actor_tg, now_iso(), reason, tx["id"]))
        done.append(get(conn, tx["id"]))
    return done


def fine_status(actor: dict, target: dict) -> str:
    """Owners and admins fine at once; a trainer's fine waits for an owner (ТЗ 5.3)."""
    if perms.can(actor, "fine_direct", target):
        return "active"
    if perms.can(actor, "fine_request", target):
        return "pending_admin_approval"
    raise ServiceError("Недостаточно прав на штраф", 403, "forbidden")


def units_of(units) -> int:
    if isinstance(units, bool) or not isinstance(units, int) or not 0 < units <= MAX_UNITS:
        raise ServiceError(f"Штраф — целое число единиц от 1 до {MAX_UNITS}", 422, "bad_units")
    return units


def unit_rate(conn: sqlite3.Connection, user_id: int) -> int:
    """One fine unit in rubles: the person's own rate now (personal, else the common one)."""
    from .rates import for_user

    return for_user(conn, user_id)["rate_base"]


def record_fine(conn: sqlite3.Connection, *, user_id: int, units: int, reason: str, actor_tg: int,
                status: str = "active", source: str = "manual", shift_id: int | None = None,
                event_date: str | None = None, media: str | None = None) -> dict:
    """A fine in units; the rate is fixed now, so a later rate change doesn't move it."""
    rate = unit_rate(conn, user_id)
    return record(conn, user_id=user_id, type="fine", amount=units * rate, reason=reason, actor_tg=actor_tg,
                  status=status, source=source, shift_id=shift_id, event_date=event_date, media=media,
                  pieces=units, rate=rate)


def units_text(tx: dict | None) -> str:
    """«3 ед.» for the employee; an old fine without units — no number (its rubles are the staff's)."""
    if not tx or not tx.get("pieces") or tx["type"] != "fine":
        return ""
    n = tx["pieces"]
    word = "единица" if n % 10 == 1 and n % 100 != 11 else "единицы" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "единиц"
    return f"{n} штрафн{'ая' if word == 'единица' else 'ые' if word == 'единицы' else 'ых'} {word}"


def staff_text(tx: dict) -> str:
    """«360 ₽ (3 ед. × 120 ₽)» for the staff and the archive."""
    amount = f"{tx['amount']} ₽" if tx.get("amount") is not None else "—"
    if tx["type"] == "fine" and tx.get("pieces"):
        return f"{amount} ({tx['pieces']} ед. × {tx['rate']} ₽)"
    return amount


def for_employee(tx: dict) -> dict:
    """The employee's own journal: fines without rubles (ТЗ 27.10 §19), bonuses and deductions as they are."""
    return {**tx, "amount": None, "rate": None} if tx["type"] == "fine" else tx


def link_fine(conn: sqlite3.Connection, shift: dict, source: str, units: int, reason: str, actor_tg: int,
              status: str = "active") -> dict:
    """The one live fine of this kind for a shift: replaces an earlier one (e.g. a decision changed)."""
    cancel_linked(conn, shift["id"], (source,), actor_tg, "Заменён новым решением")
    return record_fine(conn, user_id=shift["user_id"], units=units, reason=reason, actor_tg=actor_tg,
                       status=status, source=source, shift_id=shift["id"], event_date=shift["shift_date"])


def final_cancel_draft(conn: sqlite3.Connection, shift: dict, actor_tg: int) -> dict:
    """ТЗ: a final cancellation creates a fine draft that waits for its amount."""
    existing = linked(conn, shift["id"], ("final_cancel",))
    if existing:
        return existing[0]
    return record(conn, user_id=shift["user_id"], type="fine", amount=None, status="draft", source="final_cancel",
                  reason=f"Окончательная отмена смены {shift['shift_date'][8:10]}.{shift['shift_date'][5:7]}",
                  actor_tg=actor_tg, shift_id=shift["id"], event_date=shift["shift_date"])


def set_final_cancel_amount(conn: sqlite3.Connection, shift: dict, amount: int, actor_tg: int) -> dict:
    tx = final_cancel_draft(conn, shift, actor_tg)
    if amount == 0:
        conn.execute("UPDATE financial_transactions SET status = 'cancelled', decided_by = ?, decided_at = ?, "
                     "decision_comment = 'Без штрафа' WHERE id = ?", (actor_tg, now_iso(), tx["id"]))
    else:
        conn.execute("UPDATE financial_transactions SET amount = ?, status = 'active', decided_by = ?, decided_at = ? "
                     "WHERE id = ?", (amount, actor_tg, now_iso(), tx["id"]))
    return get(conn, tx["id"])


def _sync_shift(conn, tx: dict, note: str) -> None:
    """A fine tied to a shift was annulled or declined: update the shift's display cache."""
    if not tx["shift_id"]:
        return
    if tx["source"] == "final_cancel":
        conn.execute("UPDATE shifts SET penalty_amount = NULL, updated_at = ? WHERE id = ?", (now_iso(), tx["shift_id"]))
    elif tx["source"] in ("report", "no_show"):
        conn.execute(
            """UPDATE shifts SET shortfall_status = 'waived', shortfall_penalty = NULL, report_note = ?, updated_at = ?
               WHERE id = ? AND shortfall_status = 'fined'""", (note, now_iso(), tx["shift_id"]))


# ---------------------------------------------------------------- staff actions


def _reason(text: str | None) -> str:
    text = (text or "").strip()[:MAX_REASON]
    if not text:
        raise ServiceError("Укажите причину", 422, "reason_required")
    return text


def _amount(amount) -> int:
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= MAX_PENALTY:
        raise ServiceError("Сумма — целое число рублей больше нуля", 422, "bad_amount")
    return amount


def _target(conn, user_id: int) -> dict:
    user = svc.get_user(conn, user_id)
    if user is None or user["status"] != "approved":
        raise ServiceError("Сотрудник не найден", 404, "not_found")
    return user


def create(conn: sqlite3.Connection, actor: dict, user_id: int, type: str, amount: int | None, reason: str, key: str,
           event_date: date | None = None, media: str | None = None, units: int | None = None,
           product: str | None = None, alice_task_id: int | None = None) -> TxResult:
    """Manual fine in units (trainer: needs an approval), bonus (any staff for their people), deduction (owner).
    ТЗ 28.10 §24: a fine may be for the product «Алиса», optionally for one task — owners and admins only (В49);
    it is one debt with the other fines and is taken by whichever payout comes first (В48)."""
    if product is not None and product != "alice":
        raise ServiceError("Неизвестный продукт", 422, "bad_product")
    if alice_task_id is not None and product is None:
        product = "alice"
    if type not in TYPES:
        raise ServiceError("Неизвестный тип", 422, "bad_type")
    if type == "fine":
        units = units_of(units)
    else:
        amount = _amount(amount)
    reason = _reason(reason)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            row = conn.execute("SELECT * FROM financial_transactions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                               (user_id,)).fetchone()
            return TxResult(dict(row), False)
        actor = _fresh_actor(conn, actor)
        target = _target(conn, user_id)
        if target["id"] == actor["id"] and not perms.is_owner(actor):
            raise ServiceError("Себе начислять нельзя", 403, "self")
        if type == "fine":
            status = fine_status(actor, target)
        elif type == "bonus":
            if not perms.can(actor, "bonus", target):
                raise ServiceError("Недостаточно прав на бонус", 403, "forbidden")
            status = "active"
        else:
            if not perms.can(actor, "money"):
                raise ServiceError("Вычеты добавляет только владелец", 403, "owner_only")
            status = "active"
        if event_date and event_date > today_local():
            raise ServiceError("Дата не может быть в будущем", 422, "bad_date")
        if product:
            if type != "fine":
                raise ServiceError("За продукт назначается штраф", 422, "bad_type")
            if not perms.can(actor, "alice"):
                raise ServiceError("Штраф за «Алису» назначают владелец и администраторы", 403, "forbidden")
            if alice_task_id is not None:
                task = conn.execute("SELECT user_id FROM alice_tasks WHERE id = ?", (alice_task_id,)).fetchone()
                if task is None or task[0] != user_id:
                    raise ServiceError(f"Задание «Алиса #{alice_task_id}» не найдено у этого сотрудника", 422, "bad_task")
        day = (event_date or today_local()).isoformat()
        if type == "fine":
            tx = record_fine(conn, user_id=user_id, units=units, reason=reason, actor_tg=actor["telegram_id"],
                             status=status, event_date=day, media=media)
        else:
            tx = record(conn, user_id=user_id, type=type, amount=amount, reason=reason, actor_tg=actor["telegram_id"],
                        status=status, event_date=day, media=media)
        if product:
            conn.execute("UPDATE financial_transactions SET product = ?, alice_task_id = ? WHERE id = ?",
                         (product, alice_task_id, tx["id"]))
            tx = {**tx, "product": product, "alice_task_id": alice_task_id}
        event = f"{type.upper()}_{'REQUESTED' if status == 'pending_admin_approval' else 'CREATED'}"
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type=event, key=key,
                  new={"tx": tx["id"], "amount": tx["amount"], "units": tx["pieces"], "status": status}, description=reason)
    return TxResult(tx, True, event)


def piece_deduction(conn: sqlite3.Connection, actor: dict, user_id: int, day: date, pieces: int, reason: str, key: str,
                    media: str | None = None) -> list[dict]:
    """Owner: a deduction in pieces for a day (customer round B). The employee loses pieces × their rate of
    that day; the trainer of their network loses pieces × the ladder rate paid that day (not recalculated).
    Both see rubles. Returns [employee's deduction, trainer's deduction?]."""
    from .rates import Book

    if isinstance(pieces, bool) or not isinstance(pieces, int) or not 0 < pieces <= 100_000:
        raise ServiceError("Количество — целое число штук больше нуля", 422, "bad_pieces")
    reason = _reason(reason)
    if day > today_local():
        raise ServiceError("Дата не может быть в будущем", 422, "bad_date")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return [dict(r) for r in conn.execute(
                "SELECT * FROM financial_transactions WHERE user_id = ? AND pieces IS NOT NULL ORDER BY id DESC LIMIT 1",
                (user_id,))]
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money"):
            raise ServiceError("Вычеты добавляет только владелец", 403, "owner_only")
        target = _target(conn, user_id)
        book = Book.load(conn)
        fact = conn.execute(
            f"""SELECT fact_volume FROM shifts WHERE user_id = ? AND shift_date = ? AND status IN {svc.ACTIVE}
                AND fact_volume IS NOT NULL""", (user_id, day.isoformat())).fetchone()
        # the rate of that very day; with no result that day — the full (not lazy) rate
        rate = book.day_rate(user_id, fact[0]) if fact else book.user_rates(user_id)["rate_base"]
        dd = f"{day.day:02d}.{day.month:02d}"
        own = record(conn, user_id=user_id, type="deduction", amount=pieces * rate,
                     reason=f"{reason} ({pieces} шт. × {rate} ₽ за {dd})", actor_tg=actor["telegram_id"],
                     event_date=day.isoformat(), media=media, pieces=pieces, rate=rate)
        out = [own]
        net = book.nets.get(target["network_id"])
        if net and net["trainer_id"] and net["trainer_id"] != user_id and not net["owner_led"]:
            t_rate = book.trainer_rate_on(conn, net["id"], day)
            if t_rate > 0:
                out.append(record(conn, user_id=net["trainer_id"], type="deduction", amount=pieces * t_rate,
                                  reason=f"Вычет у {svc.full_name(target)}: {reason} ({pieces} шт. × {t_rate} ₽ за {dd})",
                                  actor_tg=actor["telegram_id"], event_date=day.isoformat(), media=media,
                                  pieces=pieces, rate=t_rate, linked_tx_id=own["id"]))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type="DEDUCTION_PIECES", key=key,
                  new={"tx": [t["id"] for t in out], "pieces": pieces, "rate": rate, "amounts": [t["amount"] for t in out]},
                  description=reason)
    return out


def decide(conn: sqlite3.Connection, actor: dict, tx_id: int, approve: bool, comment: str | None, key: str) -> TxResult:
    """An owner or an admin approves or declines a trainer's fine (ТЗ 5.3)."""
    _check_key(key)
    comment = (comment or "").strip()[:MAX_REASON] or None
    with transaction(conn):
        tx = get(conn, tx_id)
        if tx is None:
            raise ServiceError("Запись не найдена", 404, "not_found")
        if _seen(conn, key):
            return TxResult(tx, False)
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "approve_fines"):
            raise ServiceError("Согласуют штрафы владелец и администраторы", 403, "forbidden")
        if tx["status"] != "pending_admin_approval":
            return TxResult(tx, False)
        status = "active" if approve else "rejected"
        conn.execute("UPDATE financial_transactions SET status = ?, decided_by = ?, decided_at = ?, decision_comment = ? "
                     "WHERE id = ?", (status, actor["telegram_id"], now_iso(), comment, tx_id))
        if not approve:
            _sync_shift(conn, tx, f"Штраф отклонён{': ' + comment if comment else ''}")
            if tx["source"] == "attendance":
                from . import violations

                violations.reopen_for_rejected_fine(conn, tx)
        event = "FINE_APPROVED" if approve else "FINE_REJECTED"
        log_event(conn, user_id=tx["user_id"], actor_id=actor["telegram_id"], event_type=event, key=key,
                  shift_id=tx["shift_id"], new={"tx": tx_id, "status": status}, description=comment)
    return TxResult(get(conn, tx_id), True, event)


def cancel(conn: sqlite3.Connection, actor: dict, tx_id: int, reason: str, key: str) -> TxResult:
    """«Аннулировать» a fine or bonus with a reason (owners and admins; deductions — owners)."""
    reason = _reason(reason)
    _check_key(key)
    with transaction(conn):
        tx = get(conn, tx_id)
        if tx is None:
            raise ServiceError("Запись не найдена", 404, "not_found")
        if _seen(conn, key):
            return TxResult(tx, False)
        actor = _fresh_actor(conn, actor)
        target = svc.get_user(conn, tx["user_id"])
        need = "money" if tx["type"] == "deduction" else "fine_direct"
        if not perms.can(actor, need, target if need != "money" else None):
            raise ServiceError("Недостаточно прав", 403, "forbidden")
        if tx["status"] not in ("draft", "active", "pending_admin_approval"):
            raise ServiceError(f"Запись уже {STATUS_TITLES[tx['status']]}", 409, "not_cancellable")
        conn.execute("UPDATE financial_transactions SET status = 'cancelled', decided_by = ?, decided_at = ?, "
                     "decision_comment = ? WHERE id = ?", (actor["telegram_id"], now_iso(), reason, tx_id))
        # a deduction in pieces takes the trainer's part with it
        conn.execute("UPDATE financial_transactions SET status = 'cancelled', decided_by = ?, decided_at = ?, "
                     "decision_comment = ? WHERE linked_tx_id = ? AND status = 'active'",
                     (actor["telegram_id"], now_iso(), reason, tx_id))
        _sync_shift(conn, tx, f"Штраф аннулирован: {reason}")
        log_event(conn, user_id=tx["user_id"], actor_id=actor["telegram_id"], event_type="TX_CANCELLED", key=key,
                  shift_id=tx["shift_id"], old={"status": tx["status"]}, new={"tx": tx_id, "status": "cancelled"},
                  description=reason)
    return TxResult(get(conn, tx_id), True, "TX_CANCELLED")


FORGIVEN = "Прощён"


def forgive(conn: sqlite3.Connection, actor: dict, tx_id: int, reason: str | None, key: str) -> TxResult:
    """«Простить» a fine: it stops counting. Owners and admins — anyone's, a trainer — their network's;
    the reason is optional. A trainer's own request that waits for approval can be withdrawn the same way."""
    reason = (reason or "").strip()[:MAX_REASON] or None
    _check_key(key)
    with transaction(conn):
        tx = get(conn, tx_id)
        if tx is None:
            raise ServiceError("Запись не найдена", 404, "not_found")
        if _seen(conn, key):
            return TxResult(tx, False)
        actor = _fresh_actor(conn, actor)
        if tx["type"] != "fine":
            raise ServiceError("Простить можно только штраф", 409, "not_a_fine")
        if not perms.can(actor, "forgive_fines", svc.get_user(conn, tx["user_id"])):
            raise ServiceError("Недостаточно прав", 403, "forbidden")
        if tx["status"] not in ("draft", "active", "pending_admin_approval"):
            raise ServiceError(f"Штраф уже {STATUS_TITLES[tx['status']]}", 409, "not_cancellable")
        comment = f"{FORGIVEN}: {reason}" if reason else FORGIVEN
        conn.execute("UPDATE financial_transactions SET status = 'cancelled', decided_by = ?, decided_at = ?, "
                     "decision_comment = ? WHERE id = ?", (actor["telegram_id"], now_iso(), comment, tx_id))
        _sync_shift(conn, tx, f"Штраф прощён{': ' + reason if reason else ''}")
        log_event(conn, user_id=tx["user_id"], actor_id=actor["telegram_id"], event_type="FINE_FORGIVEN", key=key,
                  shift_id=tx["shift_id"], old={"status": tx["status"]}, new={"tx": tx_id, "status": "cancelled"},
                  description=reason)
    return TxResult(get(conn, tx_id), True, "FINE_FORGIVEN")


def fill_draft(conn: sqlite3.Connection, actor: dict, tx_id: int, amount: int, key: str) -> TxResult:
    """Give a draft fine (final cancellation) its amount."""
    amount = _amount(amount)
    _check_key(key)
    with transaction(conn):
        tx = get(conn, tx_id)
        if tx is None:
            raise ServiceError("Запись не найдена", 404, "not_found")
        if _seen(conn, key):
            return TxResult(tx, False)
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "fine_direct", svc.get_user(conn, tx["user_id"])):
            raise ServiceError("Недостаточно прав", 403, "forbidden")
        if tx["status"] != "draft":
            return TxResult(tx, False)
        conn.execute("UPDATE financial_transactions SET amount = ?, status = 'active', decided_by = ?, decided_at = ? "
                     "WHERE id = ?", (amount, actor["telegram_id"], now_iso(), tx_id))
        if tx["shift_id"] and tx["source"] == "final_cancel":
            conn.execute("UPDATE shifts SET penalty_amount = ?, penalty_set_by = ?, penalty_set_at = ?, updated_at = ? "
                         "WHERE id = ?", (amount, actor["telegram_id"], now_iso(), now_iso(), tx["shift_id"]))
        log_event(conn, user_id=tx["user_id"], actor_id=actor["telegram_id"], event_type="PENALTY_ASSIGNED", key=key,
                  shift_id=tx["shift_id"], new={"tx": tx_id, "amount": amount})
    return TxResult(get(conn, tx_id), True, "PENALTY_ASSIGNED")


def pay_all(conn: sqlite3.Connection, actor: dict, user_id: int, key: str) -> list[dict]:
    """«Погасить»: every active fine of the person becomes paid. Partial repayment isn't allowed (ТЗ 5.5)."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return []
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money"):
            raise ServiceError("Погашать долги может только владелец", 403, "owner_only")
        _target(conn, user_id)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM financial_transactions WHERE user_id = ? AND type = 'fine' AND status = 'active'", (user_id,))]
        if not rows:
            raise ServiceError("Активных штрафов нет", 409, "nothing_to_pay")
        conn.execute("UPDATE financial_transactions SET status = 'paid', decided_by = ?, decided_at = ? "
                     "WHERE user_id = ? AND type = 'fine' AND status = 'active'", (actor["telegram_id"], now_iso(), user_id))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type="FINES_PAID", key=key,
                  new={"count": len(rows), "total": sum(r["amount"] for r in rows)})
    return rows
