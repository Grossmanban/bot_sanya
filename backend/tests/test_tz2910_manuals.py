"""ТЗ 29.10 §3–4: настоящие HTML-страницы загружаются (meta/link больше не съедают текст), оформление сохраняется
безопасно, кодировки, понятные ошибки, сохранение из визуального редактора — та же методичка."""

import base64
import re

import pytest
from fastapi.testclient import TestClient

from app import manuals
from app import services as svc
from app.api import create_app
from app.notify import Notifier

from .test_api import headers, key

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PNG64 = base64.b64encode(PNG).decode()

PAGE = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://cdn.example/x.css">
  <link rel="icon" href="/favicon.ico"/>
  <title>Методичка продавца</title>
  <style>
    @import url("https://fonts.example/f.css");
    body {{ font-family: Georgia, serif; background: #fffaf0; }}
    .card {{ border: 1px solid #ddd; padding: 12px; background: url("https://evil.example/t.png"); }}
    .hero {{ background-image: url("data:image/png;base64,{PNG64}"); }}
    .x {{ width: expression(alert(1)); }}
  </style>
  <script>alert("head")</script>
</head>
<body class="theme-warm" style="color: #333" onload="steal()">
  <div class="container">
    <h1 style="text-align: center; color: #b00">Методичка продавца</h1>
    <p>Вводный текст <span style="color: red; behavior: url(x.htc)">важно</span>.</p>
    <section class="card">
      <h2 id="greet" onclick="x()">Как встречать</h2>
      <p align="center">Улыбка и <a href="javascript:alert(1)">тут</a>, <a href="https://ya.ru">там</a>, <a href="#rules">к правилам</a>.</p>
      <input type="text" placeholder="не форма">
      <p>После поля ввода текст не теряется.</p>
      <img src="https://evil.example/pixel.gif"><img src="data:image/png;base64,{PNG64}" alt="схема" width="200">
    </section>
    <section class="card">
      <h2 id="rules">Правила</h2>
      <table><tr><th colspan="2">Шаг</th></tr><tr><td>1</td><td><font color="green" size="5">Поздороваться</font></td></tr></table>
      <iframe src="https://x"></iframe><svg><text>svg-текст</text></svg>
    </section>
  </div>
</body>
</html>"""


def setup(conn):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    client = TestClient(create_app(conn, Notifier(None, conn)))
    oh = headers(999, "Саня", "Владелец", "boss")
    client.get("/api/me", headers=oh)
    m = manuals.create(conn, owner, "Продажи", svc.new_key())
    return owner, client, oh, m


def upload(client, oh, m, data: bytes):
    return client.post(f"/api/admin/manuals/{m['id']}/upload?idempotency_key={key()}", content=data, headers=oh)


def images(conn):
    return conn.execute("SELECT COUNT(*) FROM manual_images").fetchone()[0]


def test_a_real_page_with_meta_and_link_is_accepted_and_keeps_its_look(conn):
    owner, client, oh, m = setup(conn)
    r = upload(client, oh, m, PAGE.encode())
    assert r.status_code == 200, r.text
    v = r.json()["manual"]
    assert [s["title"] for s in v["sections"]] == ["Введение", "Как встречать", "Правила"]
    intro, greet, rules = (s["html"] for s in v["sections"])
    # the text after <meta>/<link>/<input> is all there
    assert "Вводный текст" in intro and "Методичка продавца" in intro
    assert "После поля ввода текст не теряется." in greet
    # the look: wrappers in every section (closed and reopened around the split), classes, inline styles, align
    for html in (re.sub(r">\s+<", "><", greet), re.sub(r">\s+<", "><", rules)):
        assert html.startswith('<div class="container"><section class="card">') and html.endswith("</section></div>")
    assert 'style="text-align: center; color: #b00"' in intro and 'align="center"' in greet
    assert 'style="color: green; font-size: x-large"' in rules  # an old <font> kept as a <span>
    assert '<h2 id="greet">Как встречать</h2>' in greet  # the heading stays in its section, with its look
    assert '<a href="#rules">к правилам</a>' in greet
    assert '<a href="https://ya.ru" target="_blank" rel="noopener noreferrer nofollow">там</a>' in greet
    # the style sheet: kept, without @import, outside urls and script-like values; the embedded picture kept with us
    css = v["css"]
    assert "font-family: Georgia" in css and ".card" in css and "body { color: #333 }" in css
    assert v["body_class"] == "theme-warm"
    for bad in ("@import", "fonts.example", "evil.example", "expression", "cdn.example"):
        assert bad not in css, bad
    assert "/api/media/manualimg/" in css
    body = intro + greet + rules
    for bad in ("<script", "alert", "onclick", "onload", "javascript:", "evil.example", "<iframe", "<svg", "svg-текст",
                "<input", "behavior", "<meta", "<link", "<title"):
        assert bad not in body + css, bad
    assert 'alt="схема"' in greet and 'width="200"' in greet
    assert images(conn) == 1  # the <img> and the background of .hero: the same bytes — one picture, two references


def test_the_reader_gets_the_sheet_and_the_page_is_editable_as_one_document(conn, make_user):
    owner, client, oh, m = setup(conn)
    upload(client, oh, m, PAGE.encode())
    u = make_user(first="Чтец")
    client.post(f"/api/admin/users/{u['id']}/manuals", json={"manual_id": m["id"], "allow": True, "idempotency_key": key()},
                headers=oh)
    h = headers(u["telegram_id"])
    read = client.get(f"/api/manuals/{m['id']}", headers=h).json()["manual"]
    assert ".card" in read["css"] and read["body_class"] == "theme-warm" and read["sections"][1]["heading"] is True
    first = read["sections"][0]
    client.post(f"/api/manuals/{m['id']}/sections/{first['id']}/read", headers=h)
    # the visual editor: the whole page back with a change in one section; same manual, same access, progress kept
    admin = client.get(f"/api/admin/manuals/{m['id']}", headers=oh).json()["manual"]
    doc = admin["document"].replace("Улыбка", "Тёплая улыбка")
    page = f"<html><head><style>{admin['css']}</style></head><body class=\"theme-warm\">{doc}</body></html>"
    r = client.put(f"/api/admin/manuals/{m['id']}/document", json={"html": page, "idempotency_key": key()}, headers=oh)
    assert r.status_code == 200, r.text
    saved = r.json()["manual"]
    assert saved["id"] == m["id"] and saved["version"] == 2
    assert [s["id"] for s in saved["sections"]] == [s["id"] for s in admin["sections"]]
    assert "Тёплая улыбка" in saved["sections"][1]["html"] and "/api/media/manualimg/" in saved["css"]
    assert images(conn) == 1  # pictures went back to references, nothing copied
    again = client.get(f"/api/manuals/{m['id']}", headers=h).json()["manual"]
    assert again["read"] == 1  # the untouched intro stays read; the edited section is new to read


def test_encodings(conn):
    owner, client, oh, m = setup(conn)
    text = "<h2>Касса</h2><p>Пробиваем чек</p>"
    for raw in (text.encode("cp1251"), ("<meta charset='windows-1251'>" + text).encode("cp1251"),
                text.encode("utf-16"), "\ufeff".encode() + text.encode(), text.encode("utf-16-le")):
        v = upload(client, oh, m, raw).json()["manual"]
        assert [s["title"] for s in v["sections"]] == ["Касса"] and "Пробиваем чек" in v["sections"][0]["html"], raw[:20]


@pytest.mark.parametrize("raw, code, words", [
    (b"", "empty_file", "пустой"),
    (b"   \n ", "empty_file", "пустой"),
    (b"%PDF-1.4 ...", "not_html", "PDF"),
    (b"PK\x03\x04....", "not_html", "Word"),
    (b"<html><head><script>document.write('x')</script></head><body></body></html>", "empty", "нет текста"),
])
def test_concrete_errors_and_nothing_left_behind(conn, raw, code, words):
    owner, client, oh, m = setup(conn)
    r = upload(client, oh, m, raw + (f'<img src="data:image/png;base64,{PNG64}">'.encode() if code == "empty" and False else b""))
    assert r.status_code in (413, 422) and r.json()["code"] == code and words in r.json()["detail"]
    assert images(conn) == 0 and manuals.get(conn, m["id"])["version"] == 0


def test_a_failed_file_with_pictures_writes_no_pictures(conn):
    owner, client, oh, m = setup(conn)
    raw = f'<html><body><form><img src="data:image/png;base64,{PNG64}"></form></body></html>'.encode()
    # a form is unwrapped, its picture is content — so this one is accepted; a picture-only page inside a script isn't
    bad = f'<html><body><script>var i="<img src=data:image/png;base64,{PNG64}>"</script></body></html>'.encode()
    assert upload(client, oh, m, bad).status_code == 422 and images(conn) == 0
    assert upload(client, oh, m, raw).status_code == 200 and images(conn) == 1


def test_plain_text_becomes_paragraphs_and_old_sections_still_work(conn):
    owner, client, oh, m = setup(conn)
    v = upload(client, oh, m, "Первый абзац\n\nВторой & последний".encode()).json()["manual"]
    assert v["sections"][0]["html"] == "<p>Первый абзац</p><p>Второй &amp; последний</p>"
    # sections saved the old way (no heading inside) open in the editor with their titles as headings
    manuals.save_sections(conn, owner, m["id"], [{"title": "Раз", "html": "<p>один</p>"}], svc.new_key())
    doc = manuals.view_admin(conn, m["id"])["document"]
    assert doc == "<h2>Раз</h2>\n<p>один</p>"


def test_css_cleaning_unit():
    store = lambda src: "manual-img:7" if src.startswith("data:image/png") else None  # noqa: E731
    css = manuals.clean_css(
        'a{color:red}/* x */@import "y.css";.b{background:url(https://t.example/p.gif) no-repeat}'
        f'.c{{background:url(data:image/png;base64,{PNG64})}}.d{{content:"\\3C /style>"}}'
        '.e{x: u\\72l(https://t.example/q)}', store)
    assert "@import" not in css and "t.example" not in css and "url(manual-img:7)" in css and "</style" not in css
    assert manuals.clean_style("color: red; behavior: url(x); width: 10px; {bad}") == "color: red; width: 10px"
