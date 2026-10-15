from __future__ import annotations
import tempfile
from pathlib import Path
from html import escape
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

@router.message(Command("admin"))
async def admin(message: Message, db, settings):
    if await require_admin(message,db,settings.owner_id):
        await message.answer("🛠️ ANIFLIX ADMIN PANEL",reply_markup=admin_keyboard())

@router.message(Command("cancel"))
async def cancel(message: Message, state):
    await state.clear()
    await message.answer("Cancelled. You can start a new operation.")

@router.callback_query(lambda c:c.data and c.data.startswith("admin:"))
async def admin_action(callback: CallbackQuery, state, db, settings):
    if not await require_callback_admin(callback,db,settings.owner_id): return
    action=callback.data.split(":",1)[1]
    await callback.answer()
    if action=="close":
        await callback.message.edit_text("Admin panel closed."); return
    if action=="add":
        await state.set_state(AddAnimeStates.title)
        await callback.message.answer("Enter the anime title, or /cancel.",reply_markup=cancel_keyboard()); return
    if action=="stats":
        s=await db.stats()
        await callback.message.answer("📊 STATISTICS\n\n"+"\n".join(f"{escape(k.replace('_',' ').title())}: {v}" for k,v in s.items())); return
    if action=="users":
        rows=await db.get_search_users(10,0); total=await db.count_search_users()
        await callback.message.answer(f"👥 SEARCHING USERS ({total} total)\n\n"+("\n\n".join(f"{escape(r['first_name'] or 'Unknown')} — {r['user_id']} — {r['total_searches']} searches" for r in rows) if rows else "No searching users yet.")+"\n\nUse /users for paginated results."); return
    if action=="recent":
        rows=await db.recent_searches(20)
        await callback.message.answer("🕒 RECENT SEARCHES\n\n"+("\n".join(f"{r['created_at']} — {escape(r['first_name'] or 'Unknown')} ({r['user_id']}) — {escape(r['query'])} — {'✅' if r['success'] else '❌'}" for r in rows) if rows else "No searches yet.")); return
    if action=="admins":
        if callback.from_user.id != settings.owner_id:
            await callback.message.answer("Only the owner can manage administrators."); return
        admins=await db.list_admins()
        await callback.message.answer("👥 ADMINISTRATORS\n\n"+("\n".join(str(r['user_id']) for r in admins) if admins else "No additional admins.")+"\n\nCommands: /addadmin USER_ID and /removeadmin USER_ID"); return
    if action=="forcesub":
        if callback.from_user.id != settings.owner_id:
            await callback.message.answer("Only the owner can change force-subscription settings."); return
        rows=await db.list_force_sub(); status=await db.get_setting("force_sub_enabled","0")
        lines=[f"Global force-sub: {'ON' if status=='1' else 'OFF'}"]
        lines += [f"{r['channel_id']} — {r['display_name']} — {'enabled' if r['enabled'] else 'disabled'} — {r['channel_type']}" for r in rows]
        await callback.message.answer("📢 FORCE SUB SETTINGS\n\n"+"\n".join(lines)+"\n\nCommands:\n/forcesub_add CHANNEL_ID DISPLAY_NAME [INVITE_URL]\n/forcesub_remove CHANNEL_ID\n/forcesub_on\n/forcesub_off\n/checkchannels"); return
    if action=="welcome":
        await state.set_state(AdminStates.welcome)
        await callback.message.answer("Send the new welcome text. Use {first_name} for personalization, or /cancel.",reply_markup=cancel_keyboard()); return
    if action=="startimage":
        await state.update_data(setting_key="start_image"); await state.set_state(AdminStates.setting)
        await callback.message.answer("Send an image URL or Telegram file_id, or type none to remove it.",reply_markup=cancel_keyboard()); return
    if action=="edit":
        await state.set_state(AdminStates.edit_id)
        await callback.message.answer("Enter the anime ID to edit.",reply_markup=cancel_keyboard()); return
    if action=="delete":
        await state.set_state(AdminStates.delete_id)
        await callback.message.answer("Enter the anime ID to delete. This permanently removes its aliases and links. Type /cancel to stop.",reply_markup=cancel_keyboard()); return
    if action=="links":
        await state.set_state(AdminStates.link_anime_id)
        await callback.message.answer("Enter the anime ID for the new link.",reply_markup=cancel_keyboard()); return
    if action=="aliases":
        await state.set_state(AdminStates.alias_anime_id)
        await callback.message.answer("Enter the anime ID to add an alias to.",reply_markup=cancel_keyboard()); return
    if action=="posters":
        await state.set_state(AdminStates.poster_anime_id)
        await callback.message.answer("Enter the anime ID whose poster you want to set.",reply_markup=cancel_keyboard()); return
    if action=="posts":
        await callback.message.answer("Use Manage Links to add Telegram channel/post URLs. Only actual supplied URLs are stored."); return
    if action=="userbyid":
        await callback.message.answer("Use /userbyid TELEGRAM_USER_ID to view a user's search record."); return
    if action=="restore":
        if callback.from_user.id != settings.owner_id:
            await callback.message.answer("Only the owner can restore the database."); return
        await state.set_state(AdminStates.restore_upload)
        await callback.message.answer("Send a SQLite backup as a document. It will be validated before restore. Current database will be backed up first. /cancel to stop.",reply_markup=cancel_keyboard()); return
    if action=="settings":
        await callback.message.answer("Runtime settings are stored in SQLite. Use Welcome Message and Start Image to edit these values. Force-subscription is managed in its own panel."); return
    if action=="backup":
        path=Path(tempfile.gettempdir())/f"aniflix-backup-{callback.from_user.id}.db"
        try:
            await BackupService(db).create_backup(str(path))
            await callback.message.answer_document(FSInputFile(path),caption="SQLite backup created.")
        finally:
            path.unlink(missing_ok=True)
        return

@router.message(AddAnimeStates.title)
async def add_title(message: Message,state):
    if not message.text or not message.text.strip():
        await message.answer("Title cannot be empty."); return
    await state.update_data(title=message.text.strip())
    await state.set_state(AddAnimeStates.aliases)
    await message.answer("Enter aliases separated by commas, or type skip.",reply_markup=cancel_keyboard())

@router.message(AddAnimeStates.aliases)
async def add_aliases(message: Message,state,db):
    if not message.text:
        await message.answer("Send comma-separated aliases or type skip."); return
    data=await state.get_data()
    aliases=[] if message.text.casefold().strip()=="skip" else [x.strip() for x in message.text.split(",") if x.strip()]
    try:
        anime_id=await AnimeService(db).create(data["title"],aliases)
        await db.log_audit(message.from_user.id,"add_anime",f"{anime_id}:{data['title']}")
        await state.clear()
        await message.answer(f"✅ Added {escape(data['title'])} (ID {anime_id}). Add a link from /admin → Manage Links.")
    except ValueError as exc:
        await message.answer(f"Could not add anime: {escape(str(exc))}")

@router.message(AdminStates.delete_id)
async def delete_id(message: Message,state,db,settings):
    if not await require_admin(message,db,settings.owner_id): await state.clear(); return
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Please enter a numeric anime ID or /cancel."); return
    aid=int(message.text.strip()); row=await db.get_anime(aid)
    if not row:
        await message.answer("Active anime not found. Try another ID or /cancel."); return
    from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
    await state.clear()
    await message.answer(f"Confirm deletion of {escape(row['title'])} (ID {aid})?",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑️ Confirm Delete",callback_data=f"confirmdelete:{aid}"),InlineKeyboardButton(text="Cancel",callback_data="cancel")]]))

@router.callback_query(lambda c:c.data and c.data.startswith("confirmdelete:"))
async def confirm_delete(callback: CallbackQuery,db,settings):
    if not await require_callback_admin(callback,db,settings.owner_id): return
    try: aid=int(callback.data.split(":",1)[1])
    except ValueError: await callback.answer("Invalid ID",show_alert=True); return
    row=await db.get_anime(aid)
    ok=await db.delete_anime(aid)
    if ok: await db.log_audit(callback.from_user.id,"delete_anime",str(aid))
    await callback.answer("Deleted" if ok else "Not found",show_alert=True)
    await callback.message.answer(f"✅ Deleted {escape(row['title'])}." if ok and row else ("Anime deleted." if ok else "Anime not found."))

@router.message(AdminStates.link_anime_id)
async def link_anime_id(message: Message,state,db):
    if not message.text or not message.text.strip().isdigit(): await message.answer("Enter a numeric anime ID."); return
    aid=int(message.text.strip()); row=await db.get_anime(aid)
    if not row: await message.answer("Anime not found. Enter another ID or /cancel."); return
    await state.update_data(anime_id=aid); await state.set_state(AdminStates.link_title)
    await message.answer("Enter the button label for this link.",reply_markup=cancel_keyboard())

@router.message(AdminStates.link_title)
async def link_title(message: Message,state):
    if not message.text or not message.text.strip(): await message.answer("Label cannot be empty."); return
    await state.update_data(link_title=message.text.strip()[:60]); await state.set_state(AdminStates.link_url)
    await message.answer("Send the actual HTTP(S) URL for this link.",reply_markup=cancel_keyboard())

@router.message(AdminStates.link_url)
async def link_url(message: Message,state,db):
    data=await state.get_data(); url=(message.text or "").strip()
    if not valid_url(url): await message.answer("Invalid URL. Use a valid http:// or https:// link, or /cancel."); return
    try:
        lid=await LinkService(db).add(data["anime_id"],data["link_title"],"external",url)
        await db.log_audit(message.from_user.id,"add_link",str(lid))
        await state.clear(); await message.answer(f"✅ Link saved (ID {lid}).")
    except ValueError as exc: await message.answer(f"Could not save link: {escape(str(exc))}")

@router.message(AdminStates.alias_anime_id)
async def alias_id(message: Message,state,db):
    if not message.text or not message.text.strip().isdigit(): await message.answer("Enter a numeric anime ID."); return
    aid=int(message.text.strip())
    if not await db.get_anime(aid): await message.answer("Anime not found."); return
    await state.update_data(anime_id=aid); await state.set_state(AdminStates.alias_value)
    await message.answer("Enter the alias to add.",reply_markup=cancel_keyboard())

@router.message(AdminStates.alias_value)
async def alias_value(message: Message,state,db):
    data=await state.get_data(); alias=(message.text or "").strip()
    if not alias: await message.answer("Alias cannot be empty."); return
    ok=await db.add_alias(data["anime_id"],alias,normalize_title(alias))
    if ok:
        await db.log_audit(message.from_user.id,"add_alias",f"{data['anime_id']}:{alias}")
        await state.clear(); await message.answer("✅ Alias added.")
    else: await message.answer("Alias already exists or the anime ID is invalid. Try another alias or /cancel.")

@router.message(AdminStates.poster_anime_id)
async def poster_id(message: Message,state,db):
    if not message.text or not message.text.strip().isdigit(): await message.answer("Enter a numeric anime ID."); return
    aid=int(message.text.strip())
    if not await db.get_anime(aid): await message.answer("Anime not found."); return
    await state.update_data(anime_id=aid); await state.set_state(AdminStates.setting)
    await state.update_data(setting_key="poster_for_anime")
    await message.answer("Send a poster HTTP(S) URL or Telegram file_id, or type none to remove it.",reply_markup=cancel_keyboard())

@router.message(AdminStates.welcome)
async def set_welcome(message: Message,state,db):
    if not message.text: await message.answer("Please send the welcome text."); return
    await db.set_setting("welcome_message",message.text[:3500]); await state.clear(); await message.answer("✅ Welcome message updated.")

@router.message(AdminStates.setting)
async def set_setting(message: Message,state,db):
    data=await state.get_data()
    value="" if (message.text or "").strip().casefold()=="none" else (message.text or "").strip()
    key=data.get("setting_key","start_image")
    if key=="poster_for_anime":
        aid=data.get("anime_id")
        await db.update_anime(int(aid),poster=value)
        await db.log_audit(message.from_user.id,"set_poster",str(aid))
    else:
        await db.set_setting(key,value)
    await state.clear(); await message.answer("✅ Setting updated.")

@router.message(AdminStates.restore_upload)
async def restore_upload(message: Message,state,db,settings,bot):
    if message.from_user.id != settings.owner_id:
        await state.clear(); await message.answer("⛔ Owner only."); return
    if not message.document:
        await message.answer("Please send the SQLite backup as a document, or /cancel."); return
    if message.document.file_size and message.document.file_size > 50*1024*1024:
        await message.answer("Backup is too large (maximum 50 MB)."); return
    temp=Path(tempfile.gettempdir())/f"restore-{message.from_user.id}.db"
    current=Path(tempfile.gettempdir())/f"pre-restore-{message.from_user.id}.db"
    try:
        tgfile=await bot.get_file(message.document.file_id)
        await bot.download_file(tgfile.file_path,destination=temp)
        valid,reason=BackupService.validate(str(temp))
        if not valid:
            await message.answer(f"❌ Backup rejected: {escape(reason)}"); return
        await BackupService(db).create_backup(str(current))
        await BackupService(db).restore(str(temp))
        await db.log_audit(message.from_user.id,"restore_database","validated SQLite backup")
        await message.answer_document(FSInputFile(current),caption="Safety backup of the database before restore.")
        await message.answer("✅ Database restored and reopened.")
        await state.clear()
    except Exception as exc:
        await message.answer(f"❌ Restore failed: {escape(type(exc).__name__)}. The current database may need inspection.")
    finally:
        temp.unlink(missing_ok=True); current.unlink(missing_ok=True)

@router.message(Command("addadmin"))
async def addadmin(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id: await message.answer("⛔ Owner only."); return
    parts=(message.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit() or int(parts[1])<=0: await message.answer("Usage: /addadmin TELEGRAM_USER_ID"); return
    await db.add_admin(int(parts[1]),message.from_user.id); await db.log_audit(message.from_user.id,"add_admin",parts[1]); await message.answer("✅ Admin added.")

@router.message(Command("removeadmin"))
async def removeadmin(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id: await message.answer("⛔ Owner only."); return
    parts=(message.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): await message.answer("Usage: /removeadmin TELEGRAM_USER_ID"); return
    ok=await db.remove_admin(int(parts[1]),settings.owner_id)
    if ok: await db.log_audit(message.from_user.id,"remove_admin",parts[1])
    await message.answer("✅ Admin removed." if ok else "Admin not found or cannot remove owner.")

@router.message(Command("forcesub_add"))
async def forcesub_add(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id: await message.answer("⛔ Owner only."); return
    parts=(message.text or "").split(maxsplit=3)
    if len(parts)<3: await message.answer("Usage: /forcesub_add CHANNEL_ID DISPLAY_NAME [INVITE_URL]"); return
    channel_id,name=parts[1],parts[2]; invite=parts[3] if len(parts)>3 else None
    if invite and not valid_url(invite): await message.answer("Invite URL must be http(s)."); return
    username=name if name.startswith("@") else None
    kind="private" if invite else "public"
    try:
        await db.upsert_force_sub(channel_id,name,channel_type=kind,invite_url=invite,username=username)
        await db.set_setting("force_sub_enabled","1")
        await db.log_audit(message.from_user.id,"forcesub_add",channel_id)
        await message.answer("✅ Force-sub channel saved and enabled. Ensure the bot has channel access/admin rights.")
    except Exception as exc: await message.answer(f"Could not save channel: {escape(type(exc).__name__)}")

@router.message(Command("forcesub_remove"))
async def forcesub_remove(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id: await message.answer("⛔ Owner only."); return
    p=(message.text or "").split()
    if len(p)!=2: await message.answer("Usage: /forcesub_remove CHANNEL_ID"); return
    ok=await db.remove_force_sub(p[1])
    if ok: await db.log_audit(message.from_user.id,"forcesub_remove",p[1])
    await message.answer("✅ Removed." if ok else "Channel not found.")

@router.message(Command("forcesub_on"))
async def forcesub_on(message: Message,db,settings):
    if message.from_user.id==settings.owner_id:
        await db.set_setting("force_sub_enabled","1"); await db.log_audit(message.from_user.id,"forcesub_on"); await message.answer("✅ Global force-subscription enabled.")
    else: await message.answer("⛔ Owner only.")

@router.message(Command("forcesub_off"))
async def forcesub_off(message: Message,db,settings):
    if message.from_user.id==settings.owner_id:
        await db.set_setting("force_sub_enabled","0"); await db.log_audit(message.from_user.id,"forcesub_off"); await message.answer("✅ Global force-subscription disabled.")
    else: await message.answer("⛔ Owner only.")


@router.message(Command("forcesub_enable"))
async def forcesub_enable(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id:
        await message.answer("⛔ Owner only."); return
    p=(message.text or "").split()
    if len(p)!=2:
        await message.answer("Usage: /forcesub_enable CHANNEL_ID"); return
    ok=await db.set_force_sub_channel_enabled(p[1],True)
    await message.answer("✅ Channel enabled immediately." if ok else "Channel not found.")

@router.message(Command("forcesub_disable"))
async def forcesub_disable(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id:
        await message.answer("⛔ Owner only."); return
    p=(message.text or "").split()
    if len(p)!=2:
        await message.answer("Usage: /forcesub_disable CHANNEL_ID"); return
    ok=await db.set_force_sub_channel_enabled(p[1],False)
    await message.answer("✅ Channel disabled immediately." if ok else "Channel not found.")

@router.message(Command("forcesub_invite"))
async def forcesub_invite(message: Message,db,settings):
    if message.from_user.id!=settings.owner_id:
        await message.answer("⛔ Owner only."); return
    p=(message.text or "").split(maxsplit=2)
    if len(p)!=3 or not valid_url(p[2]):
        await message.answer("Usage: /forcesub_invite CHANNEL_ID https://t.me/+INVITE"); return
    ok=await db.update_force_sub_invite(p[1],p[2])
    await message.answer("✅ Private invite link updated immediately." if ok else "Channel not found.")



@router.message(Command("addlink"))
async def addlink_command(message: Message, db, settings):
    if not await require_admin(message, db, settings.owner_id):
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) != 4 or not parts[1].isdigit():
        await message.answer("Usage: /addlink ANIME_ID LABEL URL")
        return
    try:
        link_id = await LinkService(db).add(int(parts[1]), parts[2], "external", parts[3])
        await db.log_audit(message.from_user.id, "add_link", str(link_id))
        await message.answer(f"✅ Link saved (ID {link_id}).")
    except ValueError as exc:
        await message.answer(f"Could not save link: {escape(str(exc))}")

@router.message(Command("links"))
async def list_links_command(message: Message,db,settings):
    if not await require_admin(message,db,settings.owner_id): return
    p=(message.text or "").split()
    if len(p)!=2 or not p[1].isdigit():
        await message.answer("Usage: /links ANIME_ID"); return
    rows=await db.list_links(int(p[1]))
    await message.answer("🔗 LINKS\\n\\n"+("\\n".join(f"ID {r['id']} | {'active' if r['active'] else 'disabled'} | {escape(r['display_title'])} | {escape(r['url'])}" for r in rows) if rows else "No links for that anime."))

@router.message(Command("addpost"))
async def add_post_link(message: Message,db,settings):
    if not await require_admin(message,db,settings.owner_id): return
    p=(message.text or "").split(maxsplit=3)
    if len(p)!=4 or not p[1].isdigit() or not valid_url(p[3]):
        await message.answer("Usage: /addpost ANIME_ID LABEL URL"); return
    try:
        lid=await LinkService(db).add(int(p[1]),p[2],"telegram_post",p[3])
        await db.log_audit(message.from_user.id,"add_telegram_post",str(lid))
        await message.answer(f"✅ Telegram post link saved (ID {lid}).")
    except ValueError as exc: await message.answer(f"Could not save link: {escape(str(exc))}")

@router.message(Command("deletelink"))
async def delete_link_command(message: Message,db,settings):
    if not await require_admin(message,db,settings.owner_id): return
    p=(message.text or "").split()
    if len(p)!=2 or not p[1].isdigit():
        await message.answer("Usage: /deletelink LINK_ID"); return
    ok=await db.delete_link(int(p[1]))
    if ok: await db.log_audit(message.from_user.id,"delete_link",p[1])
    await message.answer("✅ Link deleted." if ok else "Link not found.")

@router.message(Command("disablelink"))
async def disable_link_command(message: Message,db,settings):
    if not await require_admin(message,db,settings.owner_id): return
    p=(message.text or "").split()
    if len(p)!=2 or not p[1].isdigit():
        await message.answer("Usage: /disablelink LINK_ID"); return
    ok=await db.disable_link(int(p[1]))
    if ok: await db.log_audit(message.from_user.id,"disable_link",p[1])
    await message.answer("✅ Link disabled." if ok else "Link not found.")

@router.message(Command("deletealias"))
async def delete_alias_command(message: Message,db,settings):
    if not await require_admin(message,db,settings.owner_id): return
    p=(message.text or "").split()
    if len(p)!=2 or not p[1].isdigit():
        await message.answer("Usage: /deletealias ALIAS_ID"); return
    ok=await db.delete_alias(int(p[1]))
    if ok: await db.log_audit(message.from_user.id,"delete_alias",p[1])
    await message.answer("✅ Alias deleted." if ok else "Alias not found.")
