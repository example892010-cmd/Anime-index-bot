from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from app.keyboards import main_keyboard
from app.utils import safe_name
from app.services.force_sub_service import ForceSubService
from app.keyboards import force_sub_keyboard
router=Router()
async def force_gate(event, bot, db):
    service=ForceSubService(db)
    if not await service.enabled(): return True
    missing, errors=await service.missing_channels(bot,event.from_user.id)
    if missing:
        text=await db.get_setting("force_sub_message")
        if errors: text += "\n\n⚠️ Some channels could not be checked; contact an administrator."
        if isinstance(event, CallbackQuery):
            await event.message.answer(text, reply_markup=force_sub_keyboard(missing))
        else:
            await event.answer(text, reply_markup=force_sub_keyboard(missing))
        return False
    return True
@router.message(Command("start"))
async def start(message: Message, db):
    await db.upsert_user(message.from_user.id,message.from_user.first_name or "",message.from_user.username)
    welcome=await db.get_setting("welcome_message")
    text=welcome.replace("{first_name}",safe_name(message.from_user.first_name))
    image=await db.get_setting("start_image")
    updates=await db.get_setting("updates_channel")
    try:
        if image: await message.answer_photo(image,caption=text,parse_mode=None,reply_markup=main_keyboard(updates))
        else: await message.answer(text,parse_mode=None,reply_markup=main_keyboard(updates))
    except Exception: await message.answer(text,parse_mode=None,reply_markup=main_keyboard(updates))
@router.callback_query(lambda c:c.data=="help")
async def help_cb(callback: CallbackQuery):
    await callback.answer(); await callback.message.answer("Send an anime title such as Naruto, JJK, or Black Clover. Use /start to see the menu.")
@router.callback_query(lambda c:c.data=="search")
async def search_cb(callback: CallbackQuery): await callback.answer("Send an anime title to search.")
@router.callback_query(lambda c:c.data=="cancel")
async def cancel_cb(callback: CallbackQuery, state): await state.clear(); await callback.answer("Cancelled")
@router.callback_query(lambda c:c.data=="verify_sub")
async def verify_cb(callback: CallbackQuery, bot, db):
    if await force_gate(callback,bot,db): await callback.answer("Membership verified.",show_alert=True)
    else: await callback.answer("Please join every required channel.",show_alert=True)
