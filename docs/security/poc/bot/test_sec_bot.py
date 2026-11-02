import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from harness import call_update, feed, make, msg_update, sent_to  # noqa: E402

from app import app_settings, invites  # noqa: E402
from app import permissions as perms  # noqa: E402
from app import services as svc  # noqa: E402
from app.db import connect  # noqa: E402


def fresh():
    return connect(":memory:")


def test_stranger_gets_answer_to_archive_here():
    conn = fresh()
    bot, sess, dp, _ = make(conn)
    feed(bot, dp, msg_update(5555, "/archive_here"))
    print(sent_to(sess, 5555))
    assert sent_to(sess, 5555) == ["Отправьте /archive_here внутри закрытой группы, куда бот добавлен."]
    # control: other commands — silence
    sess.calls.clear()
    for cmd in ("/group_pre", "/alice_here", "/plus_here", "/group_off", "/archive_off", "/finances", "/fakt 1", "hello"):
        feed(bot, dp, msg_update(5555, cmd))
    assert sent_to(sess, 5555) == []


def test_non_owner_cannot_bind_chats():
    conn = fresh()
    bot, sess, dp, _ = make(conn)
    owner = svc.ensure_admin(conn, 999, "O", "W", "own")
    # a regular approved admin (not in ADMIN_IDS)
    u, _ = svc.create_registration(conn, 777, "adm", "A", "B")
    svc.decide_registration(conn, u["id"], True, 999)
    conn.execute("UPDATE users SET role='admin' WHERE id=?", (u["id"],))
    for cmd in ("/archive_here", "/group_pre", "/group_main", "/alice_here", "/plus_here"):
        feed(bot, dp, msg_update(777, cmd, chat_id=-100500, chat_type="supergroup", is_forum=True, title="evil"))
    assert app_settings.get_raw(conn, "archive_chat_id") is None
    assert app_settings.get_raw(conn, "group_pre_chat_id") is None
    assert app_settings.get_raw(conn, "alice_chat_id") is None
    assert [c for c in sess.calls if type(c).__name__ == "SendMessage"] == []
    # the owner can
    feed(bot, dp, msg_update(999, "/archive_here", chat_id=-100501, chat_type="supergroup", title="arch"))
    assert app_settings.get_raw(conn, "archive_chat_id") == "-100501"
    del owner


def test_ref_invitee_can_take_owner_style_chat_registration():
    """A person who came by an employee's referral link (should fill the questionnaire → training) forges
    callback 'register' and files a direct registration request; admins get a plain «Новая заявка» card and
    decide_registration() approves it (no application → is_candidate False)."""
    conn = fresh()
    bot, sess, dp, _ = make(conn)
    svc.ensure_admin(conn, 999, "O", "W", "own")
    emp, _ = svc.create_registration(conn, 2001, "emp", "E", "M")
    svc.decide_registration(conn, emp["id"], True, 999)
    link = invites.current_ref(conn, svc.get_user(conn, emp["id"]))
    feed(bot, dp, msg_update(3001, f"/start ref_{link['token']}"))
    assert invites.entry(conn, 3001) == "ref"
    feed(bot, dp, call_update(3001, "register"))
    feed(bot, dp, msg_update(3001, "Хакер Хакерович"))
    feed(bot, dp, call_update(3001, "reg_send"))
    user = svc.get_user_by_tg(conn, 3001)
    assert user is not None and user["status"] == "pending"
    cards = [c for c in sess.calls if type(c).__name__ == "SendMessage" and int(c.chat_id) == 999]
    assert any("Новая заявка на регистрацию" in c.text for c in cards)
    # the admin presses «Принять» → straight into the team, no questionnaire, no training
    u2, ch = svc.decide_registration(conn, user["id"], True, 999)
    assert ch and u2["status"] == "approved"


def test_owner_removed_from_admin_ids_stays_admin(monkeypatch):
    conn = fresh()
    from app.config import settings
    svc.ensure_admin(conn, 999, "O", "W", "own")
    u = svc.get_user_by_tg(conn, 999)
    assert perms.role_of(u) == "owner"
    object.__setattr__(settings, "admin_ids", frozenset({123}))
    try:
        u = svc.get_user_by_tg(conn, 999)
        print(perms.role_of(u), perms.capabilities(u))
        assert perms.role_of(u) == "admin" and perms.can(u, "registrations") and perms.can(u, "broadcast_all")
    finally:
        object.__setattr__(settings, "admin_ids", frozenset({999}))
