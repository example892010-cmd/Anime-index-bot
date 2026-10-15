from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import load_config
from app.database import Database
from app.handlers import (
    start,
    search,
    browse,
    admin_panel,
    user_management,
    force_sub,
)


async def health_check(request: web.Request) -> web.Response:
    """Simple HTTP endpoint for Render's health checks."""
    return web.json_response({"status": "ok", "service": "anime-index-bot"})


async def start_http_server() -> web.AppRunner:
    """Start a lightweight HTTP server on Render's assigned port."""
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()

    logging.getLogger(__name__).info("HTTP health server listening on port %s", port)
    return runner


async def main() -> None:
    settings = load_config()

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    db = await Database(settings.database_path).connect()

    # Seed environment defaults only when the database has no value yet.
    # Owner-edited runtime settings must survive restarts.
    for key, value in (
        ("start_image", settings.start_image),
        ("updates_channel", settings.updates_channel),
        ("bot_display_name", settings.bot_name),
    ):
        if value and not await db.get_setting(key, ""):
            await db.set_setting(key, value)

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    for router in (
        start.router,
        admin_panel.router,
        user_management.router,
        force_sub.router,
        browse.router,
        search.router,
    ):
        dp.include_router(router)

    http_runner = None
    try:
        http_runner = await start_http_server()
        await dp.start_polling(bot, db=db, settings=settings)
    finally:
        if http_runner is not None:
            await http_runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
