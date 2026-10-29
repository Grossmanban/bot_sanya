"""ТЗ 28.10 §14: «ждут решения» — one place that counts every open request for the person looking.

Each key is counted by the same condition and the same scope as its list, and only for people entitled to decide
it: a trainer gets the counts of their network for what they decide themselves (reports, no-shows, discipline,
urgent requests, the day's unmarked people, event entries); applications, fines on approval, «Алиса», the day's
global actual — owners and admins only (В26). Viewing never clears a count — only a decision does. One request is
one key: nothing is counted twice (a pending person with a questionnaire is «Онбординг», not «Команда»; someone
unmarked who already has an «unmarked» violation for that day is «Дисциплина», not «День»)."""

import sqlite3
from datetime import date, timedelta

from . import day_actual, recruiting, violations
from . import permissions as perms
from . import services as svc
from .timeutil import today_local

KEYS = ("review", "no_show", "actual_missing", "actual_overdue", "fines_pending", "fines_draft", "violations",
        "registrations", "onboarding_new", "internship_review", "training_requests", "profile_changes", "payment_changes",
        "urgent", "unmarked", "events_pending",
        "alice_moderation", "alice_failed", "plus_moderation", "plus_failed")
ALICE_GIVE_UP = 20  # alice.deliver stops retrying after this many attempts


def scope(conn: sqlite3.Connection, viewer: dict) -> set[int] | None:
    """None — everyone; otherwise the ids the viewer manages (themselves included, like the lists)."""
    if perms.sees_everyone(viewer):
        return None
    return {u["id"] for u in svc.list_users(conn) if u["id"] == viewer["id"] or perms.manages(viewer, u)}


def unmarked(conn: sqlite3.Connection, viewer: dict, day: date, with_violations: bool = False) -> list[dict]:
    """«День»: people with «Работаю» who have neither a shift nor a day off on `day`, in the viewer's scope.
    The count leaves those who already got an «unmarked» violation for that day to «Дисциплина» (one request — one
    key); the «День» list (with_violations) keeps them, so nobody gets lost there (ТЗ 29.10 §5.1)."""
    if not perms.can(viewer, "schedule"):
        return []
    taken = set() if with_violations else {r[0] for r in conn.execute(
        """SELECT user_id FROM attendance_violations WHERE kind = 'unmarked' AND week_start_date = ?""",
        (day.isoformat(),))}
    return [u for u in violations.unmarked(conn, day)
            if u["id"] not in taken and (perms.sees_everyone(viewer) or perms.manages(viewer, u))]


def counts(conn: sqlite3.Connection, viewer: dict, today: date | None = None) -> dict[str, int]:
    today = today or today_local()
    out = {k: 0 for k in KEYS}
    only = scope(conn, viewer)

    def mine(user_id: int) -> bool:
        return only is None or user_id in only

    if perms.can(viewer, "review"):
        # the same rule as the lists: one's own report is checked by someone else (an owner may check their own)
        own = None if perms.is_owner(viewer) else viewer["id"]
        for r in conn.execute(
                """SELECT user_id, report_state FROM shifts WHERE status IN ('active','restored') AND start_time IS NOT NULL
                   AND (report_state = 'pending_review' OR (report_state = 'no_show' AND shortfall_status IS NULL))"""):
            if mine(r["user_id"]) and r["user_id"] != own:
                out["review" if r["report_state"] == "pending_review" else "no_show"] += 1
        out["violations"] = violations.pending_count(conn, viewer)
        for r in conn.execute(
                """SELECT x.user_id FROM event_entries x JOIN events e ON e.id = x.event_id
                   WHERE x.status = 'pending_review' AND e.status = 'active' AND e.deleted_at IS NULL"""):
            if mine(r["user_id"]) and r["user_id"] != viewer["id"]:
                out["events_pending"] += 1
    if perms.can(viewer, "global_actual"):
        out["actual_missing"] = len(day_actual.missing_days(conn, today))
        out["actual_overdue"] = len(overdue_actual_days(conn, today))
    if perms.can(viewer, "approve_fines"):
        out["fines_pending"] = conn.execute(
            "SELECT COUNT(*) FROM financial_transactions WHERE status = 'pending_admin_approval'").fetchone()[0]
    if perms.can(viewer, "fine_direct"):
        out["fines_draft"] = conn.execute("SELECT COUNT(*) FROM financial_transactions WHERE status = 'draft'").fetchone()[0]
    if perms.can(viewer, "registrations"):
        out["registrations"] = conn.execute(
            f"""SELECT COUNT(*) FROM users u WHERE u.status = 'pending' AND NOT EXISTS (
                    SELECT 1 FROM applications a WHERE a.user_id = u.id AND a.status IN {recruiting.OPEN})""").fetchone()[0]
        out["onboarding_new"] = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'new'").fetchone()[0]
        out["internship_review"] = sum(
            1 for a in conn.execute("SELECT * FROM applications WHERE status IN ('internship', 'done')")
            if (recruiting.progress(conn, dict(a)) or {}).get("review") or (a["status"] == "internship" and a["finish_requested_at"]))
        # §3–§4: the trainers' and candidates' requests waiting for an admin — «Ожидает согласования»
        out["training_requests"] = conn.execute("SELECT COUNT(*) FROM training_requests WHERE status = 'pending'").fetchone()[0]
    if perms.can(viewer, "profiles"):  # §7: profile changes — owners and admins; payment details — owners (В20)
        out["profile_changes"] = conn.execute(
            "SELECT COUNT(*) FROM profile_requests WHERE kind = 'profile' AND status = 'pending'").fetchone()[0]
    if perms.can(viewer, "money"):
        out["payment_changes"] = conn.execute(
            "SELECT COUNT(*) FROM profile_requests WHERE kind = 'payment' AND status = 'pending'").fetchone()[0]
    if perms.can(viewer, "schedule"):
        out["urgent"] = sum(1 for r in conn.execute(
            "SELECT user_id FROM shifts WHERE status = 'awaiting_approval' AND shift_date = ?", (today.isoformat(),))
            if mine(r["user_id"]) and r["user_id"] != viewer["id"])
        # ТЗ 29.10 §5: today and tomorrow (each person-day once; with a violation it is «Дисциплина»'s)
        out["unmarked"] = len(unmarked(conn, viewer, today)) + len(unmarked(conn, viewer, today + timedelta(days=1)))
    if perms.can(viewer, "alice"):
        for product in ("alice", "plus"):  # ТЗ «Яндекс Плюс»: each product's queue is counted on its own
            out[f"{product}_moderation"] = conn.execute(
                "SELECT COUNT(*) FROM alice_tasks WHERE product = ? AND status = 'moderation'", (product,)).fetchone()[0]
            out[f"{product}_failed"] = conn.execute(
                "SELECT COUNT(*) FROM alice_tasks WHERE product = ? AND status = 'send_failed' AND send_attempts >= ?",
                (product, ALICE_GIVE_UP)).fetchone()[0]
    out["total"] = sum(out[k] for k in KEYS)
    return out


def overdue_actual_days(conn: sqlite3.Connection, today: date) -> list[date]:
    """В56: days with shifts but no global actual that fell out of the two-week window — shown apart as «просрочено»."""
    since = today - timedelta(days=day_actual.LOOKBACK_DAYS)
    rows = conn.execute(
        f"""SELECT DISTINCT shift_date FROM shifts WHERE status IN {svc.ACTIVE} AND start_time IS NOT NULL
            AND shift_date < ? AND shift_date >= ? AND shift_date NOT IN (SELECT day FROM day_actuals)
            ORDER BY shift_date DESC""", (since.isoformat(), _actual_start(conn))).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


def _actual_start(conn: sqlite3.Connection) -> str:
    """Days before the first global actual ever entered never needed one (the feature didn't exist)."""
    row = conn.execute("SELECT MIN(day) FROM day_actuals").fetchone()
    return row[0] if row and row[0] else "9999-12-31"




LABELS = {
    "review": "📝 Отчёты на проверке", "no_show": "🚫 Неявки без решения", "violations": "⚖️ Дисциплина",
    "urgent": "⚡ Срочные заявки на сегодня", "unmarked": "🗓 Не отметили день (сегодня, завтра)",
    "events_pending": "🏁 Заявки в событиях", "fines_pending": "🧾 Штрафы тренеров на согласовании",
    "fines_draft": "💸 Штрафы без суммы", "registrations": "👤 Заявки на доступ", "onboarding_new": "🧑‍🎓 Новые анкеты",
    "internship_review": "🎯 Стажировки на проверку", "training_requests": "⏳ Обучение: ожидает согласования",
    "profile_changes": "🪪 Изменения профилей", "payment_changes": "💳 Изменения реквизитов", "actual_missing": "📊 Дни без общего факта",
    "actual_overdue": "📊 Просрочено без общего факта", "alice_moderation": "📸 «Алиса»: на модерации",
    "alice_failed": "📸 «Алиса»: не отправлено", "plus_moderation": "📸 «Яндекс Плюс»: на модерации",
    "plus_failed": "📸 «Яндекс Плюс»: не отправлено",
}


def summary_text(c: dict) -> str:
    lines = [f"{LABELS[k]}: <b>{c[k]}</b>" for k in KEYS if c.get(k)]
    if not lines:
        return "✅ Ничего не ждёт вашего решения."
    return "⏳ <b>Ждут решения</b>\n" + "\n".join(lines) + "\n\nРешения — в приложении."
