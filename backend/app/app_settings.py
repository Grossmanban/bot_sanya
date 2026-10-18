"""Admin-editable settings for scheduled messages, stored in the app_settings table."""

import re
import sqlite3

from .db import transaction
from .services import ServiceError

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# key -> (default, kind); kind is "bool", "time", "weekday" (0 = Monday), "minutes" (0–240),
# "lead" (minutes before a deadline, 0–720) or "choice:<option>|<option>"
SCHEMA: dict[str, tuple[str, str]] = {
    "eve_reminder_enabled": ("1", "bool"),
    "eve_reminder_time": ("20:00", "time"),
    "auto_raspred_enabled": ("1", "bool"),
    "auto_raspred_time": ("21:00", "time"),
    "week_reminder_enabled": ("1", "bool"),
    "week_reminder_weekday": ("3", "weekday"),
    "week_reminder_time": ("12:00", "time"),
    "fact_request_enabled": ("1", "bool"),
    "fact_request_delay": ("15", "minutes"),
    # ТЗ 3.3: a reminder before the report deadline, and admins learn who hasn't reported yet
    "report_reminder_enabled": ("1", "bool"),
    "report_reminder_lead": ("120", "lead"),
    "report_digest_enabled": ("1", "bool"),
    "report_digest_time": ("22:00", "time"),
    "report_digest_to": ("admins", "choice:owners|admins|staff"),
    "weekly_report_enabled": ("1", "bool"),
    "weekly_report_time": ("10:00", "time"),
    "backup_enabled": ("1", "bool"),
    "backup_time": ("04:00", "time"),
}


def _parse(kind: str, raw: str):
    if kind == "bool":
        return raw == "1"
    if kind in ("weekday", "minutes", "lead"):
        return int(raw)
    return raw


def get_all(conn: sqlite3.Connection) -> dict:
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_settings")}
    return {k: _parse(kind, stored.get(k, default)) for k, (default, kind) in SCHEMA.items()}


def update(conn: sqlite3.Connection, changes: dict) -> dict:
    rows = []
    for key, value in changes.items():
        if key not in SCHEMA:
            raise ServiceError(f"Неизвестная настройка: {key}", 422, "bad_setting")
        kind = SCHEMA[key][1]
        if kind == "bool":
            if not isinstance(value, bool):
                raise ServiceError("Ожидается да/нет", 422, "bad_setting")
            raw = "1" if value else "0"
        elif kind == "time":
            if not isinstance(value, str) or not TIME_RE.match(value):
                raise ServiceError("Время в формате ЧЧ:ММ", 422, "bad_setting")
            raw = value
        elif kind == "weekday":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
                raise ServiceError("День недели от 0 (пн) до 6 (вс)", 422, "bad_setting")
            raw = str(value)
        elif kind.startswith("choice:"):
            if value not in kind[7:].split("|"):
                raise ServiceError("Недопустимое значение", 422, "bad_setting")
            raw = value
        else:
            top = 720 if kind == "lead" else 240
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
                raise ServiceError(f"Минуты от 0 до {top}", 422, "bad_setting")
            raw = str(value)
        rows.append((key, raw))
    with transaction(conn):
        conn.executemany(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", rows
        )
    return get_all(conn)


# internal values that are not edited from the settings screen (e.g. the archive group set by /archive_here)
def get_raw(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (f"raw:{key}",)).fetchone()
    return row[0] if row else None


def set_raw(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    with transaction(conn):
        if value is None:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (f"raw:{key}",))
        else:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"raw:{key}", value),
            )
