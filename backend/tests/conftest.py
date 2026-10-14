import os

os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN"
os.environ["ADMIN_IDS"] = "999"
os.environ["RUN_BOT"] = "0"
os.environ["FRONTEND_DIST"] = "/nonexistent"

import pytest  # noqa: E402

from app import services as svc  # noqa: E402
from app.db import connect  # noqa: E402


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
