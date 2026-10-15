from aiogram.types import CallbackQuery, Message
from app.database import Database

async def is_authorized(user_id: int, db: Database, owner_id: int) -> bool:
    return await db.is_admin(user_id, owner_id)

async def require_admin(message: Message, db: Database, owner_id: int) -> bool:
    if not await is_authorized(message.from_user.id, db, owner_id):
        await message.answer("⛔ You are not authorized to use this command.")
        return False
    return True

async def require_callback_admin(callback: CallbackQuery, db: Database, owner_id: int) -> bool:
    if not await is_authorized(callback.from_user.id, db, owner_id):
        await callback.answer("Not authorized", show_alert=True)
        return False
    return True
