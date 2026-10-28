"""ТЗ 28.10, этап 5: методички — очистка HTML, разделы, версии, доступ, прогресс."""

import base64

from fastapi.testclient import TestClient

from app import manuals
from app import services as svc
from app.api import create_app
from app.notify import Notifier

from .test_api import headers, key

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
HTML = f"""<html><head><style>body{{color:red}}</style><script>alert(1)</script></head><body>
<p>Вступление <b>важно</b></p>
<h2 onclick="steal()">Как встречать</h2><p style="x">Улыбка <a href="javascript:alert(1)">тут</a> и <a href="https://ya.ru">там</a></p>
<img src="https://evil.example/pixel.gif"><img src="data:image/png;base64,{base64.b64encode(PNG).decode()}" alt="схема">
<h2>Что говорить</h2><ul><li>Привет</li><li>Пока</li></ul><iframe src="https://x"></iframe><form><input></form>
</body></html>"""


def setup(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Чтец")
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=oh)
    return owner, u, client, oh, headers(u["telegram_id"])


def test_upload_is_cleaned_and_split_into_sections(conn, make_user):
    owner, u, client, oh, h = setup(conn, make_user)
    m = client.post("/api/admin/manuals", json={"title": "Встреча гостей", "idempotency_key": key()}, headers=oh).json()["manual"]
    r = client.post(f"/api/admin/manuals/{m['id']}/upload?idempotency_key={key()}", content=HTML.encode(), headers=oh).json()["manual"]
    assert [s["title"] for s in r["sections"]] == ["Введение", "Как встречать", "Что говорить"] and r["version"] == 1
    body = "".join(s["html"] for s in r["sections"])
    for bad in ("<script", "alert", "onclick", "style", "javascript:", "evil.example", "<iframe", "<form", "<input"):
        assert bad not in body, bad
    assert '<a href="https://ya.ru" target="_blank" rel="noopener noreferrer nofollow">там</a>' in body
    assert "<li>Привет</li>" in body and "/api/media/manualimg/" in body and 'alt="схема"' in body
    img = body.split('src="')[1].split('"')[0]
    assert client.get(img).status_code == 200  # the signed short-lived link works; a forged one doesn't
    assert client.get(img.replace("sig=", "sig=0")).status_code == 403


def test_access_is_checked_every_time_and_progress_follows_the_content(conn, make_user):
    owner, u, client, oh, h = setup(conn, make_user)
    m = manuals.create(conn, owner, "Касса", svc.new_key())
    v = manuals.save_sections(conn, owner, m["id"], [{"title": "Раз", "html": "<p>один</p>"}, {"title": "Два", "html": "<p>два</p>"}],
                              svc.new_key())
    assert client.get("/api/me", headers=h).json()["manuals"] == 0
    assert client.get(f"/api/manuals/{m['id']}", headers=h).status_code == 403  # not given
    client.post(f"/api/admin/users/{u['id']}/manuals", json={"manual_id": m["id"], "allow": True, "idempotency_key": key()}, headers=oh)
    assert client.get("/api/me", headers=h).json()["manuals"] == 1
    first, second = v["sections"]
    r = client.post(f"/api/manuals/{m['id']}/sections/{first['id']}/read", headers=h).json()["manual"]
    assert r["read"] == 1 and r["total"] == 2
    # the first section is edited, a third added: the edited one is unread again (В34)
    manuals.save_sections(conn, owner, m["id"], [{"id": second["id"], "title": "Два", "html": "<p>два</p>"},
                                                 {"id": first["id"], "title": "Раз", "html": "<p>один, но иначе</p>"},
                                                 {"title": "Три", "html": "<p>три</p>"}], svc.new_key())
    r = client.get(f"/api/manuals/{m['id']}", headers=h).json()["manual"]
    assert [s["title"] for s in r["sections"]] == ["Два", "Раз", "Три"] and r["read"] == 0 and r["total"] == 3
    assert [v["version"] for v in client.get(f"/api/admin/manuals/{m['id']}", headers=oh).json()["manual"]["versions"]] == [2, 1]
    # taken back — the old link stops working at once
    client.post(f"/api/admin/users/{u['id']}/manuals", json={"manual_id": None, "allow": False, "idempotency_key": key()}, headers=oh)
    assert client.get(f"/api/manuals/{m['id']}", headers=h).status_code == 403
    assert client.get("/api/me", headers=h).json()["manuals"] == 0
    # deleted — gone for everyone
    client.post(f"/api/admin/users/{u['id']}/manuals", json={"manual_id": m["id"], "allow": True, "idempotency_key": key()}, headers=oh)
    client.post(f"/api/admin/manuals/{m['id']}/delete", json={"idempotency_key": key()}, headers=oh)
    assert client.get("/api/manuals", headers=h).json()["items"] == []
    assert client.post("/api/admin/manuals", json={"title": "x", "idempotency_key": key()}, headers=h).status_code == 403


def test_the_button_to_another_bot(conn, make_user):
    """ТЗ 28.10 §10: none — no button; hidden — kept but not shown; deleted — gone; only t.me links."""
    owner, u, client, oh, h = setup(conn, make_user)
    assert client.get("/api/me", headers=h).json()["global_link"] is None
    bad = client.put("/api/admin/global-link", json={"title": "Другой бот", "url": "https://evil.example"}, headers=oh)
    assert bad.status_code == 422 and bad.json()["code"] == "bad_link"
    assert client.put("/api/admin/global-link", json={"title": "x", "url": "https://t.me/other_bot"}, headers=h).status_code == 403
    client.put("/api/admin/global-link", json={"title": "Другой бот", "url": "https://t.me/other_bot/app"}, headers=oh)
    assert client.get("/api/me", headers=h).json()["global_link"] == {"title": "Другой бот", "url": "https://t.me/other_bot/app"}
    client.put("/api/admin/global-link", json={"title": "Другой бот", "url": "https://t.me/other_bot/app", "active": False}, headers=oh)
    assert client.get("/api/me", headers=h).json()["global_link"] is None
    assert client.get("/api/admin/global-link", headers=oh).json()["link"]["active"] is False  # kept
    client.delete("/api/admin/global-link", headers=oh)
    assert client.get("/api/admin/global-link", headers=oh).json()["link"] is None
