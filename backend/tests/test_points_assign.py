"""19.11: «Надбавка за точку» + «Распределение по точкам» — ступени и дни недели, авто-бонус при принятии
отчёта (одна на смену, аннулирование, пересчёт при исправлении факта), право распределения (владелец, админ,
тренер, инструктор, личный грант), запрет самостоятельно выбирать точку, уведомление «Отправить график»,
шаблоны двух видов; регресс: надбавка — бонус и расчёт выплат не меняет; смена «время уточняется» нигде не ломает."""

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import app_settings, assign, broadcasts, export, finance, jobs, networks, payouts, reports
from app import permissions as perms
from app import services as svc
from app.api import create_app
from app.notify import Notifier

from .test_api import headers, key
from .test_features import FakeNotifier, P, THU, TODAY, at, report
from .test_tz2810_access import Recorder
from .test_violations import person

STEPS = [{"from": 28, "amount": 1000}, {"from": 21, "amount": 500}]  # unsorted on purpose
SORTED = [{"from": 21, "amount": 500}, {"from": 28, "amount": 1000}]
ALLOWANCE_LINE = "Надбавка на точке: от 21 оплач. → 500 ₽, от 28 оплач. → 1000 ₽"


def world(conn, make_user):
    """Owner; the network «Север» — trainer Вера, instructor Игорь with his person Оля, employees Иван and
    Петя; «Юг» — Гоша; Маша without a network."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    north = networks.create(conn, owner, "Север", svc.new_key())
    south = networks.create(conn, owner, "Юг", svc.new_key())
    vera = person(conn, make_user, "Вера", network=north["id"], role="trainer")
    igor = person(conn, make_user, "Игорь", network=north["id"], role="instructor")
    ivan = person(conn, make_user, "Иван", network=north["id"])
    petya = person(conn, make_user, "Петя", network=north["id"])
    olga = person(conn, make_user, "Оля", network=north["id"])
    gosha = person(conn, make_user, "Гоша", network=south["id"])
    masha = person(conn, make_user, "Маша")
    networks.set_trainer(conn, owner, north["id"], vera["id"], svc.new_key())
    networks.set_instructor(conn, owner, olga["id"], igor["id"], svc.new_key())
    return {"owner": owner, "vera": svc.get_user(conn, vera["id"]), "igor": svc.get_user(conn, igor["id"]),
            **{k: svc.get_user(conn, u["id"]) for k, u in (("ivan", ivan), ("petya", petya), ("olga", olga),
                                                            ("gosha", gosha), ("masha", masha))}}


def tomorrow() -> object:
    return svc.now_local().date() + timedelta(days=1)


def client_of(conn):
    return TestClient(create_app(conn, Notifier(Recorder(), conn)))


def europa(conn, owner, days=(THU.weekday(),), steps=STEPS, capacity=None):
    return svc.create_point(conn, owner, "Европа", capacity,
                            {"tiers": [dict(t) for t in steps], "days": list(days)} if steps else None)


def point(conn, name):
    return next(p for p in svc.list_points(conn, include_inactive=True) if p["name"] == name)


def shift(conn, user, day, location="Европа"):
    return svc.create_shift(conn, user, day, {**P, "location": location}, svc.new_key(),
                            today=day - timedelta(days=1)).shift


def bonuses(conn, shift_id):
    return finance.linked(conn, shift_id, ("location_bonus",))


# ---------------------------------------------------------------- A. the allowance: steps, days, validation


def test_allowance_steps_are_validated_sorted_and_cleared(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    made = svc.create_point(conn, owner, "Европа", 5, {"tiers": [dict(t) for t in STEPS], "days": [2, 0, 2]})
    assert svc.public_point(made)["bonus"] == {"tiers": SORTED, "days": [0, 2]}  # sorted by «от», weekdays deduped
    assert svc.public_point(svc.create_point(conn, owner, "Азия", None))["bonus"] == {"tiers": [], "days": []}
    for bad in ({"tiers": [{"from": n, "amount": 100} for n in range(1, 7)], "days": []},  # six steps
                {"tiers": [{"from": 0, "amount": 100}], "days": []},                      # a threshold from 0
                {"tiers": [{"from": 21, "amount": 0}], "days": []},                       # a zero sum
                {"tiers": [{"from": 21, "amount": 100}, {"from": 21, "amount": 200}], "days": []},  # one «от» twice
                {"tiers": [{"from": 21, "amount": 100}], "days": [7]},                    # no eighth weekday
                {"tiers": [{"from": True, "amount": 100}], "days": []}, "мусор", {"tiers": "нет", "days": []}):
        with pytest.raises(svc.ServiceError) as e:
            svc.update_point(conn, owner, made["id"], {"bonus": bad})
        assert e.value.code == "bad_bonus"
    assert point(conn, "Европа")["bonus"] == made["bonus"]  # a refused change changes nothing
    # no steps — no allowance, whatever the days say
    svc.update_point(conn, owner, made["id"], {"bonus": {"tiers": [], "days": [1]}})
    assert point(conn, "Европа")["bonus"] is None
    # set again, then cleared altogether with an explicit null
    svc.update_point(conn, owner, made["id"], {"bonus": {"tiers": [dict(t) for t in STEPS], "days": [2]}})
    assert svc.parse_point_bonus(point(conn, "Европа")["bonus"]) == {"tiers": SORTED, "days": [2]}
    svc.update_point(conn, owner, made["id"], {"bonus": None})
    assert point(conn, "Европа")["bonus"] is None
    # leaving the key out doesn't touch it: the name changes, the allowance stays
    svc.update_point(conn, owner, made["id"], {"bonus": {"tiers": [{"from": 21, "amount": 500}], "days": [1]}})
    svc.update_point(conn, owner, made["id"], {"name": "Европа-2"})
    assert svc.parse_point_bonus(point(conn, "Европа-2")["bonus"]) == {"tiers": [{"from": 21, "amount": 500}], "days": [1]}


def test_the_reached_step_pays_in_full_and_only_on_its_weekdays():
    bonus = {"tiers": SORTED, "days": [0, 2]}
    assert svc.bonus_tier(bonus, 20, 2) is None                 # below the first step
    assert svc.bonus_tier(bonus, 21, 2) == {"from": 21, "amount": 500}
    assert svc.bonus_tier(bonus, 27, 2) == {"from": 21, "amount": 500}
    assert svc.bonus_tier(bonus, 28, 2) == {"from": 28, "amount": 1000}  # 1000, never 500 + 1000
    assert svc.bonus_tier(bonus, 40, 2) == {"from": 28, "amount": 1000}
    assert svc.bonus_tier(bonus, 40, 1) is None                 # Tuesday — the allowance doesn't act
    assert svc.bonus_tier({"tiers": [], "days": [2]}, 40, 2) is None
    # broken stored data reads as «no allowance» instead of failing a page
    assert svc.parse_point_bonus("не json") == {"tiers": [], "days": []}
    assert svc.parse_point_bonus('{"tiers": [{"from": "много", "amount": 5}], "days": [9]}')["tiers"] == []


def test_points_carry_the_allowance_over_http(conn, make_user):
    w = world(conn, make_user)
    client = client_of(conn)
    r = client.post("/api/admin/points", headers=headers(999),
                    json={"name": "Европа", "bonus": {"tiers": [{"from": 21, "amount": 500}], "days": [0, 1]}})
    assert r.status_code == 200, r.text
    assert r.json()["point"]["bonus"] == {"tiers": [{"from": 21, "amount": 500}], "days": [0, 1]}
    pid = r.json()["point"]["id"]
    assert client.post("/api/admin/points", headers=headers(w["vera"]["telegram_id"]),
                       json={"name": "Чужая", "bonus": {"tiers": [{"from": 21, "amount": 500}], "days": [0]}}
                       ).status_code == 403  # the allowance is the owner's and the admins' to edit
    # clearing it with an explicit null
    r = client.patch(f"/api/admin/points/{pid}", headers=headers(999), json={"bonus": None})
    assert r.status_code == 200 and r.json()["point"]["bonus"] == {"tiers": [], "days": []}
    r = client.patch(f"/api/admin/points/{pid}", headers=headers(999),
                     json={"bonus": {"tiers": [{"from": 21, "amount": 500}], "days": [2]}})
    assert r.json()["point"]["bonus"]["tiers"] == [{"from": 21, "amount": 500}]
    # the person's own list carries it too — for the badges in the shift form
    mine = client.get("/api/points", headers=headers(w["ivan"]["telegram_id"], "Иван", "Петров", "ivan")).json()["items"]
    assert mine[0]["name"] == "Европа" and mine[0]["bonus"] == {"tiers": [{"from": 21, "amount": 500}], "days": [2]}


# ---------------------------------------------------------------- A. the auto-bonus on approval


def test_an_approved_report_on_an_allowance_point_pays_the_reached_step_once(conn, make_user):
    w = world(conn, make_user)
    owner, ivan = w["owner"], w["ivan"]
    europa(conn, owner)
    s = shift(conn, ivan, THU)
    report(conn, ivan, s, 28)
    assert bonuses(conn, s["id"]) == []  # a report waiting for review pays nothing
    assert reports.review(conn, owner, s["id"], "approve", key()).changed
    tx = bonuses(conn, s["id"])
    assert len(tx) == 1
    assert (tx[0]["user_id"], tx[0]["type"], tx[0]["amount"], tx[0]["status"], tx[0]["source"]) == (
        ivan["id"], "bonus", 1000, "active", "location_bonus")
    assert tx[0]["reason"] == "Надбавка за точку «Европа»" and tx[0]["event_date"] == THU.isoformat()
    assert finance.balance(conn, ivan["id"])["bonuses"] == 1000
    # a repeated approval and the same fact entered by hand change nothing — one per shift
    assert reports.review(conn, owner, s["id"], "approve", key()).changed is False
    reports.manual_result(conn, owner, s["id"], 28, key())
    assert [t["id"] for t in bonuses(conn, s["id"])] == [tx[0]["id"]]
    assert finance.balance(conn, ivan["id"])["bonuses"] == 1000
    # annulled like any bonus
    finance.cancel(conn, owner, tx[0]["id"], "Не положено", key())
    assert finance.get(conn, tx[0]["id"])["status"] == "cancelled" and bonuses(conn, s["id"]) == []
    assert finance.balance(conn, ivan["id"])["bonuses"] == 0


def test_a_corrected_fact_moves_the_bonus_but_never_revives_an_annulled_one(conn, make_user):
    w = world(conn, make_user)
    owner, ivan = w["owner"], w["ivan"]
    europa(conn, owner)
    s = shift(conn, ivan, THU)
    report(conn, ivan, s, 25)
    reports.review(conn, owner, s["id"], "approve", key())
    first = bonuses(conn, s["id"])[0]
    assert first["amount"] == 500
    # 20 — the first step is no longer reached: the bonus is annulled
    reports.manual_result(conn, owner, s["id"], 20, key())
    assert bonuses(conn, s["id"]) == []
    gone = finance.get(conn, first["id"])
    assert gone["status"] == "cancelled" and gone["decision_comment"] == assign.CORRECTED
    # 28 — the top step: made anew, because the correction (not a person) had taken the old one away
    reports.manual_result(conn, owner, s["id"], 28, key())
    assert [t["amount"] for t in bonuses(conn, s["id"])] == [1000]
    # the same figure again — left alone
    again = bonuses(conn, s["id"])[0]["id"]
    reports.manual_result(conn, owner, s["id"], 28, key())
    assert [t["id"] for t in bonuses(conn, s["id"])] == [again]
    # annulled by the owner — a later correction doesn't bring it back
    finance.cancel(conn, owner, again, "Не положено", key())
    reports.manual_result(conn, owner, s["id"], 25, key())
    assert bonuses(conn, s["id"]) == []
    # where nothing was reached (20), a correction up to 25 makes the bonus for the first time
    other = shift(conn, w["petya"], THU)
    report(conn, w["petya"], other, 20)
    reports.review(conn, owner, other["id"], "approve", key())
    assert bonuses(conn, other["id"]) == []
    reports.manual_result(conn, owner, other["id"], 25, key())
    assert [t["amount"] for t in bonuses(conn, other["id"])] == [500]


def test_allowance_needs_its_weekday_the_paid_pieces_and_a_paid_shift(conn, make_user):
    w = world(conn, make_user)
    owner, ivan, petya, masha = w["owner"], w["ivan"], w["petya"], w["masha"]
    europa(conn, owner)                                   # acts on Wednesdays (THU is one)
    svc.create_point(conn, owner, "Азия", None)           # no allowance
    # below the first step
    s = shift(conn, ivan, THU)
    report(conn, ivan, s, 20)
    reports.review(conn, owner, s["id"], "approve", key())
    assert bonuses(conn, s["id"]) == []
    # another weekday (the report is sent that day in the evening, as always)
    thu = shift(conn, petya, THU + timedelta(days=1))
    report(conn, petya, thu, 30, now=at(THU + timedelta(days=1), "19:30"))
    reports.review(conn, owner, thu["id"], "approve", key())
    assert bonuses(conn, thu["id"]) == []
    # a point without an allowance
    asia = shift(conn, masha, THU, location="Азия")
    report(conn, masha, asia, 30)
    reports.review(conn, owner, asia["id"], "approve", key())
    assert bonuses(conn, asia["id"]) == []
    # a trainee's shift isn't paid — and earns no allowance
    next_week = THU + timedelta(days=7)
    study = shift(conn, ivan, next_week)
    conn.execute("UPDATE shifts SET training = 1 WHERE id = ?", (study["id"],))
    report(conn, ivan, study, 30, now=at(next_week, "19:30"))
    reports.review(conn, owner, study["id"], "approve", key())
    assert bonuses(conn, study["id"]) == []
    # a fact entered by hand for the first time (a no-show) is an approval too
    silent = shift(conn, ivan, THU + timedelta(days=14))
    reports.manual_result(conn, owner, silent["id"], 28, key())
    assert [t["amount"] for t in bonuses(conn, silent["id"])] == [1000]
    # a returned report pays nothing
    later = THU + timedelta(days=21)
    draft = shift(conn, ivan, later)
    report(conn, ivan, draft, 28, now=at(later, "19:30"))
    reports.review(conn, owner, draft["id"], "return", key(), comment="Скрин обрезан")
    assert bonuses(conn, draft["id"]) == []


def test_the_shift_view_carries_the_allowance_plate(conn, make_user):
    w = world(conn, make_user)
    owner, ivan, masha = w["owner"], w["ivan"], w["masha"]
    europa(conn, owner)
    svc.create_point(conn, owner, "Азия", None)
    view = svc.serialize_shift(shift(conn, ivan, THU), TODAY, conn)
    assert view["location_bonus"] == {
        "point": "Европа", "tiers": SORTED, "days": [THU.weekday()], "weekday_ok": True,
        "paid_now": 0, "next": {"from": 21, "amount": 500}, "reached": None}
    report(conn, ivan, svc.get_shift(conn, view["id"]), 25)
    plate = svc.serialize_shift(svc.get_shift(conn, view["id"]), TODAY, conn)["location_bonus"]
    assert (plate["paid_now"], plate["reached"]["amount"], plate["next"]["amount"]) == (25, 500, 1000)
    # the day isn't one of the allowance's — said honestly
    other_day = svc.serialize_shift(shift(conn, ivan, THU + timedelta(days=1)), TODAY, conn)["location_bonus"]
    assert other_day["weekday_ok"] is False and other_day["tiers"] == SORTED
    # no allowance — no plate
    assert "location_bonus" not in svc.serialize_shift(shift(conn, masha, THU, location="Азия"), TODAY, conn)


def test_the_allowance_is_a_bonus_and_never_touches_the_payout_math(conn, make_user):
    w = world(conn, make_user)
    owner, ivan = w["owner"], w["ivan"]
    europa(conn, owner)
    s = shift(conn, ivan, THU)
    report(conn, ivan, s, 28)
    reports.review(conn, owner, s["id"], "approve", key())
    rates = payouts.Rates.from_dict(None)

    def row(amount=None):
        rows = payouts.calculate(conn, THU, THU, rates, None, {ivan["id"]: amount} if amount else None,
                                 {ivan["id"]} if amount else None)
        return next(r for r in rows if r["user_id"] == ivan["id"])

    auto = row()  # the legacy count from the shifts: pieces × the rate, as before
    assert auto["pieces_pay"] == 28 * 120 and auto["lazy_units"] == 0 and auto["entered_amount"] is None
    assert (auto["bonuses"], auto["fines"], auto["deductions"], auto["total"]) == (1000, 0, 0, 3360 + 1000)
    hand = row(2400)  # the manual one: the entered sum is the pay, the bonus comes on top
    assert (hand["entered_amount"], hand["pieces_pay"], hand["lazy_units"]) == (2400, 2400, 0)
    assert (hand["bonuses"], hand["fines"], hand["deductions"], hand["total"]) == (1000, 0, 0, 3400)
    assert hand["rate_base"] == 120  # the person's rate is not a coefficient of the allowance
    # once annulled the bonus is out of the payout, as any bonus
    finance.cancel(conn, owner, bonuses(conn, s["id"])[0]["id"], "Не положено", key())
    assert row(2400)["total"] == 2400


# ---------------------------------------------------------------- B. who may distribute whom


def test_who_may_distribute_whom(conn, make_user):
    w = world(conn, make_user)
    owner = w["owner"]
    admin_id = person(conn, make_user, "Админ")["id"]
    svc.set_role(conn, owner, admin_id, "admin", key())
    w["admin"] = svc.get_user(conn, admin_id)  # re-read: the role is in the row
    fresh = lambda name: svc.get_user(conn, w[name]["id"])  # noqa: E731  (roles are re-read from the base)
    may = assign.may_assign
    staff = ("ivan", "petya", "olga", "gosha", "masha")
    # owners and admins — every employee
    for boss in ("owner", "admin"):
        assert all(may(conn, fresh(boss), fresh(t)) for t in staff), boss
    vera = fresh("vera")
    # the trainer — the employees of their own network: not another network, not an instructor, not themselves
    assert may(conn, vera, fresh("ivan")) and may(conn, vera, fresh("olga"))
    assert not any(may(conn, vera, fresh(t)) for t in ("gosha", "masha", "igor", "vera"))
    # the instructor — only the people assigned to them
    igor = fresh("igor")
    assert may(conn, igor, fresh("olga")) and not may(conn, igor, fresh("ivan")) and not may(conn, igor, fresh("gosha"))
    # a plain employee without the grant — nobody
    ivan = fresh("ivan")
    assert not any(may(conn, ivan, fresh(t)) for t in ("petya", "olga", "gosha", "masha", "vera")) and not perms.is_staff(ivan)
    # the grant «Распределение по всем точкам»: any employee from anywhere, and the panel opens
    svc.set_grants(conn, owner, ivan["id"], None, None, key(), True)
    ivan = fresh("ivan")
    assert perms.is_staff(ivan) and "assign_points" in perms.capabilities(ivan)
    assert ivan["can_assign_points"] == 1 and svc.grants_of(ivan)["assign_points"] is True
    assert all(may(conn, ivan, fresh(t)) for t in ("petya", "olga", "gosha", "masha"))
    assert not may(conn, ivan, fresh("vera")) and not may(conn, ivan, fresh("igor"))  # managers stay undistributed
    # blocked people are nobody's targets
    svc.set_account_state(conn, owner, w["gosha"]["id"], "blocked", "пропал", key())
    assert not may(conn, owner, fresh("gosha")) and not may(conn, ivan, fresh("gosha"))

    client = client_of(conn)
    url, params = "/api/admin/assign", {"day": tomorrow().isoformat()}
    ids = lambda tg: {i["user_id"] for i in client.get(url, params=params, headers=headers(tg)).json()["items"]}  # noqa: E731
    mine = lambda *names: {w[n]["id"] for n in names}  # noqa: E731
    assert ids(999) == mine("ivan", "petya", "olga", "masha")  # Гоша is blocked; managers aren't listed
    assert ids(w["vera"]["telegram_id"]) == mine("ivan", "petya", "olga")
    assert ids(w["igor"]["telegram_id"]) == mine("olga")
    denied = client.get(url, params=params, headers=headers(w["masha"]["telegram_id"]))
    assert denied.status_code == 403  # a plain employee without the grant: no section
    # the server checks the right for every person, whatever the client sends
    body = lambda uid: {"user_id": uid, "date": tomorrow().isoformat(), "location": "Европа", "idempotency_key": key()}  # noqa: E731
    europa(conn, owner)
    assert client.post("/api/admin/assign", json=body(w["gosha"]["id"]),
                       headers=headers(w["vera"]["telegram_id"])).status_code == 403  # another network
    assert client.post("/api/admin/assign", json=body(w["vera"]["id"]),
                       headers=headers(w["igor"]["telegram_id"])).status_code == 403  # a manager is no target
    assert client.post("/api/admin/assign", json=body(w["petya"]["id"]),
                       headers=headers(w["masha"]["telegram_id"])).status_code == 403  # no grant
    # the grant holder reaches another network's person
    done = client.post("/api/admin/assign", json=body(w["masha"]["id"]), headers=headers(w["ivan"]["telegram_id"]))
    assert done.status_code == 200 and done.json()["changed"] is True
    assert svc.get_user_shift_on(conn, w["masha"]["id"], tomorrow())["location"] == "Европа"


def test_assignment_creates_fills_and_replaces_the_point(conn, make_user):
    w = world(conn, make_user)
    owner, vera, ivan, petya, masha, olga = w["owner"], w["vera"], w["ivan"], w["petya"], w["masha"], w["olga"]
    europa(conn, owner, capacity=1)
    svc.create_point(conn, owner, "Азия", None)
    day, k = tomorrow(), key()
    # no record — a shift with the point alone: no time, no volume yet
    made = assign.assign(conn, vera, ivan["id"], day, "европа", k)  # the name in any case
    assert made["location"] == "Европа" and made["changed"] is True
    s = svc.get_user_shift_on(conn, ivan["id"], day)
    assert s["id"] == made["shift_id"] and s["status"] == "active"
    assert (s["start_time"], s["end_time"], s["volume"], s["training"]) == (None, None, None, 0)
    # the same key again — nothing twice
    assert assign.assign(conn, vera, ivan["id"], day, "Азия", k)["changed"] is False
    assert svc.get_user_shift_on(conn, ivan["id"], day)["location"] == "Европа"
    # another point replaces it; the same one — nothing to change
    assert assign.assign(conn, vera, ivan["id"], day, "Азия", key())["changed"] is True
    assert assign.assign(conn, vera, ivan["id"], day, "Азия", key())["changed"] is False
    assert svc.get_user_shift_on(conn, ivan["id"], day)["location"] == "Азия"
    # «no point» takes it away
    assert assign.assign(conn, vera, ivan["id"], day, None, key())["changed"] is True
    assert svc.get_user_shift_on(conn, ivan["id"], day)["location"] is None
    # a shift of the person's own gets its point, whatever else it has
    own = shift(conn, petya, day)
    conn.execute("UPDATE shifts SET location = NULL WHERE id = ?", (own["id"],))
    assert assign.assign(conn, vera, petya["id"], day, "Азия", key())["changed"] is True
    filled = svc.get_user_shift_on(conn, petya["id"], day)
    assert (filled["id"], filled["location"], filled["start_time"], filled["volume"]) == (own["id"], "Азия", "16:00", 25)
    # the day's view: who is where
    view = assign.day(conn, vera, day)
    rows = {i["name"].split()[1]: i for i in view["items"]}
    assert rows["Иван"]["location"] is None and rows["Петя"]["location"] == "Азия" and rows["Оля"]["shift_id"] is None
    assert [p["name"] for p in view["points"]] == ["Азия", "Европа"]
    # the points' capacity is respected as always
    assign.assign(conn, vera, ivan["id"], day, "Европа", key())
    with pytest.raises(svc.ServiceError) as e:
        assign.assign(conn, vera, olga["id"], day, "Европа", key())
    assert e.value.code == "point_full"
    # a day off and a cancelled shift are changed in «День», not here; yesterday is gone
    svc.pre_declare_off(conn, masha, day, key())
    with pytest.raises(svc.ServiceError) as e:
        assign.assign(conn, owner, masha["id"], day, "Азия", key())
    assert e.value.code == "day_off"
    gone = shift(conn, olga, day, location="Азия")
    svc.cancel_shift(conn, olga, gone["id"], key(), today=day - timedelta(days=1))
    with pytest.raises(svc.ServiceError) as e:
        assign.assign(conn, vera, olga["id"], day, "Европа", key())
    assert e.value.code == "cancelled"
    with pytest.raises(svc.ServiceError) as e:
        assign.assign(conn, owner, ivan["id"], day - timedelta(days=2), "Азия", key())  # the day before yesterday
    assert e.value.code == "past_date"
    # today is still fine — and an unknown point is refused
    today = svc.now_local().date()
    assert assign.assign(conn, vera, w["olga"]["id"], today, "Азия", key())["changed"] is True
    with pytest.raises(svc.ServiceError) as e:
        assign.assign(conn, vera, ivan["id"], day, "Нигде", key())
    assert e.value.code == "unknown_point"
    with pytest.raises(svc.ServiceError) as e:
        assign.assign(conn, vera, w["gosha"]["id"], day, "Азия", key())
    assert e.value.status == 403


def test_a_shift_without_time_is_safe_everywhere(conn, make_user):
    w = world(conn, make_user)
    owner, vera, ivan = w["owner"], w["vera"], w["ivan"]
    europa(conn, owner)
    day = tomorrow()
    conn.execute("UPDATE users SET username = NULL WHERE id = ?", (ivan["id"],))  # the lines below are without a handle
    assign.assign(conn, vera, ivan["id"], day, "Европа", key())
    listed = svc.raspred(conn, day, vera)
    assert listed["total_volume"] == 0 and listed["rows"][0]["volume"] is None
    assert "время уточняется — Петров Иван — Европа" in listed["text"] and "None" not in listed["text"]
    svc.stats(conn, ivan["id"], day, day)              # the person's totals
    svc.week_view(conn, ivan, 0)                       # the week with colleagues
    svc.history(conn, ivan, 4)
    export.build_xlsx(conn, day, day)                  # the Excel report
    assign.day(conn, vera, day)
    svc.raspred(conn, day)["text"]
    rec = FakeNotifier(conn)
    asyncio.run(jobs.eve_reminders(conn, rec, day))    # the eve reminder
    assert "время уточняется" in rec.sent[0][1] and "None" not in rec.sent[0][1] and "план" not in rec.sent[0][1]
    # the next day still works for reports: the fact may be entered only once the time is known
    s = svc.get_user_shift_on(conn, ivan["id"], day)
    with pytest.raises(svc.ServiceError) as e:
        reports.manual_result(conn, owner, s["id"], 20, key())
    assert e.value.code == "bad_status"  # «Смена не состоялась» — no time means no shift to report on yet


def test_employees_may_be_kept_from_choosing_a_point(conn, make_user):
    w = world(conn, make_user)
    owner, vera, ivan, petya = w["owner"], w["vera"], w["ivan"], w["petya"]
    europa(conn, owner)
    svc.create_point(conn, owner, "Азия", None)
    client, day = client_of(conn), tomorrow()
    mine = headers(ivan["telegram_id"], "Иван", "Петров", "ivan")
    hers = headers(vera["telegram_id"], "Вера", "Петров", "vera")
    body = lambda location, **over: {"shift_date": day.isoformat(), "start_time": "16:00", "end_time": "19:00",  # noqa: E731
                                     "volume": 20, "location": location, "idempotency_key": key(), **over}
    locked = lambda tg: client.get("/api/me", headers=tg).json()["pricing"]["point_locked"]  # noqa: E731
    assert locked(mine) is False
    r = client.patch("/api/admin/settings", headers=headers(999), json={"shift_point_locked": True})
    assert r.status_code == 200, r.text
    assert locked(mine) is True
    assert locked(hers) is False  # managers aren't bound
    # the person books the day without a point; naming one is refused
    r = client.post("/api/shifts/create", headers=mine, json=body(None))
    assert r.status_code == 200, r.text
    assert r.json()["shift"]["location"] is None
    # naming one is refused — on another day (one record per day, as always)
    r = client.post("/api/shifts/create", headers=mine, json=body("Европа", shift_date=(day + timedelta(days=1)).isoformat()))
    assert r.status_code == 403 and r.json()["code"] == "point_locked"
    assert "точку назначит" in r.json()["detail"]
    assert svc.get_user_shift_on(conn, ivan["id"], day + timedelta(days=1)) is None  # and nothing was created
    # the notice to the managers about a shift without a point goes out intact
    n = FakeNotifier(conn)
    asyncio.run(n.shift_event("SHIFT_CREATED", svc.get_user_shift_on(conn, ivan["id"], day)["id"]))
    assert any("Точка: не назначена" in text for _, text in n.sent)
    # a trainer books with any point, as before
    r = client.post("/api/shifts/create", headers=hers, json=body("Европа"))
    assert r.status_code == 200 and r.json()["shift"]["location"] == "Европа"
    # the person keeps what was assigned: the time changes, the point doesn't
    sid = svc.get_user_shift_on(conn, ivan["id"], day)["id"]
    assign.assign(conn, vera, ivan["id"], day, "Европа", key())
    # naming another point is refused
    with pytest.raises(svc.ServiceError) as e:
        svc.edit_shift(conn, ivan, sid, {**P, "location": "Азия"}, key())
    assert e.value.code == "point_locked"
    edited = svc.edit_shift(conn, ivan, sid, {**P, "start_time": "17:00", "location": "Европа"}, key())
    assert edited.changed and edited.shift["start_time"] == "17:00" and edited.shift["location"] == "Европа"
    # the time changes with no point named at all (the person may not even see the list) — the assigned one stays
    kept = svc.edit_shift(conn, ivan, sid, {**P, "location": ""}, key())
    assert kept.changed and kept.shift["start_time"] == "16:00" and kept.shift["location"] == "Европа"
    # «Передумал, выйду» follows the same rule
    svc.cancel_shift(conn, ivan, sid, key())
    with pytest.raises(svc.ServiceError) as e:
        svc.restore_shift(conn, ivan, sid, {**P, "location": "Азия"}, key())
    assert e.value.code == "point_locked"
    assert svc.restore_shift(conn, ivan, sid, {**P, "location": ""}, key()).shift["location"] == "Европа"
    # off again: a shift without a point is refused as it always was
    client.patch("/api/admin/settings", headers=headers(999), json={"shift_point_locked": False})
    assert locked(mine) is False
    r = client.post("/api/shifts/create", headers=headers(petya["telegram_id"], "Петя", "Петров", "petya"),
                    json=body(None))
    assert r.status_code == 422 and r.json()["code"] == "bad_location"
    r = client.post("/api/shifts/create", headers=headers(petya["telegram_id"], "Петя", "Петров", "petya"),
                    json=body("Европа"))
    assert r.status_code == 200 and r.json()["shift"]["location"] == "Европа"


# ---------------------------------------------------------------- D. the schedule notice and its templates


TPL = ("<p>Привет, {имя}! График на {дата}. Твоя точка: <b>{точка}</b>. Коллеги: {коллеги}. "
       "{надбавка} От: {отправитель}</p>")


def test_the_schedule_notice_is_personal_per_person(conn, make_user):
    w = world(conn, make_user)
    owner, vera, ivan, petya, gosha = w["owner"], w["vera"], w["ivan"], w["petya"], w["gosha"]
    day = tomorrow()
    europa(conn, owner, days=(day.weekday(),))
    svc.create_point(conn, owner, "Азия", None)
    conn.execute("UPDATE users SET username = NULL WHERE id IN (?, ?)", (ivan["id"], petya["id"]))  # no handles in the lines
    shift(conn, ivan, day)                                # 16:00–19:00, 25 шт. — Европа
    assign.assign(conn, vera, petya["id"], day, "Европа", key())   # the point alone — Европа
    shift(conn, gosha, day, location="Азия")             # another network — not Vera's to announce

    texts = lambda actor, mode: {m["user_id"]: m["html"] for m in assign.render(conn, actor, day, TPL, mode)}  # noqa: E731
    mine = texts(vera, "all")
    assert set(mine) == {ivan["id"], petya["id"]}
    assert "Привет, Иван!" in mine[ivan["id"]] and "<b>Европа</b>" in mine[ivan["id"]]
    assert f"График на {day.strftime('%d.%m')} ({assign.WEEKDAYS[day.weekday()]})" in mine[ivan["id"]]
    assert "время уточняется — Петров Петя — Европа" in mine[ivan["id"]]       # the colleague's line
    assert "16:00–19:00 — Петров Иван — 25 шт. — Европа" in mine[petya["id"]]  # and his own view of the other
    assert ALLOWANCE_LINE in mine[ivan["id"]] and ALLOWANCE_LINE in mine[petya["id"]]
    assert f"От: {svc.full_name(vera)}" in mine[ivan["id"]]
    # «только своя точка»: the same point's colleagues only
    close = texts(vera, "point")
    assert set(close) == {ivan["id"], petya["id"]} and "Петров Иван" in close[petya["id"]]
    # the owner sees the other network too; in «своя точка» Гоша is alone on «Азия» — and gets no allowance line
    everyone = texts(owner, "all")
    assert set(everyone) == {ivan["id"], petya["id"], gosha["id"]}
    assert "<b>Азия</b>" in everyone[gosha["id"]] and ALLOWANCE_LINE not in everyone[gosha["id"]]
    # …and in «своя точка» Гоша of the other network drops out of the owner's picture too
    owner_close = texts(owner, "point")
    assert "Петров Иван" in owner_close[petya["id"]] and "Петров Гоша" not in owner_close[petya["id"]]
    assert "Коллеги: —." in owner_close[gosha["id"]]
    # names go in escaped — a surname can't break the markup
    conn.execute("UPDATE users SET last_name = 'Ба<b>ев' WHERE id = ?", (ivan["id"],))
    assert "Ба&lt;b&gt;ев Иван" in texts(owner, "all")[petya["id"]]
    with pytest.raises(svc.ServiceError) as e:
        assign.render(conn, vera, day, TPL, "свои")
    assert e.value.code == "bad_mode"

    # the sending: one registry row, a personal text each, receipts as usual
    notifier = FakeNotifier(conn)
    k = key()
    row, recipients, bodies, repeated = assign.send(conn, vera, day, TPL, "all", "подпись", k)
    assert not repeated and (row["audience"], row["recipients"], len(recipients), len(bodies)) == (
        f"assign:{day.isoformat()}:all", 2, 2, 2)
    assert row["author_id"] == vera["telegram_id"] and row["signature"] == "подпись" and row["require_ack"] == 0
    assert svc.list_broadcasts(conn, author_tg=vera["telegram_id"])[0]["id"] == row["id"]
    assert asyncio.run(notifier.assign_broadcast(row["id"], recipients, bodies, row["signature"])) == 2
    sent = {chat: text for chat, text in notifier.sent}
    assert set(sent) == {u["telegram_id"] for u in recipients}
    assert sent[ivan["telegram_id"]].startswith("подпись\n\nПривет, Иван!") and sent[petya["telegram_id"]].count("Петя!") == 1
    receipts = svc.broadcast_deliveries(conn, row["id"])
    assert len(receipts) == 2 and all(r["delivered"] == 1 for r in receipts)
    # the same key again — the row made before, nothing sent twice
    again = assign.send(conn, vera, day, TPL, "all", "подпись", k)
    assert again[3] is True and again[0]["id"] == row["id"] and again[1] == [] and again[2] == {}
    assert len(svc.list_broadcasts(conn)) == 1
    # nothing to announce, an empty text, no right
    with pytest.raises(svc.ServiceError) as e:
        assign.send(conn, vera, day + timedelta(days=20), TPL, "all", None, key())
    assert e.value.code == "nobody"
    with pytest.raises(svc.ServiceError) as e:
        assign.send(conn, vera, day, "<p>   </p>", "all", None, key())
    assert e.value.code == "empty"
    with pytest.raises(svc.ServiceError) as e:
        assign.send(conn, ivan, day, TPL, "all", None, key())
    assert e.value.status == 403


def test_a_notice_too_long_after_the_substitutes_is_refused_whole(conn, make_user):
    """The template itself fits the limit, the substitutes push it over; such a message would silently not be
    delivered — the sending is refused with the person named, nothing is recorded."""
    w = world(conn, make_user)
    owner, ivan = w["owner"], w["ivan"]
    day = tomorrow()
    europa(conn, owner, days=(day.weekday(),))
    shift(conn, ivan, day)
    # the allowance line, the name and the point add about 80 characters to what the template has
    tail = " {надбавка} {имя} {точка}</p>"
    too_long = "<p>" + "а" * 3950 + tail
    assert len(too_long) < assign.MAX_MESSAGE  # the template itself passes the common length check…
    with pytest.raises(svc.ServiceError) as e:
        assign.send(conn, owner, day, too_long, "all", None, key())
    assert e.value.code == "too_long" and "Петров Иван" in e.value.message  # …the substitutes push it over
    assert svc.list_broadcasts(conn) == []
    # the same text with room for the substitutes goes out
    fits = "<p>" + "а" * 3700 + tail
    assert len(assign.send(conn, owner, day, fits, "all", None, key())[2]) == 1


def test_the_notice_goes_over_http_by_button_only(conn, make_user):
    w = world(conn, make_user)
    owner, vera, ivan = w["owner"], w["vera"], w["ivan"]
    day = tomorrow()
    europa(conn, owner)
    client = client_of(conn)
    hers = headers(vera["telegram_id"], "Вера", "Петров", "vera")
    # assigning a point sends nothing by itself
    assign.assign(conn, vera, ivan["id"], day, "Европа", key())
    assert svc.list_broadcasts(conn) == []
    k = key()
    body = {"date": day.isoformat(), "html": TPL, "mode": "point", "signed": True, "idempotency_key": k}
    r = client.post("/api/admin/assign/notice", headers=hers, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["repeated"] is False and r.json()["broadcast"]["audience"].endswith(":point")
    # the same request again is safe
    r = client.post("/api/admin/assign/notice", headers=hers, json=body)
    assert r.status_code == 200 and r.json()["repeated"] is True
    assert len(svc.list_broadcasts(conn)) == 1
    assert client.post("/api/admin/assign/notice", headers=headers(ivan["telegram_id"], "Иван", "Петров", "ivan"),
                       json={**body, "idempotency_key": key()}).status_code == 403  # a plain employee doesn't send


def test_templates_are_split_by_kind(conn, make_user):
    w = world(conn, make_user)
    seeded = broadcasts.templates(conn, "assign")
    assert [t["title"] for t in seeded] == ["График на день"]
    assert sorted(assign.ASSIGN_VARIABLES) == ["дата", "имя", "коллеги", "надбавка", "отправитель", "точка"]
    # the default text shows the variables of the day (the sender's name goes by the signature rules)
    assert all("{" + v + "}" in seeded[0]["html"] for v in ("имя", "дата", "точка", "коллеги", "надбавка"))
    assert broadcasts.templates(conn) == []  # the broadcast list doesn't show the schedule texts
    saved = broadcasts.save_template(conn, w["vera"], "Мой график", "<p>{точка}</p>", kind="assign")
    assert broadcasts.kind_of(conn, saved["id"]) == "assign"
    assert broadcasts.save_template(conn, w["vera"], "Сбор", "Завтра сбор").get("kind", "broadcast") == "broadcast"
    assert [t["title"] for t in broadcasts.templates(conn, "assign")] == ["График на день", "Мой график"]
    assert [t["title"] for t in broadcasts.templates(conn)] == ["Сбор"]
    client = client_of(conn)
    hers = headers(w["vera"]["telegram_id"], "Вера", "Петров", "vera")
    r = client.get("/api/admin/broadcast-templates", params={"kind": "assign"}, headers=hers)
    assert r.status_code == 200 and len(r.json()["items"]) == 2
    assert client.get("/api/admin/broadcast-templates", params={"kind": "мусор"}, headers=hers).status_code == 422
    # the texts of the schedule are for whoever distributes points; a plain employee has neither
    plain = headers(w["masha"]["telegram_id"], "Маша", "Петров", "masha")
    assert client.get("/api/admin/broadcast-templates", params={"kind": "assign"}, headers=plain).status_code == 403
    assert client.post("/api/admin/broadcast-templates", headers=plain,
                       json={"title": "х", "html": "y", "kind": "assign"}).status_code == 403
    # the grant opens the schedule texts — and only them
    svc.set_grants(conn, w["owner"], w["masha"]["id"], None, None, key(), True)
    assert client.get("/api/admin/broadcast-templates", params={"kind": "assign"}, headers=plain).status_code == 200
    assert client.get("/api/admin/broadcast-templates", headers=plain).status_code == 403
    r = client.post("/api/admin/broadcast-templates", headers=plain,
                    json={"title": "Мой", "html": "<p>{точка}</p>", "kind": "assign"})
    assert r.status_code == 200 and r.json()["template"]["title"] == "Мой"
    # a schedule template is changed and deleted under the schedule's right, never the broadcast's
    tid = r.json()["template"]["id"]
    assert client.post(f"/api/admin/broadcast-templates/{tid}/delete", headers=hers).status_code == 403  # not the author
    assert client.post(f"/api/admin/broadcast-templates/{tid}/delete", headers=headers(999)).status_code == 200
