"""Who earns what per piece (customer round B). Owners set everything here; nobody else may.

Employee, per day (agreed with the customer):
    fewer pieces than lazy_threshold and no «не применять налог на лень» → rate_lazy («налог на лень»)
    otherwise: the personal rate → the common rate_base (networks' own rates were dropped on 27.10; the column stays)

Trainer, per network: a ladder of steps «from N pieces → R ₽ per piece». The step reached by the team's
volume (per day or per week, set per network) pays ALL its pieces at that rate. The trainer's own
pieces count only when the network says so. No ladder → the common trainer_commission for every piece.
A network led by an owner pays no trainer share: the owner earns it.

Deductions in pieces (finance.piece_deduction) use the rate of that very day for both the employee and
the trainer — the ladder step is not recalculated afterwards.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import permissions as perms
from . import pricing
from .services import ACTIVE, ServiceError

MAX_RATE = 100_000
MAX_STEPS = 10


def parse_ladder(raw: str | None) -> list[dict]:
    steps = json.loads(raw) if raw else []
    return sorted(({"from": int(s["from"]), "rate": int(s["rate"])} for s in steps), key=lambda s: s["from"])


def validate_ladder(steps: list[dict] | None) -> str | None:
    """[{from, rate}, …] → stored JSON (None = no ladder: the common commission)."""
    if not steps:
        return None
    if len(steps) > MAX_STEPS:
        raise ServiceError(f"Не больше {MAX_STEPS} ступеней", 422, "bad_ladder")
    clean = []
    for s in steps:
        f, r = s.get("from"), s.get("rate")
        for v in (f, r):
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_RATE:
                raise ServiceError("Ступень: «от» и ставка — целые числа от 0", 422, "bad_ladder")
        clean.append({"from": f, "rate": r})
    clean.sort(key=lambda s: s["from"])
    if len({s["from"] for s in clean}) != len(clean):
        raise ServiceError("Две ступени с одинаковым «от»", 422, "bad_ladder")
    return json.dumps(clean)


def step_rate(ladder: list[dict], volume: int, default: int) -> int:
    """The rate of the highest step reached; below the first step — nothing (0) if a ladder is set."""
    if not ladder:
        return default
    rate = 0
    for s in ladder:
        if volume >= s["from"]:
            rate = s["rate"]
    return rate


@dataclass
class Book:
    """Everything a calculation needs, read once. `common` may be the wizard's one-off override."""
    common: dict
    users: dict[int, dict] = field(default_factory=dict)
    nets: dict[int, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, conn: sqlite3.Connection, common: dict | None = None) -> "Book":
        book = cls(dict(common or pricing.current()))
        book.users = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, telegram_id, role, network_id, personal_rate, lazy_exempt FROM users")}
        for r in conn.execute("SELECT * FROM networks WHERE status = 'active'"):
            n = dict(r)
            n["steps"] = parse_ladder(n["ladder"])
            lead = book.users.get(n["trainer_id"]) if n["trainer_id"] else None
            n["owner_led"] = bool(lead) and perms.is_owner(lead)
            book.nets[n["id"]] = n
        return book

    # ------------------------------------------------------------ employees

    def user_rates(self, user_id: int) -> dict:
        """Rates in pricing's shape for this person: pricing.expected_pay(volume, book.user_rates(id))."""
        u = self.users.get(user_id) or {}
        base = self.common["rate_base"]
        if u.get("personal_rate") is not None:
            base = u["personal_rate"]
        return {"rate_base": base, "rate_lazy": self.common["rate_lazy"],
                "lazy_threshold": 0 if u.get("lazy_exempt") else self.common["lazy_threshold"]}

    def day_rate(self, user_id: int, volume: int) -> int:
        return pricing.calculate_rate(volume, self.user_rates(user_id))

    def pay(self, user_id: int, volume: int) -> int:
        return volume * self.day_rate(user_id, volume)

    # ------------------------------------------------------------ trainers

    def trainer_share(self, conn: sqlite3.Connection, network_id: int, date_from: date, date_to: date) -> list[dict]:
        """Per day of the period: the team volume, the step rate and the trainer's money.
        Weekly ladders pick the step by the whole Mon–Sun week, but pay only the days inside the period."""
        net = self.nets.get(network_id)
        if net is None or not net["trainer_id"] or net["owner_led"]:
            return []
        weekly = net["ladder_period"] == "week"
        start = date_from - timedelta(days=date_from.weekday()) if weekly else date_from
        end = date_to + timedelta(days=6 - date_to.weekday()) if weekly else date_to
        team = [uid for uid, u in self.users.items() if u["network_id"] == network_id
                and (uid != net["trainer_id"] or net["ladder_counts_trainer"])]
        by_day: dict[str, int] = {}
        if team:
            for r in conn.execute(
                f"""SELECT shift_date, SUM(fact_volume) AS v FROM shifts WHERE status IN {ACTIVE}
                    AND report_state = 'approved' AND fact_volume IS NOT NULL AND training = 0 AND shift_date BETWEEN ? AND ?
                    AND user_id IN ({','.join('?' * len(team))}) GROUP BY shift_date""",
                (start.isoformat(), end.isoformat(), *team)):
                by_day[r["shift_date"]] = r["v"]
        out = []
        d = date_from
        while d <= date_to:
            iso = d.isoformat()
            volume = by_day.get(iso, 0)
            if weekly:
                monday = d - timedelta(days=d.weekday())
                step_volume = sum(by_day.get((monday + timedelta(days=i)).isoformat(), 0) for i in range(7))
            else:
                step_volume = volume
            rate = step_rate(net["steps"], step_volume, self.common["trainer_commission"])
            if volume:
                out.append({"date": iso, "volume": volume, "step_volume": step_volume, "rate": rate, "amount": volume * rate})
            d += timedelta(days=1)
        return out

    def trainer_rate_on(self, conn: sqlite3.Connection, network_id: int, day: date) -> int:
        """The rate the trainer was paid per piece on that day (0 when nothing applies)."""
        net = self.nets.get(network_id)
        if net is None or not net["trainer_id"] or net["owner_led"]:
            return 0
        days = self.trainer_share(conn, network_id, day, day)
        if days:
            return days[0]["rate"]
        # nothing approved that day yet: the step for the volume so far (the first step for a daily ladder)
        if net["ladder_period"] == "week":
            monday = day - timedelta(days=day.weekday())
            week = self.trainer_share(conn, network_id, monday, monday + timedelta(days=6))
            return step_rate(net["steps"], week[0]["step_volume"] if week else 0, self.common["trainer_commission"])
        return step_rate(net["steps"], 0, self.common["trainer_commission"])


def for_user(conn: sqlite3.Connection, user_id: int) -> dict:
    """The person's effective rates in pricing's shape (cards, «план заработка», stats, Excel)."""
    return Book.load(conn).user_rates(user_id)


def set_user_rates(conn: sqlite3.Connection, actor: dict, user_id: int, changes: dict, key: str) -> dict:
    """Owner: a personal rate (None = the network's or the common one) and «не применять налог на лень»."""
    from . import services as svc
    from .db import transaction
    from .services import _check_key, _fresh_actor, _seen, log_event

    fields = {}
    if "personal_rate" in changes:
        v = changes["personal_rate"]
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_RATE):
            raise ServiceError("Ставка — целое число рублей", 422, "bad_rate")
        fields["personal_rate"] = v
    if "lazy_exempt" in changes:
        fields["lazy_exempt"] = int(bool(changes["lazy_exempt"]))
    if not fields:
        raise ServiceError("Нет изменений", 422, "no_changes")
    _check_key(key)
    with transaction(conn):
        target = svc.get_user(conn, user_id)
        if target is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        if _seen(conn, key):
            return target
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "money"):
            raise ServiceError("Ставки меняет только владелец", 403, "owner_only")
        conn.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), user_id))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], event_type="USER_RATES_SET", key=key,
                  old={k: target[k] for k in fields}, new=fields)
    return svc.get_user(conn, user_id)
