import os

os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"
os.environ["ADMIN_IDS"] = "999"
os.environ["RUN_BOT"] = "0"
os.environ["FRONTEND_DIST"] = "/nonexistent"
os.environ["MEDIA_DIR"] = __import__("tempfile").mkdtemp(prefix="media-")

import pytest  # noqa: E402

from app import services as svc  # noqa: E402
from app.db import connect  # noqa: E402


@pytest.fixture(autouse=True)
def schedule_noon(monkeypatch):
    """The schedule rules depend on the clock (22:00 the day before a shift): tests run at noon of the real day,
    so booking «tomorrow» works whenever the suite runs. Tests about the deadline pass `now` themselves."""
    from datetime import datetime, time

    from app.config import settings
    from app.timeutil import now_local

    monkeypatch.setattr(svc, "now_local", lambda: datetime.combine(now_local().date(), time(12), tzinfo=settings.tz))


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def make_user(conn):
    counter = {"n": 1000}

    def make(first="Иван", last="Петров", username="ivan", working=True, approve=True):
        counter["n"] += 1
        user, _ = svc.create_registration(conn, counter["n"], username, first, last)
        if approve:
            user, _ = svc.decide_registration(conn, user["id"], True, 999)
        if working:
            user = svc.set_work_status(conn, user, "working", svc.new_key())
        return user

    return make
