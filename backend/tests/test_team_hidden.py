"""19.11: «Скрыть» a blocked member in «Команда» (`users.hidden_at/hidden_by`, миграция 52). A hidden blocked
member leaves every list — «Команда» keeps the card under «Скрытые» only, `/admin/users` (the pickers) drops
them altogether; the payout wizard never had blocked people, so hidden ones are out of it too. Only a blocked
member can be hidden; unblocking returns the person to the lists on its own."""

import pytest
from fastapi.testclient import TestClient

from app import services as svc
from app.api import create_app

from .test_api import headers
from .test_features import FakeNotifier


def blocked_member(conn, make_user, first="Гоша", last="Скрытов"):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first=first, last=last)
    svc.set_account_state(conn, owner, u["id"], "blocked", "пропал", svc.new_key())
    return owner, u


def test_only_blocked_can_be_hidden_and_unblock_returns(conn, make_user):
    owner, u = blocked_member(conn, make_user)
    live = make_user(first="Жив", last="Один")
    with pytest.raises(svc.ServiceError) as e:  # an active member is not hideable
        svc.set_user_hidden(conn, owner, live["id"], True, svc.new_key())
    assert e.value.code == "not_blocked"

    user, changed = svc.set_user_hidden(conn, owner, u["id"], True, svc.new_key())
    assert changed and user["hidden_at"] and user["hidden_by"] == owner["id"]
    user, changed = svc.set_user_hidden(conn, owner, u["id"], True, svc.new_key())
    assert not changed and user["hidden_at"]  # the second call with the same state — nothing to do

    # unblock returns the person to the lists by itself — no zombie «active but hidden» state
    user, _ = svc.set_account_state(conn, owner, u["id"], "active", None, svc.new_key())
    assert user["hidden_at"] is None


def test_hidden_member_leaves_the_lists_http(conn, make_user):
    owner, u = blocked_member(conn, make_user, "Коля", "Тайнин")
    mate = make_user(first="Жив", last="Один")
    n = FakeNotifier(conn)
    client = TestClient(create_app(conn, n))
    h = headers(999, "Саня", "Владелец", "boss")

    r = client.post(f"/api/admin/team/{u['id']}/hide", json={"on": True, "idempotency_key": "k-hide-1"}, headers=h)
    assert r.status_code == 200 and r.json()["user"]["hidden"] is True
    r = client.post(f"/api/admin/team/{u['id']}/hide", json={"on": True, "idempotency_key": "k-hide-1"}, headers=h)
    assert r.status_code == 200 and r.json()["user"]["hidden"] is True  # idempotent

    team = client.get("/api/admin/team", headers=h).json()["items"]
    assert [(i["id"], i["hidden"]) for i in team if i["id"] == u["id"]] == [(u["id"], True)]  # the card — to «Скрытые»
    assert [(i["id"], i["hidden"]) for i in team if i["id"] == mate["id"]] == [(mate["id"], False)]
    ids = [i["id"] for i in client.get("/api/admin/users", headers=h).json()["items"]]
    assert u["id"] not in ids and mate["id"] in ids  # out of every picker list

    # the payout wizard's people — the same filter it always had: blocked (hidden or not) are out
    people = [p for p in svc.list_users(conn) if p["account_state"] != "blocked"]
    assert u["id"] not in [p["id"] for p in people]

    # a stranger has no say here
    r = client.post(f"/api/admin/team/{u['id']}/hide", json={"on": False, "idempotency_key": "k-x"}, headers=headers(777))
    assert r.status_code == 403

    r = client.post(f"/api/admin/team/{u['id']}/hide", json={"on": False, "idempotency_key": "k-hide-2"}, headers=h)
    assert r.status_code == 200 and r.json()["user"]["hidden"] is False
    ids = [i["id"] for i in client.get("/api/admin/users", headers=h).json()["items"]]
    assert u["id"] in ids  # «Вернуть» — back to the lists
