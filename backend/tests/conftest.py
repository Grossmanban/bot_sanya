import os

os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"
os.environ["ADMIN_IDS"] = "999"
os.environ["RUN_BOT"] = "0"
os.environ["FRONTEND_DIST"] = "/nonexistent"
os.environ["MEDIA_DIR"] = __import__("tempfile").mkdtemp(prefix="media-")

import pytest  # noqa: E402

from app import services as svc  # noqa: E402
from app import products  # noqa: E402
from app.db import connect  # noqa: E402

# 18.11: in production «Алиса»/«Яндекс Плюс» are switched off (products.PRODUCTS_ENABLED = False). The suite
# exercises their mechanics with the flag on (set once at import, no fixture-order pitfalls); the off state is
# checked in test_products_disabled.py, whose `off` fixture monkeypatches the flag back to False.
products.PRODUCTS_ENABLED = True


@pytest.fixture(autouse=True)
def schedule_noon(monkeypatch):
    """The schedule rules depend on the clock (22:00 the day before a shift): tests run at noon of the real day,
    so booking «tomorrow» works whenever the suite runs. Tests about the deadline pass `now` themselves.
    Event timestamps (`svc.log_event` → `svc.now_iso`) follow the same noon — otherwise a suite run between
    midnight and noon MSK fails recency checks («напомнили 5 минут назад»: a 00:xx real stamp is «older» than noon)."""
    from datetime import datetime, time

    from app.config import settings
    from app.timeutil import now_local

    monkeypatch.setattr(svc, "now_local", lambda: datetime.combine(now_local().date(), time(12), tzinfo=settings.tz))
    monkeypatch.setattr(svc, "now_iso", lambda: svc.now_local().isoformat(timespec="seconds"))


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
            # 18.11: an employee can't switch their own status — tests set the state directly
            conn.execute("UPDATE users SET work_status = 'working', work_status_at = ?, notify_reminders = 1 WHERE id = ?",
                         (svc.now_iso(), user["id"]))
            user = svc.get_user(conn, user["id"])
        return user

    return make


def requisites(conn, *users):
    """Approved payment details (ТЗ 28.10 §7: payouts go only to approved ones)."""
    for u in users:
        conn.execute("""INSERT OR IGNORE INTO payment_profiles (user_id, phone, bank, recipient, updated_at, updated_by)
                        VALUES (?, '+79000000000', 'Сбербанк', 'Тест Тестов', datetime('now'), 999)""", (u["id"],))
