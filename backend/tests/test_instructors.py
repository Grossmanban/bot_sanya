"""ТЗ 27.10 §22–25: instructors — their people only, their ladder, one shared delta of 150 − 120 = 30 ₽ per piece."""

import pytest

from app import networks, payouts, rates, reports
from app import permissions as perms
from app import services as svc

from .test_rates import WEEK, calc, did, team  # noqa: F401 — the fixture


def instructor(conn, owner, user, people=()):
    svc.set_role(conn, owner, user["id"], "instructor", svc.new_key())
    for p in people:
        networks.set_instructor(conn, owner, p["id"], user["id"], svc.new_key())
    return svc.get_user(conn, user["id"])


def test_scope_assignment_and_return_to_the_trainer(conn, team, make_user):  # noqa: F811
    owner, north = team["owner"], team["north"]["id"]
    olga = make_user(first="Ольга")
    networks.set_member(conn, owner, olga["id"], north, svc.new_key())
    olga = instructor(conn, owner, svc.get_user(conn, olga["id"]), [team["ivan"]])
    ivan, petya = svc.get_user(conn, team["ivan"]["id"]), svc.get_user(conn, team["petya"]["id"])
    assert perms.role_of(olga) == "instructor" and perms.is_staff(olga)
    # the instructor sees only her people; the trainer sees everyone of the network, the instructor too
    assert perms.manages(olga, ivan) and not perms.manages(olga, petya) and not perms.manages(olga, team["masha"])
    assert perms.manages(team["vera"], ivan) and perms.manages(team["vera"], olga)
    assert perms.can(olga, "review", ivan) and not perms.can(olga, "review", petya)
    assert not perms.can(olga, "fine_direct") and perms.can(olga, "fine_request")
    assert olga["telegram_id"] in reports.reviewers(conn, ivan) and olga["telegram_id"] not in reports.reviewers(conn, petya)
    # an instructor from another network, or staff as a «person», can't be assigned
    with pytest.raises(svc.ServiceError) as e:
        networks.set_instructor(conn, owner, team["masha"]["id"], olga["id"], svc.new_key())
    assert e.value.code == "other_network"
    with pytest.raises(svc.ServiceError):
        networks.set_instructor(conn, owner, petya["id"], team["vera"]["id"], svc.new_key())
    # back to the trainer: explicitly, by another network, or when the instructor loses the role
    networks.set_instructor(conn, owner, petya["id"], olga["id"], svc.new_key())
    networks.set_instructor(conn, owner, petya["id"], None, svc.new_key())
    assert svc.get_user(conn, petya["id"])["instructor_id"] is None
    svc.set_role(conn, owner, olga["id"], "employee", svc.new_key())
    assert svc.get_user(conn, ivan["id"])["instructor_id"] is None  # nobody is left without a lead


def test_shared_delta_trainer_first_then_instructor(conn, team, make_user):  # noqa: F811
    owner, north = team["owner"], team["north"]["id"]
    olga = make_user(first="Ольга")
    networks.set_member(conn, owner, olga["id"], north, svc.new_key())
    olga = instructor(conn, owner, svc.get_user(conn, olga["id"]), [team["ivan"]])
    did(conn, team["ivan"], WEEK[0], 40)   # Olga's person
    did(conn, team["petya"], WEEK[0], 60)  # directly under the trainer
    # common rates: trainer 10 ₽, instructor 10 ₽ — both fit into the 30 ₽ delta
    by, _ = calc(conn)
    assert by["Вера"]["commission"] == 100 * 10 and by["Ольга"]["commission"] == 40 * 10
    assert by["Ольга"]["commission_kind"] == "instructor" and by["Вера"]["delta"] == 30
    # the trainer at 25 ₽ leaves 5 ₽ for the instructor, whatever her ladder says
    networks.set_rates(conn, owner, north, {"ladder": [{"from": 0, "rate": 25}],
                                            "instructor_ladder": [{"from": 0, "rate": 20}]}, svc.new_key())
    by, _ = calc(conn)
    assert by["Вера"]["commission"] == 100 * 25 and by["Ольга"]["commission"] == 40 * 5
    day = by["Ольга"]["commission_days"][0]
    assert (day["step_rate"], day["rate"]) == (20, 5)
    # the trainer at 30 ₽ (individual rate 40, capped) → the instructor gets 0 for the same pieces
    rates.set_user_rates(conn, owner, team["vera"]["id"], {"personal_commission": 40}, svc.new_key())
    by, _ = calc(conn)
    assert by["Вера"]["commission"] == 100 * 30 and by["Ольга"]["commission"] == 0
    # the delta comes from the common rate, not from a person's own rate or «налог на лень»
    rates.set_user_rates(conn, owner, team["ivan"]["id"], {"personal_rate": 140}, svc.new_key())
    rates.set_user_rates(conn, owner, team["vera"]["id"], {"personal_commission": 10}, svc.new_key())
    did(conn, team["ivan"], WEEK[1], 5)  # a lazy day
    by, _ = calc(conn)
    assert by["Вера"]["commission"] == 105 * 10 and by["Ольга"]["commission"] == 45 * 20
    # the cap itself is a setting of the payout
    by, _ = calc(conn, piece_cap=130)
    assert by["Вера"]["commission"] == 105 * 10 and by["Ольга"]["commission"] == 0


def test_instructor_ladder_by_week_and_the_payout(conn, team, make_user):  # noqa: F811
    owner, north = team["owner"], team["north"]["id"]
    olga = make_user(first="Ольга")
    networks.set_member(conn, owner, olga["id"], north, svc.new_key())
    olga = instructor(conn, owner, svc.get_user(conn, olga["id"]), [team["ivan"], team["petya"]])
    networks.set_rates(conn, owner, north, {"ladder": [{"from": 0, "rate": 5}], "instructor_ladder_period": "week",
                                            "instructor_ladder": [{"from": 0, "rate": 5}, {"from": 100, "rate": 15}]},
                       svc.new_key())
    did(conn, team["ivan"], WEEK[0], 50)
    did(conn, team["petya"], WEEK[3], 60)  # the week reaches 110 → every piece of the week at 15
    by, _ = calc(conn)
    assert by["Ольга"]["commission"] == 110 * 15 and by["Ольга"]["team_volume"] == 110
    batch = payouts.commit(conn, owner, "Неделя", WEEK[0], WEEK[-1], payouts.Rates.from_dict(None), None,
                           [{"user_id": olga["id"]}], [], [], svc.new_key())
    assert batch["items"][0]["commission"] == 1650 and batch["items"][0]["role"] == "instructor"
