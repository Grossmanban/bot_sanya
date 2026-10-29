"""SBP payment profile and the payout registry (ТЗ модули 9–10). Owners only, except one's own profile.

Calculation for a period (ТЗ 11.1, decisions for stage 6, flexible rates — see rates.py):
    pieces     = P, the APPROVED pieces of the period; L — the «налог на лень» pool (ТЗ 27.10 §20–21):
                 every approved day below the threshold adds its pieces, every payout takes what it paid lazily
    base       = min(P, L) × lazy rate + the rest × the person's rate (rates as of the payout);
                 the pool is written off only by a committed payout
    trainers  += the network's ladder: team volume × the step rate, per day or per week
    owners who work shifts are paid for their own pieces like anyone (their personal rate); a network
    they lead pays no trainer share. The owners' earnings: revenue_per_piece × pieces − everything paid out
    bonuses    = active bonuses dated up to the period end
    fines      = active fines dated up to the period end
    deductions = active deductions
Money is settled oldest first while it fits: deductions, then fines. What doesn't fit stays active —
the debt is kept and the payout never goes below zero. Settled entries become 'offset' with the payout id.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date

from . import app_settings, finance, networks, pricing
from .rates import Book
from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ACTIVE, ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso

BANKS = ["Сбербанк", "Т-Банк", "ВТБ", "Альфа-Банк", "Газпромбанк", "Райффайзенбанк", "Совкомбанк", "Озон Банк",
         "Яндекс Банк", "Почта Банк", "МТС Банк", "Промсвязьбанк", "Россельхозбанк", "Другой банк"]
PHONE_RE = re.compile(r"^\+7\d{10}$")


# ---------------------------------------------------------------- SBP profile (ТЗ модуль 9)


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    phone = "+" + digits
    if not PHONE_RE.match(phone):
        raise ServiceError("Телефон в формате +7 900 123-45-67", 422, "bad_phone")
    return phone


def pretty_phone(phone: str) -> str:
    d = phone[2:]
    return f"+7 {d[:3]} {d[3:6]}-{d[6:8]}-{d[8:]}"


def mask_phone(phone: str | None) -> str | None:
    return f"+7 *** ***-{phone[-4:-2]}-{phone[-2:]}" if phone else None


def get_profile(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM payment_profiles WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def public_profile(profile: dict | None, full: bool) -> dict | None:
    if profile is None:
        return None
    bank = profile["bank"]
    if bank == "Другой банк" and profile.get("bank_name"):
        bank = profile["bank_name"]  # В22: the name the person wrote
    return {"phone": pretty_phone(profile["phone"]) if full else mask_phone(profile["phone"]),
            "bank": bank, "bank_option": profile["bank"], "bank_name": profile.get("bank_name"),
            "recipient": profile["recipient"] if full else None, "updated_at": profile["updated_at"]}


def can_see(viewer: dict, user: dict) -> bool:
    """The person themself and owners (stage 6 decision); lists elsewhere show a masked phone."""
    return viewer["id"] == user["id"] or perms.can(viewer, "money")


def save_profile(conn: sqlite3.Connection, actor: dict, user_id: int, phone: str, bank: str, recipient: str,
                 key: str, bank_name: str | None = None) -> dict:
    """Saves the details now: an owner's edit, or an approved request (ТЗ 28.10 §7). Employees send requests."""
    phone = normalize_phone(phone)
    if bank not in BANKS:
        raise ServiceError("Выберите банк из списка", 422, "bad_bank")
    recipient = " ".join((recipient or "").split())[:120]
    if len(recipient.split()) < 2:
        raise ServiceError("ФИО получателя — как в банке, минимум имя и фамилия", 422, "bad_recipient")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return get_profile(conn, user_id)
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money"):
            raise ServiceError("Реквизиты сразу сохраняет только владелец — сотрудник отправляет их на одобрение", 403, "forbidden")
        bank_name = (" ".join((bank_name or "").split())[:60] or None) if bank == "Другой банк" else None
        old = get_profile(conn, user_id)
        new = {"phone": phone, "bank": bank, "recipient": recipient, "bank_name": bank_name}
        if old and all(old.get(k) == v for k, v in new.items()):
            return old
        conn.execute(
            """INSERT INTO payment_profiles (user_id, phone, bank, recipient, updated_at, updated_by, bank_name)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET phone = excluded.phone, bank = excluded.bank, bank_name = excluded.bank_name,
                    recipient = excluded.recipient, updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
            (user_id, phone, bank, recipient, now_iso(), actor["telegram_id"], bank_name),
        )
        # audit of requisites changes (ТЗ 10.2)
        conn.execute("INSERT INTO payment_profile_log (user_id, old_value, new_value, actor, created_at) VALUES (?, ?, ?, ?, ?)",
                     (user_id, json.dumps({k: old.get(k) for k in new}, ensure_ascii=False) if old else None,
                      json.dumps(new, ensure_ascii=False), actor["telegram_id"], now_iso()))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type="PAYMENT_PROFILE_SAVED", key=key,
                  new={"bank": bank, "phone": mask_phone(phone)})
    return get_profile(conn, user_id)


def profile_history(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM payment_profile_log WHERE user_id = ? ORDER BY id DESC", (user_id,))]


# ---------------------------------------------------------------- payout registry (ТЗ модуль 10)


@dataclass
class Rates:
    rate_base: int
    rate_lazy: int
    lazy_threshold: int
    trainer_commission: int
    instructor_commission: int = pricing.DEFAULTS["instructor_commission"]
    piece_cap: int = pricing.DEFAULTS["piece_cap"]

    @classmethod
    def from_dict(cls, d: dict | None) -> "Rates":
        base = pricing.current()
        d = {**base, **{k: v for k, v in (d or {}).items() if v is not None}}
        keys = ("rate_base", "rate_lazy", "lazy_threshold", "trainer_commission", "instructor_commission", "piece_cap")
        for k in keys:
            v = d[k]
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 100_000:
                raise ServiceError("Ставки — целые числа от 0", 422, "bad_rates")
        return cls(*(d[k] for k in keys))

    def as_dict(self) -> dict:
        return {"rate_base": self.rate_base, "rate_lazy": self.rate_lazy, "lazy_threshold": self.lazy_threshold,
                "trainer_commission": self.trainer_commission, "instructor_commission": self.instructor_commission,
                "piece_cap": self.piece_cap}


def _approved(conn, user_ids: list[int], date_from: date, date_to: date) -> list[dict]:
    if not user_ids:
        return []
    return [dict(r) for r in conn.execute(
        f"""SELECT user_id, shift_date, fact_volume FROM shifts WHERE status IN {ACTIVE} AND report_state = 'approved'
            AND fact_volume IS NOT NULL AND training = 0 AND shift_date BETWEEN ? AND ?
            AND user_id IN ({','.join('?' * len(user_ids))})""",
        (date_from.isoformat(), date_to.isoformat(), *user_ids))]


def _open_money(conn, user_id: int, date_to: date) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT * FROM financial_transactions WHERE user_id = ? AND status = 'active'
           AND (type = 'deduction' OR event_date <= ?) ORDER BY event_date, id""", (user_id, date_to.isoformat()))]


def _lazy_since(conn, user_id: int) -> str | None:
    """The last day paid before the pool existed: those payouts already paid lazy days at the lazy rate."""
    row = conn.execute(
        """SELECT MAX(b.period_to) FROM payout_items i JOIN payout_batches b ON b.id = i.batch_id
           WHERE i.user_id = ? AND i.lazy_before IS NULL AND b.product IS NULL AND i.voided = 0""", (user_id,)).fetchone()
    return row[0]


def lazy_pool(conn: sqlite3.Connection, user_id: int, threshold: int, upto: date) -> int:
    """L: pieces of approved days below the threshold up to `upto`, minus what payouts took at the lazy rate.
    Kept across weeks and months; a person with «не применять налог на лень» (threshold 0) has none."""
    if threshold <= 0:
        return 0
    since = _lazy_since(conn, user_id) or ""
    accrued = conn.execute(
        f"""SELECT COALESCE(SUM(fact_volume), 0) FROM shifts WHERE user_id = ? AND status IN {ACTIVE}
            AND report_state = 'approved' AND training = 0 AND fact_volume IS NOT NULL AND fact_volume < ?
            AND shift_date > ? AND shift_date <= ?""", (user_id, threshold, since, upto.isoformat())).fetchone()[0]
    # an edited or annulled payout gives its lazy pieces back (ТЗ 28.10 §25): only live rows count
    taken = conn.execute("SELECT COALESCE(SUM(lazy_units), 0) FROM payout_items WHERE user_id = ? AND voided = 0",
                         (user_id,)).fetchone()[0]
    return max(0, accrued - taken)


def split_lazy(pieces: int, pool: int, own: dict) -> dict:
    """ТЗ 27.10 §21: min(P, L) pieces at the lazy rate, the rest at the person's rate."""
    lazy = min(pieces, pool) if own["lazy_threshold"] > 0 else 0
    full = pieces - lazy
    return {"lazy_units": lazy, "full_units": full, "lazy_before": pool, "lazy_after": pool - lazy,
            "rate_base": own["rate_base"], "rate_lazy": own["rate_lazy"],
            "pieces_pay": full * own["rate_base"] + lazy * own["rate_lazy"]}


def employee_view(conn: sqlite3.Connection, user_id: int, today: date) -> dict:
    """The employee's side (ТЗ 27.10 §19–20): approved pieces after the last payout and the pool.
    Pieces, not rubles: the payout may come out lower (fines, the pool, deductions)."""
    paid_through = conn.execute(
        """SELECT MAX(b.period_to) FROM payout_items i JOIN payout_batches b ON b.id = i.batch_id
           WHERE i.user_id = ? AND b.product IS NULL AND i.voided = 0""",  # a product's payout pays executions
        (user_id,)).fetchone()[0] or ""
    pieces = conn.execute(
        f"""SELECT COALESCE(SUM(fact_volume), 0) FROM shifts WHERE user_id = ? AND status IN {ACTIVE}
            AND report_state = 'approved' AND training = 0 AND fact_volume IS NOT NULL AND shift_date > ?""",
        (user_id, paid_through)).fetchone()[0]
    own = Book.load(conn).user_rates(user_id)
    return {"to_pay_pieces": pieces, "paid_through": paid_through or None,
            "lazy_units": lazy_pool(conn, user_id, own["lazy_threshold"], today)}


def calculate(conn: sqlite3.Connection, date_from: date, date_to: date, rates: Rates,
              network_id: int | None = None) -> list[dict]:
    """The auto-calculator grid (ТЗ 11.2, step 2). Nothing is saved. `rates` are the common rates
    (the wizard may change them for one payout); personal and network rates still apply over them."""
    if date_to < date_from or (date_to - date_from).days > 92:
        raise ServiceError("Период от 1 дня до 3 месяцев", 422, "bad_range")
    book = Book.load(conn, rates.as_dict())
    people = [u for u in svc.list_users(conn) if u["account_state"] != "blocked"]
    if network_id is not None:
        people = [u for u in people if u["network_id"] == network_id]
    nets = networks.names(conn)
    led = {n["trainer_id"]: nid for nid, n in book.nets.items() if n["trainer_id"] and not n["owner_led"]}
    rows = []
    approved = _approved(conn, [u["id"] for u in svc.list_users(conn)], date_from, date_to)
    for u in people:
        mine = [a for a in approved if a["user_id"] == u["id"]]
        personal = sum(a["fact_volume"] for a in mine)
        own = book.user_rates(u["id"])
        lazy = split_lazy(personal, lazy_pool(conn, u["id"], own["lazy_threshold"], date_to), own)
        pieces_pay = lazy["pieces_pay"]
        team, commission, share, kind = 0, 0, [], None
        if u["id"] in led:
            share, kind = book.trainer_share(conn, led[u["id"]], date_from, date_to), "trainer"
        elif perms.role_of(u) == perms.INSTRUCTOR:
            share, kind = book.instructor_share(conn, u["id"], date_from, date_to), "instructor"
        if share:
            team = sum(d["volume"] for d in share)
            commission = sum(d["amount"] for d in share)
        base = pieces_pay + commission
        money = _open_money(conn, u["id"], date_to)
        if not (personal or team or money):
            continue
        profile = get_profile(conn, u["id"])
        rows.append({
            "user_id": u["id"],
            "name": svc.full_name(u),
            "role": perms.role_of(u),
            "network": nets.get(u["network_id"]),
            "phone": pretty_phone(profile["phone"]) if profile else None,
            "bank": profile["bank"] if profile else None,
            "recipient": profile["recipient"] if profile else None,
            "personal_volume": personal,
            "team_volume": team,
            "pieces_pay": pieces_pay,
            "commission": commission,
            "commission_days": share,
            "commission_kind": kind,  # trainer / instructor: whose ladder and which people
            "delta": book.delta(),
            "rates": own,
            **lazy,
            "base_calc": base,
            **settle(base, money),
        })
    return sorted(rows, key=lambda r: r["name"])


def summary(conn: sqlite3.Connection, rows: list[dict], date_from: date, date_to: date,
            network_id: int | None = None) -> dict:
    """The owner's side (customer round B): revenue for every approved piece minus what people get."""
    revenue_rate = app_settings.get_all(conn)["revenue_per_piece"]
    scope = [u["id"] for u in svc.list_users(conn) if network_id is None or u["network_id"] == network_id]
    pieces = sum(a["fact_volume"] for a in _approved(conn, scope, date_from, date_to))
    paid = sum(r["total"] for r in rows)
    return {
        "pieces": pieces,
        "revenue_per_piece": revenue_rate,
        "revenue": pieces * revenue_rate,
        "pieces_pay": sum(r.get("pieces_pay", 0) for r in rows),
        "commission": sum(r.get("commission", 0) for r in rows),
        "bonuses": sum(r["bonuses"] for r in rows),
        "withheld": sum(r["fines"] + r["deductions"] for r in rows),
        "paid": paid,
        "owner": pieces * revenue_rate - paid,
    }


def settle(base: int, money: list[dict]) -> dict:
    """What a payout takes: bonuses add up; deductions, then fines (oldest first) are taken while they fit."""
    bonuses = [t for t in money if t["type"] == "bonus"]
    available = base + sum(t["amount"] for t in bonuses)
    used, carried = [t["id"] for t in bonuses], 0
    taken = {"deduction": 0, "fine": 0}
    for kind in ("deduction", "fine"):
        for t in (x for x in money if x["type"] == kind):
            if t["amount"] <= available:
                available -= t["amount"]
                taken[kind] += t["amount"]
                used.append(t["id"])
            else:
                carried += t["amount"]
    fine_units = sum(t.get("pieces") or 0 for t in money if t["type"] == "fine" and t["id"] in used)
    return {"bonuses": sum(t["amount"] for t in bonuses), "fines": taken["fine"], "fine_units": fine_units,
            "deductions": taken["deduction"],
            "total": available, "carried_debt": carried, "tx_ids": used,
            "has_deductions": any(t["type"] == "deduction" for t in money)}


def batch_by_key(conn: sqlite3.Connection, key: str) -> int | None:
    """The payout a repeated «Зафиксировать» already made (never just «the last one» — ТЗ 28.10 §25)."""
    row = conn.execute("SELECT new_value FROM event_logs WHERE idempotency_key = ?", (key,)).fetchone()
    return json.loads(row[0] or "{}").get("batch") if row else None


def _overlap(conn, user_ids: list[int], date_from: date, date_to: date, exclude: int | None = None) -> list[str]:
    """В55: a live regular payout of the same person over the same days would pay the same pieces twice."""
    if not user_ids:
        return []
    rows = conn.execute(
        f"""SELECT DISTINCT i.user_id, b.id, b.title FROM payout_items i JOIN payout_batches b ON b.id = i.batch_id
            WHERE b.product IS NULL AND b.status != 'annulled' AND i.voided = 0 AND b.id != ?
            AND b.period_from <= ? AND b.period_to >= ? AND i.user_id IN ({','.join('?' * len(user_ids))})""",
        (exclude or 0, date_to.isoformat(), date_from.isoformat(), *user_ids)).fetchall()
    return [f"{svc.full_name(svc.get_user(conn, r[0]))} — уже в выплате №{r[1]} «{r[2]}»" for r in rows]


def _write_regular(conn, actor: dict, batch_id: int, rows: list[dict], picked: dict, date_from: date, date_to: date) -> int:
    """The items and the settled money of a regular payout (a new one, or a new version of an edited one)."""
    grand = 0
    for r in rows:
        override = picked[r["user_id"]].get("base_override")
        if override is not None:
            if isinstance(override, bool) or not isinstance(override, int) or not 0 <= override <= 10_000_000:
                raise ServiceError("База — целое число рублей", 422, "bad_base")
            money = [finance.get(conn, t) for t in r["tx_ids"]] + [
                t for t in _open_money(conn, r["user_id"], date_to) if t["id"] not in r["tx_ids"]]
            r = {**r, **settle(override, sorted(money, key=lambda t: (t["event_date"], t["id"])))}
        base = override if override is not None else r["base_calc"]
        conn.execute(
            """INSERT INTO payout_items (batch_id, user_id, role, network, phone, bank, recipient, personal_volume,
                   team_volume, base_calc, base, bonuses, fines, deductions, total, carried_debt, pieces_pay, commission,
                   lazy_units, lazy_before, rate_base, rate_lazy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, r["user_id"], r["role"], r["network"], r["phone"], r["bank"], r["recipient"], r["personal_volume"],
             r["team_volume"], r["base_calc"], base, r["bonuses"], r["fines"], r["deductions"], r["total"],
             r["carried_debt"], r["pieces_pay"], r["commission"],
             # the pool is written off here, inside the payout's transaction — never by a preview
             r["lazy_units"], r["lazy_before"], r["rate_base"], r["rate_lazy"]),
        )
        settle_txs(conn, batch_id, r["tx_ids"], actor)
        grand += r["total"]
    return grand


def settle_txs(conn, batch_id: int, tx_ids: list[int], actor: dict) -> None:
    """Fines, deductions and bonuses taken by a payout: «учтён в выплате». Who approved a fine stays (§25)."""
    if tx_ids:
        conn.execute(
            f"""UPDATE financial_transactions SET status = 'offset', payout_id = ?, settled_by = ?, settled_at = ?
                WHERE id IN ({','.join('?' * len(tx_ids))}) AND status = 'active'""",
            (batch_id, actor["telegram_id"], now_iso(), *tx_ids))


def _check_rows(rows: list[dict], picked: dict) -> None:
    no_details = [r["name"] for r in rows if r["phone"] is None
                  and (picked[r["user_id"]].get("base_override") or r["total"]) > 0]
    if no_details:  # ТЗ 28.10 §7 (В21): money goes only to approved payment details
        raise ServiceError("Нет одобренных реквизитов: " + ", ".join(no_details) +
                           ". Одобрите их или уберите людей из выплаты", 409, "no_requisites")


def commit(conn: sqlite3.Connection, actor: dict, title: str, date_from: date, date_to: date, rates: Rates,
           network_id: int | None, choices: list[dict], proof: list[str], deduction_proof: list[str], key: str) -> dict:
    """«Зафиксировать выплату» (ТЗ 11.3): recalculated on the server; chosen people only; base may be overridden."""
    title = " ".join((title or "").split())[:100]
    if not title:
        raise ServiceError("Назовите выплату, например «Зарплата, октябрь 1–15»", 422, "title_required")
    _check_key(key)
    picked = {c["user_id"]: c for c in choices if c.get("include", True)}
    if not picked:
        raise ServiceError("Выберите хотя бы одного человека", 422, "nobody")
    with transaction(conn):
        if _seen(conn, key):
            done = batch_by_key(conn, key)
            if done is None:
                raise ServiceError("Выплата уже обрабатывается — обновите список", 409, "in_progress")
            return {**batch(conn, done), "repeated": True}
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money"):
            raise ServiceError("Выплаты фиксирует только владелец", 403, "owner_only")
        rows = [r for r in calculate(conn, date_from, date_to, rates, network_id) if r["user_id"] in picked]
        if not rows:
            raise ServiceError("Нечего выплачивать за этот период", 409, "nothing")
        _check_rows(rows, picked)
        clash = _overlap(conn, [r["user_id"] for r in rows], date_from, date_to)
        if clash:
            raise ServiceError("Период пересекается с действующей выплатой: " + "; ".join(clash[:5]), 409, "overlap")
        cur = conn.execute(
            """INSERT INTO payout_batches (title, period_from, period_to, network_id, rates, proof_media, deduction_media,
                                           total, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (title, date_from.isoformat(), date_to.isoformat(), network_id, json.dumps(rates.as_dict()),
             json.dumps(proof[:10]), json.dumps(deduction_proof[:10]), actor["telegram_id"], now_iso()),
        )
        batch_id = cur.lastrowid
        grand = _write_regular(conn, actor, batch_id, rows, picked, date_from, date_to)
        committed = [dict(i) for i in conn.execute("SELECT * FROM payout_items WHERE batch_id = ? AND voided = 0", (batch_id,))]
        conn.execute("UPDATE payout_batches SET total = ?, summary = ? WHERE id = ?",
                     (grand, json.dumps(summary(conn, committed, date_from, date_to, network_id)), batch_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="PAYOUT_COMMITTED", key=key,
                  new={"batch": batch_id, "total": grand, "people": len(rows)}, description=title)
    return batch(conn, batch_id)


# ---------------------------------------------------------------- edit and annul (ТЗ 28.10 §25)


def _live(conn, batch_id: int) -> dict:
    b = conn.execute("SELECT * FROM payout_batches WHERE id = ?", (batch_id,)).fetchone()
    if b is None:
        raise ServiceError("Выплата не найдена", 404, "not_found")
    if b["status"] == "annulled":
        raise ServiceError("Выплата уже аннулирована", 409, "annulled")
    return dict(b)


def _later(conn, b: dict) -> list[str]:
    """В53: only the latest payout of these people (for «Алиса» — of these QR codes) may change: a later one was
    counted on top of it (the lazy pool, debts, paid tasks)."""
    if b["product"] in ("alice", "plus"):  # by QR codes: «Алиса» and «Яндекс Плюс»
        rows = conn.execute(
            """SELECT DISTINCT b2.id, b2.title FROM alice_tasks t JOIN alice_tasks t2 ON t2.qr_id = t.qr_id
               JOIN payout_batches b2 ON b2.id = t2.payout_id WHERE t.payout_id = ? AND b2.id > ? AND b2.status != 'annulled'""",
            (b["id"], b["id"])).fetchall()
    else:
        rows = conn.execute(
            """SELECT DISTINCT b2.id, b2.title FROM payout_items i JOIN payout_items i2 ON i2.user_id = i.user_id AND i2.voided = 0
               JOIN payout_batches b2 ON b2.id = i2.batch_id WHERE i.batch_id = ? AND i.voided = 0 AND b2.id > ?
               AND b2.status != 'annulled' AND (b2.product IS NULL) = (? IS NULL)""",
            (b["id"], b["id"], b["product"])).fetchall()
    return [f"№{r[0]} «{r[1]}»" for r in rows]


def revert(conn, b: dict, voided: int) -> None:
    """Undo what a payout did, inside the caller's transaction: its money back to active (to be taken again only
    once), its lazy pieces back to the pool, its «Алиса» tasks back to «Принято, ожидает оплаты»."""
    conn.execute("""UPDATE financial_transactions SET status = 'active', payout_id = NULL, settled_by = NULL, settled_at = NULL
                    WHERE payout_id = ? AND status = 'offset'""", (b["id"],))
    conn.execute("UPDATE payout_items SET voided = ? WHERE batch_id = ? AND voided = 0", (voided, b["id"]))
    conn.execute("""UPDATE alice_tasks SET status = 'accepted', payout_id = NULL, paid_at = NULL
                    WHERE payout_id = ? AND status IN ('paid', 'yandex_rejected')""", (b["id"],))


def snapshot(conn, b: dict, actor: dict, note: str | None) -> None:
    items = [dict(r) for r in conn.execute("SELECT * FROM payout_items WHERE batch_id = ? AND voided = 0", (b["id"],))]
    tasks = [dict(r) for r in conn.execute("SELECT id, status FROM alice_tasks WHERE payout_id = ?", (b["id"],))]
    conn.execute("""INSERT INTO payout_versions (batch_id, version, snapshot, created_at, created_by, note)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                 (b["id"], b["version"], json.dumps({"batch": {k: b[k] for k in ("title", "total", "rates", "summary")},
                                                     "items": items, "tasks": tasks}, ensure_ascii=False),
                  now_iso(), actor["telegram_id"], note))


def edit(conn: sqlite3.Connection, actor: dict, batch_id: int, choices: list[dict], key: str, note: str | None = None) -> dict:
    """§25: the same payout, recalculated — who is in it and the base; the money taken is redone (only the current
    deductions, each once); the previous version is kept."""
    _check_key(key)
    picked = {c["user_id"]: c for c in choices if c.get("include", True)}
    if not picked:
        raise ServiceError("Выберите хотя бы одного человека", 422, "nobody")
    with transaction(conn):
        if _seen(conn, key):
            return batch(conn, batch_id)
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money"):
            raise ServiceError("Выплаты меняет только владелец", 403, "owner_only")
        b = _live(conn, batch_id)
        if b["product"]:
            raise ServiceError("Выплату «Алисы» или «Яндекс Плюса» меняют в её мастере", 409, "wrong_kind")
        later = _later(conn, b)
        if later:
            raise ServiceError("Сначала измените более позднюю выплату: " + ", ".join(later), 409, "not_latest")
        snapshot(conn, b, actor, note)
        revert(conn, b, 1)
        date_from, date_to = date.fromisoformat(b["period_from"]), date.fromisoformat(b["period_to"])
        rows = [r for r in calculate(conn, date_from, date_to, Rates.from_dict(json.loads(b["rates"])), b["network_id"])
                if r["user_id"] in picked]
        if not rows:
            raise ServiceError("Нечего выплачивать за этот период", 409, "nothing")
        _check_rows(rows, picked)
        clash = _overlap(conn, [r["user_id"] for r in rows], date_from, date_to, exclude=batch_id)
        if clash:
            raise ServiceError("Период пересекается с действующей выплатой: " + "; ".join(clash[:5]), 409, "overlap")
        grand = _write_regular(conn, actor, batch_id, rows, picked, date_from, date_to)
        committed = [dict(i) for i in conn.execute("SELECT * FROM payout_items WHERE batch_id = ? AND voided = 0", (batch_id,))]
        conn.execute("""UPDATE payout_batches SET total = ?, summary = ?, version = version + 1, updated_at = ?, updated_by = ?
                        WHERE id = ?""",
                     (grand, json.dumps(summary(conn, committed, date_from, date_to, b["network_id"])), now_iso(),
                      actor["telegram_id"], batch_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="PAYOUT_EDITED", key=key,
                  old={"total": b["total"], "version": b["version"]}, new={"batch": batch_id, "total": grand}, description=note)
    return batch(conn, batch_id)


def annul(conn: sqlite3.Connection, actor: dict, batch_id: int, reason: str | None, money_sent: bool, key: str) -> tuple[dict, list[dict]]:
    """«Аннулировать выплату» (§25): status «Аннулирована», into «Архив», nothing deleted. Its fines, deductions
    and lazy pieces count again from the next payout; «Алиса» tasks wait for payment again. Money already sent
    (В54): a deduction «Переплата по выплате №…» per person — the usual correction, not a silent refund.
    Returns (batch, the deductions made)."""
    from . import templates

    reason = templates.require_reason(reason, "Укажите причину аннулирования")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return batch(conn, batch_id), []
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money"):
            raise ServiceError("Аннулирует выплату только владелец", 403, "owner_only")
        b = _live(conn, batch_id)
        later = _later(conn, b)
        if later:
            raise ServiceError("Сначала аннулируйте более позднюю выплату: " + ", ".join(later), 409, "not_latest")
        items = [dict(r) for r in conn.execute("SELECT * FROM payout_items WHERE batch_id = ? AND voided = 0", (batch_id,))]
        revert(conn, b, 2)
        conn.execute("""UPDATE payout_batches SET status = 'annulled', annulled_at = ?, annulled_by = ?, annul_reason = ?,
                        money_sent = ? WHERE id = ?""", (now_iso(), actor["telegram_id"], reason, int(money_sent), batch_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="PAYOUT_ANNULLED", key=key,
                  new={"batch": batch_id, "total": b["total"], "money_sent": money_sent}, description=reason)
    made = []
    if money_sent:
        for it in items:
            if it["total"] > 0:
                made.append(finance.create(conn, actor, it["user_id"], "deduction", it["total"],
                                           f"Переплата по аннулированной выплате №{batch_id} «{b['title']}»",
                                           f"{key}:{it['user_id']}").tx)
    return batch(conn, batch_id), made


def batch(conn: sqlite3.Connection, batch_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM payout_batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        return None
    b = dict(row)
    b["rates"] = json.loads(b["rates"])
    b["proof_media"] = json.loads(b["proof_media"])
    b["deduction_media"] = json.loads(b["deduction_media"])
    b["summary"] = json.loads(b["summary"]) if b.get("summary") else None
    shown = 2 if b["status"] == "annulled" else 0  # an annulled payout keeps showing what it was
    b["items"] = [{**dict(r), "name": svc.full_name(svc.get_user(conn, r["user_id"]))}
                  for r in conn.execute("SELECT * FROM payout_items WHERE batch_id = ? AND voided = ? ORDER BY id",
                                        (batch_id, shown))]
    names = {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT telegram_id, first_name, last_name FROM users")}
    b["annulled_by_name"] = names.get(b.get("annulled_by")) if b.get("annulled_by") else None
    b["versions"] = [{"version": r["version"], "created_at": r["created_at"], "by": names.get(r["created_by"]), "note": r["note"]}
                     for r in conn.execute("SELECT * FROM payout_versions WHERE batch_id = ? ORDER BY version DESC", (batch_id,))]
    if b.get("product") in ("alice", "plus"):
        b["tasks"] = [{"id": r["id"], "status": r["status"], "user_id": r["user_id"]} for r in conn.execute(
            "SELECT id, status, user_id FROM alice_tasks WHERE payout_id = ? ORDER BY id", (batch_id,))]
    return b


def history(conn: sqlite3.Connection, limit: int = 60) -> list[dict]:
    """The registry; annulled payouts go to its «Архив» (the client splits by status)."""
    return [{**dict(r), "people": r["people"]} for r in conn.execute(
        """SELECT b.id, b.title, b.period_from, b.period_to, b.total, b.created_at, b.product, b.status, b.version,
                  b.annulled_at, b.annul_reason,
                  (SELECT COUNT(*) FROM payout_items i WHERE i.batch_id = b.id
                   AND i.voided = CASE WHEN b.status = 'annulled' THEN 2 ELSE 0 END) AS people
           FROM payout_batches b ORDER BY b.id DESC LIMIT ?""", (limit,))]


def bank_text(b: dict) -> str:
    """Copy-paste list for the bank, grouped by bank (ТЗ 11.3 step 4)."""
    groups: dict[str, list[dict]] = {}
    for it in b["items"]:
        if it["total"] > 0:
            groups.setdefault(it["bank"] or "Реквизиты не указаны", []).append(it)
    out = [f"{b['title']} ({b['period_from'][8:10]}.{b['period_from'][5:7]}–{b['period_to'][8:10]}.{b['period_to'][5:7]})"]
    for bank in sorted(groups, key=lambda x: (x == "Реквизиты не указаны", x)):
        items = groups[bank]
        out.append(f"\n{bank}:")
        out += [f"{it['recipient'] or it['name']} — {it['phone'] or 'нет телефона'} — {it['total']} ₽" for it in items]
        out.append(f"Итого {bank}: {sum(it['total'] for it in items)} ₽")
    out.append(f"\nВсего к выплате: {b['total']} ₽")
    return "\n".join(out)
