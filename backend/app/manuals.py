"""ТЗ 28.10 §12: методички в разделе «Обучение».

An admin creates a manual, uploads HTML or edits it section by section (title, text, headings, lists, bold/italic),
renames, reorders, deletes sections, previews; every save is a new version (the old ones are kept). Manuals are
given to people one by one in «Команда» and taken back one by one or all at once. A person sees «Методички» in
«Обучение» only while at least one live manual is given to them; every request is checked on the server, old links
included, and pictures go through short-lived signed links only (no permanent public URLs).

HTML is cleaned on the server with a whitelist: no scripts, styles, frames, forms, event handlers, external pictures
(a tracking pixel would leak who reads), only http(s)/mailto links. Embedded data: pictures are kept on the server.
Nothing is sent to outside services.

Progress is per person and section: a section counts as read while its content is the same (a hash); an edited or a
new section becomes unread again, a deleted one stops counting (В34)."""

import base64
import hashlib
import json
import re
import sqlite3
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from . import media
from . import permissions as perms
from . import services as svc
from .config import settings
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso

MAX_HTML = 2_000_000
MAX_SECTION = 200_000
MAX_TITLE = 120
MAX_IMAGE = 5 * 1024 * 1024

ALLOWED = {"h1", "h2", "h3", "h4", "p", "br", "ul", "ol", "li", "b", "strong", "i", "em", "u", "s", "blockquote",
           "a", "img", "table", "thead", "tbody", "tr", "td", "th", "hr", "code", "pre", "sub", "sup", "mark"}
VOID = {"br", "img", "hr"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math", "form",
                     "head", "title", "meta", "link", "button", "select", "textarea", "input", "video", "audio", "canvas"}
LINK_RE = re.compile(r"^(https?://|mailto:)", re.I)
DATA_IMG = re.compile(r"^data:image/(png|jpe?g|webp|gif);base64,(.+)$", re.I | re.S)


class _Cleaner(HTMLParser):
    """A whitelist sanitizer: unknown tags are unwrapped (their text kept), dangerous ones dropped with content."""

    def __init__(self, store_image):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.open: list[str] = []
        self.store_image = store_image

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            if tag not in VOID:
                self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        a = dict(attrs)
        if tag == "a":
            href = (a.get("href") or "").strip()
            if not LINK_RE.match(href):
                self.out.append("<a>")
            else:
                self.out.append(f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer nofollow">')
            self.open.append(tag)
            return
        if tag == "img":
            ref = self.store_image((a.get("src") or "").strip())
            if ref:  # only pictures kept on our server; external ones would tell a stranger who reads
                alt = escape(a.get("alt") or "", quote=True)
                self.out.append(f'<img src="{ref}" alt="{alt}">')
            return
        if tag in ("td", "th"):
            span = "".join(f' {k}="{int(a[k])}"' for k in ("colspan", "rowspan") if (a.get(k) or "").isdigit())
            self.out.append(f"<{tag}{span}>")
        else:
            self.out.append(f"<{tag}>")
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and tag in ALLOWED and not self.skip and self.open and self.open[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in ALLOWED or tag in VOID or tag not in self.open:
            return
        while self.open:
            top = self.open.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def _image_store(conn: sqlite3.Connection, manual_id: int):
    def store(src: str) -> str | None:
        if src.startswith("manual-img:") and src[11:].isdigit():
            row = conn.execute("SELECT id FROM manual_images WHERE id = ? AND manual_id = ?", (int(src[11:]), manual_id)).fetchone()
            return f"manual-img:{row[0]}" if row else None
        m = DATA_IMG.match(src)
        if not m:
            return None
        try:
            data = base64.b64decode(m.group(2), validate=False)
        except ValueError:
            return None
        return save_image(conn, manual_id, data)
    return store


def save_image(conn: sqlite3.Connection, manual_id: int, data: bytes) -> str | None:
    if len(data) > MAX_IMAGE:
        return None
    ext = media.sniff(data) or ("gif" if data[:6] in (b"GIF87a", b"GIF89a") else None)
    if ext is None:
        return None
    folder = Path(settings.media_dir) / "manuals"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"
    (folder / name).write_bytes(data)
    cur = conn.execute("INSERT INTO manual_images (manual_id, name, created_at) VALUES (?, ?, ?)", (manual_id, name, now_iso()))
    return f"manual-img:{cur.lastrowid}"


def clean(conn: sqlite3.Connection, manual_id: int, html: str) -> str:
    c = _Cleaner(_image_store(conn, manual_id))
    c.feed(html or "")
    c.close()
    out = c.result()
    return re.sub(r"(<p>\s*</p>|\n{3,})", "", out).strip()


HEAD_RE = re.compile(r"<h([12])>(.*?)</h\1>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def split(html: str) -> list[dict]:
    """Sections by <h2> (or <h1> when there are no <h2>); what comes before the first heading is «Введение»."""
    level = "2" if "<h2>" in html else "1" if "<h1>" in html else None
    if level is None:
        return [{"title": "Методичка", "html": html}] if html.strip() else []
    parts = re.split(rf"(<h{level}>.*?</h{level}>)", html, flags=re.S)
    sections, intro = [], parts[0].strip()
    if TAG_RE.sub("", intro).strip():
        sections.append({"title": "Введение", "html": intro})
    for i in range(1, len(parts), 2):
        title = TAG_RE.sub("", parts[i]).strip()[:MAX_TITLE] or f"Раздел {len(sections) + 1}"
        sections.append({"title": title, "html": parts[i + 1].strip() if i + 1 < len(parts) else ""})
    return sections


def _hash(title: str, html: str) -> str:
    return hashlib.sha256(f"{title}\n{html}".encode()).hexdigest()[:24]


# ---------------------------------------------------------------- admin


def _admin(conn, actor: dict) -> dict:
    actor = _fresh_actor(conn, actor)
    if not perms.can(actor, "manuals"):
        raise ServiceError("Методички ведут владелец и администраторы", 403, "forbidden")
    return actor


def get(conn: sqlite3.Connection, manual_id: int, live: bool = True) -> dict:
    row = conn.execute("SELECT * FROM manuals WHERE id = ?", (manual_id,)).fetchone()
    if row is None or (live and row["deleted_at"]):
        raise ServiceError("Методичка не найдена", 404, "not_found")
    return dict(row)


def sections(conn: sqlite3.Connection, manual_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM manual_sections WHERE manual_id = ? AND deleted_at IS NULL ORDER BY position, id", (manual_id,))]


def listing(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for m in conn.execute("SELECT * FROM manuals WHERE deleted_at IS NULL ORDER BY id DESC"):
        m = dict(m)
        out.append({**m, "sections": len(sections(conn, m["id"])),
                    "people": conn.execute("SELECT COUNT(*) FROM manual_access WHERE manual_id = ? AND revoked_at IS NULL",
                                           (m["id"],)).fetchone()[0]})
    return out


def create(conn: sqlite3.Connection, actor: dict, title: str, key: str) -> dict:
    title = " ".join((title or "").split())[:MAX_TITLE]
    if not title:
        raise ServiceError("Назовите методичку", 422, "title_required")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return dict(conn.execute("SELECT * FROM manuals ORDER BY id DESC LIMIT 1").fetchone())
        actor = _admin(conn, actor)
        cur = conn.execute("INSERT INTO manuals (title, created_at, created_by, version) VALUES (?, ?, ?, 0)",
                           (title, now_iso(), actor["telegram_id"]))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="MANUAL_CREATED", key=key,
                  new={"manual": cur.lastrowid, "title": title})
    return get(conn, cur.lastrowid)


def rename(conn: sqlite3.Connection, actor: dict, manual_id: int, title: str, key: str) -> dict:
    title = " ".join((title or "").split())[:MAX_TITLE]
    if not title:
        raise ServiceError("Назовите методичку", 422, "title_required")
    _check_key(key)
    with transaction(conn):
        if not _seen(conn, key):
            actor = _admin(conn, actor)
            old = get(conn, manual_id)
            conn.execute("UPDATE manuals SET title = ? WHERE id = ?", (title, manual_id))
            log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="MANUAL_RENAMED", key=key,
                      old={"title": old["title"]}, new={"title": title})
    return get(conn, manual_id)


def delete(conn: sqlite3.Connection, actor: dict, manual_id: int, key: str) -> None:
    """Deleted — gone for everyone at once (the record and its versions stay in the database)."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return
        actor = _admin(conn, actor)
        get(conn, manual_id)
        conn.execute("UPDATE manuals SET deleted_at = ? WHERE id = ?", (now_iso(), manual_id))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="MANUAL_DELETED", key=key,
                  new={"manual": manual_id})


def save_sections(conn: sqlite3.Connection, actor: dict, manual_id: int, items: list[dict], key: str,
                  note: str | None = None, raw_html: bool = True) -> dict:
    """The whole list in its order: existing ids are updated (renamed, edited, moved), new ones created, missing ones
    deleted. A new version is written; reading progress follows the content of each section."""
    if not isinstance(items, list) or len(items) > 200:
        raise ServiceError("Слишком много разделов", 422, "too_many")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return view_admin(conn, manual_id)
        actor = _admin(conn, actor)
        manual = get(conn, manual_id)
        current = {s["id"]: s for s in sections(conn, manual_id)}
        keep = set()
        for pos, item in enumerate(items):
            title = " ".join(str(item.get("title") or "").split())[:MAX_TITLE] or f"Раздел {pos + 1}"
            html = clean(conn, manual_id, unrender(str(item.get("html") or ""))) if raw_html else str(item.get("html") or "")
            if len(html) > MAX_SECTION:
                raise ServiceError(f"Раздел «{title}» слишком большой", 422, "too_long")
            sid = item.get("id")
            if sid in current:
                keep.add(sid)
                conn.execute("UPDATE manual_sections SET position = ?, title = ?, html = ?, hash = ?, updated_at = ? WHERE id = ?",
                             (pos, title, html, _hash(title, html), now_iso(), sid))
            else:
                conn.execute("""INSERT INTO manual_sections (manual_id, position, title, html, hash, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?)""", (manual_id, pos, title, html, _hash(title, html), now_iso()))
        for sid in set(current) - keep:
            conn.execute("UPDATE manual_sections SET deleted_at = ? WHERE id = ?", (now_iso(), sid))
        version = manual["version"] + 1
        snapshot = [{"title": s["title"], "html": s["html"]} for s in sections(conn, manual_id)]
        conn.execute("UPDATE manuals SET version = ? WHERE id = ?", (version, manual_id))
        conn.execute("""INSERT INTO manual_versions (manual_id, version, snapshot, created_at, created_by, note)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (manual_id, version, json.dumps(snapshot, ensure_ascii=False), now_iso(), actor["telegram_id"], note))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="MANUAL_SAVED", key=key,
                  new={"manual": manual_id, "version": version, "sections": len(snapshot)}, description=note)
    return view_admin(conn, manual_id)


def upload(conn: sqlite3.Connection, actor: dict, manual_id: int, html: str, key: str) -> dict:
    """«Загрузить HTML» — replaces the content (a new version; the old one stays in the history)."""
    if len(html or "") > MAX_HTML:
        raise ServiceError("Файл слишком большой (до 2 МБ)", 413, "too_big")
    with transaction(conn):
        _admin(conn, actor)
        get(conn, manual_id)
        cleaned = clean(conn, manual_id, html)
    parts = split(cleaned)
    if not parts:
        raise ServiceError("В файле нет текста", 422, "empty")
    # sections with the same title keep their id (and so the progress, if the text didn't change)
    old = {s["title"]: s["id"] for s in sections(conn, manual_id)}
    items = [{"id": old.pop(p["title"], None), **p} for p in parts]
    return save_sections(conn, actor, manual_id, items, key, "загружен HTML", raw_html=False)


def view_admin(conn: sqlite3.Connection, manual_id: int) -> dict:
    m = get(conn, manual_id)
    versions = [dict(r) for r in conn.execute(
        "SELECT version, created_at, note FROM manual_versions WHERE manual_id = ? ORDER BY version DESC LIMIT 20", (manual_id,))]
    return {**m, "sections": [{"id": s["id"], "title": s["title"], "html": render(s["html"])} for s in sections(conn, manual_id)],
            "versions": versions}


def render(html: str) -> str:
    """Pictures become short-lived signed links at reading time."""
    return re.sub(r'src="manual-img:(\d+)"', lambda m: f'src="{media.photo_url(int(m.group(1)), kind="manualimg")}"', html)


def unrender(html: str) -> str:
    """The editor sends signed links back: they become references again."""
    return re.sub(r'src="[^"]*/api/media/manualimg/(\d+)\?[^"]*"', r'src="manual-img:\1"', html)


def image_path(conn: sqlite3.Connection, image_id: int) -> Path | None:
    row = conn.execute("SELECT name FROM manual_images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        return None
    path = (Path(settings.media_dir) / "manuals" / row[0]).resolve()
    return path if path.is_file() else None


# ---------------------------------------------------------------- access


def set_access(conn: sqlite3.Connection, actor: dict, user_id: int, manual_id: int | None, allow: bool, key: str) -> list[dict]:
    """Give or take one manual; manual_id None with allow=False — take back all of them."""
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return assigned(conn, user_id)
        actor = _admin(conn, actor)
        user = svc.get_user(conn, user_id)
        if user is None:
            raise ServiceError("Пользователь не найден", 404, "not_found")
        if manual_id is None:
            if allow:
                raise ServiceError("Выберите методичку", 422, "manual_required")
            conn.execute("UPDATE manual_access SET revoked_at = ?, revoked_by = ? WHERE user_id = ? AND revoked_at IS NULL",
                         (now_iso(), actor["telegram_id"], user_id))
        else:
            get(conn, manual_id)
            live = conn.execute("SELECT id FROM manual_access WHERE user_id = ? AND manual_id = ? AND revoked_at IS NULL",
                                (user_id, manual_id)).fetchone()
            if allow and not live:
                conn.execute("INSERT INTO manual_access (manual_id, user_id, granted_at, granted_by) VALUES (?, ?, ?, ?)",
                             (manual_id, user_id, now_iso(), actor["telegram_id"]))
            elif not allow and live:
                conn.execute("UPDATE manual_access SET revoked_at = ?, revoked_by = ? WHERE id = ?",
                             (now_iso(), actor["telegram_id"], live[0]))
        log_event(conn, user_id=user_id, actor_id=actor["telegram_id"], key=key,
                  event_type="MANUAL_GRANTED" if allow else "MANUAL_REVOKED", new={"manual": manual_id})
    return assigned(conn, user_id)


def assigned(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    mine = {r[0] for r in conn.execute("SELECT manual_id FROM manual_access WHERE user_id = ? AND revoked_at IS NULL", (user_id,))}
    return [{"id": m["id"], "title": m["title"], "assigned": m["id"] in mine} for m in listing(conn)]


def available(conn: sqlite3.Connection, user: dict) -> list[dict]:
    """The live manuals given to this person (a blocked person — none)."""
    if user is None or user["status"] != "approved" or user["account_state"] == "blocked":
        return []
    return [dict(r) for r in conn.execute(
        """SELECT m.* FROM manuals m JOIN manual_access a ON a.manual_id = m.id AND a.revoked_at IS NULL
           WHERE a.user_id = ? AND m.deleted_at IS NULL ORDER BY m.title""", (user["id"],))]


def _readable(conn, user: dict, manual_id: int) -> dict:
    """Checked on every open — an old link stops working the moment access is taken back."""
    m = next((m for m in available(conn, user) if m["id"] == manual_id), None)
    if m is None and not perms.can(user, "manuals"):
        raise ServiceError("Методичка недоступна", 403, "no_manual_access")
    return m or get(conn, manual_id)


def progress(conn: sqlite3.Connection, user_id: int, manual_id: int) -> tuple[set[int], int]:
    secs = sections(conn, manual_id)
    read = {r[0] for r in conn.execute(
        """SELECT p.section_id FROM manual_progress p JOIN manual_sections s ON s.id = p.section_id
           WHERE p.user_id = ? AND s.manual_id = ? AND s.deleted_at IS NULL AND p.hash = s.hash""", (user_id, manual_id))}
    return read, len(secs)


def my_list(conn: sqlite3.Connection, user: dict) -> list[dict]:
    out = []
    for m in available(conn, user):
        read, total = progress(conn, user["id"], m["id"])
        out.append({"id": m["id"], "title": m["title"], "read": len(read), "total": total})
    return out


def read_view(conn: sqlite3.Connection, user: dict, manual_id: int) -> dict:
    m = _readable(conn, user, manual_id)
    read, total = progress(conn, user["id"], manual_id)
    return {"id": m["id"], "title": m["title"], "total": total, "read": len(read),
            "sections": [{"id": s["id"], "title": s["title"], "read": s["id"] in read, "html": render(s["html"])}
                         for s in sections(conn, manual_id)]}


def mark_read(conn: sqlite3.Connection, user: dict, manual_id: int, section_id: int) -> dict:
    _readable(conn, user, manual_id)
    with transaction(conn):
        s = conn.execute("SELECT * FROM manual_sections WHERE id = ? AND manual_id = ? AND deleted_at IS NULL",
                         (section_id, manual_id)).fetchone()
        if s is None:
            raise ServiceError("Раздел не найден", 404, "not_found")
        conn.execute("""INSERT INTO manual_progress (user_id, section_id, hash, read_at) VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id, section_id) DO UPDATE SET hash = excluded.hash, read_at = excluded.read_at""",
                     (user["id"], section_id, s["hash"], now_iso()))
    return read_view(conn, user, manual_id)


def can_view_image(conn: sqlite3.Connection, image_id: int) -> bool:
    return image_path(conn, image_id) is not None
