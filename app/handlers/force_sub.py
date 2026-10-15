from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
router=Router()
@router.message(Command("checkchannels"))
async def checkchannels(message: Message,bot,db,settings):
    if message.from_user.id!=settings.owner_id: await message.answer("⛔ Owner only."); return
    rows=await db.list_force_sub(); lines=[]
    for r in rows:
        try:
            chat=await bot.get_chat(r['channel_id']); lines.append(f"✅ {r['display_name']} | {r['channel_id']} | {r['channel_type']} | bot can access channel; membership checks require a real user check")
        except Exception as exc: lines.append(f"⚠️ {r['display_name']} | {r['channel_id']} | {r['channel_type']} | access: unavailable ({type(exc).__name__}); add bot as admin")
    await message.answer("📢 CHANNEL DIAGNOSTICS\n\n"+("\n".join(lines) if lines else "No channels configured."))
