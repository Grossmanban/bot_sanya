"""Whitelist sanitizer for Telegram HTML (parse_mode=HTML).

The Mini App editor produces Telegram-style HTML; this module accepts only the tags
Telegram supports, checks link schemes, keeps nesting valid (no formatting inside
code, no nested quotes or links), closes anything left open and escapes all text.
"""

import re
from html import escape
from html.parser import HTMLParser

TAGS = {
    "b": "b", "strong": "b",
    "i": "i", "em": "i",
    "u": "u", "ins": "u",
    "s": "s", "strike": "s", "del": "s",
    "tg-spoiler": "tg-spoiler",
    "code": "code", "pre": "pre",
    "blockquote": "blockquote",
    "a": "a",
}
VOID = {"br", "img", "hr", "input", "meta", "link", "wbr"}
LINK_RE = re.compile(r"^(https?://|tg://)[^\s\"'<>]+$", re.I)
MAX_TEXT = 4000  # Telegram limit is 4096 characters for the whole message


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.text: list[str] = []
        self.stack: list[tuple[str, str | None]] = []  # (source tag, emitted tag or None)

    def _open(self) -> list[str]:
        return [m for _, m in self.stack if m]

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.out.append("\n")
            self.text.append("\n")
            return
        if tag in VOID:
            return
        attrs = dict(attrs)
        mapped = TAGS.get(tag)
        if tag == "span" and "tg-spoiler" in (attrs.get("class") or "").split():
            mapped = "tg-spoiler"
        opened = self._open()
        if mapped and (
            "code" in opened or "pre" in opened  # code is plain text in Telegram
            or (mapped == "blockquote" and "blockquote" in opened)
            or (mapped == "a" and "a" in opened)
        ):
            mapped = None
        if mapped == "a":
            href = (attrs.get("href") or "").strip()
            if not LINK_RE.match(href):
                mapped = None
            else:
                self.out.append(f'<a href="{escape(href, quote=True)}">')
        elif mapped:
            self.out.append(f"<{mapped}>")
        self.stack.append((tag, mapped))

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                while len(self.stack) > i:
                    _, mapped = self.stack.pop()
                    if mapped:
                        self.out.append(f"</{mapped}>")
                return

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))
        self.text.append(data)

    def finish(self) -> tuple[str, str]:
        self.close()
        while self.stack:
            _, mapped = self.stack.pop()
            if mapped:
                self.out.append(f"</{mapped}>")
        html = "".join(self.out)
        # drop empty formatting like <b></b> left by the editor
        prev = None
        while prev != html:
            prev = html
            html = re.sub(r"<(b|i|u|s|tg-spoiler|code|pre|blockquote)></\1>", "", html)
            html = re.sub(r'<a href="[^"]*"></a>', "", html)
        return html.strip(), "".join(self.text).strip()


def sanitize(html: str) -> tuple[str, str]:
    """Return (telegram_html, plain_text)."""
    parser = _Sanitizer()
    parser.feed(html or "")
    return parser.finish()


def plain_to_html(text: str) -> str:
    return escape(text, quote=False)
