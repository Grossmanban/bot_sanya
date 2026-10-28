"""ТЗ 28.10 §2 (В1, В2): the reason templates and the bot's editable message texts — one place for both.

Reasons: a list per kind of action, chosen and then edited before sending; the server still decides whether a reason
is required. Texts: the messages the customer wants to edit (the candidate's «Хорошие новости», the invitations,
the profile reminder); a text that was never edited is the default from the code, «Вернуть исходный» drops the edit."""

import re
import sqlite3
from html import escape

from . import permissions as perms
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event, now_iso

# kind -> (title in «Настройки», defaults). The frontend and the bot fall back to the defaults when a kind is empty.
REASON_KINDS: dict[str, tuple[str, list[str]]] = {
    "fine": ("Штраф", ["Опоздание", "Нарушение правил точки", "Не вышел без предупреждения"]),
    "bonus": ("Бонус", ["Лучший результат дня", "Помощь команде", "Перевыполнил план"]),
    "deduction": ("Вычет", ["Форма", "Аванс", "Материалы"]),
    "report_fine": ("Отчёт: отклонить со штрафом", ["Не выполнена норма", "Скриншот не подтверждает результат"]),
    "no_show_fine": ("Неявка: штраф", ["Не сдан отчёт о смене", "Не вышел на смену"]),
    "no_show_excuse": ("Неявка: без штрафа", ["Уважительная причина", "Смена не состоялась"]),
    "excuse": ("Дисциплина: простить", ["Больничный лист предоставлен", "Согласовано с руководителем", "Технический простой"]),
    "annul": ("Аннулирование результата", ["Нечестная работа", "Скриншот не подтверждает результат", "Условия изменились"]),
    "forgive": ("Простить штраф", ["Уважительная причина", "Первое нарушение"]),
    "pause": ("Пауза", ["Отпуск", "Больничный", "По собственной просьбе"]),
    "block": ("Блокировка", ["Грубое нарушение правил", "Прекращение сотрудничества"]),
    "reject_candidate": ("Отказ кандидату", ["Не вышел на связь", "Не подходит по требованиям", "Не явился на обучение"]),
    "cancel_training": ("Отмена обучения", ["Кандидат попросил перенести", "Тренер не может провести", "Кандидат не пришёл"]),
    "change_training": ("Перенос обучения", ["Кандидат попросил перенести", "Тренер занят в это время", "Другое место удобнее"]),
    "reject_request": ("Отклонение запроса", ["Нет оснований", "Нужно согласовать лично"]),
    "alice_reject": ("«Алиса»: отклонить", ["Фото не подтверждает выполнение", "Не тот QR-код", "Повтор фото"]),
}
MAX_REASON = 500

# key -> (title, default text, placeholders). Placeholders are written as {ФИО_тренера} and filled by the code.
TEXTS: dict[str, tuple[str, str, list[str]]] = {
    "candidate_trainer": (
        "Кандидату: назначен тренер",
        "🎉 Хорошие новости!\nМы готовы рассмотреть вашу кандидатуру для дальнейшего сотрудничества.\n"
        "📩 Следующий шаг — согласование обучения.\nСвяжитесь с {ФИО_тренера}, чтобы согласовать дату и время обучения.\n"
        "⚠️ Обратите внимание!\n⏳ Если в течение 48 часов после получения этого сообщения вы не свяжетесь с назначенным "
        "сотрудником, это может стать основанием для отказа в дальнейшем сотрудничестве.\n🤝 Будем рады видеть вас в нашей команде!",
        ["ФИО_тренера"]),
    "pre_invite": (
        "Кандидату: приглашение в «Предзакреп»",
        "✅ Обучение согласовано!\nСледующий шаг — подготовка к работе. Вступите, пожалуйста, в закрытую группу сотрудников, "
        "проходящих обучение, — кнопка ниже. Ссылка личная: по ней войдёте только вы.",
        []),
    "team_welcome": (
        "Принят в команду",
        "🎉 Поздравляем, {имя}! Вы успешно прошли обучение и приняты в команду.\n"
        "Вступите в основную рабочую группу — кнопка ниже. Ссылка личная: по ней войдёте только вы.",
        ["имя"]),
    "profile_reminder": (
        "Напоминание о заполнении профиля",
        "⚠️ Важное уведомление\n🚨 Срочно заполните данные профиля!\nНеобходимо завершить заполнение всех обязательных данных "
        "в боте, включая информацию о себе и реквизиты для выплат.\nЕсли ваши данные были отклонены администратором, "
        "исправьте замечания и отправьте их повторно.\n📌 Перейдите в раздел „Профиль“ и проверьте все поля.\n"
        "Спасибо за оперативность! 🤝",
        []),
    "referrer_help": (
        "Пригласившему: кандидат не связался с тренером",
        "👋 {имя_пригласившего}, нужна ваша помощь.\nКандидат {ФИО_кандидата}, которого вы пригласили, пока не связался "
        "с тренером {ФИО_тренера} и не согласовал обучение.\nПожалуйста, помогите организовать связь.",
        ["имя_пригласившего", "ФИО_кандидата", "ФИО_тренера"]),
}
MAX_TEXT = 3500


def _manage(conn, actor: dict) -> dict:
    actor = _fresh_actor(conn, actor)
    if not perms.can(actor, "settings"):
        raise ServiceError("Шаблоны меняют владелец и администраторы", 403, "forbidden")
    return actor


# ---------------------------------------------------------------- reasons


def reasons(conn: sqlite3.Connection, kind: str) -> list[str]:
    rows = [r[0] for r in conn.execute(
        "SELECT text FROM reason_templates WHERE kind = ? AND deleted_at IS NULL ORDER BY position, id", (kind,))]
    return rows


def reason_catalog(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for kind, (title, _) in REASON_KINDS.items():
        items = [dict(r) for r in conn.execute(
            "SELECT id, text, position FROM reason_templates WHERE kind = ? AND deleted_at IS NULL ORDER BY position, id",
            (kind,))]
        out.append({"kind": kind, "title": title, "items": items})
    return out


def _clean(text: str | None) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        raise ServiceError("Напишите текст причины", 422, "reason_required")
    if len(text) > MAX_REASON:
        raise ServiceError(f"Не длиннее {MAX_REASON} символов", 422, "too_long")
    return text


def add_reason(conn: sqlite3.Connection, actor: dict, kind: str, text: str, key: str) -> dict:
    if kind not in REASON_KINDS:
        raise ServiceError("Неизвестный вид действия", 422, "bad_kind")
    text = _clean(text)
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return {}
        actor = _manage(conn, actor)
        pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM reason_templates WHERE kind = ?", (kind,)).fetchone()[0]
        cur = conn.execute("INSERT INTO reason_templates (kind, text, position, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                           (kind, text, pos, now_iso(), actor["telegram_id"]))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="REASON_TEMPLATE_ADDED", key=key,
                  new={"kind": kind, "text": text})
    return {"id": cur.lastrowid, "kind": kind, "text": text}


def edit_reason(conn: sqlite3.Connection, actor: dict, reason_id: int, text: str | None, delete: bool, key: str) -> None:
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return
        actor = _manage(conn, actor)
        row = conn.execute("SELECT * FROM reason_templates WHERE id = ? AND deleted_at IS NULL", (reason_id,)).fetchone()
        if row is None:
            raise ServiceError("Шаблон не найден", 404, "not_found")
        if delete:
            conn.execute("UPDATE reason_templates SET deleted_at = ? WHERE id = ?", (now_iso(), reason_id))
        else:
            conn.execute("UPDATE reason_templates SET text = ? WHERE id = ?", (_clean(text), reason_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="REASON_TEMPLATE_CHANGED", key=key,
                  old={"text": row["text"]}, new={"text": None if delete else text, "deleted": delete})


def seed_reasons(conn: sqlite3.Connection) -> None:
    """The presets that used to live in the code become editable templates (migration 31)."""
    for kind, (_, items) in REASON_KINDS.items():
        for i, text in enumerate(items):
            conn.execute("INSERT INTO reason_templates (kind, text, position, created_at) VALUES (?, ?, ?, datetime('now'))",
                         (kind, text, i + 1))


def require_reason(text: str | None, what: str = "Укажите причину") -> str:
    """A required reason can't be skipped — checked on the server for every action that needs one (В2)."""
    text = (text or "").strip()
    if not text:
        raise ServiceError(what, 422, "reason_required")
    return text[:MAX_REASON]


# ---------------------------------------------------------------- message texts


def text(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT text FROM message_texts WHERE key = ?", (key,)).fetchone()
    return row[0] if row else TEXTS[key][1]


def render(conn: sqlite3.Connection, key: str, **values: str) -> str:
    """The text as Telegram HTML: the admin's words are escaped, placeholders get the values (already HTML)."""
    body = escape(text(conn, key))
    for name in TEXTS[key][2]:
        body = body.replace("{" + name + "}", values.get(name, ""))
    return body


def text_catalog(conn: sqlite3.Connection) -> list[dict]:
    edited = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM message_texts")}
    return [{"key": k, "title": title, "text": edited[k]["text"] if k in edited else default, "default": default,
             "edited": k in edited, "placeholders": names, "updated_at": edited.get(k, {}).get("updated_at")}
            for k, (title, default, names) in TEXTS.items()]


def save_text(conn: sqlite3.Connection, actor: dict, key: str, value: str | None, key_: str) -> None:
    """value None — back to the default text."""
    if key not in TEXTS:
        raise ServiceError("Неизвестный текст", 404, "not_found")
    if value is not None:
        value = value.strip()
        if not value:
            raise ServiceError("Текст не может быть пустым — или верните исходный", 422, "empty")
        if len(value) > MAX_TEXT:
            raise ServiceError(f"Не длиннее {MAX_TEXT} символов", 422, "too_long")
        missing = [n for n in TEXTS[key][2] if "{" + n + "}" not in value]
        if missing:
            raise ServiceError("В тексте нужно оставить: " + ", ".join("{" + n + "}" for n in missing), 422, "placeholder_missing")
    _check_key(key_)
    with transaction(conn):
        if _seen(conn, key_):
            return
        actor = _manage(conn, actor)
        old = text(conn, key)
        if value is None:
            conn.execute("DELETE FROM message_texts WHERE key = ?", (key,))
        else:
            conn.execute("""INSERT INTO message_texts (key, text, updated_at, updated_by) VALUES (?, ?, ?, ?)
                            ON CONFLICT(key) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at,
                                                           updated_by = excluded.updated_by""",
                         (key, value, now_iso(), actor["telegram_id"]))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="MESSAGE_TEXT_CHANGED", key=key_,
                  old={"key": key, "text": old}, new={"text": value})
