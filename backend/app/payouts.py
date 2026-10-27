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
    return {"phone": pretty_phone(profile["phone"]) if full else mask_phone(profile["phone"]),
            "bank": profile["bank"], "recipient": profile["recipient"] if full else None,
            "updated_at": profile["updated_at"]}


def can_see(viewer: dict, user: dict) -> bool:
    """The person themself and owners (stage 6 decision); lists elsewhere show a masked phone."""
    return viewer["id"] == user["id"] or perms.can(viewer, "money")


def save_profile(conn: sqlite3.Connection, actor: dict, user_id: int, phone: str, bank: str, recipient: str,
                 key: str) -> dict:
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
        if actor["id"] != user_id and not perms.can(actor, "money"):
            raise ServiceError("Реквизиты меняет сам сотрудник или владелец", 403, "forbidden")
        old = get_profile(conn, user_id)
        new = {"phone": phone, "bank": bank, "recipient": recipient}
        if old and all(old[k] == v for k, v in new.items()):
            return old
        conn.execute(
            """INSERT INTO payment_profiles (user_id, phone, bank, recipient, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET phone = excluded.phone, bank = excluded.bank,
                    recipient = excluded.recipient, updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
            (user_id, phone, bank, recipient, now_iso(), actor["telegram_id"]),
        )
        # audit of requisites changes (ТЗ 10.2)
        conn.execute("INSERT INTO payment_profile_log (user_id, old_value, new_value, actor, created_at) VALUES (?, ?, ?, ?, ?)",
                     (user_id, json.dumps({k: old[k] for k in new}, ensure_ascii=False) if old else None,
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
           WHERE i.user_id = ? AND i.lazy_before IS NULL""", (user_id,)).fetchone()
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
    taken = conn.execute("SELECT COALESCE(SUM(lazy_units), 0) FROM payout_items WHERE user_id = ?", (user_id,)).fetchone()[0]
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
        """SELECT MAX(b.period_to) FROM payout_items i JOIN payout_batches b ON b.id = i.batch_id WHERE i.user_id = ?""",
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
            return dict(conn.execute("SELECT * FROM payout_batches ORDER BY id DESC LIMIT 1").fetchone())
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money"):
            raise ServiceError("Выплаты фиксирует только владелец", 403, "owner_only")
        rows = [r for r in calculate(conn, date_from, date_to, rates, network_id) if r["user_id"] in picked]
        if not rows:
            raise ServiceError("Нечего выплачивать за этот период", 409, "nothing")
        cur = conn.execute(
            """INSERT INTO payout_batches (title, period_from, period_to, network_id, rates, proof_media, deduction_media,
                                           total, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (title, date_from.isoformat(), date_to.isoformat(), network_id, json.dumps(rates.as_dict()),
             json.dumps(proof[:10]), json.dumps(deduction_proof[:10]), actor["telegram_id"], now_iso()),
        )
        batch_id, grand = cur.lastrowid, 0
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
            if r["tx_ids"]:
                conn.execute(
                    f"""UPDATE financial_transactions SET status = 'offset', payout_id = ?, decided_by = ?, decided_at = ?
                        WHERE id IN ({','.join('?' * len(r['tx_ids']))}) AND status = 'active'""",
                    (batch_id, actor["telegram_id"], now_iso(), *r["tx_ids"]))
            grand += r["total"]
        committed = [dict(i) for i in conn.execute("SELECT * FROM payout_items WHERE batch_id = ?", (batch_id,))]
        conn.execute("UPDATE payout_batches SET total = ?, summary = ? WHERE id = ?",
                     (grand, json.dumps(summary(conn, committed, date_from, date_to, network_id)), batch_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="PAYOUT_COMMITTED", key=key,
                  new={"batch": batch_id, "total": grand, "people": len(rows)}, description=title)
    return batch(conn, batch_id)


def batch(conn: sqlite3.Connection, batch_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM payout_batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        return None
    b = dict(row)
    b["rates"] = json.loads(b["rates"])
    b["proof_media"] = json.loads(b["proof_media"])
    b["deduction_media"] = json.loads(b["deduction_media"])
    b["summary"] = json.loads(b["summary"]) if b.get("summary") else None
    b["items"] = [{**dict(r), "name": svc.full_name(svc.get_user(conn, r["user_id"]))}
                  for r in conn.execute("SELECT * FROM payout_items WHERE batch_id = ? ORDER BY id", (batch_id,))]
    return b


def history(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    return [{**dict(r), "people": r["people"]} for r in conn.execute(
        """SELECT b.id, b.title, b.period_from, b.period_to, b.total, b.created_at,
                  (SELECT COUNT(*) FROM payout_items i WHERE i.batch_id = b.id) AS people
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
