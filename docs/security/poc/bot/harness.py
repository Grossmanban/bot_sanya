"""Throwaway harness: feeds real Telegram updates through the real router with a recording Bot session."""
import os
import sys

os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("ADMIN_IDS", "999")
os.environ.setdefault("RUN_BOT", "0")
os.environ.setdefault("FRONTEND_DIST", "/nonexistent")
os.environ.setdefault("MEDIA_DIR", __import__("tempfile").mkdtemp(prefix="media-"))
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4] / "backend"))

import asyncio  # noqa: E402
from datetime import datetime  # noqa: E402

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import CallbackQuery, Chat, ChatMemberAdministrator, Message, Update, User  # noqa: E402

from app.bot import build_router  # noqa: E402
from app.notify import Notifier  # noqa: E402


class RecSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.n = 100

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name in ("SendMessage", "SendPhoto", "SendDocument", "EditMessageText", "EditMessageCaption"):
            self.n += 1
            chat_id = getattr(method, "chat_id", None) or 1
            return Message(message_id=self.n, date=datetime.now(), chat=Chat(id=int(chat_id), type="private"),
                           text=getattr(method, "text", None) or "x")
        if name == "GetChatMember":
            return ChatMemberAdministrator(user=User(id=method.user_id, is_bot=True, first_name="b"), can_be_edited=False,
                                           is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
                                           can_manage_video_chats=True, can_restrict_members=True, can_promote_members=False,
                                           can_change_info=True, can_invite_users=True, can_post_stories=False,
                                           can_edit_stories=False, can_delete_stories=False, can_manage_topics=True)
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    async def close(self):
        pass


def make(conn):
    sess = RecSession()
    bot = Bot("123456:TEST-TOKEN", session=sess)
    notifier = Notifier(bot, conn)
    dp = Dispatcher()
    dp.include_router(build_router(conn, notifier))
    return bot, sess, dp, notifier


_uid = {"n": 0}


def _id():
    _uid["n"] += 1
    return _uid["n"]


def msg_update(tg_id, text, chat_id=None, chat_type="private", first="X", is_forum=None, title=None):
    chat = Chat(id=chat_id if chat_id is not None else tg_id, type=chat_type, is_forum=is_forum, title=title)
    m = Message(message_id=_id(), date=datetime.now(), chat=chat, from_user=User(id=tg_id, is_bot=False, first_name=first),
                text=text)
    return Update(update_id=_id(), message=m)


def call_update(tg_id, data, chat_id=None):
    m = Message(message_id=_id(), date=datetime.now(), chat=Chat(id=chat_id or tg_id, type="private"), text="old",
                from_user=User(id=42, is_bot=True, first_name="bot"))
    c = CallbackQuery(id=str(_id()), from_user=User(id=tg_id, is_bot=False, first_name="X"), chat_instance="ci",
                      data=data, message=m)
    return Update(update_id=_id(), callback_query=c)


def feed(bot, dp, upd):
    return asyncio.get_event_loop().run_until_complete(dp.feed_update(bot, upd)) if False else asyncio.run(dp.feed_update(bot, upd))


def sent_to(sess, chat_id):
    out = []
    for m in sess.calls:
        name = type(m).__name__
        if name in ("SendMessage",) and int(m.chat_id) == chat_id:
            out.append(m.text)
        if name == "AnswerCallbackQuery":
            out.append(("alert", m.text))
    return out
