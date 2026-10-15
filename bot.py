from __future__ import annotations
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from config import load_config
from app.database import Database
from app.handlers import start, search, browse, admin_panel, user_management, force_sub

async def main() -> None:
    settings=load_config()
    logging.basicConfig(level=getattr(logging,settings.log_level,logging.INFO),format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db=await Database(settings.database_path).connect()
    for key, value in (("start_image", settings.start_image), ("updates_channel", settings.updates_channel), ("bot_display_name", settings.bot_name)):
        if value: await db.set_setting(key, value)
    bot=Bot(settings.bot_token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp=Dispatcher()
    for router in (start.router, admin_panel.router, user_management.router, force_sub.router, browse.router, search.router): dp.include_router(router)
    try:
        await dp.start_polling(bot,db=db,settings=settings)
    finally:
        await db.close(); await bot.session.close()

if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt,SystemExit): pass
