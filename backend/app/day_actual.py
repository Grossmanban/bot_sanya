"""The day's global actual output and the team-level recalculation (ТЗ 3.5).

Only owners enter it. Global plan = sum of the planned volumes of the day's held shifts.

    actual ≥ plan  →  the team met the plan: reports below the individual plan that wait for review
                      become approved_team; shortfall fines already given can be annulled
                      («Команда справилась»); later reports of that day are approved the same way.
    actual < plan  →  individual responsibility: below-plan reports stay in manual review.

Fines for a SHORTFALL are allowed only after the actual is entered and only if the team missed the plan.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import permissions as perms
from . import services as svc
from .db import transaction
from .services import ACTIVE, MAX_VOLUME, ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso, today_local

TEAM_NOTE = "Общий план команды выполнен"
ANNUL_NOTE = "Штраф аннулирован: команда справилась"
LOOKBACK_DAYS = 14


@dataclass
class Recalc:
    changed: bool
    row: dict | None
    approved: list[dict] = field(default_factory=list)   # shifts approved by this recalculation
    fined: list[dict] = field(default_factory=list)      # shortfall fines that may now be annulled
    old_value: int | None = None


def _held(conn, day: date) -> list[dict]:
    return [dict(r) for r in conn.execute(
        f"""SELECT * FROM shifts WHERE shift_date = ? AND status IN {ACTIVE} AND start_time IS NOT NULL""",
        (day.isoformat(),))]


def day_plan(conn: sqlite3.Connection, day: date) -> int:
    return sum(s["volume"] or 0 for s in _held(conn, day))


def get(conn: sqlite3.Connection, day: date | str) -> dict | None:
    key = day if isinstance(day, str) else day.isoformat()
    row = conn.execute("SELECT * FROM day_actuals WHERE day = ?", (key,)).fetchone()
    return dict(row) if row else None


def under_plan(shift: dict) -> bool:
    return bool(shift.get("volume")) and shift.get("fact_volume") is not None and shift["fact_volume"] < shift["volume"]


def shortfall_fine_block(conn: sqlite3.Connection, shift: dict) -> str | None:
    """Why a fine for a missed individual plan can't be given now (None = allowed)."""
    if not under_plan(shift):
        return None
    row = get(conn, shift["shift_date"])
    if row is None:
        return "Штраф за недобор — только после того, как владелец введёт общий факт дня"
    if row["met"]:
        return "Команда выполнила общий план — штраф за недобор не назначается"
    return None


def team_view(conn: sqlite3.Connection, day: date | str) -> dict | None:
    row = get(conn, day)
    return None if row is None else {"plan": row["plan"], "actual": row["actual"], "met": bool(row["met"])}


def _fined_under(conn, day: date) -> list[dict]:
    return [s for s in _held(conn, day)
            if s["shortfall_status"] == "fined" and s["report_state"] == "rejected" and under_plan(s)]


def set_actual(conn: sqlite3.Connection, actor: dict, day: date, value: int, key: str,
               today: date | None = None) -> Recalc:
    today = today or today_local()
    _check_key(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_VOLUME * 100:
        raise ServiceError("Общий факт — целое число от 0", 422, "bad_actual")
    if day > today:
        raise ServiceError("Общий факт вводится за сегодня или прошедшие дни", 409, "future_day")
    with transaction(conn):
        if _seen(conn, key):
            return Recalc(False, get(conn, day))
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "global_actual"):
            raise ServiceError("Общий факт дня вводит только владелец", 403, "owner_only")
        old = get(conn, day)
        plan = day_plan(conn, day)
        met = value >= plan
        if old and old["actual"] == value and old["plan"] == plan:
            return Recalc(False, old, old_value=old["actual"])
        conn.execute(
            """INSERT INTO day_actuals (day, actual, plan, met, set_by, set_at) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET actual = excluded.actual, plan = excluded.plan, met = excluded.met,
                                              set_by = excluded.set_by, set_at = excluded.set_at""",
            (day.isoformat(), value, plan, int(met), actor["telegram_id"], now_iso()),
        )
        conn.execute(
            "INSERT INTO day_actual_log (day, old_value, new_value, plan, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (day.isoformat(), old["actual"] if old else None, value, plan, actor["telegram_id"], now_iso()),
        )
        approved: list[dict] = []
        if met:
            for s in _held(conn, day):
                if s["report_state"] != "pending_review":
                    continue
                _approve_team(conn, s, actor["telegram_id"])
                approved.append(svc.get_shift(conn, s["id"]))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="GLOBAL_ACTUAL_SET", key=key,
                  old={"actual": old["actual"] if old else None}, new={"actual": value, "plan": plan, "met": met},
                  description=day.isoformat())
        fined = _fined_under(conn, day) if met else []
    return Recalc(True, get(conn, day), approved, fined, old["actual"] if old else None)


def _approve_team(conn, shift: dict, actor_tg: int) -> None:
    """A below-plan report waiting for review is approved because the team met the plan."""
    report = conn.execute("SELECT * FROM reports WHERE shift_id = ? ORDER BY attempt DESC LIMIT 1", (shift["id"],)).fetchone()
    if report is not None and report["status"] == "pending_review":
        conn.execute("UPDATE reports SET status = 'approved_team', reviewed_by = ?, reviewed_at = ?, review_comment = ? "
                     "WHERE id = ?", (actor_tg, now_iso(), TEAM_NOTE, report["id"]))
    conn.execute(
        """UPDATE shifts SET report_state = 'approved', report_note = ?, shortfall_status = 'waived', shortfall_penalty = NULL,
                             shortfall_decided_by = ?, shortfall_decided_at = ?, updated_at = ? WHERE id = ?""",
        (TEAM_NOTE, actor_tg, now_iso(), now_iso(), shift["id"]),
    )
    log_event(conn, user_id=shift["user_id"], actor_id=actor_tg, event_type="REPORT_TEAM_APPROVED", key=svc.new_key(),
              shift_id=shift["id"], description=TEAM_NOTE)


def annul_fines(conn: sqlite3.Connection, actor: dict, day: date, shift_ids: list[int] | None, key: str) -> list[dict]:
    """«Авто-компенсация»: the team met the plan, so shortfall fines of that day are annulled (owner)."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return []
        actor = _fresh_actor(conn, actor)
        if not perms.can(actor, "global_actual"):
            raise ServiceError("Аннулировать может только владелец", 403, "owner_only")
        row = get(conn, day)
        if row is None or not row["met"]:
            raise ServiceError("Аннулировать можно, только если команда выполнила общий план", 409, "team_not_met")
        targets = [s for s in _fined_under(conn, day) if shift_ids is None or s["id"] in shift_ids]
        done = []
        for s in targets:
            report = conn.execute("SELECT id FROM reports WHERE shift_id = ? ORDER BY attempt DESC LIMIT 1", (s["id"],)).fetchone()
            if report:
                conn.execute("UPDATE reports SET status = 'approved_team', reviewed_by = ?, reviewed_at = ?, review_comment = ?, "
                             "penalty_amount = NULL WHERE id = ?", (actor["telegram_id"], now_iso(), ANNUL_NOTE, report["id"]))
            conn.execute(
                """UPDATE shifts SET report_state = 'approved', report_note = ?, shortfall_status = 'waived',
                                     shortfall_penalty = NULL, shortfall_decided_by = ?, shortfall_decided_at = ?,
                                     updated_at = ? WHERE id = ?""",
                (ANNUL_NOTE, actor["telegram_id"], now_iso(), now_iso(), s["id"]),
            )
            log_event(conn, user_id=s["user_id"], actor_id=actor["telegram_id"], event_type="FINE_ANNULLED",
                      key=svc.new_key(), shift_id=s["id"], old={"penalty": s["shortfall_penalty"]},
                      new={"penalty": None}, description=ANNUL_NOTE)
            done.append({**svc.get_shift(conn, s["id"]), "annulled_amount": s["shortfall_penalty"]})
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="FINES_ANNULLED_TEAM", key=key,
                  new={"day": day.isoformat(), "count": len(done)})
    return done


def history(conn: sqlite3.Connection, day: date) -> list[dict]:
    names = {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT telegram_id, first_name, last_name FROM users")}
    return [{**dict(r), "actor_name": names.get(r["actor"], str(r["actor"]))}
            for r in conn.execute("SELECT * FROM day_actual_log WHERE day = ? ORDER BY id DESC", (day.isoformat(),))]


def missing_days(conn: sqlite3.Connection, today: date | None = None) -> list[date]:
    """Past days (up to yesterday, last two weeks) that had shifts but no global actual yet."""
    today = today or today_local()
    since = today - timedelta(days=LOOKBACK_DAYS)
    rows = conn.execute(
        f"""SELECT DISTINCT shift_date FROM shifts WHERE status IN {ACTIVE} AND start_time IS NOT NULL
            AND shift_date >= ? AND shift_date < ?
            AND shift_date NOT IN (SELECT day FROM day_actuals) ORDER BY shift_date DESC""",
        (since.isoformat(), today.isoformat()),
    ).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


def overview(conn: sqlite3.Connection, today: date | None = None, days: int = LOOKBACK_DAYS) -> list[dict]:
    """Days from today back: plan, sum of individual facts, reports, the global actual and its log."""
    today = today or today_local()
    out = []
    names = {r["telegram_id"]: svc.full_name(dict(r)) for r in conn.execute("SELECT telegram_id, first_name, last_name FROM users")}
    for i in range(days):
        day = today - timedelta(days=i)
        held = _held(conn, day)
        row = get(conn, day)
        if not held and row is None:
            continue
        fined = _fined_under(conn, day) if row and row["met"] else []
        out.append({
            "date": day.isoformat(),
            "is_today": i == 0,
            "shifts": len(held),
            "plan": sum(s["volume"] or 0 for s in held),
            "facts": sum(s["fact_volume"] or 0 for s in held if s["fact_volume"] is not None),
            "reported": sum(s["fact_volume"] is not None for s in held),
            "pending": sum(s["report_state"] == "pending_review" for s in held),
            "actual": row["actual"] if row else None,
            "met": bool(row["met"]) if row else None,
            "set_by": names.get(row["set_by"]) if row else None,
            "set_at": row["set_at"] if row else None,
            "fined": [{"shift_id": s["id"], "user_id": s["user_id"], "amount": s["shortfall_penalty"],
                       "name": svc.full_name(svc.get_user(conn, s["user_id"]))} for s in fined],
            "log": history(conn, day)[:10],
        })
    return out
