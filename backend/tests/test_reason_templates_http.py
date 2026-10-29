"""«Настройки → Шаблоны причин» over HTTP: add, edit and delete a template (a shadowed request model used to refuse
all three with 422 — «Что-то пошло не так» in the app); annulling an event entry still takes its «reason»."""

from fastapi.testclient import TestClient

from app import services as svc
from app.api import create_app
from app.notify import Notifier

from .test_api import headers, key


def test_add_edit_delete_reason_templates(conn, make_user):
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=oh)

    def items(kinds, kind):
        return next(k for k in kinds if k["kind"] == kind)["items"]

    r = client.post("/api/admin/reasons", json={"kind": "plus_reject", "text": "Чужой аккаунт", "idempotency_key": key()},
                    headers=oh)
    assert r.status_code == 200, r.text
    added = next(i for i in items(r.json()["kinds"], "plus_reject") if i["text"] == "Чужой аккаунт")
    r = client.patch(f"/api/admin/reasons/{added['id']}", json={"text": "Аккаунт не клиента", "idempotency_key": key()},
                     headers=oh)
    assert r.status_code == 200, r.text
    assert any(i["text"] == "Аккаунт не клиента" for i in items(r.json()["kinds"], "plus_reject"))
    r = client.patch(f"/api/admin/reasons/{added['id']}", json={"delete": True, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 200, r.text
    assert all(i["id"] != added["id"] for i in items(r.json()["kinds"], "plus_reject"))
    # an employee can't
    u = make_user()
    r = client.post("/api/admin/reasons", json={"kind": "fine", "text": "x", "idempotency_key": key()},
                    headers=headers(u["telegram_id"]))
    assert r.status_code == 403
    # the other request with «reason» still validates as before (404 — no such entry, not 422)
    r = client.post("/api/admin/event-entries/99999/annul", json={"reason": "Нечестно", "idempotency_key": svc.new_key()},
                    headers=oh)
    assert r.status_code != 422
