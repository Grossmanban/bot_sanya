"""18.11: «Заполнить по фото» — parsing the model's reply, matching names to the team, the route."""

import json

import pytest

from app import networks, ocr
from app import services as svc

from .test_rates import WEEK, did, person  # noqa: F401 — re-exported helpers
from .test_payouts_manual import Http, world

# a minimal valid PNG (1×1) — the endpoint sniffs the image before asking the model
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626001"
    "000000ffff03000006000557bfabd40000000049454e44ae426082"
)


def fake_reply(rows, extra=""):
    return "Вот ведомость:\n```json\n" + json.dumps(rows, ensure_ascii=False) + "\n```" + extra


def test_parse_report_cleans_the_model_reply():
    reply = fake_reply([
        {"name": "Петров И.", "amount": "4 950"},
        {"name": "Петрова  Вера", "amount": 1200.50},
        {"name": "Тренерские (Петрова)", "amount": 3000},
        {"name": "Итого", "amount": 99999},
        {"name": "", "amount": 100},
        {"name": "Без суммы", "amount": "—"},
    ], "\nЕсли что-то непонятно — напишите.")
    rows, dropped = ocr.parse_report(reply)
    assert [(r["name"], r["amount"]) for r in rows] == [("Петров И.", 4950), ("Петрова Вера", 1200)]
    assert dropped == ["Тренерские (Петрова)", "Итого"]  # «тренерские» and totals never reach the wizard
    with pytest.raises(svc.ServiceError) as e:
        ocr.parse_report("модель сходила с ума, массива нет")
    assert e.value.code == "ocr_no_json"


def test_matching_survives_reorder_initials_and_typos(conn, make_user):
    # world(): Вера Петрова (trainer), Иван Петров, Петя Петров — one surname, matching must tell them apart
    owner, north, vera, ivan, petya = world(conn, make_user)
    rows = [{"name": "Петров И", "amount": 100},     # фамилия + инициал
            {"name": "Иван Петров", "amount": 200},   # как в системе (имя первым)
            {"name": "петя петров", "amount": 300},   # регистр неважен
            {"name": "Петров П", "amount": 400},      # инициал Пети, не Ивана
            {"name": "Нетякин Кто", "amount": 500}]   # такого человека нет
    out = {r["text"]: r for r in ocr.match_rows(conn, rows, north["id"])}
    assert out["Петров И"]["matched"] and out["Петров И"]["user_id"] == ivan["id"]
    assert out["Иван Петров"]["user_id"] == ivan["id"]
    assert out["петя петров"]["user_id"] == petya["id"]
    assert out["Петров П"]["user_id"] == petya["id"]
    assert out["Нетякин Кто"]["matched"] is False and out["Нетякин Кто"]["user_id"] is None
    assert 0 < len(out["Нетякин Кто"]["alternatives"]) <= 2  # a picker with close candidates
    # the network fence: a person of another network is not among the candidates at all
    other = person(conn, make_user, "Чужая")
    south = networks.create(conn, owner, "Юг", svc.new_key())
    networks.set_member(conn, owner, other["id"], south["id"], svc.new_key())
    stranger = ocr.match_rows(conn, [{"name": "Чужая Петрова", "amount": 1}], north["id"])[0]
    assert stranger["matched"] is False


def test_close_but_wrong_names_never_match(conn, make_user):
    """The real photo's traps: «Ливанов Александр» is NOT «Александр Иванов», «Лавренова Анастасия» is
    NOT «Анастасия Дунаева» — the surname leads, from its first letter. A typo'd surname still matches."""
    ivanov = make_user(first="Александр", last="Иванов")
    dunaeva = make_user(first="Анастасия", last="Дунаева")
    ek = make_user(first="Екатерина", last="Бокусова")
    smirnovs = [make_user(first="Андрей", last="Смирнов"), make_user(first="Кирилл", last="Смирнов")]
    fake = [dict(u) for u in (ivanov, dunaeva, ek, *smirnovs)]
    rows = [{"name": "Ливанов Александр", "amount": 1}, {"name": "Лавренова Анастасия", "amount": 1},
            {"name": "Боксусова Екатерина", "amount": 1}, {"name": "Смирнов Андрей", "amount": 1},
            {"name": "Смирнов", "amount": 1}]
    class FakeConn:  # match_rows only needs list_users
        def execute(self, *a, **k):
            class R:
                def fetchall(self):
                    return fake
            return R()
    out = {r["text"]: r for r in ocr.match_rows(FakeConn(), rows, None)}
    assert out["Ливанов Александр"]["matched"] is False        # not Иванов
    assert out["Лавренова Анастасия"]["matched"] is False      # not Дунаева
    assert out["Боксусова Екатерина"]["matched"] and out["Боксусова Екатерина"]["user_id"] == ek["id"]  # typo ok
    assert out["Смирнов Андрей"]["user_id"] == smirnovs[0]["id"]
    assert out["Смирнов"]["matched"] is False                  # ambiguous: two Смирновs


def test_the_route_requires_configuration_and_parses(conn, make_user, monkeypatch):
    owner, north, vera, ivan, petya = world(conn, make_user)
    did(conn, ivan, WEEK[0], 10)
    client = Http(conn)
    # not configured → a clear 409; the button stays hidden in the app
    r = client.post_raw(f"/api/admin/payouts/parse-photo?network_id={north['id']}", 999, PNG)
    assert r.status_code == 409 and r.json()["code"] == "ocr_not_configured"
    monkeypatch.setattr(ocr, "configured", lambda: True)

    async def fake_ask(data, mime):
        return fake_reply([{"name": "Иван Петров", "amount": "1 200"}, {"name": "тренерские", "amount": 5}])

    monkeypatch.setattr(ocr, "_ask", fake_ask)
    r = client.post_raw(f"/api/admin/payouts/parse-photo?network_id={north['id']}", 999, PNG)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"][0]["user_id"] == ivan["id"] and body["rows"][0]["amount"] == 1200
    assert body["dropped"] == ["тренерские"]
    # a trainer can't call it; garbage instead of an image is rejected before the model
    assert client.post_raw("/api/admin/payouts/parse-photo", vera["telegram_id"], PNG).status_code == 403
    assert client.post_raw("/api/admin/payouts/parse-photo", 999, b"not-an-image").status_code == 415


def test_opencode_transport_makes_a_session_polls_and_cleans_up(monkeypatch):
    """The local `opencode serve --service` protocol: session → model → prompt with the photo → poll → delete."""
    import asyncio
    from types import SimpleNamespace

    calls = []

    async def fake_oc_request(method, path, body=None):
        calls.append((method, path))
        if (method, path) == ("POST", "/api/session"):
            return 200, {"data": {"id": "ses_1"}}
        if path.endswith("/model"):
            return 200, {}
        if path.endswith("/prompt"):
            assert body and body["files"] and body["files"][0]["uri"].startswith("data:image/png;base64,")
            assert "Фамилия" in body["text"]  # the strict JSON prompt goes as is
            return 200, {"data": {"id": "msg_1"}}
        if path.endswith("/message"):
            if len([c for c in calls if c[1].endswith("/message")]) == 1:  # the first poll: nothing yet
                return 200, {"data": [{"type": "user", "text": "вопрос",
                                       "files": [{"data": "…", "mime": "image/png", "source": {"type": "inline"}}]}]}
            return 200, {"data": [
                {"type": "model-switched", "model": {"id": "nemotron-vl", "providerID": "opencode"}},
                {"type": "assistant", "content": [
                    {"type": "reasoning", "text": "думает…"},
                    {"type": "text", "text": fake_reply([{"name": "X Y", "amount": 1}])}]}]}
        return 200, {}

    monkeypatch.setattr(ocr, "_oc_request", fake_oc_request)
    monkeypatch.setattr(ocr, "settings", SimpleNamespace(ocr_kind="opencode", ocr_base_url="http://opencode.local",
                                                         ocr_model="opencode/nemotron-vl", ocr_api_key="opencode:secret"))
    monkeypatch.setattr(ocr, "POLL_EVERY", 0)

    text = asyncio.run(ocr._ask(b"png-bytes", "image/png"))
    rows, dropped = ocr.parse_report(text)
    assert rows == [{"name": "X Y", "amount": 1}] and dropped == []
    ops = {(m, p) for m, p in calls}
    assert ("POST", "/api/session") in ops and ("DELETE", "/api/session/ses_1") in ops
    assert ("POST", "/api/session/ses_1/model") in ops and ("POST", "/api/session/ses_1/prompt") in ops
