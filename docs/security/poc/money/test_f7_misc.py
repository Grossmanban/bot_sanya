"""F7: trainer bonuses need no approval; F8: annul «деньги уже отправлены» loses the overpayment deduction;
F9: a referral bonus that can't be created is lost after the stage is confirmed."""
import pytest

from app import finance, invites, networks, payouts, recruiting
from app import services as svc
from app.timeutil import now_local
from tests.conftest import requisites
from tests.test_rates import WEEK, did
from tests.test_recruiting import FORM


def test_trainer_mints_bonuses_without_approval(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    net = networks.create(conn, owner, "Север", svc.new_key())
    trainer, buddy = make_user(first="Тренер"), make_user(first="Друг")
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    networks.set_member(conn, owner, buddy["id"], net["id"], svc.new_key())
    trainer = svc.get_user(conn, trainer["id"])
    for _ in range(3):
        tx = finance.create(conn, trainer, buddy["id"], "bonus", 1_000_000, "молодец", svc.new_key()).tx
        assert tx["status"] == "active"
    fine = finance.create(conn, trainer, buddy["id"], "fine", None, "опоздал", svc.new_key(), units=1).tx
    assert fine["status"] == "pending_admin_approval"  # fines wait, bonuses do not
    requisites(conn, buddy)
    row = next(r for r in payouts.calculate(conn, WEEK[0], now_local().date(),
                                            payouts.Rates.from_dict(None)) if r["user_id"] == buddy["id"])
    assert row["bonuses"] == 3_000_000 and row["total"] == 3_000_000


def test_annul_money_sent_loses_deduction_above_max(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u, v = make_user(first="Аня"), make_user(first="Боря")
    did(conn, u, WEEK[0], 20)
    did(conn, v, WEEK[0], 20)
    requisites(conn, u, v)
    b = payouts.commit(conn, owner, "Зарплата", WEEK[0], WEEK[0], payouts.Rates.from_dict(None), None,
                       [{"user_id": u["id"], "base_override": 1_500_000}, {"user_id": v["id"]}], [], [], svc.new_key())
    k = svc.new_key()
    with pytest.raises(svc.ServiceError):
        payouts.annul(conn, owner, b["id"], "ошибка", True, k)
    assert payouts.batch(conn, b["id"])["status"] == "annulled"   # committed already
    assert finance.balance(conn, u["id"])["deductions"] == 0         # no «Переплата» deduction for anyone
    assert finance.balance(conn, v["id"])["deductions"] == 0
    again, made = payouts.annul(conn, owner, b["id"], "ошибка", True, k)  # the retry does nothing
    assert made == [] and finance.balance(conn, v["id"])["deductions"] == 0


def test_referral_bonus_lost_when_bonus_cannot_be_created(conn, make_user):
    svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin = make_user(first="Адм")
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (admin["id"],))
    admin = svc.get_user(conn, admin["id"])
    trainer = make_user(first="Тренер")
    assert invites.use(conn, 7001, invites.current_ref(conn, admin))  # the admin invited the candidate
    user, app = recruiting.submit(conn, {"id": 7001, "username": "k"}, FORM)
    day = now_local().date() + __import__("datetime").timedelta(days=1)
    recruiting.assign_training(conn, admin, app["id"], day, "11:00", "Европа", trainer["id"], svc.new_key())
    recruiting.start_internship(conn, admin, app["id"], svc.new_key())
    with pytest.raises(svc.ServiceError) as e:
        recruiting.confirm_stage(conn, admin, app["id"], 1, 20, 3000, svc.new_key())
    assert e.value.code == "self"
    a = recruiting.get(conn, app["id"])
    assert a["status"] == "done" and a["stage1_bonus_tx"] is None    # stage confirmed, no bonus, cannot redo
    with pytest.raises(svc.ServiceError):
        recruiting.confirm_stage(conn, svc.get_user_by_tg(conn, 999), app["id"], 1, 20, 3000, svc.new_key())
