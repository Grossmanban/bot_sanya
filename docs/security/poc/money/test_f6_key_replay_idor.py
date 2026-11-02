"""F6: idempotency keys are global and the «already seen» branch returns the object by id without an ownership
check: any employee reads anyone's shift (fact, rubles, personal rate, a signed link to the screenshot)."""
from datetime import timedelta

from fastapi.testclient import TestClient
from app.timeutil import today_local

from app import rates
from app import services as svc
from app.api import create_app
from app.notify import Notifier
from tests.test_api import headers, key
from tests.test_features import THU, report, shift


def test_employee_reads_foreign_shift_via_own_old_key(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    victim, spy = make_user(first="Жертва"), make_user(first="Шпион")
    rates.set_user_rates(conn, owner, victim["id"], {"personal_rate": 175}, svc.new_key())  # owner-only data
    s = shift(conn, victim)
    report(conn, victim, s, 33)
    client = TestClient(create_app(conn, Notifier(None, conn)))
    h = headers(spy["telegram_id"], "Шпион")
    my_key = key()
    # any action of the spy that writes an event log with a key will do; booking a day off:
    r = client.post("/api/shifts/pre-declare-off", json={"shift_date": (today_local() + timedelta(days=2)).isoformat(), "idempotency_key": my_key}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/shifts/{s['id']}/report", params={"fact_volume": 1, "idempotency_key": my_key}, headers=h)
    assert r.status_code == 200
    got = r.json()["shift"]
    assert got["id"] == s["id"] and got["fact_volume"] == 33
    assert got["rate"] == 175 and got["fact_pay"] == 33 * 175          # the victim's personal rate
    assert got["report"]["last"]["photo_url"]                          # a signed link to the victim's screenshot
    # the same through the shift edit / cancel endpoints
    r = client.post(f"/api/shifts/{s['id']}/cancel", json={"idempotency_key": my_key}, headers=h)
    assert r.status_code == 200 and r.json()["shift"]["fact_volume"] == 33
