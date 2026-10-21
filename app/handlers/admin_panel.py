from __future__ import annotations
import tempfile
from pathlib import Path
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, FSInputFile
from app.keyboards import admin_keyboard, cancel_keyboard
from app.permissions import require_admin, require_callback_admin
from app.states import AddAnimeStates, AdminStates
from app.services.anime_service import AnimeService
from app.services.backup_service import BackupService
from app.services.link_service import LinkService
from app.utils import normalize_title, valid_url
router=Router()

ONGOING_ANIMES_URL = "https://t.me/+cVwTJSgsEvUzNWZl"

def parse_add_anime_input(value: str) -> tuple[str, str, str, str]:
    """Parse ``title - download URL - color - complete post text``.

    The fourth field is deliberately not stripped or normalized so Telegram
    newlines, emojis, and formatting are preserved exactly as supplied.
    """
    parts = value.split(" - ", 3)
    if len(parts) != 4:
        raise ValueError("Use: 🍥 Title - https://download-url - green - complete post text")
    title, download_url, color, post_text = parts
    if not title.strip():
        raise ValueError("Anime title cannot be empty.")
    if not valid_url(download_url.strip()):
        raise ValueError("Download URL must be a valid http(s) or Telegram URL.")
    if not color.strip():
        raise ValueError("Button color cannot be empty.")
    if not post_text:
        raise ValueError("Complete post text cannot be empty.")
    return title.strip(), download_url.strip(), color.strip().casefold(), post_text

@router.message(Command("admin"))
async def admin(message: Message, db, settings):
    if await require_admin(message,db,settings.owner_id): await message.answer("🛠️ ANIFLIX ADMIN PANEL",reply_markup=admin_keyboard())
@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext): await state.clear(); await message.answer("Cancelled. You can start a new operation.")
@router.message(Command("addlink"))
async def addlink(message: Message, db, settings):
    if not await require_admin(message, db, settings.owner_id): return
    parts=(message.text or "").split(maxsplit=3)
    if len(parts)!=4 or not parts[1].isdigit():
        await message.answer("Usage: /addlink ANIME_ID DISPLAY_TITLE URL")
        return
    try:
        link_id=await LinkService(db).add(int(parts[1]), parts[2], "external", parts[3])
        await db.log_audit(message.from_user.id,"add_link",str(link_id))
        await message.answer(f"✅ Link saved (ID {link_id}).")
    except ValueError as exc:
        await message.answer(f"Could not save link: {exc}")
@router.callback_query(lambda c:c.data and c.data.startswith("admin:"))
async def admin_action(callback: CallbackQuery, state: FSMContext, db, settings):
    if not await require_callback_admin(callback,db,settings.owner_id): return
    action=callback.data.split(":",1)[1]; await callback.answer()
    if action=="close": await callback.message.edit_text("Admin panel closed."); return
    if action=="add": await state.set_state(AddAnimeStates.title); await callback.message.answer("Send: 🍥 Title - https://download-url - green - complete post text\nThe post text after the third separator is preserved exactly. Use /cancel to stop.",reply_markup=cancel_keyboard()); return
    if action=="stats":
        s=await db.stats(); await callback.message.answer("📊 STATISTICS\n\n"+"\n".join(f"{k.replace('_',' ').title()}: {v}" for k,v in s.items())); return
    if action=="users":
        rows=await db.get_search_users(20,0); await callback.message.answer("👥 SEARCHING USERS\n\n"+("\n\n".join(f"{r['first_name'] or 'Unknown'} — {r['user_id']} — {r['total_searches']} searches" for r in rows) if rows else "No searching users yet.")); return
    if action=="recent":
        rows=await db.recent_searches(20); await callback.message.answer("🕒 RECENT SEARCHES\n\n"+("\n".join(f"{r['created_at']} — {r['query']} — {'✅' if r['success'] else '❌'}" for r in rows) if rows else "No searches yet.")); return
    if action=="admins": await callback.message.answer("Use /addadmin <numeric Telegram ID> or /removeadmin <numeric Telegram ID>. Only the owner may change admins."); return
    if action=="forcesub":
        rows=await db.list_force_sub(); status=await db.get_setting("force_sub_enabled","0"); lines=[f"Global force-sub: {'ON' if status=='1' else 'OFF'}"]+[f"{r['channel_id']} — {r['display_name']} — {'enabled' if r['enabled'] else 'disabled'}" for r in rows]
        await callback.message.answer("📢 FORCE SUB SETTINGS\n\n"+"\n".join(lines)+"\n\nCommands: /forcesub_add, /forcesub_remove, /forcesub_on, /forcesub_off"); return
    if action=="welcome": await state.set_state(AdminStates.welcome); await callback.message.answer("Send the new welcome text. Use {first_name} for personalization, or /cancel.",reply_markup=cancel_keyboard()); return
    if action=="startimage": await state.update_data(setting_key="start_image"); await state.set_state(AdminStates.setting); await callback.message.answer("Send an image URL or 'none' to remove it.",reply_markup=cancel_keyboard()); return
    if action=="settings": await callback.message.answer("Editable settings: welcome text and start image are available in this MVP. Force-subscription is managed with /forcesub_* commands."); return
    if action in {"links","posts","posters","aliases","delete","restore","userbyid"}: await callback.message.answer(f"This panel entry is available; use the documented commands or /cancel. Detailed guided workflow for '{action}' is a documented MVP limitation."); return
    if action=="backup":
        path=Path(tempfile.gettempdir())/f"aniflix-backup-{callback.from_user.id}.db"; await BackupService(db).create_backup(str(path)); await callback.message.answer_document(FSInputFile(path),caption="SQLite backup created from the current database."); path.unlink(missing_ok=True); return
@router.message(AddAnimeStates.title)
async def add_title(message: Message,state: FSMContext,db):
    if not message.text or not message.text.strip(): await message.answer("Add Anime input cannot be empty."); return
    try:
        title, download_url, color, post_text = parse_add_anime_input(message.text)
        anime_id = await AnimeService(db).create(title, description=post_text)
        await db.add_link(anime_id, title, "external", download_url, display_order=0)
        await db.add_link(anime_id, "🔥 Ongoing Animes", "external", ONGOING_ANIMES_URL, display_order=1)
        await state.clear()
        await message.answer(f"✅ Added {title}. Download button and 🔥 Ongoing Animes button saved below it.\nRequested button color: {color}. Telegram inline buttons do not support custom colors, so the color was not applied.")
    except ValueError as exc: await message.answer(f"Could not add anime: {exc}")
@router.message(AddAnimeStates.aliases)
async def add_aliases(message: Message,state: FSMContext,db):
    data=await state.get_data(); aliases=[] if message.text.casefold().strip()=="skip" else [x.strip() for x in message.text.split(",") if x.strip()]
    try: anime_id=await AnimeService(db).create(data["title"],aliases); await state.clear(); await message.answer(f"✅ Added {data['title']} (ID {anime_id}). You can add links with /addlink {anime_id}.")
    except ValueError as exc: await message.answer(f"Could not add anime: {exc}")
@router.message(AdminStates.welcome)
async def set_welcome(message: Message,state: FSMContext,db):
    await db.set_setting("welcome_message",message.text or ""); await state.clear(); await message.answer("✅ Welcome message updated.")
@router.message(AdminStates.setting)
async def set_setting(message: Message,state: FSMContext,db):
    data=await state.get_data(); value="" if (message.text or "").strip().casefold()=="none" else (message.text or "").strip()
    await db.set_setting(data.get("setting_key","start_image"),value); await state.clear(); await message.answer("✅ Setting updated.")
@router.message(Command("addadmin"))
async def addadmin(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id: await message.answer("⛔ Owner only."); return
    parts=(message.text or "").split();
    if len(parts)!=2 or not parts[1].isdigit(): await message.answer("Usage: /addadmin TELEGRAM_USER_ID"); return
    await db.add_admin(int(parts[1]),message.from_user.id); await db.log_audit(message.from_user.id,"add_admin",parts[1]); await message.answer("✅ Admin added.")
@router.message(Command("removeadmin"))
async def removeadmin(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id: await message.answer("⛔ Owner only."); return
    parts=(message.text or "").split();
    if len(parts)!=2 or not parts[1].isdigit(): await message.answer("Usage: /removeadmin TELEGRAM_USER_ID"); return
    ok=await db.remove_admin(int(parts[1]),settings.owner_id); await message.answer("✅ Admin removed." if ok else "Admin not found or cannot remove owner.")
@router.message(Command("forcesub_add"))
async def forcesub_add(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id: await message.answer("⛔ Owner only."); return
    parts=(message.text or "").split(maxsplit=3)
    if len(parts)<3: await message.answer("Usage: /forcesub_add CHANNEL_ID DISPLAY_NAME [INVITE_URL]"); return
    channel_id,name=parts[1],parts[2]; invite=parts[3] if len(parts)>3 else None
    if invite and not valid_url(invite): await message.answer("Invite URL must be http(s)."); return
    kind="private" if invite else "public"; await db.upsert_force_sub(channel_id,name,channel_type=kind,invite_url=invite,username=name if name.startswith("@") else None); await db.set_setting("force_sub_enabled","1"); await message.answer("✅ Force-sub channel saved and enabled.")
@router.message(Command("forcesub_remove"))
async def forcesub_remove(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id: await message.answer("⛔ Owner only."); return
    p=(message.text or "").split();
    if len(p)!=2: await message.answer("Usage: /forcesub_remove CHANNEL_ID"); return
    await message.answer("✅ Removed." if await db.remove_force_sub(p[1]) else "Channel not found.")
@router.message(Command("forcesub_on"))
async def forcesub_on(message: Message,db,settings):
    if message.from_user.id==settings.owner_id: await db.set_setting("force_sub_enabled","1"); await message.answer("✅ Global force-subscription enabled.")
    else: await message.answer("⛔ Owner only.")
@router.message(Command("forcesub_off"))
async def forcesub_off(message: Message,db,settings):
    if message.from_user.id==settings.owner_id: await db.set_setting("force_sub_enabled","0"); await message.answer("✅ Global force-subscription disabled.")
    else: await message.answer("⛔ Owner only.")
