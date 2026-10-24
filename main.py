import os
import re
import logging
import random
from difflib import SequenceMatcher
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Configuration & Environment Variables
# ---------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_IDS = set(
    [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
)
if OWNER_ID != 0:
    ADMIN_IDS.add(OWNER_ID)

PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # e.g., https://your-app.onrender.com

DB_FILE = os.getenv("DB_FILE", "anime_db.txt")
BOT_TITLE = "ANIFLIX ANIME INDEX BOT TAMIL"

# ---------------------------------------------------------
# In-Memory Anime Database Management
# ---------------------------------------------------------
# Storage format: List of dicts -> [{"title": "Naruto", "link": "https://t.me/..."}]
anime_database = []


def load_database():
    """Loads anime titles and links from the local DB file."""
    global anime_database
    anime_database = []
    if not os.path.exists(DB_FILE):
        return

    with open(DB_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Acceptable formats: Title - Link OR Title | Link OR Title,Link
            parts = re.split(r"\s*[-|,]\s*(https?://\S+)", line, maxsplit=1)
            if len(parts) >= 2:
                title = parts[0].strip()
                link = parts[1].strip()
                if title and link:
                    anime_database.append({"title": title, "link": link})
            elif " - " in line:
                title, link = line.split(" - ", 1)
                anime_database.append({"title": title.strip(), "link": link.strip()})


def save_anime_to_db(title: str, link: str):
    """Appends a new anime record to the storage file."""
    global anime_database
    anime_database.append({"title": title, "link": link})
    with open(DB_FILE, "a", encoding="utf-8") as f:
        f.write(f"{title} - {link}\n")


def is_admin(user_id: int) -> bool:
    """Checks if the user is owner or listed admin."""
    return user_id in ADMIN_IDS or user_id == OWNER_ID


# ---------------------------------------------------------
# Smart Search Algorithm
# ---------------------------------------------------------
def clean_query(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9\s]", "", text).lower().strip()


def find_anime(query: str):
    """
    Search anime in the database:
    1. Exact / Substring match.
    2. Fuzzy ratio matching (Smart search).
    """
    clean_q = clean_query(query)
    if not clean_q:
        return None

    # Step 1: Substring / Case-insensitive exact match
    for item in anime_database:
        clean_item = clean_query(item["title"])
        if clean_q == clean_item or clean_q in clean_item:
            return item

    # Step 2: Fuzzy match
    best_match = None
    best_ratio = 0.65  # Threshold
    for item in anime_database:
        clean_item = clean_query(item["title"])
        ratio = SequenceMatcher(None, clean_q, clean_item).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = item

    return best_match


def get_random_suggestions(count: int = 5):
    """Returns random anime suggestions for the fallback menu."""
    if not anime_database:
        return []
    sample_size = min(count, len(anime_database))
    return random.sample(anime_database, sample_size)


# ---------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler."""
    welcome_text = (
        f"👋 *Welcome to {BOT_TITLE}!*\n\n"
        "🔎 *How to Search:*\n"
        "Simply send the name of any anime directly in the chat or group (e.g., `Naruto`, `One Piece`).\n\n"
        "Use /list to see all available anime titles."
    )
    keyboard = [
        [
            InlineKeyboardButton("📢 Channel", url="https://t.me/"),
            InlineKeyboardButton("👥 Support Group", url="https://t.me/"),
        ],
        [InlineKeyboardButton("📜 Browse All Anime", callback_data="browse_list_0")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.effective_message.reply_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the indexed anime list with pagination buttons."""
    if not anime_database:
        await update.effective_message.reply_text(
            f"ℹ️ No anime records indexed yet in *{BOT_TITLE}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    page = 0
    await send_list_page(update.effective_message, page)


async def send_list_page(message, page: int, edit: bool = False):
    page_size = 10
    total_pages = (len(anime_database) + page_size - 1) // page_size
    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    end_idx = start_idx + page_size
    current_items = anime_database[start_idx:end_idx]

    text = f"📚 *{BOT_TITLE} - Available Anime (Page {page + 1}/{total_pages}):*\n\n"
    for i, item in enumerate(current_items, start=start_idx + 1):
        text += f"{i}. [{item['title']}]({item['link']})\n"

    buttons = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"browse_list_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"browse_list_{page + 1}"))
    if nav_row:
        buttons.append(nav_row)

    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    if edit:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    else:
        await message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )


async def add_anime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows admins to add single anime entries: /add Anime Name - Link"""
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ You do not have permission to add anime.")
        return

    raw_args = " ".join(context.args)
    if " - " not in raw_args:
        await update.effective_message.reply_text(
            "⚠️ Invalid format.\nUsage: `/add Anime Name - https://t.me/link`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    title, link = raw_args.split(" - ", 1)
    save_anime_to_db(title.strip(), link.strip())
    await update.effective_message.reply_text(
        f"✅ *Successfully Added:*\n*{title.strip()}*\n🔗 {link.strip()}",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------
# File Import Handler (.txt file upload)
# ---------------------------------------------------------
async def import_txt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles TXT file imports for admins."""
    if not is_admin(update.effective_user.id):
        return

    document = update.effective_message.document
    if not document.file_name.endswith(".txt"):
        return

    status_msg = await update.effective_message.reply_text("📥 Processing TXT anime file...")
    file_obj = await context.bot.get_file(document.file_id)
    download_path = f"temp_{document.file_name}"
    await file_obj.download_to_drive(download_path)

    imported_count = 0
    with open(download_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"\s*[-|,]\s*(https?://\S+)", line, maxsplit=1)
            if len(parts) >= 2:
                save_anime_to_db(parts[0].strip(), parts[1].strip())
                imported_count += 1
            elif " - " in line:
                t, l = line.split(" - ", 1)
                save_anime_to_db(t.strip(), l.strip())
                imported_count += 1

    if os.path.exists(download_path):
        os.remove(download_path)

    await status_msg.edit_text(
        f"✅ Import complete! Added *{imported_count}* anime entries to *{BOT_TITLE}*.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------
# Core Group & Private Search Handler
# ---------------------------------------------------------
async def handle_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggers automatically when any user, admin, or owner types text in:
    - Group chats
    - Supergroups
    - Private DMs
    """
    msg = update.effective_message
    if not msg or not msg.text:
        return

    query = msg.text.strip()

    # Ignore slash commands and very long text
    if query.startswith("/") or len(query) > 100:
        return

    match = find_anime(query)

    if match:
        response_text = (
            f"🎬 *{BOT_TITLE}*\n\n"
            f"📌 *Anime:* `{match['title']}`\n"
            f"🔗 *Watch / Download Link:*\n{match['link']}"
        )
        keyboard = [[InlineKeyboardButton("📥 Get Anime", url=match["link"])]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await msg.reply_text(
            response_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )
    else:
        # Fallback suggestions when anime is not found
        suggestions = get_random_suggestions(count=4)
        buttons = []
        for anime in suggestions:
            buttons.append([InlineKeyboardButton(f"👉 {anime['title']}", url=anime["link"])])

        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        not_found_text = (
            f"❌ *Anime Not Found:* `{query}`\n\n"
            f"Please check your spelling. Here are some suggestions from *{BOT_TITLE}*:"
        )
        await msg.reply_text(
            not_found_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN,
        )


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles pagination buttons."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("browse_list_"):
        page = int(data.split("_")[-1])
        await send_list_page(query.message, page, edit=True)


# ---------------------------------------------------------
# Application Initialization & Setup
# ---------------------------------------------------------
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is missing! Provide it in your environment variables.")
        return

    load_database()
    logger.info(f"Loaded {len(anime_database)} anime entries from {DB_FILE}.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("add", add_anime_command))

    # TXT import handler
    app.add_handler(
        MessageHandler(filters.Document.FileExtension("txt") & filters.ChatType.PRIVATE, import_txt_handler)
    )

    # Callback queries for inline buttons
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # Universal search handler for group members, admins, owner, and private chats
    # Explicitly matches non-command text in all chat types
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & (~filters.COMMAND)
            & (filters.ChatType.GROUPS | filters.ChatType.PRIVATE),
            handle_search_text,
        )
    )

    # Webhook or Polling mode
    if WEBHOOK_URL:
        logger.info(f"Running via Webhook on port {PORT}...")
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
