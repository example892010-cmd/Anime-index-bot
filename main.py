import os
import re
import json
import logging
import random
from difflib import SequenceMatcher
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
if OWNER_ID:
    ADMIN_IDS.add(OWNER_ID)

PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
DB_FILE = os.getenv("DB_FILE", "anime_db.txt")
SETTINGS_FILE = os.getenv("SETTINGS_FILE", "anime_settings.json")
BOT_TITLE = "ANIFLIX ANIME INDEX BOT TAMIL"
MAX_BATCH_ENTRIES = 50

DEFAULT_POST_TEXT = (
    "🍥 *{bot_title}*\n\n"
    "📌 *Anime:* `{anime}`\n"
    "🔊 *Audio:* Tamil Audio\n"
    "🎞️ *Quality:* 480p | 720p | 1080p\n\n"
    "Click the button below to get the episodes.\n"
    "🔗 {link}"
)

anime_database = []
ADMIN_ACTIONS = {}
settings = {
    "post_text": DEFAULT_POST_TEXT,
    "global_poster": None,
    "anime_posters": {},
}


# ---------------- DATABASE & SETTINGS ----------------

def clean_query(value):
    return re.sub(r"[^a-zA-Z0-9\s]", "", value or "").lower().strip()


def is_admin(user_id):
    return user_id in ADMIN_IDS or user_id == OWNER_ID


def parse_anime_line(line):
    """Format: TITLE - URL - SEARCH NAME, or TITLE - URL."""
    match = re.match(
        r"^(.+?)\s+-\s+(https?://\S+?)(?:\s+-\s+(.+))?$",
        line.strip(),
    )
    if not match:
        return None

    title, link, search_name = match.groups()
    title = title.strip()
    link = link.strip().rstrip(".,")
    search_name = (search_name or title).strip()

    parsed = urlparse(link)
    if (
        not title
        or not search_name
        or parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or any(c in title for c in "\r\n")
        or any(c in search_name for c in "\r\n")
    ):
        return None

    return {
        "title": title,
        "link": link,
        "search_name": search_name,
    }


def save_database():
    folder = os.path.dirname(DB_FILE)
    if folder:
        os.makedirs(folder, exist_ok=True)

    temp_file = DB_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        for item in anime_database:
            file.write(
                f"{item['title']} - {item['link']} - "
                f"{item.get('search_name', item['title'])}\n"
            )
    os.replace(temp_file, DB_FILE)


def load_database():
    global anime_database
    anime_database = []

    if not os.path.exists(DB_FILE):
        return

    titles, links = set(), set()

    with open(DB_FILE, encoding="utf-8", errors="replace") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            item = parse_anime_line(line)
            if not item:
                continue

            title_key = clean_query(item["title"])
            link_key = item["link"].rstrip("/").lower()

            if title_key in titles or link_key in links:
                continue

            anime_database.append(item)
            titles.add(title_key)
            links.add(link_key)


def save_settings():
    folder = os.path.dirname(SETTINGS_FILE)
    if folder:
        os.makedirs(folder, exist_ok=True)

    temp_file = SETTINGS_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)

    os.replace(temp_file, SETTINGS_FILE)


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as file:
                saved = json.load(file)
            if isinstance(saved, dict):
                settings.update(saved)
        except (OSError, ValueError):
            logger.exception("Could not load settings")

    settings.setdefault("post_text", DEFAULT_POST_TEXT)
    settings.setdefault("global_poster", None)
    settings.setdefault("anime_posters", {})


# ---------------- ADD / REMOVE ANIME ----------------

def add_anime_lines(text):
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if len(lines) > MAX_BATCH_ENTRIES:
        return 0, [], [], True

    titles = {clean_query(x["title"]) for x in anime_database}
    links = {x["link"].rstrip("/").lower() for x in anime_database}

    added, invalid, duplicates = 0, [], []

    for line in lines:
        item = parse_anime_line(line)
        if not item:
            invalid.append(line)
            continue

        title_key = clean_query(item["title"])
        link_key = item["link"].rstrip("/").lower()

        if title_key in titles or link_key in links:
            duplicates.append(item["title"])
            continue

        anime_database.append(item)
        titles.add(title_key)
        links.add(link_key)
        added += 1

    if added:
        save_database()

    return added, invalid, duplicates, False


def remove_anime_from_db(title):
    target = clean_query(title)

    for index, item in enumerate(anime_database):
        if clean_query(item["title"]) == target:
            removed = anime_database.pop(index)
            save_database()

            settings.get("anime_posters", {}).pop(
                clean_query(removed["title"]), None
            )
            save_settings()
            return True

    return False


def find_anime(query):
    query = clean_query(query)
    if not query:
        return None

    for item in anime_database:
        title = clean_query(item["title"])
        search_name = clean_query(
            item.get("search_name", item["title"])
        )

        if (
            query == title
            or query == search_name
            or query in title
            or query in search_name
        ):
            return item

    best_match, best_score = None, 0.65

    for item in anime_database:
        score = max(
            SequenceMatcher(
                None, query, clean_query(item["title"])
            ).ratio(),
            SequenceMatcher(
                None,
                query,
                clean_query(item.get("search_name", item["title"])),
            ).ratio(),
        )

        if score > best_score:
            best_match, best_score = item, score

    return best_match


# ---------------- ADMIN PANEL ----------------

def admin_panel_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Anime (max 50)", callback_data="admin_add")],
        [InlineKeyboardButton("➖ Remove Anime", callback_data="admin_remove")],
        [InlineKeyboardButton("📥 Import TXT (max 50)", callback_data="admin_import")],
        [InlineKeyboardButton("🖼️ Poster Settings", callback_data="admin_posters")],
        [InlineKeyboardButton("📝 Post Text Editor", callback_data="admin_post_text")],
        [InlineKeyboardButton("📚 View Anime List", callback_data="admin_list")],
        [InlineKeyboardButton("❌ Cancel", callback_data="admin_cancel")],
    ])


def poster_panel_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 Separate Poster for an Anime", callback_data="poster_specific")],
        [InlineKeyboardButton("🌐 Same Poster for All Anime", callback_data="poster_global")],
        [InlineKeyboardButton("🗑️ Remove Global Poster", callback_data="poster_clear_global")],
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_back")],
    ])


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message

    if not user or not is_admin(user.id):
        await msg.reply_text("⛔ You don't have permission to open the admin panel.")
        return

    ADMIN_ACTIONS.pop(user.id, None)
    await msg.reply_text(
        f"⚙️ {BOT_TITLE}\n\nAdmin Panel\n\nChoose an action:",
        reply_markup=admin_panel_markup(),
    )


# ---------------- WELCOME ----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "╔════════════════════════════════════════╗\n"
        "   ✧ ✦ 𝐖𝐄𝐋𝐂𝐎𝐌𝐄 𝐓𝐎 𝐀𝐍𝐈𝐅𝐋𝐈𝐗 𝐁𝐎𝐓 ✦ ✧\n"
        "       『 ᴀɴɪᴍᴇ ɪɴᴅᴇx • ᴛᴀᴍɪʟ 』\n"
        "╚════════════════════════════════════════╝\n\n"
        "╭─► ⌕ Just type the Anime Name to search\n"
        "│   ‣ ᴇxᴀᴍᴘʟᴇ: Naruto\n"
        "│\n"
        "│ ‣ 📚 /list ➔ View full anime database\n"
        "│\n"
        "│ ‣ ⚠ If no anime is found, check the\n"
        "│   spelling and try again!\n"
        "│ POWERED BY : @ANIFLIXANIMETAMIL\n"
        "╰───────────────────────────────"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 ONGOING ANIME",
                url="https://t.me/+cVwTJSgsEvUzNWZl",
            ),
            InlineKeyboardButton(
                "👥 Support Group",
                url="https://t.me/+4YfcdPoSdK9jYmVl",
            ),
        ],
        [
            InlineKeyboardButton(
                "📜 Browse All Anime",
                callback_data="browse_list_0",
            )
        ],
    ]

    await update.effective_message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True,
    )


# ---------------- ANIME LIST ----------------

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anime_database:
        await update.effective_message.reply_text(
            f"📚 No anime added yet to {BOT_TITLE}."
        )
        return

    await send_list_page(update.effective_message, 0)


async def send_list_page(message, page, edit=False):
    page_size = 10
    total_pages = max(
        1, (len(anime_database) + page_size - 1) // page_size
    )

    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    entries = anime_database[start:start + page_size]

    text = (
        f"📚 *{BOT_TITLE} | Available Anime "
        f"(Page {page + 1}/{total_pages})*\n\n"
    )

    for number, item in enumerate(entries, start=start + 1):
        title = item["title"].replace("[", "\\[").replace("]", "\\]")
        text += f"{number}. [{title}]({item['link']})\n"

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f"browse_list_{page - 1}",
            )
        )

    if page < total_pages - 1:
        navigation.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"browse_list_{page + 1}",
            )
        )

    markup = InlineKeyboardMarkup([navigation]) if navigation else None

    if edit:
        await message.edit_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    else:
        await message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )


def add_result_text(added, invalid, duplicates):
    response = f"✅ Added {added} anime successfully."

    if invalid:
        response += f"\n⚠️ Skipped {len(invalid)} invalid line(s)."

    if duplicates:
        response += f"\n♻️ Skipped {len(duplicates)} duplicate title/link(s)."

    return response


# ---------------- ADD COMMAND ----------------

async def add_anime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message

    if not user or not is_admin(user.id):
        await msg.reply_text("⛔ You don't have permission to add anime.")
        return

    raw = msg.text.partition(" ")[2].strip()

    if not raw:
        ADMIN_ACTIONS[user.id] = "add"
        await msg.reply_text(
            "➕ Send up to 50 entries, one per line:\n"
            "NARUTO - https://example.com/naruto - naruto\n\n"
            "Use /admin to cancel."
        )
        return

    added, invalid, duplicates, too_many = add_anime_lines(raw)

    if too_many:
        await msg.reply_text(
            "⚠️ Maximum 50 anime entries per batch. "
            "Please split your list into smaller batches."
        )
        return

    await msg.reply_text(add_result_text(added, invalid, duplicates))


# ---------------- TXT IMPORT ----------------

async def import_txt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message
    document = msg.document

    if not user or not is_admin(user.id) or not document or not document.file_name:
        return

    if not document.file_name.lower().endswith(".txt"):
        await msg.reply_text("⚠️ Please upload a .txt file.")
        return

    status = await msg.reply_text("📥 Checking and importing anime list...")
    path = f"temp_{user.id}_{os.path.basename(document.file_name)}"

    try:
        tg_file = await context.bot.get_file(document.file_id)
        await tg_file.download_to_drive(path)

        with open(path, encoding="utf-8", errors="replace") as file:
            lines = [
                line.strip()
                for line in file
                if line.strip() and not line.strip().startswith("#")
            ]

        if len(lines) > MAX_BATCH_ENTRIES:
            await status.edit_text(
                "⚠️ This TXT has more than 50 entries. Nothing was imported. "
                "Split it into batches of 50 or fewer."
            )
            return

        added, invalid, duplicates, _ = add_anime_lines("\n".join(lines))
        await status.edit_text(
            f"✅ Import complete!\n"
            f"📚 Added {added} anime entries.\n"
            f"⚠️ Skipped {len(invalid)} invalid line(s).\n"
            f"♻️ Skipped {len(duplicates)} duplicate title/link(s)."
        )

    except Exception:
        logger.exception("TXT import failed")
        await status.edit_text(
            "❌ Import failed. Please check the file and try again."
        )

    finally:
        if os.path.exists(path):
            os.remove(path)


# ---------------- POST TEXT & POSTERS ----------------

def make_search_text(item):
    template = settings.get("post_text") or DEFAULT_POST_TEXT

    try:
        return template.format(
            anime=item["title"],
            link=item["link"],
            bot_title=BOT_TITLE,
        )
    except (KeyError, ValueError, IndexError):
        logger.warning("Invalid post text template; using default.")
        return DEFAULT_POST_TEXT.format(
            anime=item["title"],
            link=item["link"],
            bot_title=BOT_TITLE,
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message

    if (
        not user or not msg or not msg.photo
        or not is_admin(user.id)
        or msg.chat.type != ChatType.PRIVATE
    ):
        return

    action = ADMIN_ACTIONS.get(user.id)
    file_id = msg.photo[-1].file_id

    if action == "poster_global":
        settings["global_poster"] = file_id
        save_settings()
        ADMIN_ACTIONS.pop(user.id, None)
        await msg.reply_text(
            "✅ Global poster saved. It will be used for anime "
            "without a separate poster."
        )
        return

    if action == "poster_specific_upload":
        title = ADMIN_ACTIONS.get(f"{user.id}:poster_title", "")
        item = next(
            (
                x for x in anime_database
                if clean_query(x["title"]) == clean_query(title)
            ),
            None,
        )

        if not item:
            ADMIN_ACTIONS.pop(user.id, None)
            ADMIN_ACTIONS.pop(f"{user.id}:poster_title", None)
            await msg.reply_text(
                "❌ Anime title not found. Open /admin and try again."
            )
            return

        settings.setdefault("anime_posters", {})[
            clean_query(item["title"])
        ] = file_id

        save_settings()
        ADMIN_ACTIONS.pop(user.id, None)
        ADMIN_ACTIONS.pop(f"{user.id}:poster_title", None)

        await msg.reply_text(
            f"✅ Separate poster saved for {item['title']}."
        )


# ---------------- SEARCH ----------------

async def handle_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if not msg or not msg.text or not user:
        return

    query = msg.text.strip()
    action = ADMIN_ACTIONS.get(user.id)

    if action and is_admin(user.id) and msg.chat.type == ChatType.PRIVATE:
        if query == "/admin":
            ADMIN_ACTIONS.pop(user.id, None)
            return

        if query.startswith("/") and action != "post_text":
            return

        if action == "add":
            added, invalid, duplicates, too_many = add_anime_lines(query)

            if too_many:
                await msg.reply_text(
                    "⚠️ Maximum 50 anime entries per batch. "
                    "Split your list into smaller batches."
                )
                return

            if added:
                ADMIN_ACTIONS.pop(user.id, None)
                await msg.reply_text(
                    add_result_text(added, invalid, duplicates)
                )
            else:
                await msg.reply_text(
                    "⚠️ No new valid entries found.\n"
                    "Send up to 50 lines in this format:\n"
                    "NARUTO - https://example.com/naruto - naruto"
                )
            return

        if action == "remove":
            removed = remove_anime_from_db(query)
            ADMIN_ACTIONS.pop(user.id, None)

            await msg.reply_text(
                f"✅ Removed anime: {query}"
                if removed
                else f"❌ No exact title match found for: {query}"
            )
            return

        if action == "post_text":
            if query.lower() in ("/reset", "reset default"):
                settings["post_text"] = DEFAULT_POST_TEXT
                save_settings()
                ADMIN_ACTIONS.pop(user.id, None)
                await msg.reply_text("✅ Post text reset to the default template.")
                return

            if len(query) > 3500:
                await msg.reply_text("⚠️ Keep the post text under 3500 characters.")
                return

            if "{anime}" not in query:
                await msg.reply_text(
                    "⚠️ Please include {anime} so the anime title appears."
                )
                return

            settings["post_text"] = query
            save_settings()
            ADMIN_ACTIONS.pop(user.id, None)

            await msg.reply_text(
                "✅ Search post text saved!\n"
                "Placeholders: {anime}, {link}, {bot_title}."
            )
            return

        if action == "poster_specific_title":
            item = next(
                (
                    x for x in anime_database
                    if clean_query(x["title"]) == clean_query(query)
                ),
                None,
            )

            if not item:
                await msg.reply_text(
                    "❌ Exact anime title not found. Send the exact title "
                    "or use /admin to cancel."
                )
                return

            ADMIN_ACTIONS[f"{user.id}:poster_title"] = item["title"]
            ADMIN_ACTIONS[user.id] = "poster_specific_upload"

            await msg.reply_text(
                f"🖼️ Now send the poster photo for {item['title']}."
            )
            return

    if query.startswith("/") or len(query) > 100:
        return

    item = find_anime(query)

    if item:
        response_text = make_search_text(item)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🟢 DOWNLOAD NOW",
                url=item["link"],
            )
        ]])

        poster = (
            settings.get("anime_posters", {}).get(clean_query(item["title"]))
            or settings.get("global_poster")
        )

        if poster:
            try:
                await msg.reply_photo(
                    photo=poster,
                    caption=response_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            except Exception:
                logger.exception("Could not send poster; using text result.")

        await msg.reply_text(
            response_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )
        return

    not_found = (
        f"╭─► ✖ 𝐍𝐨𝐭 𝐅𝐨𝐮𝐧𝐝: {query}\n"
        "│\n"
        "│ ‣ Check the spelling and try again.\n"
        "│ ‣ Use /list to see all available anime.\n"
        "╰───────────────────────────────"
    )

    suggestions = random.sample(
        anime_database,
        min(4, len(anime_database)),
    ) if anime_database else []

    buttons = [
        [InlineKeyboardButton(f"👉 {x['title']}", url=x["link"])]
        for x in suggestions
    ]

    await msg.reply_text(
        not_found,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


# ---------------- CALLBACKS ----------------

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user = query.from_user

    if data.startswith("admin_") or data.startswith("poster_"):
        if not is_admin(user.id):
            await query.message.reply_text(
                "⛔ You don't have permission to use the admin panel."
            )
            return

        if data == "admin_add":
            ADMIN_ACTIONS[user.id] = "add"
            await query.message.reply_text(
                "➕ Send up to 50 entries, one per line:\n"
                "NARUTO - https://example.com/naruto - naruto\n\n"
                "Use /admin to cancel."
            )
            return

        if data == "admin_remove":
            ADMIN_ACTIONS[user.id] = "remove"
            await query.message.reply_text(
                "➖ Send the exact anime title you want to remove.\n"
                "Use /admin to cancel."
            )
            return

        if data == "admin_import":
            ADMIN_ACTIONS.pop(user.id, None)
            await query.message.reply_text(
                "📥 Upload a .txt file with up to 50 entries.\n"
                "NARUTO - https://example.com/naruto - naruto"
            )
            return

        if data == "admin_list":
            ADMIN_ACTIONS.pop(user.id, None)
            await send_list_page(query.message, 0)
            return

        if data == "admin_posters":
            ADMIN_ACTIONS.pop(user.id, None)
            await query.message.reply_text(
                "🖼️ Poster Settings\nChoose a poster option:",
                reply_markup=poster_panel_markup(),
            )
            return

        if data == "admin_post_text":
            ADMIN_ACTIONS[user.id] = "post_text"
            await query.message.reply_text(
                "📝 Send the message shown when someone searches for an anime.\n\n"
                "Placeholders:\n"
                "{anime} = anime title (required)\n"
                "{link} = download URL text\n"
                "{bot_title} = bot name\n\n"
                "Send /reset to restore the default. Use /admin to cancel."
            )
            return

        if data == "admin_back":
            ADMIN_ACTIONS.pop(user.id, None)
            await query.message.reply_text(
                "⚙️ Admin Panel",
                reply_markup=admin_panel_markup(),
            )
            return

        if data == "admin_cancel":
            ADMIN_ACTIONS.pop(user.id, None)
            ADMIN_ACTIONS.pop(f"{user.id}:poster_title", None)
            await query.message.reply_text("Admin action cancelled.")
            return

        if data == "poster_specific":
            ADMIN_ACTIONS[user.id] = "poster_specific_title"
            await query.message.reply_text(
                "🎴 Send the exact anime title for its separate poster.\n"
                "Use /admin to cancel."
            )
            return

        if data == "poster_global":
            ADMIN_ACTIONS[user.id] = "poster_global"
            await query.message.reply_text(
                "🌐 Send the poster photo to use for anime without a separate poster."
            )
            return

        if data == "poster_clear_global":
            settings["global_poster"] = None
            save_settings()
            await query.message.reply_text("✅ Global poster removed.")
            return

    if data.startswith("browse_list_"):
        try:
            page = int(data.rsplit("_", 1)[1])
        except ValueError:
            return

        await send_list_page(query.message, page, edit=True)


# ---------------- START BOT ----------------

def main():
    if not BOT_TOKEN:
        logger.error(
            "BOT_TOKEN is missing! Set it in Render environment variables."
        )
        return

    load_database()
    load_settings()

    logger.info(
        "Loaded %s anime entries from %s.",
        len(anime_database),
        DB_FILE,
    )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("add", add_anime_command))

    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE,
            handle_photo,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Document.FileExtension("txt") & filters.ChatType.PRIVATE,
            import_txt_handler,
        )
    )

    app.add_handler(CallbackQueryHandler(callback_query_handler))

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & (~filters.COMMAND)
            & (filters.ChatType.GROUPS | filters.ChatType.PRIVATE),
            handle_search_text,
        )
    )

    if WEBHOOK_URL:
        logger.info("Running via Webhook on port %s...", PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Running via Long Polling...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
