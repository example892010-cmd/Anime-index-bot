from __future__ import annotations
import re
import tempfile
from pathlib import Path
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, FSInputFile
from app.keyboards import admin_keyboard, cancel_keyboard, manage_posts_keyboard, confirm_remove_poster_keyboard
from app.permissions import require_admin, require_callback_admin
from app.states import AddAnimeStates, AdminStates
from app.services.anime_service import AnimeService
from app.services.backup_service import BackupService
from app.services.link_service import LinkService
from app.utils import normalize_title, valid_url
router=Router()

ONGOING_ANIMES_URL = "https://t.me/+cVwTJSgsEvUzNWZl"
SUPPORTED_BUTTON_COLORS = {"green", "blue", "red"}

def parse_bulk_anime_input(value: str) -> tuple[list[tuple[str, str, str, str]], list[tuple[int, str]]]:
    valid, invalid = [], []
    for line_number, raw_line in enumerate(value.splitlines(), 1):
        if not raw_line.strip():
            continue
        parts = raw_line.split(" - ", 2)
        if len(parts) != 3:
            invalid.append((line_number, "expected title - URL - color and post text")); continue
        title, download_url, color_and_post = (part.strip() for part in parts)
        match = re.match(r"^(green|blue|red)(?:\s*-\s*|\s+)(.+)$", color_and_post, re.IGNORECASE)
        if not title or not valid_url(download_url) or not match:
            invalid.append((line_number, "invalid title, URL, color, or post text")); continue
        color, post_text = match.group(1).casefold(), match.group(2)
        if color not in SUPPORTED_BUTTON_COLORS or not post_text:
            invalid.append((line_number, "supported colors are green, blue, and red; post text is required")); continue
        valid.append((title, download_url, color, post_text))
    return valid, invalid

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
    if action=="add": await state.set_state(AddAnimeStates.title); await callback.message.answer("Send one or more anime entries, one per line:\n🍥 Title - https://download-url - green - complete post text\nSupported colors: green, blue, red. Blank lines are ignored; invalid lines are reported. Use /cancel to stop.",reply_markup=cancel_keyboard()); return
    if action=="posts": await callback.message.edit_text("📝 Manage Posts\n\nChoose the shared landscape poster action:", reply_markup=manage_posts_keyboard()); return
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
    if action in {"links","posters","aliases","delete","restore","userbyid"}: await callback.message.answer(f"This panel entry is available; use the documented commands or /cancel. Detailed guided workflow for '{action}' is a documented MVP limitation."); return
    if action=="backup":
        path=Path(tempfile.gettempdir())/f"aniflix-backup-{callback.from_user.id}.db"; await BackupService(db).create_backup(str(path)); await callback.message.answer_document(FSInputFile(path),caption="SQLite backup created from the current database."); path.unlink(missing_ok=True); return
@router.message(AddAnimeStates.title)
async def add_title(message: Message,state: FSMContext,db):
    if not message.text or not message.text.strip(): await message.answer("Bulk Anime input cannot be empty."); return
    entries, invalid = parse_bulk_anime_input(message.text)
    results=[]; added=duplicates=failures=0
    for title, download_url, color, post_text in entries:
        normalized=normalize_title(title)
        existing=await db.search_anime(normalized, limit=10)
        if any(row["normalized_title"] == normalized for row in existing):
            duplicates += 1; results.append(f"⏭️ {title} — Duplicate"); continue
        anime_id=None
        try:
            anime_id=await AnimeService(db).create(title, description=post_text)
            await db.add_link(anime_id, title, "external", download_url, button_color=color, display_order=0)
            await db.add_link(anime_id, "🔥 Ongoing Animes", "external", ONGOING_ANIMES_URL, button_color="green", display_order=1)
            added += 1; results.append(f"✅ {title} — Added successfully")
        except Exception as exc:
            failures += 1
            if anime_id is not None: await db.delete_anime(anime_id)
            results.append(f"⚠️ {title} — Save failed: {str(exc)[:120]}")
    for line_number, reason in invalid:
        results.append(f"❌ Line {line_number} — Invalid: {reason}")
    await state.clear()
    report=["✅ Bulk Adding Completed!", ""] + results + ["", f"📊 Total received: {len(entries)+len(invalid)}", f"✅ Added: {added}", f"⏭️ Duplicates: {duplicates}", f"❌ Invalid: {len(invalid)}", f"⚠️ Save failures: {failures}"]
    text="\n".join(report)
    for start in range(0, len(text), 3500): await message.answer(text[start:start+3500])

@router.callback_query(lambda c: c.data in {"posts:set", "posts:preview", "posts:remove", "posts:back", "posts:remove_confirm", "posts:cancel"})
async def manage_posts_action(callback: CallbackQuery, state: FSMContext, db, settings):
    if not await require_callback_admin(callback,db,settings.owner_id): return
    await callback.answer()
    action=callback.data
    if action == "posts:back": await callback.message.edit_text("🛠️ ANIFLIX ADMIN PANEL", reply_markup=admin_keyboard()); return
    if action == "posts:set":
        await state.set_state(AdminStates.shared_poster)
        await callback.message.answer("Send a landscape photo or an image document. Portrait images are rejected. Use /cancel to stop.", reply_markup=cancel_keyboard()); return
    if action == "posts:preview":
        image_id=await db.get_setting("shared_landscape_poster", "")
        if not image_id: await callback.message.answer("No shared landscape poster is configured."); return
        try: await callback.message.answer_photo(image_id, caption="Current shared landscape poster")
        except Exception: await callback.message.answer("The saved landscape poster is unavailable. Set it again.")
        return
    if action == "posts:remove":
        await callback.message.answer("Remove the shared landscape poster? Anime records and individual posters will not be affected.", reply_markup=confirm_remove_poster_keyboard()); return
    if action == "posts:remove_confirm":
        await db.set_setting("shared_landscape_poster", "")
        await callback.message.answer("✅ Shared landscape poster removed.", reply_markup=manage_posts_keyboard()); return
    await callback.message.answer("Removal cancelled.", reply_markup=manage_posts_keyboard())

@router.message(AdminStates.shared_poster)
async def save_shared_poster(message: Message, state: FSMContext, db):
    file_id=None
    if message.photo:
        photo=message.photo[-1]
        if photo.width <= photo.height: await message.answer("Please upload a landscape image wider than it is tall."); return
        file_id=photo.file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id=message.document.file_id
    else:
        await message.answer("Send a Telegram photo or an image document."); return
    await db.set_setting("shared_landscape_poster", file_id)
    await state.clear()
    await message.answer("✅ Shared landscape poster saved. It will be used for every anime search result.")
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
