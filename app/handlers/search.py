from aiogram import Router
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from html import escape
from app.filters import NonCommandText
from app.services.search_service import SearchService
from app.services.force_sub_service import ForceSubService
from app.keyboards import force_sub_keyboard
router=Router()
@router.message(NonCommandText())
async def search(message: Message, db, bot):
    query=message.text.strip()
    gate=ForceSubService(db)
    if not await gate.allowed(bot,message.from_user.id):
        missing,_=await gate.missing_channels(bot,message.from_user.id)
        await db.record_search(message.from_user.id,message.from_user.first_name or "",message.from_user.username,query,None)
        await message.answer(await db.get_setting("force_sub_message"),reply_markup=force_sub_keyboard(missing)); return
    rows=await SearchService(db).search(query)
    await db.record_search(message.from_user.id,message.from_user.first_name or "",message.from_user.username,query,rows[0]["id"] if rows else None)
    if not rows:
        suggestions=await SearchService(db).suggestions(query)
        text="❌ Anime not found.\n\nPlease check the spelling or try another title."
        if suggestions: text += "\n\nDid you mean:\n"+"\n".join(f"• {r['title']}" for r in suggestions)
        await message.answer(text); return
    for row in rows[:5]:
        links=await db.links_for_anime(row["id"])
        text=f"✦ <b>{escape(row['title'])}</b>\n\n📺 Type: {escape(row['anime_type'])}\n🎙️ Audio: {escape(row['audio_language'] or 'Not specified')}\n📦 Available links: {len(links)}"
        if row["description"]: text+=f"\n\n{escape(row['description'])}"
        buttons=[[InlineKeyboardButton(text=l["display_title"],url=l["url"])] for l in links]
        markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        try:
            if row["poster"]: await message.answer_photo(row["poster"],caption=text,parse_mode="HTML",reply_markup=markup)
            else: await message.answer(text,parse_mode="HTML",reply_markup=markup)
        except Exception: await message.answer(text,parse_mode="HTML",reply_markup=markup)
