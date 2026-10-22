"""ТЗ №20–22: live editing of goals and events, results taken back, hiding, «Архив», deleting, raspred by date."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import app_settings, events, goals, jobs, lifecycle, networks
from app import services as svc
from app.api import create_app
from app.timeutil import today_local

from .test_api import headers, key
from .test_features import THU, TODAY, FakeNotifier, approved, at, report, run, shift
from .test_violations import person


@pytest.fixture
def team(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    north = networks.create(conn, owner, "Север", svc.new_key())
    south = networks.create(conn, owner, "Юг", svc.new_key())
    trainer = person(conn, make_user, "Тренер", north["id"], "trainer")
    other_trainer = person(conn, make_user, "Чужой", south["id"], "trainer")
    admin = person(conn, make_user, "Админ", None, "admin")
    a = person(conn, make_user, "Аня", north["id"])
    b = person(conn, make_user, "Боря", north["id"])
    return dict(owner=owner, north=north, south=south, trainer=trainer, other=other_trainer, admin=admin, a=a, b=b)


def race(conn, actor, **kw):
    base = dict(title="Гонка", mode="first_to_reach", target=30, max_winners=1, max_attempts=2,
                starts_at=at(THU, "10:00"), ends_at=at(THU, "18:00"), key=svc.new_key())
    return events.create(conn, actor, **{**base, **kw})


def edit(conn, actor, event, now, **changes):
    fresh = events.get(conn, event["id"])
    return events.update(conn, actor, event["id"], fresh["version"], changes, svc.new_key(), now=now)


# ---------------------------------------------------------------- events


def test_event_rights_and_version(conn, team):
    t = team
    mine = race(conn, t["trainer"], network_id=t["north"]["id"])
    company = race(conn, t["owner"])
    before = at(THU, "09:00")
    with pytest.raises(svc.ServiceError) as e:  # a trainer — only their own
        edit(conn, t["trainer"], company, before, title="Моё")
    assert e.value.status == 403
    with pytest.raises(svc.ServiceError):  # …and only inside their network
        edit(conn, t["other"], mine, before, title="Чужое")
    assert edit(conn, t["trainer"], mine, before, title="Гонка севера").changed == ["title"]
    assert edit(conn, t["admin"], mine, before, description="Админ правит любые").changed == ["description"]

    stale = events.get(conn, mine["id"])["version"] - 1
    with pytest.raises(svc.ServiceError) as e:  # optimistic locking: someone saved in between
        events.update(conn, t["owner"], mine["id"], stale, {"title": "Поздно"}, svc.new_key(), now=before)
    assert e.value.code == "stale"


def test_event_critical_changes_and_start(conn, team):
    t = team
    ev = race(conn, t["owner"])
    result = edit(conn, t["owner"], ev, at(THU, "09:00"), title="Гонка до 40", prize_photo="file-1",
                  target=40, prize_type="Подарок", prize="Наушники")
    assert set(result.changed) == {"title", "prize_photo", "target", "prize_type", "prize"}
    assert set(result.critical) == {"target", "prize_type", "prize"}  # title and photo are cosmetic
    assert events.view(conn, result.event)["prize_photo_url"]

    with pytest.raises(svc.ServiceError) as e:  # the start moves only before the event starts
        edit(conn, t["owner"], ev, at(THU, "11:00"), starts_at=at(THU, "12:00"))
    assert e.value.code == "started"
    late = edit(conn, t["owner"], ev, at(THU, "11:00"), ends_at=at(THU, "20:00"))
    assert late.critical == ["ends_at"] and late.event["status"] == "active"

    n = FakeNotifier(conn)
    sent = run(n.event_updated(late))
    to_a = [text for chat, text in n.sent if chat == t["a"]["telegram_id"]]
    assert sent >= 2 and len(to_a) == 1 and "Важное обновление" in to_a[0] and "Финиш" in to_a[0] and "20:00" in to_a[0]


def test_race_with_winners_keeps_target_and_result_can_be_taken_back(conn, team):
    t = team
    ev = race(conn, t["owner"])
    noon = at(THU, "12:00")
    entry, _ = events.submit(conn, t["a"], ev["id"], 31, "p", svc.new_key(), now=noon)
    assert events.review(conn, t["owner"], entry["id"], True, None, svc.new_key()).finished  # 1 winner of 1
    with pytest.raises(svc.ServiceError) as e:
        edit(conn, t["owner"], ev, noon, target=50)
    assert e.value.code == "has_winners"

    with pytest.raises(svc.ServiceError) as e:
        events.annul_entry(conn, t["owner"], entry["id"], "", svc.new_key(), now=noon)
    assert e.value.code == "reason_required"
    back = events.annul_entry(conn, t["owner"], entry["id"], "Скрин из прошлой недели", svc.new_key(), now=noon)
    assert back.changed and back.reopened and back.event["status"] == "active"  # the race goes on
    assert events.leaderboard(conn, back.event)[0]["winner"] is False
    me = events.view(conn, back.event, t["a"], noon)["my"]
    assert me["attempts_left"] == 2 and me["can_submit"]  # a cancelled result doesn't use up an attempt
    assert edit(conn, t["owner"], ev, noon, target=50).critical == ["target"]  # no winners → the target may move

    n = FakeNotifier(conn)
    run(n.entry_annulled(back, t["owner"]))
    assert any("отменён" in text and "Скрин из прошлой недели" in text for chat, text in n.sent if chat == t["a"]["telegram_id"])


def test_shorten_finishes_and_later_finish_restarts(conn, team):
    t = team
    ev = race(conn, t["owner"], mode="accumulative", target=None)
    one, _ = events.submit(conn, t["a"], ev["id"], 10, "p", svc.new_key(), now=at(THU, "11:00"))
    events.review(conn, t["owner"], one["id"], True, None, svc.new_key())
    short = edit(conn, t["owner"], ev, at(THU, "13:00"), ends_at=at(THU, "12:30"))
    assert short.finished and short.event["status"] == "finished"
    assert [r["winner"] for r in events.leaderboard(conn, short.event)] == [True]

    again = edit(conn, t["owner"], ev, at(THU, "13:05"), ends_at=at(THU, "20:00"))
    assert again.restarted and again.event["status"] == "active" and again.event["finished_at"] is None
    assert events.view(conn, again.event, t["b"], at(THU, "14:00"))["my"]["can_submit"]

    # a race whose winners are all known restarts only with more winners
    full = race(conn, t["owner"], title="Полная гонка")
    win, _ = events.submit(conn, t["a"], full["id"], 31, "p", svc.new_key(), now=at(THU, "12:00"))
    events.review(conn, t["owner"], win["id"], True, None, svc.new_key())
    with pytest.raises(svc.ServiceError) as e:
        edit(conn, t["owner"], full, at(THU, "13:00"), ends_at=at(THU, "19:00"))
    assert e.value.code == "race_full"
    assert edit(conn, t["owner"], full, at(THU, "13:00"), ends_at=at(THU, "19:00"), max_winners=2).restarted


def test_hidden_events_archive_and_delete(conn, team):
    t = team
    ev = race(conn, t["owner"], network_id=t["north"]["id"])
    noon = at(THU, "12:00")
    with pytest.raises(svc.ServiceError):  # the trainer didn't create it
        lifecycle.set_visibility(conn, t["trainer"], "event", ev["id"], True, svc.new_key())
    lifecycle.set_visibility(conn, t["owner"], "event", ev["id"], True, svc.new_key())
    assert ev["id"] not in [e["id"] for e in events.visible(conn, t["a"], noon)]
    assert ev["id"] in [e["id"] for e in events.archived(conn, t["a"], noon)]
    with pytest.raises(svc.ServiceError) as e:
        events.submit(conn, t["a"], ev["id"], 31, "p", svc.new_key(), now=noon)
    assert e.value.code == "hidden" and "скрыто администрацией" in e.value.message
    lifecycle.set_visibility(conn, t["owner"], "event", ev["id"], False, svc.new_key())
    assert ev["id"] in [e["id"] for e in events.visible(conn, t["a"], noon)]

    # auto: N days after the finish it leaves the feed by itself; «показать» pins it
    app_settings.update(conn, {"feed_hide_after_days": 2})
    old = race(conn, t["owner"], title="Прошлая")
    run(jobs.tick(conn, FakeNotifier(conn), at(THU, "18:16")))
    assert events.get(conn, old["id"])["status"] == "finished"
    later = at(THU + timedelta(days=2), "12:00")
    assert old["id"] in [e["id"] for e in events.visible(conn, t["a"], later)]
    assert old["id"] not in [e["id"] for e in events.visible(conn, t["a"], later + timedelta(days=1))]
    assert old["id"] in [e["id"] for e in events.archived(conn, t["a"], later + timedelta(days=1))]

    lifecycle.delete(conn, t["owner"], "event", old["id"], svc.new_key())
    assert old["id"] not in [e["id"] for e in events.managed(conn, t["owner"])]
    assert old["id"] not in [e["id"] for e in events.archived(conn, t["a"], later + timedelta(days=1))]
    with pytest.raises(svc.ServiceError):
        events.readable(conn, t["a"], old["id"])
    assert events.get(conn, old["id"])["deleted_at"]  # soft: the history stays


# ---------------------------------------------------------------- goals


def test_goal_edit_lower_raise_revoke_annul(conn, team):
    t = team
    goal = goals.create(conn, t["trainer"], title="Северу 60", kind="network", target=60, date_from=TODAY, date_to=THU,
                        network_id=t["north"]["id"], key=svc.new_key())
    for u in (t["a"], t["b"]):
        goals.join(conn, u, goal["id"], TODAY)
    approved(conn, t["a"], shift(conn, t["a"]), 30)
    approved(conn, t["b"], shift(conn, t["b"]), 25)
    assert goals.check_completions(conn, THU) == []

    with pytest.raises(svc.ServiceError):  # another network's trainer
        goals.update(conn, t["other"], goal["id"], goal["version"], {"target": 50}, svc.new_key())
    up = goals.update(conn, t["trainer"], goal["id"], goal["version"], {"target": 50, "title": "Северу 50"}, svc.new_key())
    assert up.critical == ["target"]
    done = goals.check_completions(conn, THU)  # lowered: 55 ≥ 50 → completed at once
    assert done and goals.get(conn, goal["id"])["status"] == "completed"

    g = goals.get(conn, goal["id"])
    goals.update(conn, t["admin"], goal["id"], g["version"], {"target": 70}, svc.new_key())
    g = goals.get(conn, goal["id"])
    assert g["status"] == "completed"  # raising the norm doesn't take «выполнено» away by itself
    assert goals.view(conn, g)["revocable"] == 2
    g, lost = goals.revoke(conn, t["trainer"], goal["id"], svc.new_key())
    assert g["status"] == "active" and len(lost) == 2
    with pytest.raises(svc.ServiceError) as e:
        goals.revoke(conn, t["trainer"], goal["id"], svc.new_key())
    assert e.value.code == "nothing_to_revoke"
    n = FakeNotifier(conn)
    run(n.goal_revoked(g, lost))
    assert any("подняли до 70" in text and "осталось 15" in text for chat, text in n.sent if chat == t["a"]["telegram_id"])

    goals.annul(conn, t["owner"], goal["id"], t["b"]["id"], "Отчёт с чужого аккаунта", svc.new_key())
    v = goals.view(conn, goals.get(conn, goal["id"]), t["b"])
    assert v["total"] == 30 and v["my_annulled"] == "Отчёт с чужого аккаунта" and not v["joined"]
    assert [p["annulled"] for p in v["participants"]] == [False, True]


def test_goal_feed_archive_and_old_archived_goals(conn, team):
    t = team
    goal = goals.create(conn, t["owner"], title="Всем 10", kind="team", target=10, date_from=TODAY, date_to=THU,
                        key=svc.new_key())
    assert [g["title"] for g in goals.for_user(conn, t["a"], TODAY)] == ["Всем 10"]
    app_settings.update(conn, {"feed_hide_after_days": 3})
    assert goals.for_user(conn, t["a"], THU + timedelta(days=3))  # three days after the end — still in the feed
    assert not goals.for_user(conn, t["a"], THU + timedelta(days=4))
    assert [g["title"] for g in goals.archived(conn, t["a"], THU + timedelta(days=4))] == ["Всем 10"]
    lifecycle.set_visibility(conn, t["owner"], "goal", goal["id"], False, svc.new_key())  # «показать» pins it
    assert goals.for_user(conn, t["a"], THU + timedelta(days=30))
    lifecycle.set_visibility(conn, t["owner"], "goal", goal["id"], True, svc.new_key())
    assert not goals.for_user(conn, t["a"], TODAY)
    with pytest.raises(svc.ServiceError):  # hidden: nobody joins
        goals.join(conn, t["b"], goal["id"], TODAY)
    lifecycle.delete(conn, t["owner"], "goal", goal["id"], svc.new_key())
    assert not goals.archived(conn, t["a"], TODAY) and not goals.listing(conn, t["owner"])


def test_migration_turns_archived_goals_into_hidden(tmp_path):
    import sqlite3

    from app import db

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA)
    for step in db.MIGRATIONS[:16]:
        db._run_script(old, step) if isinstance(step, str) else step(old)
    old.execute("PRAGMA user_version = 16")
    old.execute("""INSERT INTO goals (title, kind, mode, target, date_from, date_to, status, created_by, created_at)
                   VALUES ('Старая', 'team', 'shared', 5, '2026-09-01', '2026-09-30', 'archived', 999, '2026-09-01')""")
    old.commit()
    old.close()
    conn = db.connect(str(path))
    row = conn.execute("SELECT status, visibility, hidden_at, version FROM goals").fetchone()
    assert tuple(row) == ("active", "hidden", "2026-09-01", 1)


# ---------------------------------------------------------------- HTTP


def test_http_edit_hide_archive_and_raspred_by_date(conn, team):
    t = team
    n = FakeNotifier(conn)
    client = TestClient(create_app(conn, n))
    owner, emp = headers(999), headers(t["a"]["telegram_id"])
    start = (today_local() + timedelta(days=1)).isoformat()
    made = client.post("/api/admin/events", headers=owner, json={
        "title": "Завтра гонка", "mode": "first_to_reach", "target": 20, "starts_at": f"{start}T10:00",
        "ends_at": f"{start}T18:00", "prize_type": "Деньги", "prize": "1000 ₽", "idempotency_key": key()}).json()["event"]
    assert made["prize_type"] == "Деньги" and made["version"] == 1

    listing = client.get("/api/admin/events", headers=owner).json()
    assert "Деньги" in listing["prize_types"] and listing["items"][0]["can_manage"]
    patched = client.patch(f"/api/admin/events/{made['id']}", headers=owner, json={
        "version": 1, "notify": True, "ends_at": f"{start}T20:00", "idempotency_key": key()})
    assert patched.status_code == 200 and patched.json()["critical"] == ["ends_at"]
    stale = client.patch(f"/api/admin/events/{made['id']}", headers=owner, json={
        "version": 1, "title": "Поздно", "idempotency_key": key()})
    assert stale.status_code == 409 and "устарели" in stale.json()["detail"]
    forbidden = client.patch(f"/api/admin/events/{made['id']}", headers=headers(t["trainer"]["telegram_id"]),
                             json={"version": 2, "title": "Моё", "idempotency_key": key()})
    assert forbidden.status_code == 403

    hide = client.post(f"/api/admin/events/{made['id']}/visibility", headers=owner,
                       json={"hidden": True, "idempotency_key": key()}).json()["event"]
    assert hide["visibility"] == "hidden" and not hide["in_feed"]
    assert made["id"] not in [e["id"] for e in client.get("/api/events", headers=emp).json()["items"]]
    archive = client.get("/api/archive", headers=emp).json()
    assert [e["id"] for e in archive["events"]] == [made["id"]]
    assert client.get(f"/api/events/{made['id']}", headers=emp).status_code == 200  # opens from «Архив»
    assert client.post(f"/api/admin/events/{made['id']}/delete", headers=owner,
                       json={"idempotency_key": key()}).json() == {"deleted": True}
    assert client.get(f"/api/events/{made['id']}", headers=emp).status_code == 404

    day = (today_local() - timedelta(days=3)).isoformat()
    raspred = client.get(f"/api/admin/raspred?date={day}", headers=owner).json()
    assert raspred["date"] == day and "Распред на" in raspred["text"]
    assert client.get("/api/admin/raspred?date=вчера", headers=owner).status_code == 422
    assert client.get(f"/api/admin/raspred?date={day}", headers=emp).status_code == 403


# ---------------------------------------------------------------- goals: a hand-picked group, a point's daily norm


def test_group_goal_by_checkboxes(conn, team):
    t = team
    with pytest.raises(svc.ServiceError) as e:  # nobody ticked
        goals.create(conn, t["owner"], title="Пусто", kind="group", target=10, date_from=TODAY, date_to=THU,
                     user_ids=[], key=svc.new_key())
    assert e.value.code == "people_required"
    with pytest.raises(svc.ServiceError):  # a trainer — only their own people
        goals.create(conn, t["trainer"], title="Чужие", kind="group", target=10, date_from=TODAY, date_to=THU,
                     user_ids=[t["a"]["id"], t["admin"]["id"]], key=svc.new_key())
    g = goals.create(conn, t["trainer"], title="Аня и Боря", kind="group", mode="each", target=20, date_from=TODAY,
                     date_to=THU, user_ids=[t["a"]["id"], t["b"]["id"]], key=svc.new_key())
    assert {p["user_id"] for p in goals.participants(conn, g["id"])} == {t["a"]["id"], t["b"]["id"]}  # joined at once
    assert [x["title"] for x in goals.for_user(conn, t["a"], TODAY)] == ["Аня и Боря"]
    assert goals.for_user(conn, t["other"], TODAY) == []
    assert lifecycle.can_manage(conn, svc.get_user(conn, t["trainer"]["id"]), "goal", g)
    approved(conn, t["a"], shift(conn, t["a"]), 25)
    done = goals.check_completions(conn, THU)
    assert [p["first_name"] for _, people in done for p in people] == ["Аня"]  # «норма каждому»


def test_point_goal_resets_daily_and_counts_reviews(conn, team):
    from app import reports

    t = team
    svc.create_point(conn, t["owner"], "Европа", None)
    with pytest.raises(svc.ServiceError):  # trainers don't set points' goals
        goals.create(conn, t["trainer"], title="Европа 50", kind="point", target=50, date_from=TODAY,
                     date_to=date.fromisoformat(goals.FOREVER), location="Европа", key=svc.new_key())
    g = goals.create(conn, t["owner"], title="Европа 50", kind="point", target=50, date_from=TODAY,
                     date_to=date.fromisoformat(goals.FOREVER), location="европа", reset_time="06:00", key=svc.new_key())
    assert g["location"] == "Европа" and g["reset_time"] == "06:00"
    sa, sb = shift(conn, t["a"]), shift(conn, t["b"])  # both at «Европа» on THU
    assert goals.goal_day(g, at(THU, "05:59")) == TODAY and goals.goal_day(g, at(THU, "06:00")) == THU
    report(conn, t["a"], sa, 30)  # under review — counts already
    approved(conn, t["b"], sb, 15)
    v = goals.view(conn, g, t["a"], at(THU, "18:00"))
    assert (v["progress"], v["pending"], v["joined"], v["day"]) == (45, 30, True, THU.isoformat())
    assert goals.eligible(conn, g, t["a"], at(THU, "18:00")) and not goals.eligible(conn, g, t["other"], at(THU, "18:00"))
    assert goals.check_completions(conn, THU, at(THU, "18:00")) == []
    # an admin corrects the fact: it counts as corrected
    reports.manual_result(conn, t["owner"], sb["id"], 20, svc.new_key(), comment="Пересчитали")
    done = goals.check_completions(conn, THU, at(THU, "19:00"))
    assert [(x["title"], len(p)) for x, p in done] == [("Европа 50", 2)]
    assert goals.check_completions(conn, THU, at(THU, "19:05")) == []  # once a day
    # the next morning after 06:00 it starts from zero
    assert goals.view(conn, g, None, at(THU + timedelta(days=1), "07:00"))["progress"] == 0
    assert goals.view(conn, g, None, at(THU + timedelta(days=1), "05:00"))["done"]  # before the reset — still Thursday
    upd = goals.update(conn, t["owner"], g["id"], g["version"], {"reset_time": "08:00"}, svc.new_key())
    assert upd.critical == ["reset_time"]
    lifecycle.delete(conn, t["owner"], "goal", g["id"], svc.new_key())
    assert goals.for_user(conn, t["a"], THU) == []


def test_event_wording_formatting_and_video_prize(conn, team):
    """«Гонка до 30 шт., 1 победитель»; the description keeps bold/italic; the prize can be a video."""
    rule = lambda e: events.rule(e).replace("\xa0", " ")  # numbers are glued to their words with a no-break space
    t = team
    one = race(conn, t["owner"], description="<b>Первый</b>, кто сделает 30 отзывов <i>и загрузит</i> скриншот",
               prize_photo="tg-video-prize", prize_media_kind="video")
    assert rule(one) == "Гонка до 30 шт., 1 победитель"
    assert events.how_to_win(one).replace("\xa0", " ") == "Побеждает первый, кто сделает 30 шт. и подтвердит скриншотом"
    three = race(conn, t["owner"], mode="accumulative", target=None, max_winners=3)
    five = race(conn, t["owner"], max_winners=5, target=50)
    assert rule(three) == "Кто больше сделает, 3 победителя"
    assert events.how_to_win(three) == "Побеждают 3 лучших по подтверждённым штукам к финишу"
    assert rule(five) == "Гонка до 50 шт., 5 победителей"
    assert events.winners_text(21) == "21\xa0победитель" and events.winners_text(12) == "12\xa0победителей"

    view = events.view(conn, one)
    assert view["description_html"] == "<b>Первый</b>, кто сделает 30 отзывов <i>и загрузит</i> скриншот"
    assert view["description"] == "Первый, кто сделает 30 отзывов и загрузит скриншот"
    assert view["prize_media_kind"] == "video" and view["rule"] == events.rule(one)
    # an old plain description is shown escaped
    conn.execute("UPDATE events SET description_html = NULL, description = 'a < b' WHERE id = ?", (three["id"],))
    assert events.view(conn, events.get(conn, three["id"]))["description_html"] == "a &lt; b"
    with pytest.raises(svc.ServiceError) as e:
        race(conn, t["owner"], prize_media_kind="gif")
    assert e.value.code == "bad_media"
    with pytest.raises(svc.ServiceError) as e:
        race(conn, t["owner"], description="я" * 1001)
    assert e.value.code == "too_long"

    # a new prize photo replaces the video; only the formatting changed still counts as «description»
    before = at(THU, "09:00")
    assert edit(conn, t["owner"], one, before, prize_photo="tg-photo").changed == ["prize_photo"]
    assert events.get(conn, one["id"])["prize_media_kind"] == "photo"
    assert edit(conn, t["owner"], one, before,
                description="<i>Первый</i>, кто сделает 30 отзывов <i>и загрузит</i> скриншот").changed == ["description"]

    # the announcement: the rule, how to win and the formatted description, the video carries it
    edit(conn, t["owner"], one, before, prize_photo="tg-video-prize", prize_media_kind="video")
    n = FakeNotifier(conn)
    assert run(n.event_created(events.get(conn, one["id"]))) == len(n.sent) > 0  # everyone in the company
    text = n.sent[0][1].replace("\xa0", " ")
    assert "Гонка до 30 шт., 1 победитель" in text and "👑 Побеждает первый" in text and "<i>Первый</i>" in text
    assert set(n.kinds) == {"video"}
