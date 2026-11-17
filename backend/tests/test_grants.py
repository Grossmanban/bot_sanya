"""18.11: личные разрешения из «Команды» — рассылка всем (тренеру — вне своей сети) и онбординг без админа."""

import pytest
from fastapi.testclient import TestClient

from app import networks
from app import permissions as perms
from app import services as svc
from app.api import create_app
from app.notify import Notifier

from .test_api import headers, key
from .test_tz2810_access import Recorder


def setup_world(conn, make_user):
    """Two networks; the trainer leads the North, three more people are spread over both."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    north = networks.create(conn, owner, "Север", svc.new_key())
    south = networks.create(conn, owner, "Юг", svc.new_key())
    trainer = make_user(first="Вера", username=None)
    mine, theirs, plain = make_user(first="Мой"), make_user(first="Чужой"), make_user(first="Иван")
    for person, net in ((trainer, north), (mine, north), (theirs, south), (plain, south)):
        networks.set_member(conn, owner, person["id"], net["id"], svc.new_key())
    networks.set_trainer(conn, owner, north["id"], trainer["id"], svc.new_key())
    return owner, trainer, mine, theirs, plain, north, south


def client_of(conn):
    return TestClient(create_app(conn, Notifier(Recorder(), conn)))


def grant(conn, owner, user_id, **flags):
    svc.set_grants(conn, owner, user_id, flags.get("broadcast_all"), flags.get("onboarding"), svc.new_key())


# ---------------------------------------------------------------- the toggles themselves


def test_grants_change_only_what_is_sent(conn, make_user):
    owner, trainer, mine, theirs, plain, north, south = setup_world(conn, make_user)
    user, flags, changed = svc.set_grants(conn, owner, plain["id"], True, None, key())
    assert changed and flags == {"broadcast_all": True, "onboarding": False}
    assert svc.grants_of(svc.get_user(conn, plain["id"])) == flags
    # the same key again — idempotent
    _, flags2, changed2 = svc.set_grants(conn, owner, plain["id"], None, True, key())
    assert changed2 and flags2 == {"broadcast_all": True, "onboarding": True}  # broadcast_all untouched
    with pytest.raises(svc.ServiceError) as e:
        svc.set_grants(conn, owner, plain["id"], None, None, svc.new_key())
    assert e.value.code == "empty"


def test_only_owners_and_admins_flip_the_toggles(conn, make_user):
    owner, trainer, mine, theirs, plain, north, south = setup_world(conn, make_user)
    client = client_of(conn)
    th = headers(trainer["telegram_id"], "Вера", "Петров", None)
    r = client.post(f"/api/admin/team/{mine['id']}/grants",
                    json={"broadcast_all": True, "idempotency_key": key()}, headers=th)
    assert r.status_code == 403
    # the owner over HTTP
    r = client.post(f"/api/admin/team/{mine['id']}/grants",
                    json={"broadcast_all": True, "onboarding": True, "idempotency_key": key()}, headers=headers(999))
    assert r.status_code == 200, r.text
    assert r.json()["grants"] == {"broadcast_all": True, "onboarding": True}
    assert r.json()["user"]["grants"] == {"broadcast_all": True, "onboarding": True}
    # the team list carries the flags for the switches
    item = next(u for u in client.get("/api/admin/team", headers=headers(999)).json()["items"] if u["id"] == mine["id"])
    assert item["grants"] == {"broadcast_all": True, "onboarding": True}
    # an owner needs no grant; a pending person can't get one
    with pytest.raises(svc.ServiceError) as e:
        svc.set_grants(conn, owner, owner["id"], True, None, svc.new_key())
    assert e.value.code == "owner"
    newbie, _ = svc.create_registration(conn, 777, "new", "Новый", "Человек")
    with pytest.raises(svc.ServiceError) as e:
        svc.set_grants(conn, owner, newbie["id"], True, None, svc.new_key())
    assert e.value.code == "not_found"
    with pytest.raises(svc.ServiceError) as e:
        svc.set_grants(conn, owner, mine["id"], None, None, svc.new_key())
    assert e.value.code == "empty"


# ---------------------------------------------------------------- the broadcast grant


def test_employee_broadcasts_to_everyone_with_the_grant(conn, make_user):
    owner, trainer, mine, theirs, plain, north, south = setup_world(conn, make_user)
    client = client_of(conn)
    ph = headers(plain["telegram_id"], "Иван", "Петров", "ivan")
    # before: no admin panel, no capabilities
    me = client.get("/api/me", headers=ph).json()
    assert me["capabilities"] == [] and me["user"]["grants"] == {"broadcast_all": False, "onboarding": False}
    assert client.get("/api/admin/broadcasts", headers=ph).status_code == 403

    grant(conn, owner, plain["id"], broadcast_all=True)
    me = client.get("/api/me", headers=ph).json()
    assert set(me["capabilities"]) >= {"broadcast_all", "broadcast_network"}
    view = client.get("/api/admin/broadcasts", headers=ph).json()
    assert view["scope"] == "all"
    assert {p["id"] for p in view["people"]} == {owner["id"], trainer["id"], mine["id"], theirs["id"], plain["id"]}
    # §13: an employee never signs as the administration — always by name, like a trainer
    assert view["signer"]["optional"] is False and "Сообщение от сотрудника" in view["signer"]["text"]

    r = client.post("/api/admin/broadcasts", json={"audiences": ["working"], "html": "Всем привет", "signed": False,
                                                   "idempotency_key": key()}, headers=ph)
    assert r.status_code == 200, r.text
    # the whole team, both networks — the fence is down (the owner's account is «not working» by default)
    assert r.json()["broadcast"]["recipients"] == 4
    assert "Сообщение от сотрудника" in r.json()["broadcast"]["signature"]


def test_trainer_broadcasts_outside_their_network_with_the_grant(conn, make_user):
    owner, trainer, mine, theirs, plain, north, south = setup_world(conn, make_user)
    client = client_of(conn)
    th = headers(trainer["telegram_id"], "Вера", "Петров", None)
    body = {"audiences": [], "html": "Югу привет", "signed": True, "network_id": south["id"], "idempotency_key": key()}
    assert client.post("/api/admin/broadcasts", json=body, headers=th).status_code == 403  # only their own network

    grant(conn, owner, trainer["id"], broadcast_all=True)
    r = client.post("/api/admin/broadcasts", json=body, headers=th)
    assert r.status_code == 200, r.text
    assert r.json()["broadcast"]["recipients"] == 2  # theirs + plain of the South
    view = client.get("/api/admin/broadcasts", headers=th).json()
    assert view["scope"] == "all" and {p["id"] for p in view["people"]} >= {theirs["id"], plain["id"]}
    assert [n["id"] for n in view["networks"]] == sorted((north["id"], south["id"]))


def test_without_the_grant_the_old_fences_hold(conn, make_user):
    owner, trainer, mine, theirs, plain, north, south = setup_world(conn, make_user)
    # the trainer reaches only their network in the recipient resolution (not themselves)
    people = svc.broadcast_recipients(conn, ["working"], actor=svc.get_user(conn, trainer["id"]))
    assert {u["id"] for u in people} == {mine["id"]}
    # an employee with the onboarding grant (no broadcast one) still broadcasts to nobody new
    grant(conn, owner, plain["id"], onboarding=True)
    assert svc.broadcast_recipients(conn, ["working"], actor=svc.get_user(conn, plain["id"])) == []


# ---------------------------------------------------------------- the onboarding grant


def test_onboarding_without_an_admin_with_the_grant(conn, make_user):
    owner, trainer, mine, theirs, plain, north, south = setup_world(conn, make_user)
    client = client_of(conn)
    ph = headers(plain["telegram_id"], "Иван", "Петров", "ivan")
    assert client.get("/api/admin/onboarding", headers=ph).status_code == 403

    grant(conn, owner, plain["id"], onboarding=True)
    assert client.get("/api/admin/onboarding", headers=ph).status_code == 200
    me = client.get("/api/me", headers=ph).json()
    assert "registrations" in me["capabilities"]
    # the questionnaire and training-request cards also reach the grant holder
    assert svc.onboarding_telegram_ids(conn) == sorted([999, plain["telegram_id"]])


def test_a_grant_holder_decides_a_registration_in_the_bot(conn, make_user):
    owner, trainer, mine, theirs, plain, north, south = setup_world(conn, make_user)
    grant(conn, owner, plain["id"], onboarding=True)
    actor = svc.get_user_by_tg(conn, plain["telegram_id"])
    assert perms.can(actor, "registrations")  # what bot.allowed() checks for the «reg:» buttons


def test_grants_do_not_manage_anyone_and_die_with_the_account(conn, make_user):
    owner, trainer, mine, theirs, plain, north, south = setup_world(conn, make_user)
    grant(conn, owner, plain["id"], broadcast_all=True, onboarding=True)
    actor = svc.get_user(conn, plain["id"])
    # a granted employee manages nobody — not even their own network neighbours
    assert not perms.manages(actor, svc.get_user(conn, mine["id"])) and not perms.manages(actor, actor)
    assert not perms.sees_everyone(actor)
    # the team list still shows only themselves
    client = client_of(conn)
    items = client.get("/api/admin/team", headers=headers(plain["telegram_id"], "Иван", "Петров", "ivan")).json()["items"]
    assert [u["id"] for u in items] == [plain["id"]]
    # a blocked account loses everything, flag or not
    svc.set_account_state(conn, owner, plain["id"], "blocked", "проверка", svc.new_key())
    actor = svc.get_user(conn, plain["id"])
    assert not perms.is_staff(actor) and perms.capabilities(actor) == []
    assert svc.onboarding_telegram_ids(conn) == [999]
