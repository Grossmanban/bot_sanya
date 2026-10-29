"""ТЗ 28.10 §12: методички в разделе «Обучение».

An admin creates a manual, uploads HTML or edits it section by section (title, text, headings, lists, bold/italic),
renames, reorders, deletes sections, previews; every save is a new version (the old ones are kept). Manuals are
given to people one by one in «Команда» and taken back one by one or all at once. A person sees «Методички» in
«Обучение» only while at least one live manual is given to them; every request is checked on the server, old links
included, and pictures go through short-lived signed links only (no permanent public URLs).

HTML is cleaned on the server with a whitelist: no scripts, frames, forms, event handlers, external pictures, fonts or
style sheets (a tracking pixel would leak who reads), only http(s)/mailto/#anchor links. ТЗ 29.10 §3: the page's look
is kept — layout blocks, classes, inline styles and its <style> sheet (cleaned: no @import, no outside url(), nothing
script-like) — and the app shows it in a sandboxed frame without scripts. Embedded data: pictures are kept on the
server. Nothing is sent to outside services.

Progress is per person and section: a section counts as read while its content is the same (a hash); an edited or a
new section becomes unread again, a deleted one stops counting (В34)."""

import base64
import hashlib
import json
import re
import sqlite3
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path

from . import media
from . import permissions as perms
from . import services as svc
from .config import settings
from .db import transaction
from .services import ServiceError, _check_key, _fresh_actor, _seen, log_event
from .timeutil import now_iso

MAX_HTML = 8_000_000  # bytes of an uploaded file — pictures embedded in it included
MAX_SECTION = 300_000
MAX_TITLE = 120
MAX_IMAGE = 5 * 1024 * 1024
MAX_CSS = 1_000_000

# ТЗ 29.10 §3: a ready HTML page keeps its look — layout blocks, classes, inline styles and its <style> sheet are kept
# (cleaned); scripts, frames, forms, event handlers and anything loaded from outside are not.
ALLOWED = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "ul", "ol", "li", "b", "strong", "i", "em", "u", "s", "strike",
           "del", "ins", "blockquote", "q", "cite", "a", "img", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
           "caption", "colgroup", "col", "hr", "code", "pre", "kbd", "samp", "sub", "sup", "mark", "small", "abbr", "span",
           "div", "section", "article", "header", "footer", "main", "aside", "nav", "figure", "figcaption", "dl", "dt",
           "dd", "details", "summary", "address", "time", "wbr"}
# void elements never have content or a closing tag — <meta>/<link> in <head> used to swallow the whole page
VOID_ALL = {"area", "base", "br", "col", "embed", "hr", "img", "input", "keygen", "link", "meta", "param", "source",
            "track", "wbr"}
DROP_WITH_CONTENT = {"script", "iframe", "object", "noscript", "template", "svg", "math", "button", "select", "textarea",
                     "video", "audio", "canvas", "title", "frameset", "frame", "applet", "noembed", "noframes", "datalist",
                     "map"}
RENAME = {"center": ("div", "text-align: center"), "big": ("span", "font-size: larger"), "tt": ("code", "")}
BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote", "pre", "section", "article",
         "header", "footer", "main", "aside", "nav", "figure", "dl", "details", "address", "hr"}
GLOBAL_ATTRS = {"class", "id", "title", "style", "dir", "lang", "align"}
TAG_ATTRS = {"a": {"href"}, "img": {"src", "alt", "width", "height"}, "td": {"colspan", "rowspan", "width", "height", "valign"},
             "th": {"colspan", "rowspan", "width", "height", "valign", "scope"}, "table": {"width", "border", "cellpadding",
             "cellspacing"}, "col": {"span", "width"}, "colgroup": {"span", "width"}, "ol": {"start", "type", "reversed"},
             "ul": {"type"}, "li": {"value"}, "details": {"open"}, "time": {"datetime"}}
NUM_RE = re.compile(r"^\d{1,5}%?$")
CLASS_RE = re.compile(r"^[A-Za-z_][\w-]{0,63}$")
ID_RE = re.compile(r"^[A-Za-z][\w:.-]{0,63}$")
LINK_RE = re.compile(r"^(https?://|mailto:|#[\w:.-]{1,64}$)", re.I)
DATA_IMG = re.compile(r"^data:image/(png|jpe?g|webp|gif);base64,(.+)$", re.I | re.S)
DATA_FONT = re.compile(r"^data:(font/[\w.+-]+|application/(x-)?font[\w.+-]*|application/vnd\.ms-fontobject);base64,[A-Za-z0-9+/=\s]+$", re.I)
REF_RE = re.compile(r"^manual-img:(\d+|new\d+)$")
FONT_SIZES = {"1": "x-small", "2": "small", "3": "medium", "4": "large", "5": "x-large", "6": "xx-large", "7": "xxx-large"}


# ---------------------------------------------------------------- CSS

CSS_ESC = re.compile(r"\\([0-9a-fA-F]{1,6})\s?|\\(.)", re.S)
URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)


def _unescape_css(text: str) -> str:
    def one(m):
        if m.group(1):
            code = int(m.group(1), 16)
            return chr(code) if 0 < code < 0x110000 and not 0xD800 <= code <= 0xDFFF else ""
        return m.group(2) if m.group(2) != "\n" else ""
    return CSS_ESC.sub(one, text)


def _css_urls(text: str, store) -> str:
    """Pictures embedded as data: are kept on our server; fonts embedded as data: stay; anything else — none
    (a picture or a font from outside would tell a stranger who reads)."""
    def one(m):
        src = m.group(2).strip()
        if DATA_FONT.match(src):
            return f'url("{src}")'
        ref = store(src) if store else None
        return f"url({ref})" if ref else "none"  # no quotes: it lives inside style="" attributes too
    return URL_RE.sub(one, text)


def clean_css(text: str, store=None) -> str:
    """A whole sheet (<style>): comments, @import/@charset/@namespace, script-like values and outside urls go; the
    rest is kept as written. `<` is escaped so the sheet can never close its <style> tag."""
    text = re.sub(r"/\*.*?\*/", "", text or "", flags=re.S)
    text = _unescape_css(text)
    text = re.sub(r"@(import|charset|namespace)[^;{}]*;?", "", text, flags=re.I)
    text = _css_urls(text, store)
    # a declaration with anything script-like goes whole (old IE expression(), bindings, javascript: urls)
    text = re.sub(r"[^;{}]*(?:expression\s*\(|javascript\s*:|vbscript\s*:|-moz-binding|behavior\s*:)[^;{}]*;?", "", text, flags=re.I)
    text = text.replace("<", "\\3C ")
    return text.strip()[:MAX_CSS]


def clean_style(text: str, store=None) -> str:
    """One style="" attribute: only `property: value` pairs, cleaned like a sheet."""
    text = clean_css(text, store)
    urls: list[str] = []

    def keep(m):  # data: urls contain «;» — hide them while splitting
        urls.append(m.group(0))
        return f"\x00{len(urls) - 1}\x00"
    text = URL_RE.sub(keep, text)
    out = []
    for decl in text.split(";"):
        m = re.match(r"^\s*(-?[A-Za-z][A-Za-z-]{0,40})\s*:\s*(.+?)\s*$", decl, re.S)
        if m and "{" not in m.group(2) and "}" not in m.group(2):
            out.append(f"{m.group(1).lower()}: {m.group(2)}")
    return re.sub(r"\x00(\d+)\x00", lambda m: urls[int(m.group(1))], "; ".join(out))


# ---------------------------------------------------------------- HTML


class _Cleaner(HTMLParser):
    """A whitelist sanitizer into a token list: known tags keep their cleaned attributes, unknown ones are unwrapped
    (their text kept), dangerous ones dropped with their content. <style> sheets and the <body>'s class/style are
    collected for the page."""

    def __init__(self, store_image):
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple] = []  # ("start", tag, attrs) | ("end", tag) | ("void", tag, attrs) | ("text", html)
        self.skip = 0
        self.open: list[str] = []
        self.store = store_image
        self.css: list[str] = []
        self.in_style = False
        self.style_buf: list[str] = []
        self.body_class = ""
        self.body_style: list[str] = []
        self.text_seen = False

    # -- attributes
    def _attrs(self, tag: str, attrs) -> str:
        allowed = GLOBAL_ATTRS | TAG_ATTRS.get(tag, set())
        out = []
        for k, v in attrs:
            k = (k or "").lower()
            v = (v or "").strip()
            if k not in allowed:
                continue
            if k == "style":
                v = clean_style(v, self.store)
            elif k == "class":
                v = " ".join(c for c in v.split() if CLASS_RE.match(c))[:300]
            elif k == "id":
                v = v if ID_RE.match(v) else ""
            elif k in ("width", "height", "colspan", "rowspan", "span", "start", "border", "cellpadding", "cellspacing", "value"):
                v = v if NUM_RE.match(v) else ""
            elif k in ("align", "valign"):
                v = v.lower() if v.lower() in ("left", "right", "center", "justify", "top", "middle", "bottom", "baseline") else ""
            elif k == "dir":
                v = v.lower() if v.lower() in ("ltr", "rtl", "auto") else ""
            elif k == "lang":
                v = v if re.match(r"^[A-Za-z-]{1,20}$", v) else ""
            elif k == "type":
                v = v if v in ("1", "a", "A", "i", "I", "disc", "circle", "square") else ""
            elif k in ("reversed", "open"):
                out.append(f" {k}")
                continue
            elif k == "scope":
                v = v if v in ("row", "col", "rowgroup", "colgroup") else ""
            elif k in ("title", "alt", "datetime"):
                v = v[:300]
            elif k in ("href", "src"):
                continue  # handled by the tag
            if v:
                out.append(f' {k}="{escape(v, quote=True)}"')
        return "".join(out)

    def _close_implied(self, tag: str):
        # what a browser closes by itself, so a split never reopens a paragraph around a heading
        if tag in BLOCK and self.open and self.open[-1] == "p":
            self._end("p")
        if tag == "li" and self.open and self.open[-1] == "li":
            self._end("li")
        if tag in ("td", "th", "tr") and self.open and self.open[-1] in ("td", "th"):
            self._end(self.open[-1])
        if tag == "tr" and self.open and self.open[-1] == "tr":
            self._end("tr")
        if tag in ("dt", "dd") and self.open and self.open[-1] in ("dt", "dd"):
            self._end(self.open[-1])

    def _end(self, tag: str):
        while self.open:
            top = self.open.pop()
            self.tokens.append(("end", top))
            if top == tag:
                break

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        if tag == "style" and not self.skip:
            self.in_style, self.style_buf = True, []
            return
        if tag in VOID_ALL and tag not in ALLOWED:
            return  # <meta>, <link>, <input>…: nothing inside, nothing to wait for
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "body":
            a = dict(attrs)
            self.body_class = " ".join(c for c in (a.get("class") or "").split() if CLASS_RE.match(c))[:300]
            if a.get("style"):
                self.body_style.append(clean_style(a["style"], self.store))
            return
        extra = ""
        if tag == "font":  # an old <font>: kept as a <span> with the same look
            a = dict(attrs)
            styles = []
            if re.match(r"^#?[\w]{1,20}$", a.get("color") or ""):
                styles.append(f"color: {a['color']}")
            if (a.get("size") or "") in FONT_SIZES:
                styles.append(f"font-size: {FONT_SIZES[a['size']]}")
            if a.get("face") and re.match(r"^[\w ,'\"-]{1,100}$", a["face"]):
                styles.append(f"font-family: {a['face']}")
            tag, attrs, extra = "span", [(k, v) for k, v in attrs if k in ("class", "style")], "; ".join(styles)
        elif tag in RENAME:
            tag, extra = RENAME[tag]
        if tag not in ALLOWED:
            return
        self._close_implied(tag)
        attr = self._attrs(tag, attrs)
        if extra:
            attr = _merge_style(attr, extra)
        a = dict(attrs)
        if tag == "a":
            href = (a.get("href") or "").strip()
            if LINK_RE.match(href):
                attr += f' href="{escape(href, quote=True)}"'
                if not href.startswith("#"):
                    attr += ' target="_blank" rel="noopener noreferrer nofollow"'
        if tag == "img":
            ref = self.store((a.get("src") or "").strip())
            if not ref:  # only pictures kept on our server; external ones would tell a stranger who reads
                return
            attr = f' src="{ref}"' + attr
            self.tokens.append(("void", tag, attr))
            return
        if tag in VOID_ALL:
            self.tokens.append(("void", tag, attr))
            return
        self.tokens.append(("start", tag, attr))
        self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag == "style":
            return
        before = self.skip
        self.handle_starttag(tag, attrs)
        if tag in DROP_WITH_CONTENT and self.skip > before:
            self.skip -= 1  # <svg/>, <iframe/> — closed right away
        elif tag not in VOID_ALL and tag in ALLOWED and self.open and self.open[-1] == tag:
            self._end(tag)

    def handle_endtag(self, tag):
        if tag == "style":
            if self.in_style:
                self.css.append("".join(self.style_buf))
            self.in_style = False
            return
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        tag = {"font": "span", "center": "div", "big": "span", "tt": "code"}.get(tag, tag)
        if self.skip or tag not in ALLOWED or tag in VOID_ALL or tag not in self.open:
            return
        self._end(tag)

    def handle_data(self, data):
        if self.in_style:
            self.style_buf.append(data)
            return
        if not self.skip:
            if data.strip():
                self.text_seen = True
            self.tokens.append(("text", escape(data, quote=False)))

    def finish(self) -> list[tuple]:
        if self.in_style:
            self.css.append("".join(self.style_buf))
        while self.open:
            self.tokens.append(("end", self.open.pop()))
        return self.tokens


def _merge_style(attr: str, extra: str) -> str:
    m = re.search(r' style="([^"]*)"', attr)
    if m:
        return attr.replace(m.group(0), f' style="{escape(extra, quote=True)}; {m.group(1)}"')
    return attr + f' style="{escape(extra, quote=True)}"'


def _join(tokens) -> str:
    out = []
    for t in tokens:
        if t[0] == "text":
            out.append(t[1])
        elif t[0] == "end":
            out.append(f"</{t[1]}>")
        else:
            out.append(f"<{t[1]}{t[2]}>")
    return "".join(out)


class _Images:
    """Pictures found while reading a file: kept in memory until the content is accepted, then written once — a file
    that fails (empty, wrong) leaves nothing behind. References to this manual's own pictures stay as they are."""

    def __init__(self, conn: sqlite3.Connection, manual_id: int, write: bool = False):
        self.conn, self.manual_id, self.write = conn, manual_id, write
        self.new: dict[str, bytes] = {}
        self.by_hash: dict[str, str] = {}
        self.written: dict[str, str] = {}

    def __call__(self, src: str) -> str | None:
        src = (src or "").strip()
        m = re.match(r"^manual-img:(\d+)$", src)
        if m:
            row = self.conn.execute("SELECT id FROM manual_images WHERE id = ? AND manual_id = ?",
                                    (int(m.group(1)), self.manual_id)).fetchone()
            return f"manual-img:{row[0]}" if row else None
        m = DATA_IMG.match(src)
        if not m:
            return None
        try:
            data = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=False)
        except ValueError:
            return None
        if len(data) > MAX_IMAGE or not (media.sniff(data) or data[:6] in (b"GIF87a", b"GIF89a")):
            return None
        if self.write:
            return save_image(self.conn, self.manual_id, data)
        digest = hashlib.sha256(data).hexdigest()
        if digest not in self.by_hash:
            self.by_hash[digest] = f"manual-img:new{len(self.new)}"
            self.new[self.by_hash[digest]] = data
        return self.by_hash[digest]

    def commit(self, text: str) -> str:
        """Inside the saving transaction: the pictures are written (once, on first use) and their placeholders
        replaced."""
        def ref(m):
            ph = m.group(0)
            if ph not in self.written:
                self.written[ph] = save_image(self.conn, self.manual_id, self.new[ph]) or "" if ph in self.new else ""
            return self.written[ph]
        return re.sub(r"manual-img:new\d+\b", ref, text)


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


def _parse(html: str, store) -> _Cleaner:
    c = _Cleaner(store)
    c.feed(html or "")
    c.close()
    c.finish()
    return c


def clean(conn: sqlite3.Connection, manual_id: int, html: str) -> str:
    """One section's HTML (the editor of sections): cleaned body content; pictures written at once."""
    c = _parse(html, _Images(conn, manual_id, write=True))
    return _tidy(_join(c.tokens))


def _tidy(html: str) -> str:
    html = re.sub(r"<p>\s*</p>|\n{3,}", "", html).strip()
    while True:  # empty wrappers left at the very end by a split
        cut = re.sub(r"<(\w+)(?:\s[^>]*)?>\s*</\1>\s*$", "", html)
        if cut == html:
            return html.strip()
        html = cut


TAG_RE = re.compile(r"<[^>]+>")


def _visible(html: str) -> bool:
    return bool(TAG_RE.sub("", html).replace("&nbsp;", "").strip()) or "<img" in html


def split_tokens(tokens: list[tuple]) -> list[dict]:
    """Sections by <h2> (or <h1> when there are no <h2>); what comes before the first heading is «Введение». The
    heading stays in its section (with its look), and the blocks around it are closed at the end of a section and
    opened again at the start of the next one — every section keeps the page's wrappers and their styles. Blocks
    opened at the very end of a section with nothing in them are left out, so splitting a joined page again gives the
    same sections (and the same reading progress)."""
    tags = {t[1] for t in tokens if t[0] == "start"}
    level = "h2" if "h2" in tags else "h1" if "h1" in tags else None
    if level is None:
        html = _tidy(_join(tokens))
        return [{"title": "Методичка", "html": html}] if _visible(html) else []
    sections: list[dict] = []
    cur: list[tuple] = []
    stack: list[tuple] = []
    title: str | None = None
    grab: list[str] | None = None
    depth = 0

    def flush():
        toks, open_ = list(cur), list(stack)
        while toks and (toks[-1][0] == "start" or (toks[-1][0] == "text" and not toks[-1][1].strip())):
            if toks.pop()[0] == "start":
                open_.pop()
        html = _tidy(_join(toks) + "".join(f"</{t[1]}>" for t in reversed(open_)))
        if title is None:
            if _visible(html):
                sections.append({"title": "Введение", "html": html})
        else:
            sections.append({"title": title, "html": html})

    for t in tokens:
        if t[0] == "start" and t[1] == level and grab is None:
            flush()
            cur = list(stack)  # the wrappers, opened again
            grab, depth, title = [], 0, None
        cur.append(t)
        if t[0] == "text":
            if grab is not None:
                grab.append(t[1])
        elif t[0] == "start":
            stack.append(t)
            if grab is not None:
                depth += 1
        elif t[0] == "end":
            while stack:
                if stack.pop()[1] == t[1]:
                    break
            if grab is not None:
                depth -= 1
                if depth == 0:
                    text = re.sub(r"\s+", " ", unescape("".join(grab))).strip()
                    title = text[:MAX_TITLE] or f"Раздел {len(sections) + 1}"
                    grab = None
    if grab is not None:
        title = re.sub(r"\s+", " ", unescape("".join(grab))).strip()[:MAX_TITLE] or f"Раздел {len(sections) + 1}"
    flush()
    return sections


def split(html: str) -> list[dict]:
    """Sections of already clean HTML (kept for callers that have a string)."""
    return split_tokens(_parse(html, lambda src: src if REF_RE.match(src or "") else None).tokens)


def decode_html(raw: bytes) -> str:
    """The file's bytes as text: a BOM, UTF-16, UTF-8, the page's own <meta charset>, then Windows-1251 (Word and old
    editors in Russian). Files that aren't HTML at all get a concrete reason."""
    if not raw or not raw.strip(b" \t\r\n\x00"):
        raise ServiceError("Файл пустой", 422, "empty_file")
    if raw[:4] == b"%PDF":
        raise ServiceError("Это PDF, а нужен HTML. Сохраните документ как «Веб-страница (.html)»", 422, "not_html")
    if raw[:2] == b"PK":
        raise ServiceError("Это документ Word или архив, а нужен HTML. В Word: «Файл → Сохранить как → Веб-страница (.html)»",
                           422, "not_html")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "replace")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    head = raw[:400]
    if head.count(b"\x00") > len(head) // 8:  # UTF-16 without a BOM (every ASCII letter carries a zero byte)
        return raw.decode("utf-16-le" if head[1:2] == b"\x00" else "utf-16-be", "replace")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
        m = re.search(rb"""charset\s*=\s*["']?([A-Za-z0-9_-]{2,40})""", raw[:8192])
        if m:
            try:
                text = raw.decode(m.group(1).decode(), "strict")
            except (LookupError, UnicodeDecodeError):
                text = None
        if text is None:
            text = raw.decode("cp1251", "replace")
    if text.count("\x00") > 10:
        raise ServiceError("Файл повреждён или это не текстовый HTML", 422, "not_html")
    return text


def parse_document(conn: sqlite3.Connection, manual_id: int, html: str) -> tuple[list[dict], str, str, "_Images"]:
    """A whole page (a file or the visual editor): its sections, its style sheet, the body's class and the pictures
    waiting to be written. Plain text without any tags becomes paragraphs. Raises a concrete error when nothing
    can be shown."""
    if "<" not in html:
        html = "".join(f"<p>{escape(p.strip())}</p>" for p in re.split(r"\n\s*\n", html) if p.strip())
    images = _Images(conn, manual_id)
    c = _parse(html, images)
    sections = split_tokens(c.tokens)
    if not sections:
        raise ServiceError("В файле нет текста, который можно показать: страница пустая или весь текст внутри скриптов и форм",
                           422, "empty")
    css = "\n".join(clean_css(x, images) for x in c.css if x.strip())
    if c.body_style:
        css += "\nbody { " + "; ".join(x for x in c.body_style if x) + " }"
    if len(css) > MAX_CSS:
        raise ServiceError("Стили страницы слишком большие", 422, "too_long")
    return sections, css.strip(), c.body_class, images


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
                  note: str | None = None, raw_html: bool = True, page: dict | None = None,
                  images: "_Images | None" = None) -> dict:
    """The whole list in its order: existing ids are updated (renamed, edited, moved), new ones created, missing ones
    deleted. A new version is written; reading progress follows the content of each section. `page` — the page's
    style sheet and body class (ТЗ 29.10 §3); `images` — pictures of a parsed file, written only now."""
    if not isinstance(items, list) or len(items) > 200:
        raise ServiceError("Слишком много разделов", 422, "too_many")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return view_admin(conn, manual_id)
        actor = _admin(conn, actor)
        manual = get(conn, manual_id)
        if page is not None:
            css = images.commit(page.get("css") or "") if images else page.get("css") or ""
            conn.execute("UPDATE manuals SET css = ?, body_class = ? WHERE id = ?",
                         (css or None, page.get("body_class") or None, manual_id))
        current = {s["id"]: s for s in sections(conn, manual_id)}
        keep = set()
        for pos, item in enumerate(items):
            title = " ".join(str(item.get("title") or "").split())[:MAX_TITLE] or f"Раздел {pos + 1}"
            html = clean(conn, manual_id, unrender(str(item.get("html") or ""))) if raw_html else str(item.get("html") or "")
            if images:
                html = images.commit(html)
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
        css_now = conn.execute("SELECT css FROM manuals WHERE id = ?", (manual_id,)).fetchone()[0]
        conn.execute("""INSERT INTO manual_versions (manual_id, version, snapshot, created_at, created_by, note, css)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (manual_id, version, json.dumps(snapshot, ensure_ascii=False), now_iso(), actor["telegram_id"], note,
                      css_now))
        log_event(conn, user_id=actor["id"], actor_id=actor["telegram_id"], event_type="MANUAL_SAVED", key=key,
                  new={"manual": manual_id, "version": version, "sections": len(snapshot)}, description=note)
    return view_admin(conn, manual_id)


def upload(conn: sqlite3.Connection, actor: dict, manual_id: int, raw: bytes | str, key: str) -> dict:
    """«Загрузить HTML» — replaces the content (a new version; the old one stays in the history). The file's bytes are
    decoded here (UTF-8, UTF-16, Windows-1251…); nothing is written unless the file has something to show."""
    if len(raw or b"") > MAX_HTML:
        raise ServiceError("Файл слишком большой (до 8 МБ вместе с картинками)", 413, "too_big")
    _check_key(key)
    html = decode_html(raw) if isinstance(raw, bytes) else raw
    return save_document(conn, actor, manual_id, html, key, "загружен HTML")


def save_document(conn: sqlite3.Connection, actor: dict, manual_id: int, html: str, key: str,
                  note: str = "изменено в редакторе") -> dict:
    """ТЗ 29.10 §3–4: a whole page — an uploaded file or the visual editor's result — becomes the manual's sections and
    style sheet. The same manual: its id, its people and the reading progress of unchanged sections stay."""
    if len(html or "") > MAX_HTML:
        raise ServiceError("Методичка слишком большая", 413, "too_big")
    _check_key(key)
    with transaction(conn):
        if _seen(conn, key):
            return view_admin(conn, manual_id)
        _admin(conn, actor)
        get(conn, manual_id)
    parts, css, body_class, images = parse_document(conn, manual_id, unrender(html))
    for p in parts:
        if len(p["html"]) > MAX_SECTION:
            raise ServiceError(f"Раздел «{p['title']}» слишком большой — разделите его заголовками", 422, "too_long")
    # sections with the same title keep their id (and so the progress, if the text didn't change)
    old = {s["title"]: s["id"] for s in sections(conn, manual_id)}
    items = [{"id": old.pop(p["title"], None), **p} for p in parts]
    return save_sections(conn, actor, manual_id, items, key, note, raw_html=False,
                         page={"css": css, "body_class": body_class}, images=images)


def _has_heading(html: str) -> bool:
    return bool(re.search(r"<h[12][\s>]", html))


def document(m: dict, secs: list[dict]) -> str:
    """The whole manual as one page body for the visual editor: sections in order; a section saved without its own
    heading (older ones) gets its title as «Заголовок 2», so saving the page splits it the same way again."""
    out = []
    for i, s in enumerate(secs):
        if not _has_heading(s["html"]) and not (i == 0 and s["title"] == "Введение"):
            out.append(f"<h2>{escape(s['title'])}</h2>")
        out.append(s["html"])
    return "\n".join(out)


def view_admin(conn: sqlite3.Connection, manual_id: int) -> dict:
    m = get(conn, manual_id)
    versions = [dict(r) for r in conn.execute(
        "SELECT version, created_at, note FROM manual_versions WHERE manual_id = ? ORDER BY version DESC LIMIT 20", (manual_id,))]
    secs = sections(conn, manual_id)
    return {**m, "css": render(m.get("css") or ""), "body_class": m.get("body_class") or "",
            "sections": [{"id": s["id"], "title": s["title"], "html": render(s["html"]), "heading": _has_heading(s["html"])}
                         for s in secs],
            "document": render(document(m, secs)), "versions": versions}


def render(html: str) -> str:
    """Pictures become short-lived signed links at reading time (in tags and in style sheets)."""
    html = re.sub(r'src="manual-img:(\d+)"', lambda m: f'src="{media.photo_url(int(m.group(1)), kind="manualimg")}"', html)
    return re.sub(r"url\(manual-img:(\d+)\)", lambda m: f"url({media.photo_url(int(m.group(1)), kind='manualimg')})", html)


def unrender(html: str) -> str:
    """The editor sends signed links back: they become references again."""
    html = re.sub(r'src="[^"]*/api/media/manualimg/(\d+)\?[^"]*"', r'src="manual-img:\1"', html)
    return re.sub(r"""url\(\s*(?:&quot;|['"])?[^'"()]*?/api/media/manualimg/(\d+)\?[^'"()]*?(?:&quot;|['"])?\s*\)""",
                  r"url(manual-img:\1)", html)


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
    m = get(conn, manual_id)
    return {"id": m["id"], "title": m["title"], "total": total, "read": len(read),
            # ТЗ 29.10 §3: the page's own look — shown in an isolated frame (no scripts) in the app
            "css": render(m.get("css") or ""), "body_class": m.get("body_class") or "",
            "sections": [{"id": s["id"], "title": s["title"], "read": s["id"] in read, "html": render(s["html"]),
                          "heading": _has_heading(s["html"])} for s in sections(conn, manual_id)]}


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
