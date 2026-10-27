"""Entry point: one process runs the Telegram bot (long polling) and the Mini App API/static files.

    python -m app.main          (from the backend/ directory)
"""

import asyncio
import logging
import os

import uvicorn
from aiogram import Bot, Dispatcher

from .api import create_app
from .bot import build_router, setup_menu_button
from .config import settings
from .db import connect
from .jobs import run_scheduler
from .notify import Notifier

log = logging.getLogger("app")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not set (see .env.example)")
    conn = connect(settings.db_path)

    bot = Bot(settings.bot_token) if settings.run_bot else None
    notifier = Notifier(bot, conn)
    bot_username = None
    if bot is not None:
        me = await bot.get_me()
        bot_username = me.username
        log.info("bot @%s started", bot_username)

    app = create_app(conn, notifier, bot_username)
    server = uvicorn.Server(
        uvicorn.Config(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")),
                       log_level="info", proxy_headers=True)
    )

    tasks = [asyncio.create_task(server.serve())]
    if bot is not None:
        dp = Dispatcher()
        dp.include_router(build_router(conn, notifier))
        tasks.append(asyncio.create_task(setup_menu_button(bot, conn)))
        tasks.append(asyncio.create_task(dp.start_polling(bot, handle_signals=False)))
        tasks.append(asyncio.create_task(run_scheduler(conn, notifier)))

    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()
    finally:
        for t in tasks:
            t.cancel()
        if bot is not None:
            await bot.session.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
