"""«Заполнить по фото» in the payout wizard (18.11): the accountant's photo → rows «name → rubles».

The photo goes to a vision model through an OpenAI-compatible endpoint (the customer's local opencode
server; OCR_BASE_URL/OCR_MODEL/OCR_API_KEY in .env — empty base URL turns the feature off). The model
returns a strict JSON, we clean it up (headers, «Итого», «тренерские» lines — ignored) and fuzzy-match
the names to the team (the wizard's network, when chosen). Nothing is saved: the owner confirms every
row in the app and commits the payout as usual — recognition only pre-fills the amounts.
"""

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

TIMEOUT = 90  # a vision model may think for a while on a big photo
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
    """One call to the OpenAI-compatible endpoint; any failure becomes a clear ServiceError."""
    url = settings.ocr_base_url + "/chat/completions"
    if not urlparse(url).scheme.startswith("http"):
        raise ServiceError("OCR_BASE_URL должен быть http(s) адресом", 409, "ocr_bad_url")
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
            async with session.post(url, json=payload, headers=headers) as resp:
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
    the system stores «Имя Фамилия» — both orders and the surname-with-initials form count."""
    variants = {_norm(f"{first} {last}"), _norm(f"{last} {first}")}
    if _norm(text) in variants:
        return 1.0
    squeezed = re.sub(r"[^а-яa-z\d]", "", _norm(text))
    surname = re.sub(r"[^а-яa-z\d]", "", _norm(last))
    first_letters = re.sub(r"[^а-яa-z]", "", _norm(first))
    if squeezed == surname + first_letters[:1] or squeezed == surname + first_letters:
        return 0.95  # «Селизнев М» / «Селизнев МА»
    if squeezed and squeezed == surname:
        return 0.85
    ratio = max(difflib.SequenceMatcher(None, _norm(text), v).ratio() for v in variants)
    if surname:
        ratio = max(ratio, difflib.SequenceMatcher(None, squeezed, surname).ratio())
    return ratio


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
