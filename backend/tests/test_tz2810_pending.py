"""ТЗ 28.10, этап 1б: индикаторы «ждут решения», «День» — кто не отметился, шаблоны причин и тексты сообщений."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import networks, pending, recruiting, templates
from app import services as svc
from app.api import create_app
from app.notify import Notifier
from app.timeutil import today_local

from .test_api import headers, key
from .test_features import P, TODAY, shift


def network_with_trainer(conn, make_user, owner):
    net = networks.create(conn, owner, "Север", svc.new_key())
    trainer, mine = make_user(first="Вера"), make_user(first="Свой")
    other = make_user(first="Чужой")
    for p in (trainer, mine):
        networks.set_member(conn, owner, p["id"], net["id"], svc.new_key())
    networks.set_trainer(conn, owner, net["id"], trainer["id"], svc.new_key())
    return svc.get_user(conn, trainer["id"]), svc.get_user(conn, mine["id"]), other


def test_counts_follow_rights_and_scope_and_viewing_changes_nothing(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer, mine, other = network_with_trainer(conn, make_user, owner)
    today = today_local()
    # urgent requests for today: one of the trainer's people, one outside the network
    for u in (mine, other):
        svc.create_shift(conn, u, today, dict(P), svc.new_key(), today=today)
    # a new questionnaire and a pending registration (an owner's invitation)
    cand, _ = svc.create_registration(conn, 5001, None, "Кандидат", "Новый")
    conn.execute("""INSERT INTO applications (user_id, status, full_name, age, metro, min_days, training_days, communication,
                    consent_at, created_at) VALUES (?, 'new', 'Новый Кандидат', 20, 'Сокол', 3, '[0]', 7, datetime('now'),
                    datetime('now'))""", (cand["id"],))
    svc.create_registration(conn, 5002, None, "Заявка", "Обычная")

    o = pending.counts(conn, owner, today)
    assert o["urgent"] == 2 and o["onboarding_new"] == 1 and o["registrations"] == 1  # the candidate is not counted twice
    t = pending.counts(conn, trainer, today)
    assert t["urgent"] == 1  # only their network
    assert t["onboarding_new"] == 0 and t["registrations"] == 0 and t["fines_pending"] == 0  # not theirs to decide (В26)
    assert pending.counts(conn, owner, today) == o  # looking twice changes nothing
    assert o["total"] == sum(o[k] for k in pending.KEYS)
    # deciding closes it
    s = svc.get_user_shift_on(conn, mine["id"], today)
    svc.decide_urgent(conn, s["id"], True, 999)
    assert pending.counts(conn, trainer, today)["urgent"] == 0 and pending.counts(conn, owner, today)["urgent"] == 1


def test_a_candidate_is_decided_only_in_onboarding(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    cand, _ = svc.create_registration(conn, 5001, None, "Кандидат", "Новый")
    conn.execute("""INSERT INTO applications (user_id, status, full_name, age, metro, min_days, training_days, communication,
                    consent_at, created_at) VALUES (?, 'new', 'Новый Кандидат', 20, 'Сокол', 3, '[0]', 7, datetime('now'),
                    datetime('now'))""", (cand["id"],))
    for call in (lambda: svc.set_access(conn, owner, cand["id"], True, svc.new_key()),
                 lambda: svc.decide_registration(conn, cand["id"], True, 999)):
        with pytest.raises(svc.ServiceError) as e:
            call()
        assert e.value.code == "candidate"
    client = TestClient(create_app(conn, Notifier(None, conn)))
    team = client.get("/api/admin/team", headers=headers(999, "Саня", "Владелец", "boss")).json()["items"]
    assert next(u for u in team if u["id"] == cand["id"])["candidate"] is True


def test_reports_count_matches_the_list(conn, make_user):
    """An admin's own report is checked by someone else: it is neither in their list nor in their count."""
    from app import reports

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    admin, u = make_user(first="Админ"), make_user(first="Аня")
    svc.set_role(conn, owner, admin["id"], "admin", svc.new_key())
    admin = svc.get_user(conn, admin["id"])
    from .test_features import report

    for person in (admin, u):
        s = shift(conn, person, payload={**P, "volume": 20})
        report(conn, person, s, 10)
    client = TestClient(create_app(conn, Notifier(None, conn)))
    h = headers(admin["telegram_id"], "Админ", "Петров")
    listed = client.get("/api/admin/reports?kind=review", headers=h).json()["items"]
    assert len(listed) == 1 == client.get("/api/admin/pending", headers=h).json()["review"]
    assert pending.counts(conn, owner)["review"] == 2 and len(reports.queue(conn, "review")) == 2


def test_day_shows_the_unmarked_and_a_manager_marks_them(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    trainer, mine, other = network_with_trainer(conn, make_user, owner)
    tomorrow = today_local() + timedelta(days=1)
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh, th = headers(999, "Саня", "Владелец", "boss"), headers(trainer["telegram_id"], "Вера", "Петров")
    client.get("/api/me", headers=oh)
    day = client.get(f"/api/admin/day?day={tomorrow}", headers=oh).json()
    names = {p["user_id"] for p in day["unmarked"]}
    assert {mine["id"], other["id"], trainer["id"]} <= names and day["can_mark"] is True
    assert pending.counts(conn, owner)["unmarked"] == len(names)
    # a trainer sees and marks only their network
    tday = client.get(f"/api/admin/day?day={tomorrow}", headers=th).json()
    assert other["id"] not in {p["user_id"] for p in tday["unmarked"]}
    r = client.post(f"/api/admin/day/{tomorrow}/mark", json={"user_id": other["id"], "kind": "off", "idempotency_key": key()},
                    headers=th)
    assert r.status_code == 403
    r = client.post(f"/api/admin/day/{tomorrow}/mark", json={"user_id": mine["id"], "kind": "off", "idempotency_key": key()},
                    headers=th)
    assert r.status_code == 200 and r.json()["shift"]["status"] == "pre_declared_off"
    k = key()
    body = {"user_id": other["id"], "kind": "shift", "start_time": "10:00", "end_time": "18:00", "volume": 30,
            "location": "Европа", "idempotency_key": k}
    assert client.post(f"/api/admin/day/{tomorrow}/mark", json=body, headers=oh).json()["changed"] is True
    assert client.post(f"/api/admin/day/{tomorrow}/mark", json=body, headers=oh).json()["changed"] is False  # a double tap
    r = client.post(f"/api/admin/day/{tomorrow}/mark", json={**body, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 409 and r.json()["code"] == "already_exists"
    left = {p["user_id"] for p in client.get(f"/api/admin/day?day={tomorrow}", headers=oh).json()["unmarked"]}
    assert mine["id"] not in left and other["id"] not in left
    past = today_local() - timedelta(days=1)
    r = client.post(f"/api/admin/day/{past}/mark", json={"user_id": trainer["id"], "kind": "off", "idempotency_key": key()},
                    headers=oh)
    assert r.status_code == 409 and r.json()["code"] == "past_date"


def test_unmarked_with_a_violation_counts_once(conn, make_user):
    from app import violations

    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user()
    tomorrow = today_local() + timedelta(days=1)
    before = pending.counts(conn, owner)
    assert before["unmarked"] == 1 and before["violations"] == 0
    violations.scan_unmarked(conn, tomorrow)  # the deadline passed: now it's «Дисциплина»
    after = pending.counts(conn, owner)
    assert after["unmarked"] == 0 and after["violations"] == 1 and after["total"] == before["total"]


def test_an_urgent_request_nobody_decided_expires(conn, make_user):
    u = make_user()
    s = svc.create_shift(conn, u, TODAY, dict(P), svc.new_key(), today=TODAY).shift
    assert s["status"] == "awaiting_approval"
    assert svc.expire_urgent(conn, TODAY) == []
    gone = svc.expire_urgent(conn, TODAY + timedelta(days=1))
    assert [x["id"] for x in gone] == [s["id"]] and gone[0]["status"] == "rejected_by_admin"
    assert svc.expire_urgent(conn, TODAY + timedelta(days=1)) == []  # once


def test_reason_templates_are_seeded_and_editable_by_admins_only(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    worker = make_user()
    assert templates.reasons(conn, "fine")[0] == "Опоздание"  # the presets that lived in the code
    added = templates.add_reason(conn, owner, "block", "Передача аккаунта третьим лицам", svc.new_key())
    assert "Передача аккаунта третьим лицам" in templates.reasons(conn, "block")
    templates.edit_reason(conn, owner, added["id"], "Передача аккаунта", False, svc.new_key())
    templates.edit_reason(conn, owner, conn.execute("SELECT id FROM reason_templates WHERE text = 'Опоздание'").fetchone()[0],
                          None, True, svc.new_key())
    assert "Опоздание" not in templates.reasons(conn, "fine") and "Передача аккаунта" in templates.reasons(conn, "block")
    with pytest.raises(svc.ServiceError):
        templates.add_reason(conn, worker, "fine", "x", svc.new_key())
    with pytest.raises(svc.ServiceError) as e:
        templates.require_reason("   ")
    assert e.value.code == "reason_required"


def test_message_texts_render_and_keep_their_placeholders(conn):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    link = '<a href="https://t.me/vera">Вера Петрова</a>'
    out = templates.render(conn, "candidate_trainer", ФИО_тренера=link)
    assert out.startswith("🎉 Хорошие новости!") and link in out and "48 часов" in out
    with pytest.raises(svc.ServiceError) as e:
        templates.save_text(conn, owner, "candidate_trainer", "Свяжитесь с тренером", svc.new_key())
    assert e.value.code == "placeholder_missing"
    templates.save_text(conn, owner, "candidate_trainer", "Напишите <b>{ФИО_тренера}</b> сегодня", svc.new_key())
    assert templates.render(conn, "candidate_trainer", ФИО_тренера=link) == f"Напишите &lt;b&gt;{link}&lt;/b&gt; сегодня"
    templates.save_text(conn, owner, "candidate_trainer", None, svc.new_key())  # «Вернуть исходный»
    assert templates.text(conn, "candidate_trainer") == templates.TEXTS["candidate_trainer"][1]
    item = next(t for t in templates.text_catalog(conn) if t["key"] == "candidate_trainer")
    assert item["edited"] is False and item["placeholders"] == ["ФИО_тренера"]
