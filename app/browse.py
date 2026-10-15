from html import escape
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.services.force_sub_service import ForceSubService
from app.keyboards import force_sub_keyboard

router = Router()

async def show_browse(message, db, bot, user_id: int):
    gate = ForceSubService(db)
    if not await gate.allowed(bot, user_id):
        missing, _ = await gate.missing_channels(bot, user_id)
        await message.answer(await db.get_setting("force_sub_message"), reply_markup=force_sub_keyboard(missing))
        return
    rows = await db._fetchall("SELECT title FROM anime WHERE active=1 ORDER BY title LIMIT 50")
    body = "\\n".join("• " + escape(r[0]) for r in rows)
    await message.answer("📚 Anime catalog:\\n" + (body if body else "No anime has been added yet."))

@router.message(Command("browse"))
async def browse(message: Message, db, bot):
    await show_browse(message, db, bot, message.from_user.id)

@router.callback_query(lambda c: c.data == "browse")
async def browse_cb(callback, db, bot):
    await callback.answer()
    await show_browse(callback.message, db, bot, callback.from_user.id)
