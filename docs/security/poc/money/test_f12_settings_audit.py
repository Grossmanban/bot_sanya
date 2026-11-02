"""F12: common rates (rate_base, alice_rate, plus_rate, piece_cap, revenue_per_piece…) change without any audit row."""
from fastapi.testclient import TestClient

from app import services as svc
from app.api import create_app
from app.notify import Notifier
from tests.test_api import headers


def test_rate_change_leaves_no_trace(conn):
    svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    client = TestClient(create_app(conn, Notifier(None, conn)))
    before = conn.execute("SELECT COUNT(*) FROM event_logs").fetchone()[0]
    r = client.patch("/api/admin/settings", json={"rate_base": 90, "alice_rate": 10, "plus_rate": 5},
                     headers=headers(999, "Саня", "Владелец", "boss"))
    assert r.status_code == 200 and r.json()["settings"]["rate_base"] == 90
    after = conn.execute("SELECT COUNT(*) FROM event_logs").fetchone()[0]
    assert after == before  # nobody can tell later who changed the rates, when, and from what
