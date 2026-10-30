from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import settings

MSK = ZoneInfo("Europe/Moscow")

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WEEKDAY_SHORT_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def now_local() -> datetime:
    return datetime.now(settings.tz)


def today_local() -> date:
    return now_local().date()


def now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_dates(offset: int = 0, today: date | None = None) -> list[date]:
    start = week_start(today or today_local()) + timedelta(weeks=offset)
    return [start + timedelta(days=i) for i in range(7)]


def ddmm(day: date) -> str:
    return day.strftime("%d.%m")


def ddmm_wd(day: date) -> str:
    return f"{ddmm(day)} ({WEEKDAY_SHORT_RU[day.weekday()]})"


def msk_datetime(iso: str | None) -> str | None:
    """ТЗ 30.10 §7: «30.10.2026 в 03:47 МСК» — always Moscow time, whatever the server's zone or the stored offset
    (a value without an offset was written in the project's zone)."""
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=settings.tz)
    return moment.astimezone(MSK).strftime("%d.%m.%Y в %H:%M МСК")
