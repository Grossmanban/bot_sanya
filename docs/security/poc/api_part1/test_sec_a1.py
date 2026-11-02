import uuid
from datetime import timedelta, datetime

import pytest
from fastapi.testclient import TestClient

from app import services as svc, events, networks
from app.api import create_app
from app.auth import sign_init_data
from app.config import settings
from app.notify import Notifier
from app.timeutil import today_local, now_local

TOKEN = settings.bot_token


def headers(tg_id):
    return {"X-Telegram-Init-Data": sign_init_data({"id": tg_id, "first_name": "X", "last_name": "Y"}, TOKEN)}


def key():
    return str(uuid.uuid4())


@pytest.fixture
def client(conn):
    return TestClient(create_app(conn, Notifier(None, conn)))


def _victim_with_report(client, conn):
    owner = headers(999)
    client.get("/api/me", headers=owner)
    victim, _ = svc.create_registration(conn, 1001, "vic", "Виктор", "Жертвин")
    svc.decide_registration(conn, victim["id"], True, 999)
    svc.set_work_status(conn, victim, "working", svc.new_key())
    conn.execute("UPDATE users SET personal_rate = 777 WHERE id = ?", (victim["id"],))
    h = headers(1001)
    today = today_local().isoformat()
    sid = client.post("/api/shifts/create", json={"shift_date": today, "start_time": "00:00", "end_time": "00:30",
                                                  "volume": 25, "location": "европа", "idempotency_key": key()},
                      headers=h).json()["shift"]["id"]
    client.patch(f"/api/admin/shifts/{sid}", json={"action": "approve_urgent", "reason": "", "idempotency_key": key()},
                 headers=owner)
    jpeg = b"\xff\xd8\xff\xe0" + b"SECRET" * 10
    r = client.post(f"/api/shifts/{sid}/report?fact_volume=7&idempotency_key={key()}", content=jpeg,
                    headers={**h, "Content-Type": "image/jpeg"})
    assert r.status_code == 200, r.text
    return sid, jpeg


def _attacker_with_seen_key(client, conn):
    att, _ = svc.create_registration(conn, 2002, "att", "Атакующий", "Коллега")
    svc.decide_registration(conn, att["id"], True, 999)
    k = "attacker-own-key-1"
    r = client.patch("/api/me/work-status", json={"work_status": "working", "idempotency_key": k}, headers=headers(2002))
    assert r.status_code == 200
    return k


def test_report_replay_leaks_foreign_shift_and_photo(client, conn):
    sid, jpeg = _victim_with_report(client, conn)
    k = _attacker_with_seen_key(client, conn)
    a = headers(2002)
    # the normal path refuses
    r = client.post(f"/api/shifts/{sid}/report?fact_volume=1&idempotency_key={key()}", content=jpeg,
                    headers={**a, "Content-Type": "image/jpeg"})
    assert r.status_code == 404
    # replaying an own used key returns the victim's shift
    r = client.post(f"/api/shifts/{sid}/report?fact_volume=0&idempotency_key={k}", headers=a)
    assert r.status_code == 200, r.text
    shift = r.json()["shift"]
    print(shift)
    assert shift["fact_volume"] == 7 and shift["rate"] == 777
    photo = shift["report"]["last"]["photo_url"]
    got = client.get(photo)
    assert got.status_code == 200 and got.content == jpeg


@pytest.mark.parametrize("path,method", [("cancel", "post"), ("restore", "post"), ("", "patch")])
def test_shift_mutation_replay_leaks_foreign_shift(client, conn, path, method):
    sid, _ = _victim_with_report(client, conn)
    k = _attacker_with_seen_key(client, conn)
    body = {"start_time": "10:00", "end_time": "11:00", "volume": 1, "location": "x", "idempotency_key": k}
    url = f"/api/shifts/{sid}" + (f"/{path}" if path else "")
    r = getattr(client, method)(url, json=body, headers=headers(2002))
    assert r.status_code == 200, r.text
    assert r.json()["shift"]["id"] == sid and r.json()["shift"]["fact_volume"] == 7


def test_event_entry_replay_reads_foreign_network_event(client, conn):
    client.get("/api/me", headers=headers(999))
    owner = svc.get_user_by_tg(conn, 999)
    net = networks.create(conn, owner, "Секретная сеть", key())
    member, _ = svc.create_registration(conn, 3003, "m", "Член", "Сети")
    svc.decide_registration(conn, member["id"], True, 999)
    conn.execute("UPDATE users SET network_id = ? WHERE id = ?", (net["id"], member["id"]))
    now = now_local()
    ev = events.create(conn, owner, title="Закрытое событие сети", mode="accumulative", starts_at=now - timedelta(hours=1),
                       ends_at=now + timedelta(hours=5), key=key(), network_id=net["id"], prize="10000 руб")
    conn.execute("""INSERT INTO event_entries (event_id, user_id, attempt, value, photo_file_id, submitted_at)
                    VALUES (?, ?, 1, 55, 'x', ?)""", (ev["id"], member["id"], now.isoformat()))
    k = _attacker_with_seen_key(client, conn)
    a = headers(2002)
    assert client.get(f"/api/events/{ev['id']}", headers=a).status_code == 404
    r = client.post(f"/api/events/{ev['id']}/entries?value=1&idempotency_key={k}", headers=a)
    assert r.status_code == 200, r.text
    e = r.json()["event"]
    print(e["title"], e["prize"], e["leaderboard"])
    assert e["title"] == "Закрытое событие сети" and e["leaderboard"][0]["name"] == "Член Сети"


def test_body_parsed_before_auth(client):
    # no initData at all, broken JSON: 422 from the body parser, not 401 — the body was read before auth
    r = client.post("/api/register", content=b"{" * 10, headers={"Content-Type": "application/json"})
    print(r.status_code, r.text[:200])
    assert r.status_code == 422
