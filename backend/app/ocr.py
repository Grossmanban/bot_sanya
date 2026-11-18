"""«Заполнить по фото» in the payout wizard (18.11): the accountant's photo → rows «name → rubles».

The photo goes to a vision model through an OpenAI-compatible endpoint (the customer's local opencode
server; OCR_BASE_URL/OCR_MODEL/OCR_API_KEY in .env — empty base URL turns the feature off). The model
returns a strict JSON, we clean it up (headers, «Итого», «тренерские» lines — ignored) and fuzzy-match
the names to the team (the wizard's network, when chosen). Nothing is saved: the owner confirms every
row in the app and commits the payout as usual — recognition only pre-fills the amounts.
"""

import asyncio
import base64
import difflib
import json
import re
import sqlite3
from urllib.parse import urlparse

import aiohttp

from . import permissions as perms
from . import services as svc
from .config import settings
from .services import ServiceError

TIMEOUT = 120  # a vision model may think for a while on a big photo
POLL_EVERY = 2  # the opencode service answers through its inbox — we poll the session messages
MAX_ROWS = 100  # a sane report is a couple dozen lines; garbage guards the JSON size

PROMPT = (
    "Это фотография ведомости выплат. Найди строки с людьми и их суммами к выплате в рублях. "
    "Верни ТОЛЬКО JSON-массив без пояснений, вида "
    '[{"name": "Фамилия Имя", "amount": 12345}] — один элемент на человека, имя как на фото (можно с инициалами), '
    "amount — целое число рублей без пробелов и символов. Игнорируй: шапку таблицы, нумерацию, колонку количества, "
    'строки «Итого»/«Всего», подписи и всё, что помечено как «тренерские» (комиссии тренеру). '
    "Если сумму определить нельзя — не включай строку."
)

# double safety on top of the prompt: lines that are never a person's pay
DROP_RE = re.compile(r"тренерск|итого|всего|подпис|выплат\w*\s+всего|^итд$|период\b|ведомост", re.IGNORECASE)


def configured() -> bool:
    return bool(settings.ocr_base_url and settings.ocr_model)


async def _ask(data: bytes, mime: str) -> str:
    """The photo → the model's text reply, through whichever endpoint kind .env points at."""
    if not urlparse(settings.ocr_base_url).scheme.startswith("http"):
        raise ServiceError("OCR_BASE_URL должен быть http(s) адресом", 409, "ocr_bad_url")
    if settings.ocr_kind == "opencode":
        return await _ask_opencode(data, mime)
    return await _ask_openai(data, mime)


async def _ask_openai(data: bytes, mime: str) -> str:
    """One call to an OpenAI-compatible endpoint; any failure becomes a clear ServiceError."""
    payload = {
        "model": settings.ocr_model,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}},
            ],
        }],
    }
    headers = {"Authorization": f"Bearer {settings.ocr_api_key}"} if settings.ocr_api_key else {}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            async with session.post(settings.ocr_base_url + "/chat/completions", json=payload, headers=headers) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise ServiceError(f"Модель не ответила (HTTP {resp.status}): {body[:200]}", 502, "ocr_error")
                try:
                    return resp.json()["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError, aiohttp.ContentTypeError) as exc:
                    raise ServiceError("Модель ответила не так, как ждёт OpenAI-совместимый API", 502, "ocr_error") from exc
    except aiohttp.ClientError as exc:
        raise ServiceError(f"Распознавание недоступно: {exc.__class__.__name__}. Проверьте, что сервер модели запущен",
                           502, "ocr_unreachable") from exc


# ---------------------------------------------------------------- the local `opencode serve --service` API


async def _oc_request(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | None]:
    """One HTTP call to the opencode service (Basic auth: OCR_API_KEY = «user:password»)."""
    key = settings.ocr_api_key or ""
    user, _, password = key.partition(":")
    auth = aiohttp.BasicAuth(user or "opencode", password or key)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            async with session.request(method, settings.ocr_base_url + path, json=body, auth=auth) as resp:
                text = await resp.text()
                try:
                    return resp.status, json.loads(text) if text else None
                except json.JSONDecodeError:
                    return resp.status, {"raw": text[:200]}
    except aiohttp.ClientError as exc:
        raise ServiceError(f"Сервер opencode недоступен: {exc.__class__.__name__}. Проверьте, что он запущен",
                           502, "ocr_unreachable") from exc


def _assistant_text(messages: list) -> str:
    """The assistant replies' text parts of the session's message list (flat: {type, content[]})."""
    texts = []
    for msg in messages or []:
        if msg.get("type") != "assistant":
            continue
        texts += [p.get("text", "") for p in (msg.get("content") or []) if p.get("type") == "text"]
    return "\n".join(t for t in texts if t)


async def _ask_opencode(data: bytes, mime: str) -> str:
    """A throw-away session: attach the photo, ask, poll for the answer, delete the session."""
    status, made = await _oc_request("POST", "/api/session", {})
    session_id = (made or {}).get("data", {}).get("id") if isinstance(made, dict) else None
    if status != 200 or not session_id:
        raise ServiceError(f"opencode не создал сессию (HTTP {status})", 502, "ocr_error")
    try:
        provider, _, model = settings.ocr_model.partition("/")
        if provider and model:
            await _oc_request("POST", f"/api/session/{session_id}/model",
                              {"model": {"providerID": provider, "id": model}})
        status, _ = await _oc_request("POST", f"/api/session/{session_id}/prompt", {
            "text": PROMPT,
            "files": [{"uri": f"data:{mime};base64,{base64.b64encode(data).decode()}"}],
        })
        if status not in (200, 201, 202):
            raise ServiceError(f"opencode не принял запрос (HTTP {status})", 502, "ocr_error")
        waited = 0
        while waited < TIMEOUT:
            await asyncio.sleep(POLL_EVERY)
            waited += POLL_EVERY
            status, listed = await _oc_request("GET", f"/api/session/{session_id}/message")
            if status != 200 or not isinstance(listed, dict):
                continue
            text = _assistant_text(listed.get("data"))
            if text.strip():
                return text
        raise ServiceError("Модель не ответила за отведённое время — попробуйте ещё раз", 504, "ocr_timeout")
    finally:
        await _oc_request("DELETE", f"/api/session/{session_id}")


def _to_rubles(raw) -> int | None:
    """«1 200,50» / «1200.00 ₽» → int rubles (kopecks rounded); None when it isn't a number."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(round(raw))
    digits = re.sub(r"[^\d.,]", "", str(raw)).replace(",", ".")
    if not digits.strip("."):
        return None
    try:
        return int(round(float(digits)))
    except ValueError:
        return None


def parse_report(reply: str) -> tuple[list[dict], list[str]]:
    """The model's text → ([{name, amount}], dropped lines). Tolerates ```-fences and prose around the JSON."""
    text = re.sub(r"```(?:json)?", "", reply)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ServiceError("Модель не вернула таблицу — попробуйте фото получше", 422, "ocr_no_json")
    try:
        items = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ServiceError("Ответ модели не разобрать — попробуйте ещё раз", 422, "ocr_bad_json") from exc
    if not isinstance(items, list) or len(items) > MAX_ROWS:
        raise ServiceError("Ведомость выглядит неправдоподобно", 422, "ocr_bad_json")
    rows, dropped = [], []
    for item in items:
        name = " ".join(str(item.get("name") or "").split()) if isinstance(item, dict) else ""
        amount = _to_rubles(item.get("amount")) if isinstance(item, dict) else None
        if not name or amount is None or amount <= 0:
            continue
        if DROP_RE.search(name):
            dropped.append(name)
            continue
        rows.append({"name": name, "amount": amount})
    if not rows:
        raise ServiceError("На фото не нашлось строк с людьми и суммами", 422, "ocr_empty")
    return rows, dropped


# ---------------------------------------------------------------- matching names to the team


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("ё", "е").split())


def _score(text: str, first: str, last: str) -> float:
    """How well the photo's name fits the person. The report writes «Фамилия Имя» or «Фамилия И.»,
    the system stores «Имя Фамилия» — both orders and the surname-with-initials form count.
    The SURNAME is the key: it must match from its first letter (a fuzzy «Ливанов» never becomes «Иванов»)."""
    variants = {_norm(f"{first} {last}"), _norm(f"{last} {first}")}
    if _norm(text) in variants:
        return 1.0
    squeezed = re.sub(r"[^а-яa-z\d]", "", _norm(text))
    surname = re.sub(r"[^а-яa-z\d]", "", _norm(last))
    name = re.sub(r"[^а-яa-z]", "", _norm(first))
    if squeezed == surname + name[:1] or squeezed == surname + name:
        return 0.95  # «Селизнев М» / «Селизнев МА»
    if squeezed and squeezed == surname:
        return 0.85  # the surname alone — the caller decides if it is ambiguous
    # fuzzy: pick the word that looks most like the surname (from the same first letter), check the name too
    words = _norm(text).split()
    if not words or not surname:
        return 0.0

    def close(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a, b).ratio()

    cand = max(words, key=lambda w: close(w, surname) if w[:1] == surname[:1] else -1.0)
    if not cand or cand[:1] != surname[:1] or close(cand, surname) < 0.85:
        return 0.0  # «Ливанов» never matches «Иванов»: wrong surname letter
    given = re.sub(r"[^а-яa-z]", "", next((w for w in words if w != cand), ""))
    if given and name:
        if given[0] != name[0]:
            return 0.0  # «Смирнов Андрей» is not «Смирнов Кирилл»
        if len(given) > 1 and close(given, name) < 0.6:
            return 0.0
    return 0.8  # close enough — the dialog still shows the pair to the owner


def match_rows(conn: sqlite3.Connection, rows: list[dict], network_id: int | None) -> list[dict]:
    """Attach the best candidate (and alternatives for the picker) to every recognized row."""
    people = [u for u in svc.list_users(conn) if u["account_state"] != "blocked"]
    if network_id is not None:
        people = [u for u in people if u["network_id"] == network_id]
    out = []
    for row in rows:
        ranked = sorted(((_score(row["name"], u["first_name"], u["last_name"]), u) for u in people),
                        key=lambda x: x[0], reverse=True)
        best = ranked[0] if ranked else None
        matched = bool(best and best[0] >= 0.75)
        if matched and best[0] == 0.85 and sum(1 for s, u in ranked[:4] if s == 0.85 and u["last_name"] == best[1]["last_name"]) > 1:
            matched = False  # «Смирнов» alone with two Смирновs in the team — the owner picks
        alts = [{"user_id": u["id"], "name": svc.full_name(u)} for _, u in ranked[:4]]
        if matched:
            alts = [a for a in alts if a["user_id"] != best[1]["id"]]
        out.append({
            "text": row["name"],
            "amount": row["amount"],
            "matched": matched,
            "user_id": best[1]["id"] if best and matched else None,
            "name": svc.full_name(best[1]) if best and matched else None,
            "role": perms.role_of(best[1]) if best and matched else None,
            "alternatives": alts[:2],  # options for the manual picker
        })
    return out


async def parse_photo(conn: sqlite3.Connection, data: bytes, mime: str, network_id: int | None) -> dict:
    """The whole «photo → rows matched to the team» — nothing is written to the database."""
    if not configured():
        raise ServiceError("Распознавание не настроено: задайте OCR_BASE_URL и OCR_MODEL в .env", 409, "ocr_not_configured")
    rows, dropped = parse_report(await _ask(data, mime))
    return {"rows": match_rows(conn, rows, network_id), "dropped": dropped}
