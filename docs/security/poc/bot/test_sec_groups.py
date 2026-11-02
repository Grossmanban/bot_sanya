import asyncio
import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import harness  # noqa: E402,F401  (env + sys.path)

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4] / "backend"))
from html.parser import HTMLParser  # noqa: E402

from app import groups, invites, notify  # noqa: E402
from app import services as svc  # noqa: E402
from app.db import connect  # noqa: E402
from tests.test_groups import MAIN, PRE, FakeBot, connected  # noqa: E402


def run(c):
    return asyncio.run(c)


def approved(conn, tg, first="Иван", last="Петров", username="u"):
    u, _ = svc.create_registration(conn, tg, username, first, last)
    u, _ = svc.decide_registration(conn, u["id"], True, 999)
    return u


def test_removed_member_rejoins_by_old_personal_link():
    conn = connect(":memory:")
    owner = svc.ensure_admin(conn, 999, "O", "W", "own")
    connected(conn, owner)
    bot = FakeBot()
    u = approved(conn, 4001)
    inv = run(groups.invite(conn, bot, u, "main", owner))
    assert run(groups.join_request(conn, bot, MAIN, u["telegram_id"], inv["link"]))[0]
    # an admin removes the person from «Основная команда» (board action remove_main → groups.remove)
    run(groups.remove(conn, bot, u, "main", owner))
    assert groups.states(conn)[(u["id"], "main")]["state"] == "out"
    # the same link (Telegram link still valid for 7 days, never revoked) — approved again
    ok, _ = run(groups.join_request(conn, bot, MAIN, u["telegram_id"], inv["link"]))
    assert ok and bot.answers[-1] == ("approve", MAIN, u["telegram_id"])


class Strict(HTMLParser):
    """Rough check of Telegram's rule: every opened tag closed, no broken tag/entity at the end."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()


class Rec:
    def __init__(self):
        self.photos = []
        self.msgs = []

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        self.photos.append(caption)

    async def send_message(self, chat_id, text, **kw):
        self.msgs.append((chat_id, text))


def test_photo_caption_cut_inside_html_breaks_archive_copy():
    conn = connect(":memory:")
    from app import app_settings
    app_settings.set_raw(conn, "archive_chat_id", "-1009")
    rec = Rec()
    n = notify.Notifier(rec, conn)
    # an employee's own name (questionnaire allows any 100 chars): '&' is escaped to '&amp;' — 5x longer
    emp = approved(conn, 4100, first='"' * 49, last='"' * 49, username=None)
    actor = svc.ensure_admin(conn, 999, "Саня", "Владелец", None)
    text = (f"✅ ОТЧЁТ ПРИНЯТ — {notify.who(emp)}\nпн 01.01 10:00–18:00, «Точка»\nПлан 20, факт 20 шт.\n"
            f"Комментарий: {notify.escape('Скриншот не совпадает с планом, переделай отчёт и приложи полный экран с датой и временем. ' * 2)}\nПроверил: {notify.who(actor)}")
    run(n.archive(["отчёт", "принят"], text, emp, photo="AgACfileid"))
    cap = rec.photos[-1]
    print(len(text), len(cap), repr(cap[-60:]))
    p = Strict()
    p.feed(cap)
    assert p.stack, "caption must end with an unclosed tag for this PoC"
    # the visible text is far below Telegram's 1024 limit — the cut was not even needed
    import re
    from html import unescape
    print("visible length:", len(unescape(re.sub(r"<[^>]+>", "", text))))


def test_unescaped_names_in_group_alerts():
    conn = connect(":memory:")
    owner = svc.ensure_admin(conn, 999, "O", "W", "own")
    connected(conn, owner)
    rec = Rec()
    n = notify.Notifier(rec, conn)
    u = approved(conn, 4200, first="<b", last="Иванов")
    bot = FakeBot(rights=False)
    bot.members[(PRE, u["telegram_id"])] = "member"

    async def nosleep(*a, **k):
        return None
    asyncio_sleep = asyncio.sleep
    asyncio.sleep = nosleep
    try:
        run(groups.kick(conn, bot, n, u, ("pre",), owner))
    finally:
        asyncio.sleep = asyncio_sleep
    texts = [t for _, t in rec.msgs]
    print(texts)
    assert any("<b" in t and "&lt;b" not in t for t in texts)


def test_access_attempts_unbounded_rows():
    conn = connect(":memory:")
    for i in range(2000):
        invites.admit(conn, {"id": 6001, "first_name": "x"}, f"ref_{i:06d}")
    rows = conn.execute("SELECT COUNT(*) FROM access_attempts WHERE telegram_id = 6001").fetchone()[0]
    answered = conn.execute("SELECT SUM(answered) FROM access_attempts WHERE telegram_id = 6001").fetchone()[0]
    print(rows, answered)
    assert rows == 2000 and answered == 2000
