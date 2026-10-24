import os
import sqlite3
import logging
import threading
import asyncio
import io
import random
import difflib
import re
from datetime import datetime, timedelta, timezone

from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.error import TelegramError

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", "/var/data/anime.db")
DEFAULT_EXPIRY = int(os.getenv("DEFAULT_EXPIRY_MINUTES", "10"))

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("anime-channel-finder")

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db_lock = threading.RLock()
db.execute("PRAGMA foreign_keys=ON")
db.execute("""
    CREATE TABLE IF NOT EXISTS anime (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        poster_file_id TEXT,
        button_name TEXT,
        direct_url TEXT,
        search_name TEXT
    )
""")
# Migrate existing databases without deleting existing anime/posters/channels.
_existing_columns = {row[1] for row in db.execute("PRAGMA table_info(anime)").fetchall()}
for _column, _definition in (
    ("button_name", "TEXT"),
    ("direct_url", "TEXT"),
    ("search_name", "TEXT"),
):
    if _column not in _existing_columns:
        db.execute(f"ALTER TABLE anime ADD COLUMN {_column} {_definition}")
db.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anime_id INTEGER NOT NULL,
        chat_id TEXT NOT NULL,
        title TEXT NOT NULL,
        FOREIGN KEY(anime_id) REFERENCES anime(id) ON DELETE CASCADE
    )
""")
db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
db.execute(
    "INSERT OR IGNORE INTO settings(key,value) VALUES('expiry_minutes',?)",
    (str(DEFAULT_EXPIRY),)
)
db.execute(
    "INSERT OR IGNORE INTO settings(key,value) VALUES('post_text',?)",
    ("🎬 <b>{anime}</b>\n\n👇 Tap the button below to open the link:",)
)
db.commit()

app = Flask(__name__)
telegram_app = Application.builder().token(BOT_TOKEN).build()

# Add more names here if you want them to appear in the availability list.
ANIME_CATALOG = ["Black Clover", "Black Clover Tamil"]
ONGOING_ANIME_URL = "https://t.me/+cVwTJSgsEvUzNWZl"
SUPPORT_GROUP_URL = "https://t.me/+4YfcdPoSdK9jYmVl"


def is_admin(user_id):
    return user_id in ADMIN_IDS


def get_expiry():
    with db_lock:
        row = db.execute("SELECT value FROM settings WHERE key='expiry_minutes'").fetchone()
    return int(row[0]) if row else DEFAULT_EXPIRY


def set_expiry(minutes):
    with db_lock:
        db.execute(
            "INSERT INTO settings(key,value) VALUES('expiry_minutes',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(minutes),)
        )
        db.commit()


def get_setting(key, default=""):
    with db_lock:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key, value):
    with db_lock:
        db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )
        db.commit()


def get_global_poster():
    return get_setting("global_poster_file_id", "")


def get_poster_mode():
    return get_setting("poster_mode", "separate")


def get_post_text():
    return get_setting(
        "post_text",
        "🎬 <b>{anime}</b>\n\n👇 Tap the button below to open the link:"
    )


def render_post_text(anime, expiry=None):
    """Render the admin-configured message with supported placeholders."""
    template = get_post_text()
    replacements = {
        "{anime}": anime,
        "{expiry}": str(expiry if expiry is not None else get_expiry()),
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def normalize_search(value):
    """Normalize case and punctuation for forgiving anime-name matching."""
    value = (value or "").casefold().strip()
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())


def get_anime(name):
    """Find by display name or configured search name: exact match first, then smart substring/fuzzy."""
    target_raw = (name or "").strip()
    if not target_raw:
        return None

    with db_lock:
        # Step 1: Exact case-insensitive match on name or search_name
        row = db.execute(
            "SELECT id,name,poster_file_id,button_name,direct_url,search_name "
            "FROM anime WHERE lower(name)=lower(?) OR lower(COALESCE(search_name,''))=lower(?) "
            "ORDER BY id LIMIT 1",
            (target_raw, target_raw)
        ).fetchone()
        if row:
            return row

        # Step 2: Substring / Smart ratio match against SQLite records
        all_rows = db.execute(
            "SELECT id,name,poster_file_id,button_name,direct_url,search_name "
            "FROM anime ORDER BY lower(name)"
        ).fetchall()

    target_clean = normalize_search(target_raw)
    if not target_clean or not all_rows:
        return None

    best_match = None
    best_score = 0.0

    for r in all_rows:
        candidate_names = [r[1], r[5] or "", r[3] or ""]
        for cand in candidate_names:
            cand_clean = normalize_search(cand)
            if not cand_clean:
                continue
            # Exact clean match
            if target_clean == cand_clean:
                return r
            # Substring match
            if target_clean in cand_clean or cand_clean in target_clean:
                score = 0.85
            else:
                score = difflib.SequenceMatcher(None, target_clean, cand_clean).ratio()

            if score > best_score:
                best_score = score
                best_match = r

    if best_score >= 0.72:
        return best_match

    return None


def all_anime():
    with db_lock:
        return db.execute(
            "SELECT id,name,poster_file_id,button_name,direct_url,search_name "
            "FROM anime ORDER BY lower(name)"
        ).fetchall()


def channels_for(anime_id):
    with db_lock:
        return db.execute(
            "SELECT id,chat_id,title FROM channels WHERE anime_id=? ORDER BY id",
            (anime_id,)
        ).fetchall()


def add_anime(name):
    with db_lock:
        cur = db.execute("INSERT INTO anime(name) VALUES(?)", (name.strip(),))
        db.commit()
        return cur.lastrowid


def save_direct_anime(button_name, url, search_name):
    """Insert or update a direct-link anime entry without creating Telegram invites."""
    button_name = button_name.strip()
    url = url.strip()
    search_name = search_name.strip()
    with db_lock:
        row = db.execute(
            "SELECT id FROM anime WHERE lower(name)=lower(?) OR lower(COALESCE(search_name,''))=lower(?) LIMIT 1",
            (search_name, search_name)
        ).fetchone()
        if row:
            db.execute(
                "UPDATE anime SET name=?, button_name=?, direct_url=?, search_name=? WHERE id=?",
                (search_name, button_name, url, search_name, row[0])
            )
        else:
            db.execute(
                "INSERT INTO anime(name,button_name,direct_url,search_name) VALUES(?,?,?,?)",
                (search_name, button_name, url, search_name)
            )
        db.commit()


def remove_anime(identifier):
    """Remove an entry by anime/search name, button label, or exact saved URL."""
    identifier = identifier.strip()
    if not identifier:
        return False
    with db_lock:
        row = db.execute(
            "SELECT id FROM anime WHERE lower(name)=lower(?) "
            "OR lower(COALESCE(search_name,''))=lower(?) "
            "OR lower(COALESCE(button_name,''))=lower(?) "
            "OR lower(COALESCE(direct_url,''))=lower(?) LIMIT 1",
            (identifier, identifier, identifier, identifier)
        ).fetchone()
        if not row:
            return False
        db.execute("DELETE FROM channels WHERE anime_id=?", (row[0],))
        db.execute("DELETE FROM anime WHERE id=?", (row[0],))
        db.commit()
    return True


def add_channel(anime_id, chat_id, title):
    with db_lock:
        db.execute(
            "INSERT INTO channels(anime_id,chat_id,title) VALUES(?,?,?)",
            (anime_id, str(chat_id), title)
        )
        db.commit()


def set_poster(anime_id, file_id):
    with db_lock:
        db.execute("UPDATE anime SET poster_file_id=? WHERE id=?", (file_id, anime_id))
        db.commit()


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Anime", callback_data="admin:add"),
            InlineKeyboardButton("🗑️ Remove", callback_data="admin:remove"),
        ],
        [
            InlineKeyboardButton("🖼️ Set Poster", callback_data="admin:poster"),
            InlineKeyboardButton("📋 Anime List", callback_data="admin:list"),
        ],
        [InlineKeyboardButton("⏳ Expiry", callback_data="admin:expiry")],
        [InlineKeyboardButton("📝 Post Text", callback_data="admin:post_text")],
    ])


def availability_text():
    """Show each catalog title as available only when it has a configured channel."""
    lines = ["🎬 <b>Search Anime</b>\n"]
    names = list(ANIME_CATALOG)
    for row in all_anime():
        if not any(row[1].casefold() == n.casefold() for n in names):
            names.append(row[1])

    for name in names:
        row = get_anime(name)
        available = bool(row and (row[4] or channels_for(row[0])))
        lines.append(f"{name} {'✅' if available else '❌'}")

    lines.append("\n🔎 Send an anime name to get its channel link.")
    return "\n".join(lines)


def search_anime_suggestions(query, limit=3):
    """Return likely matches first, then random entries when no close match exists."""
    rows = all_anime()
    if not rows:
        return []
    target = normalize_search(query)
    scored = []
    for row in rows:
        names = [row[1], row[5] or "", row[3] or ""]
        best = max((difflib.SequenceMatcher(None, target, normalize_search(n)).ratio()
                    for n in names if n.strip()), default=0)
        if target and any(target in normalize_search(n) or normalize_search(n) in target
                          for n in names if n.strip()):
            best = max(best, 0.82)
        if best >= 0.48:
            scored.append((best, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return [row for _, row in scored[:limit]]
    return random.sample(rows, min(limit, len(rows)))


def suggestion_keyboard(rows):
    buttons = []
    for row in rows:
        label = (row[3] or row[1])[:60]
        buttons.append([InlineKeyboardButton(f"🎬 {label}", callback_data=f"suggest:{row[0]}")])
    return InlineKeyboardMarkup(buttons) if buttons else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🍥 WELCOME TO ANIFLIX ANIME INDEX BOT TAMIL 🍥\n\n"
        "🎌 Just type the anime you want to search! 🔎\n\n"
        "📥 Find your favourite anime episodes easily.\n"
        "Type /list to get the Anime List\n"
        "💚 Powered By: @ANIFLIXANIMETAMIL"
    )
    if is_admin(update.effective_user.id):
        text += "\n\n⚙️ Admin: use /admin"
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📺 ONGOING ANIME", url=ONGOING_ANIME_URL)],
        [InlineKeyboardButton("💬 Support Group", url=SUPPORT_GROUP_URL)],
    ])
    await update.effective_message.reply_text(text, reply_markup=markup)


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    await update.effective_message.reply_text(
        f"⚙️ <b>Admin Panel</b>\n\n⏳ Current expiry: <b>{get_expiry()} minutes</b>",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⚙️ <b>Admin commands</b>\n\n"
            "/admin — Open panel\n"
            "/add BUTTON - URL - anime name — Add one or many direct links\n"
            "/remove Anime — Remove anime\n"
            "/setexpiry 10 — Set expiry\n"
            "/setphoto Anime — Reply to a photo\n"
            "/list — List anime\n"
            "Upload a .txt file to import multiple anime entries.\n\n"
            "👤 Users can send an anime name to search.",
            parse_mode="HTML"
        )
    else:
        await start(update, context)


async def send_anime_list(chat_id, context):
    rows = all_anime()
    if not rows:
        await context.bot.send_message(chat_id, "📋 No anime added yet.")
        return
    lines = ["📋 <b>Anime List</b>\n"]
    for row in rows:
        chs = channels_for(row[0])
        direct = row[4] if len(row) > 4 else None
        button_name = row[3] if len(row) > 3 else None
        if direct:
            lines.append(f"🎬 <b>{row[1]}</b> — direct link ({button_name or row[1]})")
        else:
            lines.append(f"🎬 <b>{row[1]}</b> — {len(chs)} channel(s)")
        for ch in chs:
            lines.append(f" • {ch[2]}")
    await context.bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_anime_list(update.effective_chat.id, context)


async def add_entry(update, context, raw):
    """Accept one or many lines: BUTTON NAME - URL - anime search name."""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        await update.effective_message.reply_text(
            "Send one or more lines in this format:\n"
            "<code>NARUTO - https://example.com/naruto - naruto</code>",
            parse_mode="HTML"
        )
        return

    added = 0
    errors = []
    for line_no, line in enumerate(lines, start=1):
        parts = [part.strip() for part in line.split(" - ", 2)]
        if len(parts) != 3:
            errors.append(f"Line {line_no}: expected BUTTON NAME - URL - anime name")
            continue
        button_name, url, search_name = parts
        if not button_name or not search_name or not url.startswith(("https://", "http://")):
            errors.append(f"Line {line_no}: button name, anime name, and a valid http(s) URL are required")
            continue
        try:
            save_direct_anime(button_name, url, search_name)
            added += 1
        except sqlite3.Error:
            logger.exception("Could not save anime entry on line %s", line_no)
            errors.append(f"Line {line_no}: database error; entry was not saved")

    context.user_data.pop("state", None)
    response = [f"✅ Saved {added} anime entr{'y' if added == 1 else 'ies'}." if added else "❌ No anime entries were saved."]
    if errors:
        response.append("\n⚠️ Problems:\n" + "\n".join(errors[:20]))
        if len(errors) > 20:
            response.append(f"\n…and {len(errors) - 20} more error(s).")
    response.append("\nUsers can search using the anime name in the third field.")
    response.append("📂 TXT import format: BUTTON NAME - URL - anime name (one entry per line).")
    await update.effective_message.reply_text("\n".join(response))


async def import_anime_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only TXT importer; each line uses BUTTON NAME - URL - anime name."""
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Only admins can import anime files.")
        return
    document = update.effective_message.document
    filename = (document.file_name or "").lower()
    if not filename.endswith(".txt"):
        await update.effective_message.reply_text("Please upload a .txt file with one anime entry per line.")
        return
    if document.file_size and document.file_size > 2 * 1024 * 1024:
        await update.effective_message.reply_text("❌ TXT file is too large. Maximum size is 2 MB.")
        return
    buffer = io.BytesIO()
    telegram_file = await context.bot.get_file(document.file_id)
    await telegram_file.download_to_memory(out=buffer)
    try:
        contents = buffer.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        await update.effective_message.reply_text("❌ File must be UTF-8 text. Please save it as UTF-8 and upload again.")
        return
    await add_entry(update, context, contents)


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await add_entry(update, context, update.effective_message.text.partition(" ")[2].strip())


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    name = update.effective_message.text.partition(" ")[2].strip()
    if not name:
        await update.effective_message.reply_text("Usage: /remove Anime Name")
        return
    await update.effective_message.reply_text(
        "✅ Removed." if remove_anime(name) else "❌ Anime not found."
    )


async def setexpiry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    raw = update.effective_message.text.partition(" ")[2].strip()
    try:
        minutes = int(raw)
        if not 1 <= minutes <= 1440:
            raise ValueError
        set_expiry(minutes)
        await update.effective_message.reply_text(f"⏳ Expiry set to {minutes} minutes.")
    except ValueError:
        await update.effective_message.reply_text("Use a number from 1 to 1440.")


async def setphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    name = update.effective_message.text.partition(" ")[2].strip()
    reply = update.effective_message.reply_to_message
    if not name or not reply or not reply.photo:
        await update.effective_message.reply_text("Reply to a photo and use:\n/setphoto Anime Name")
        return
    row = get_anime(name)
    if not row:
        await update.effective_message.reply_text("❌ Anime not found.")
        return
    set_poster(row[0], reply.photo[-1].file_id)
    await update.effective_message.reply_text("🖼️ Separate poster saved for this anime.")


async def poster_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    if context.user_data.get("state") != "global_poster":
        return
    message = update.effective_message
    if not message or not message.photo:
        return
    set_setting("global_poster_file_id", message.photo[-1].file_id)
    set_setting("poster_mode", "global")
    context.user_data.pop("state", None)
    await message.reply_text(
        "✅ Same poster saved. It will be used for anime entries without their own separate poster."
    )


async def send_anime_links(update: Update, context: ContextTypes.DEFAULT_TYPE, search_override=None):
    name = (search_override or update.effective_message.text or "").strip()
    row = get_anime(name)
    if not row:
        suggestions = search_anime_suggestions(name, limit=3)
        markup = suggestion_keyboard(suggestions)
        message = (
            "❌ No Anime Found!\n\n"
            "🔎 Please check your spelling and try again.\n\n"
            "📚 Type /list to get the Anime List.\n\n"
            "💚 Powered By: @ANIFLIXANIMETAMIL"
        )
        if suggestions:
            message += "\n\n🎲 You may also like:"
        await update.effective_message.reply_text(message, reply_markup=markup)
        return

    # Direct-link entries use the URL provided by the admin. Legacy channel entries
    # continue to use expiring Telegram invite links.
    if row[4]:
        button_label = row[3] or row[1]
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(button_label, url=row[4])]])
        caption = render_post_text(row[1])
        poster_id = get_global_poster() if get_poster_mode() == "global" else row[2]
        if poster_id:
            try:
                await update.effective_message.reply_photo(
                    photo=poster_id, caption=caption, parse_mode="HTML", reply_markup=markup
                )
                return
            except TelegramError as e:
                logger.warning("Poster send failed: %s", e)
        await update.effective_message.reply_text(caption, parse_mode="HTML", reply_markup=markup)
        return

    chs = channels_for(row[0])
    if not chs:
        await update.effective_message.reply_text("❌ This anime is listed but has no link configured yet.")
        return

    expiry = get_expiry()
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=expiry)
    buttons = []
    for ch in chs:
        try:
            invite = await context.bot.create_chat_invite_link(
                chat_id=int(ch[1]),
                name=f"Anime Finder {expiry}m",
                expire_date=expire_at,
                creates_join_request=True
            )
            buttons.append([InlineKeyboardButton(f"🔗 {ch[2]}", url=invite.invite_link)])
        except TelegramError as e:
            logger.warning("Invite creation failed for %s (%s): %s", ch[2], ch[1], e)

    if not buttons:
        await update.effective_message.reply_text(
            "❌ I found the anime, but couldn't create its channel link. "
            "The bot needs administrator permission to manage invite links."
        )
        return

    caption = render_post_text(row[1], expiry=expiry)
    markup = InlineKeyboardMarkup(buttons)
    poster_id = get_global_poster() if get_poster_mode() == "global" else row[2]
    if poster_id:
        try:
            await update.effective_message.reply_photo(
                photo=poster_id, caption=caption, parse_mode="HTML", reply_markup=markup
            )
            return
        except TelegramError as e:
            logger.warning("Poster send failed: %s", e)
    await update.effective_message.reply_text(caption, parse_mode="HTML", reply_markup=markup)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data.startswith("suggest:"):
        try:
            anime_id = int(q.data.split(":", 1)[1])
        except (ValueError, IndexError):
            await q.answer("Invalid anime selection.", show_alert=True)
            return
        row = next((item for item in all_anime() if item[0] == anime_id), None)
        if not row:
            await q.message.reply_text("❌ This anime is no longer available. Type /list to browse anime.")
            return
        await send_anime_links(update, context, search_override=row[5] or row[1])
        return
    if not is_admin(q.from_user.id):
        await q.answer("Admin only.", show_alert=True)
        return

    if q.data == "admin:home":
        await q.edit_message_text(
            f"⚙️ <b>Admin Panel</b>\n\n⏳ Current expiry: <b>{get_expiry()} minutes</b>",
            parse_mode="HTML", reply_markup=admin_keyboard()
        )
    elif q.data == "admin:add":
        context.user_data["state"] = "add"
        await q.edit_message_text(
            "➕ <b>Add Anime</b>\n\nSend one or more lines in this format:\n"
            "<code>NARUTO - https://example.com/naruto - naruto</code>\n\n"
            "You can paste 4, 50, or more anime entries at once.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]])
        )
    elif q.data == "admin:remove":
        context.user_data["state"] = "remove"
        await q.edit_message_text(
            "🗑️ Send the anime name, button name, or exact saved link to remove.\n"
            "Example: naruto or https://example.com/naruto",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]])
        )
    elif q.data == "admin:poster":
        await q.edit_message_text(
            "🖼️ <b>Set Poster</b>\n\nChoose how you want to set posters:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🖼️ Separate Poster", callback_data="admin:poster_separate")],
                [InlineKeyboardButton("🌐 Same Poster", callback_data="admin:poster_global")],
                [InlineKeyboardButton("⬅️ Back", callback_data="admin:home")],
            ])
        )
    elif q.data == "admin:poster_separate":
        context.user_data.pop("state", None)
        set_setting("poster_mode", "separate")
        await q.edit_message_text(
            "🖼️ <b>Separate Poster</b>\n\nReply to a poster photo with:\n"
            "<code>/setphoto Anime Name</code>\n\n"
            "Example: reply to the Naruto poster with <code>/setphoto naruto</code>.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:poster")]])
        )
    elif q.data == "admin:poster_global":
        context.user_data["state"] = "global_poster"
        await q.edit_message_text(
            "🌐 <b>Same Poster</b>\n\nSend the poster photo you want to use for all anime entries. "
            "Anime with a separate poster will keep their own poster; other anime will use this same poster.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:poster")]])
        )
    elif q.data == "admin:post_text":
        context.user_data["state"] = "post_text"
        await q.edit_message_text(
            "📝 <b>Post Text</b>\n\nSend the message text to show when someone searches for an anime.\n"
            "Supported placeholders: <code>{anime}</code> = anime name, "
            "<code>{expiry}</code> = expiry minutes.\n\n"
            "Current text:\n" + get_post_text(),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]])
        )
    elif q.data == "admin:list":
        await send_anime_list(q.message.chat_id, context)
    elif q.data == "admin:expiry":
        context.user_data["state"] = "expiry"
        await q.edit_message_text(
            f"⏳ Current expiry: <b>{get_expiry()} minutes</b>\n\nSend a number from 1 to 1440.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin:home")]])
        )


async def group_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct group search for all group members, admins, and the bot owner."""
    user = update.effective_user
    message = update.effective_message
    if not user or user.is_bot or not message or not message.text:
        return

    text = message.text.strip()
    # Ignore commands or excessively long chat messages
    if text.startswith("/") or len(text) > 100:
        return

    await send_anime_links(update, context)


async def admin_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await send_anime_links(update, context)
        return
    state = context.user_data.get("state")
    if not state:
        await send_anime_links(update, context)
        return
    text = update.effective_message.text.strip()

    if state == "add":
        await add_entry(update, context, text)
    elif state == "remove":
        await update.effective_message.reply_text(
            "✅ Removed." if remove_anime(text) else "❌ Anime not found."
        )
        context.user_data.pop("state", None)
    elif state == "expiry":
        try:
            minutes = int(text)
            if not 1 <= minutes <= 1440:
                raise ValueError
            set_expiry(minutes)
            context.user_data.pop("state", None)
            await update.effective_message.reply_text(f"⏳ Expiry changed to {minutes} minutes.")
        except ValueError:
            await update.effective_message.reply_text("Enter a number from 1 to 1440.")
    elif state == "post_text":
        if not text:
            await update.effective_message.reply_text("Post text cannot be empty. Send the text again.")
            return
        set_setting("post_text", text)
        context.user_data.pop("state", None)
        await update.effective_message.reply_text(
            "✅ Post text saved. It will be used for future anime search results."
        )


@app.get("/")
def health():
    return jsonify({"status": "online", "service": "anime-channel-finder", "mode": "webhook"})


@app.post("/webhook")
def webhook():
    if WEBHOOK_SECRET:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if supplied != WEBHOOK_SECRET:
            return jsonify({"ok": False}), 403
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": True})
    update = Update.de_json(data, telegram_app.bot)
    future = asyncio.run_coroutine_threadsafe(telegram_app.process_update(update), bot_loop)
    try:
        future.result(timeout=30)
    except Exception:
        logger.exception("Failed to process Telegram webhook update")
        return jsonify({"ok": False}), 500
    return jsonify({"ok": True})


async def setup():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it to your service environment variables.")
    await telegram_app.initialize()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("admin", admin))
    telegram_app.add_handler(CommandHandler("help", help_cmd))
    telegram_app.add_handler(CommandHandler("list", list_cmd))
    telegram_app.add_handler(CommandHandler("add", add_cmd))
    telegram_app.add_handler(CommandHandler("remove", remove_cmd))
    telegram_app.add_handler(CommandHandler("setexpiry", setexpiry_cmd))
    telegram_app.add_handler(CommandHandler("setphoto", setphoto_cmd))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, poster_photo_handler))
    telegram_app.add_handler(MessageHandler(filters.Document.ALL, import_anime_file))
    
    # Direct group search for any member, admin, or owner
    telegram_app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
        group_text_router
    ))
    
    # Private chat text handler for admins & private searches
    telegram_app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        admin_text_router
    ))
    
    await telegram_app.start()
    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL is missing. Set it to your Render public URL.")
    
    # Explicitly allow all updates to ensure group messages are received
    await telegram_app.bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook",
        secret_token=WEBHOOK_SECRET or None,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )
    logger.info("Webhook set: %s/webhook with allowed_updates=ALL_TYPES", WEBHOOK_URL)


bot_loop = asyncio.new_event_loop()


def _run_bot_loop():
    asyncio.set_event_loop(bot_loop)
    bot_loop.run_forever()


bot_loop_thread = threading.Thread(target=_run_bot_loop, name="telegram-bot-loop", daemon=True)
bot_loop_thread.start()
asyncio.run_coroutine_threadsafe(setup(), bot_loop).result(timeout=60)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
