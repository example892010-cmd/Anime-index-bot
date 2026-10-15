from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from app.permissions import require_admin, require_callback_admin
from html import escape

router = Router()
PAGE_SIZE = 5

def format_user(r):
    username = "@" + r["username"] if r["username"] else "—"
    return (f"👤 Name: {escape(r['first_name'] or 'Unknown')}\n🔗 Username: {username}\n"
            f"🆔 User ID: {r['user_id']}\n🔎 Total Searches: {r['total_searches']}\n"
            f"✅ Successful Searches: {r['successful_searches']}\n❌ Unsuccessful Searches: {r['unsuccessful_searches']}\n"
            f"🎬 Last Search: {escape(r['last_searched_title'] or '—')}\n🕒 Last Search At: {r['last_search_at'] or '—'}")

def users_markup(page, total):
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"userspage:{page-1}"))
    if (page + 1) * PAGE_SIZE < total:
        buttons.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"userspage:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None

async def send_users(message, db, page=0):
    total = await db.count_search_users()
    page = max(0, min(page, max(0, (total - 1) // PAGE_SIZE)))
    rows = await db.get_search_users(PAGE_SIZE, page * PAGE_SIZE)
    body = "\n\n".join(format_user(r) for r in rows)
    await message.answer(f"👥 ANIME SEARCH USERS\nPage {page+1} | Total: {total}\n\n{body or 'No user has submitted an anime search yet.'}", reply_markup=users_markup(page, total))

@router.message(Command("users"))
async def users(message: Message, db, settings):
    if await require_admin(message, db, settings.owner_id):
        await send_users(message, db)

@router.callback_query(lambda c: c.data and c.data.startswith("userspage:"))
async def users_page(callback: CallbackQuery, db, settings):
    if not await require_callback_admin(callback, db, settings.owner_id):
        return
    try:
        page = max(0, int(callback.data.split(":", 1)[1]))
    except ValueError:
        await callback.answer("Invalid page", show_alert=True)
        return
    await callback.answer()
    await send_users(callback.message, db, page)

@router.message(Command("userbyid"))
async def user_by_id(message: Message, db, settings):
    if not await require_admin(message, db, settings.owner_id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /userbyid TELEGRAM_USER_ID")
        return
    row = await db.get_user(int(parts[1]))
    if not row or not row["total_searches"]:
        await message.answer("No anime-search history found for that user.")
        return
    await message.answer(format_user(row))

@router.message(Command("userstats"))
async def userstats(message: Message, db, settings):
    if not await require_admin(message, db, settings.owner_id):
        return
    stats = await db.stats()
    keys = ("registered_users", "searching_users", "total_searches", "successful_searches", "unsuccessful_searches", "anime", "aliases", "links", "force_sub", "admins")
    await message.answer("📊 SEARCH STATISTICS\n\n" + "\n".join(f"{k.replace('_', ' ').title()}: {stats[k]}" for k in keys))

@router.message(Command("recentsearches"))
async def recent(message: Message, db, settings):
    if not await require_admin(message, db, settings.owner_id):
        return
    rows = await db.recent_searches(20)
    body = "\n".join(f"{r['created_at']} — {escape(r['first_name'] or 'Unknown')} ({r['user_id']}) — {escape(r['query'])} — {'✅' if r['success'] else '❌'}" for r in rows)
    await message.answer("🕒 RECENT SEARCHES\n\n" + (body or "No searches yet."))
