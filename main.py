
import os
import re
import logging
import random
from difflib import SequenceMatcher

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------
# LOGGING
# ---------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
DB_FILE = os.getenv("DB_FILE", "anime_db.txt")

BOT_TITLE = "ANIFLIX ANIME INDEX BOT TAMIL"

ONGOING_URL = "https://t.me/+cVwTJSgsEvUzNWZl"
SUPPORT_URL = "https://t.me/+4YfcdPoSdK9jYmVl"

anime_database = []


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------
def load_database():
    global anime_database
    anime_database = []

    if not os.path.exists(DB_FILE):
        logger.warning("Database file not found: %s", DB_FILE)
        return

    with open(DB_FILE, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            match = re.match(
                r"^(.*?)\s*(?:-\s*|\|\s*|,\s*)(https?://\S+)\s*$",
                line,
            )
            if match:
                title = match.group(1).strip()
                link = match.group(2).strip()
                if title and link:
                    anime_database.append({
                        "title": title,
                        "link": link,
                    })

    logger.info("Loaded %s anime entries", len(anime_database))


def save_anime_to_db(title: str, link: str):
    title = title.strip()
    link = link.strip()

    if not title or not re.match(r"^https?://\S+$", link):
        return False

    # Avoid duplicate titles with the same link
    for item in anime_database:
        if (
            item["title"].casefold() == title.casefold()
            and item["link"] == link
        ):
            return False

    anime_database.append({"title": title, "link": link})

    with open(DB_FILE, "a", encoding="utf-8") as file:
        file.write(f"{title} - {link}\n")

    return True


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS or (
        OWNER_ID != 0 and user_id == OWNER_ID
    )


# ---------------------------------------------------------
# SMART ANIME SEARCH
# ---------------------------------------------------------
def clean_query(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def find_anime(query: str):
    clean_q = clean_query(query)
    if not clean_q:
        return None

    # Exact match first
    for item in anime_database:
        if clean_q == clean_query(item["title"]):
            return item

    # Then substring match
    for item in anime_database:
        clean_title = clean_query(item["title"])
        if clean_q in clean_title or clean_title in clean_q:
            return item

    # Finally, fuzzy matching for spelling mistakes
    best_match = None
    best_ratio = 0.65

    for item in anime_database:
        clean_title = clean_query(item["title"])
        ratio = SequenceMatcher(
            None, clean_q, clean_title
        ).ratio()

        if ratio > best_ratio:
            best_ratio = ratio
            best_match = item

    return best_match


def get_random_suggestions(count=3):
    if not anime_database:
        return []

    return random.sample(
        anime_database,
        min(count, len(anime_database)),
    )


# ---------------------------------------------------------
# WELCOME MESSAGE
# ---------------------------------------------------------
async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    welcome_text = (
        "🍥 WELCOME TO ANIFLIX ANIME INDEX BOT TAMIL 🍥\n\n"
        "🎌 Just type the anime you want to search! 🔎\n\n"
        "📥 Find your favourite anime episodes easily.\n"
        "Type /list to get the Anime List\n\n"
        "💚 Powered By: @ANIFLIXANIMETAMIL"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "📺 ONGOING ANIME",
                url=ONGOING_URL,
            ),
            InlineKeyboardButton(
                "💬 Support Group",
                url=SUPPORT_URL,
            ),
        ]
    ]

    await update.effective_message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------
# ANIME LIST WITH PAGINATION
# ---------------------------------------------------------
async def send_list_page(message, page=0, edit=False):
    page_size = 10
    total = len(anime_database)

    if total == 0:
        text = (
            "❌ No Anime Found!\n\n"
            "🔎 Please check your spelling and try again.\n\n"
            "📚 Type /list to get the Anime List.\n\n"
            "💚 Powered By: @ANIFLIXANIMETAMIL"
        )
        if edit:
            await message.edit_text(text)
        else:
            await message.reply_text(text)
        return

    total_pages = (total + page_size - 1) // page_size
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    current_items = anime_database[start:start + page_size]

    text = (
        f"📚 ANIFLIX ANIME INDEX BOT TAMIL\n"
        f"Anime List - Page {page + 1}/{total_pages}\n\n"
    )

    for index, item in enumerate(current_items, start=start + 1):
        text += f"{index}. {item['title']}\n"

    buttons = []
    nav_row = []

    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                "⬅️ Prev",
                callback_data=f"browse_list_{page - 1}",
            )
        )

    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"browse_list_{page + 1}",
            )
        )

    if nav_row:
        buttons.append(nav_row)

    markup = InlineKeyboardMarkup(buttons) if buttons else None

    if edit:
        await message.edit_text(
            text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    else:
        await message.reply_text(
            text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )


async def list_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await send_list_page(update.effective_message, 0)


# ---------------------------------------------------------
# ADD ANIME - ADMINS ONLY
# Usage: /add Anime Name - https://t.me/link
# ---------------------------------------------------------
async def add_anime_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user or not is_admin(user.id):
        await update.effective_message.reply_text(
            "⛔ You do not have permission to add anime."
        )
        return

    raw_args = " ".join(context.args)

    match = re.match(
        r"^(.*?)\s+-\s+(https?://\S+)$",
        raw_args,
    )

    if not match:
        await update.effective_message.reply_text(
            "⚠️ Invalid format.\n"
            "Usage: /add Anime Name - https://t.me/link"
        )
        return

    title, link = match.group(1).strip(), match.group(2).strip()

    if save_anime_to_db(title, link):
        await update.effective_message.reply_text(
            f"✅ Anime added successfully!\n\n"
            f"🍥 {title}\n"
            f"🔗 {link}",
            disable_web_page_preview=True,
        )
    else:
        await update.effective_message.reply_text(
            "⚠️ This anime entry already exists, or the link is invalid."
        )


# ---------------------------------------------------------
# TXT IMPORT - ADMINS ONLY
# Format: Anime Name - https://t.me/link
# ---------------------------------------------------------
async def import_txt_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    message = update.effective_message
    document = message.document if message else None

    if not user or not is_admin(user.id):
        return

    if not document or not (document.file_name or "").lower().endswith(".txt"):
        return

    if document.file_size and document.file_size > 2 * 1024 * 1024:
        await message.reply_text("❌ TXT file must be smaller than 2 MB.")
        return

    status = await message.reply_text("📥 Importing anime links...")

    path = f"temp_anime_{user.id}.txt"

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        await telegram_file.download_to_drive(path)

        added = 0
        skipped = 0

        with open(path, "r", encoding="utf-8-sig", errors="replace") as file:
            for line in file:
                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                match = re.match(
                    r"^(.*?)\s*(?:-\s*|\|\s*|,\s*)(https?://\S+)\s*$",
                    line,
                )

                if not match:
                    skipped += 1
                    continue

                title = match.group(1).strip()
                link = match.group(2).strip()

                if save_anime_to_db(title, link):
                    added += 1
                else:
                    skipped += 1

        await status.edit_text(
            f"✅ TXT import complete!\n\n"
            f"🍥 Added: {added}\n"
            f"⚠️ Skipped: {skipped}"
        )

    except Exception:
        logger.exception("TXT import failed")
        await status.edit_text(
            "❌ Could not import the TXT file. Check the file format and logs."
        )

    finally:
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------
# UNIVERSAL SEARCH
# All group members, admins, owner, and private users
# ---------------------------------------------------------
async def handle_search_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message or not message.text:
        return

    query = message.text.strip()

    if not query or query.startswith("/") or len(query) > 100:
        return

    try:
        result = find_anime(query)

        if result:
            # Existing search result format from the supplied code
            response_text = (
                f"🎬 *{BOT_TITLE}*\n\n"
                f"📌 *Anime:* `{result['title']}`\n"
                f"🔗 *Watch / Download Link:*\n{result['link']}"
            )

            keyboard = [[
                InlineKeyboardButton(
                    "📥 Get Anime",
                    url=result["link"],
                )
            ]]

            await message.reply_text(
                response_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
            )

        else:
            suggestions = get_random_suggestions(count=3)

            keyboard = [
                [
                    InlineKeyboardButton(
                        f"🍥 {item['title']}",
                        url=item["link"],
                    )
                ]
                for item in suggestions
            ]

            not_found_text = (
                "❌ No Anime Found!\n\n"
                "🔎 Please check your spelling and try again.\n\n"
                "📚 Type /list to get the Anime List.\n\n"
                "💚 Powered By: @ANIFLIXANIMETAMIL"
            )

            if suggestions:
                not_found_text += "\n\n🎲 You may also like:"

            await message.reply_text(
                not_found_text,
                reply_markup=(
                    InlineKeyboardMarkup(keyboard)
                    if keyboard else None
                ),
                disable_web_page_preview=True,
            )

    except Exception:
        logger.exception(
            "Search failed in chat %s for user %s",
            message.chat_id,
            update.effective_user.id if update.effective_user else "unknown",
        )


# ---------------------------------------------------------
# INLINE BUTTON HANDLER
# ---------------------------------------------------------
async def callback_query_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data or ""

    if data.startswith("browse_list_"):
        try:
            page = int(data.rsplit("_", 1)[1])
            await send_list_page(query.message, page, edit=True)
        except (ValueError, AttributeError):
            logger.exception("Invalid list pagination callback")


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is missing from environment variables.")
        return

    load_database()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("add", add_anime_command))

    # TXT uploads are handled only in private chat.
    app.add_handler(
        MessageHandler(
            filters.Document.FileExtension("txt")
            & filters.ChatType.PRIVATE,
            import_txt_handler,
        )
    )

    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # IMPORTANT: Search all non-command text in groups and private chats.
    # Do not add an admin-only filter to this handler.
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & (~filters.COMMAND)
            & (filters.ChatType.GROUPS | filters.ChatType.PRIVATE),
            handle_search_text,
        )
    )

    if WEBHOOK_URL:
        logger.info("Starting webhook server on port %s", PORT)

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{BOT_TOKEN}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Starting polling mode")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
