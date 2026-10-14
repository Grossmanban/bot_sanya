from datetime import date, datetime, timedelta

from .config import settings

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
