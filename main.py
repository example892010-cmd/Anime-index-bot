
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

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

if OWNER_ID != 0:
    ADMIN_IDS.add(OWNER_ID)

PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

DB_FILE = os.getenv("DB_FILE", "anime_db.txt")
BOT_TITLE = "ANIFLIX ANIME INDEX BOT TAMIL"

# Admin conversations are tracked per Telegram user.
ADMIN_ACTIONS = {}


# ---------------------------------------------------------
# Anime Database
# ---------------------------------------------------------
anime_database = []


def load_database():
    """Load anime titles and links from the database file."""
    global anime_database
    anime_database = []

    if not os.path.exists(DB_FILE):
        return

    with open(DB_FILE, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            parts = re.split(
                r"\s*[-|,]\s*(https?://\S+)",
                line,
                maxsplit=1,
            )

            if len(parts) >= 2:
                title = parts[0].strip()
                link = parts[1].strip()

                if title and link:
                    anime_database.append({
                        "title": title,
                        "link": link,
                    })


def save_anime_to_db(title: str, link: str):
    """Add an anime to memory and append it to the database file."""
    anime_database.append({
        "title": title,
        "link": link,
    })

    with open(DB_FILE, "a", encoding="utf-8") as file:
        file.write(f"{title} - {link}\n")


def remove_anime_from_db(title: str) -> bool:
    """Remove an anime by exact title and update the database file."""
    global anime_database

    target = clean_query(title)

    for index, item in enumerate(anime_database):
        if clean_query(item["title"]) == target:
            anime_database.pop(index)

            with open(DB_FILE, "w", encoding="utf-8") as file:
                for saved_item in anime_database:
                    file.write(
                        f"{saved_item['title']} - "
                        f"{saved_item['link']}\n"
                    )

            return True

    return False


def is_admin(user_id: int) -> bool:
    """Check whether a Telegram user is an authorized admin."""
    return user_id in ADMIN_IDS or user_id == OWNER_ID


# ---------------------------------------------------------
# Smart Search
# ---------------------------------------------------------
def clean_query(text: str) -> str:
    return re.sub(
        r"[^a-zA-Z0-9\s]",
        "",
        text,
    ).lower().strip()


def find_anime(query: str):
    """Find an exact, partial, or fuzzy anime title match."""
    clean_q = clean_query(query)

    if not clean_q:
        return None

    # Exact or partial match.
    for item in anime_database:
        clean_item = clean_query(item["title"])

        if clean_q == clean_item or clean_q in clean_item:
            return item

    # Fuzzy match.
    best_match = None
    best_ratio = 0.65

    for item in anime_database:
        clean_item = clean_query(item["title"])
        ratio = SequenceMatcher(
            None,
            clean_q,
            clean_item,
        ).ratio()

        if ratio > best_ratio:
            best_ratio = ratio
            best_match = item

    return best_match


def get_random_suggestions(count: int = 5):
    """Return random anime suggestions."""
    if not anime_database:
        return []

    return random.sample(
        anime_database,
        min(count, len(anime_database)),
    )


# ---------------------------------------------------------
# Admin Panel
# ---------------------------------------------------------
def admin_panel_markup():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ Add Anime",
                callback_data="admin_add",
            )
        ],
        [
            InlineKeyboardButton(
                "➖ Remove Anime",
                callback_data="admin_remove",
            )
        ],
        [
            InlineKeyboardButton(
                "📥 Import TXT",
                callback_data="admin_import",
            )
        ],
        [
            InlineKeyboardButton(
                "📚 View Anime List",
                callback_data="admin_list",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="admin_cancel",
            )
        ],
    ])


async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """Open the admin panel for authorized users."""
    user = update.effective_user
    message = update.effective_message

    if not user or not is_admin(user.id):
        await message.reply_text(
            "⛔ You don't have permission to open the admin panel."
        )
        return

    ADMIN_ACTIONS.pop(user.id, None)

    await message.reply_text(
        f"⚙️ {BOT_TITLE}\n\n"
        "Admin Panel\n\n"
        "Choose an action:",
        reply_markup=admin_panel_markup(),
    )


# ---------------------------------------------------------
# Start Command
# ---------------------------------------------------------
async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    welcome_text = (
        f"🍥 *WELCOME TO {BOT_TITLE}* 🍥\n\n"
        "🔎 Just type the anime name you want to search.\n"
        "Example: `Naruto`\n\n"
        "📚 Use /list to see all available anime.\n\n"
        "❗ If no anime is found, check the spelling and try again."
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Channel",
                url="https://t.me/",
            ),
            InlineKeyboardButton(
                "👥 Support Group",
                url="https://t.me/",
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
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------
# Anime List & Pagination
# ---------------------------------------------------------
async def list_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not anime_database:
        await update.effective_message.reply_text(
            f"📚 No anime added yet to *{BOT_TITLE}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await send_list_page(update.effective_message, 0)


async def send_list_page(message, page: int, edit: bool = False):
    page_size = 10
    total_pages = max(
        1,
        (len(anime_database) + page_size - 1) // page_size,
    )

    page = max(0, min(page, total_pages - 1))

    start_idx = page * page_size
    current_items = anime_database[
        start_idx:start_idx + page_size
    ]

    text = (
        f"📚 *{BOT_TITLE} | Available Anime "
        f"(Page {page + 1}/{total_pages})*\n\n"
    )

    for index, item in enumerate(
        current_items,
        start=start_idx + 1,
    ):
        text += f"{index}. [{item['title']}]({item['link']})\n"

    buttons = []
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

    if navigation:
        buttons.append(navigation)

    markup = InlineKeyboardMarkup(buttons) if buttons else None

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


# ---------------------------------------------------------
# Existing /add Command
# ---------------------------------------------------------
async def add_anime_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ You don't have permission to add anime."
        )
        return

    raw_args = " ".join(context.args)

    if " - " not in raw_args:
        await update.effective_message.reply_text(
            "⚠️ Invalid format.\n"
            "Usage: /add Anime Name - https://t.me/link"
        )
        return

    title, link = raw_args.split(" - ", 1)
    title = title.strip()
    link = link.strip()

    if not title or not re.match(r"^https?://\S+$", link):
        await update.effective_message.reply_text(
            "⚠️ Please provide an anime title and a valid http(s) link."
        )
        return

    save_anime_to_db(title, link)

    await update.effective_message.reply_text(
        f"✅ Anime Added Successfully!\n\n"
        f"📌 Title: {title}\n"
        f"🔗 {link}",
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------
# TXT Import
# ---------------------------------------------------------
async def import_txt_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    document = update.effective_message.document

    if not user or not is_admin(user.id):
        return

    if not document or not document.file_name:
        return

    if not document.file_name.lower().endswith(".txt"):
        await update.effective_message.reply_text(
            "⚠️ Please upload a .txt file."
        )
        return

    status_msg = await update.effective_message.reply_text(
        "📥 Importing anime list..."
    )

    file_obj = await context.bot.get_file(document.file_id)
    download_path = f"temp_{user.id}_{document.file_name}"

    try:
        await file_obj.download_to_drive(download_path)
        imported_count = 0

        with open(
            download_path,
            "r",
            encoding="utf-8",
            errors="ignore",
        ) as file:
            for line in file:
                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                parts = re.split(
                    r"\s*[-|,]\s*(https?://\S+)",
                    line,
                    maxsplit=1,
                )

                if len(parts) >= 2:
                    title = parts[0].strip()
                    link = parts[1].strip()

                    if title and link:
                        save_anime_to_db(title, link)
                        imported_count += 1

        await status_msg.edit_text(
            f"✅ Import complete!\n"
            f"📚 Added {imported_count} anime entries."
        )

    except Exception:
        logger.exception("TXT import failed")
        await status_msg.edit_text(
            "❌ Import failed. Please check the file and try again."
        )

    finally:
        if os.path.exists(download_path):
            os.remove(download_path)


# ---------------------------------------------------------
# Group & Private Search
# ---------------------------------------------------------
async def handle_search_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    msg = update.effective_message
    user = update.effective_user

    if not msg or not msg.text or not user:
        return

    query = msg.text.strip()

    # Process replies requested by the admin panel.
    action = ADMIN_ACTIONS.get(user.id)

    if action and is_admin(user.id) and msg.chat.type == ChatType.PRIVATE:
        if query.startswith("/"):
            return

        if action == "add":
            if " - " not in query:
                await msg.reply_text(
                    "⚠️ Invalid format.\n"
                    "Send: Anime Name - https://t.me/link"
                )
                return

            title, link = query.split(" - ", 1)
            title = title.strip()
            link = link.strip()

            if not title or not re.match(r"^https?://\S+$", link):
                await msg.reply_text(
                    "⚠️ Please send a valid title and link:\n"
                    "Anime Name - https://t.me/link"
                )
                return

            save_anime_to_db(title, link)
            ADMIN_ACTIONS.pop(user.id, None)

            await msg.reply_text(
                f"✅ Anime added successfully!\n"
                f"📌 Title: {title}\n"
                f"🔗 {link}",
                disable_web_page_preview=True,
            )
            return

        if action == "remove":
            removed = remove_anime_from_db(query)
            ADMIN_ACTIONS.pop(user.id, None)

            if removed:
                await msg.reply_text(
                    f"✅ Removed anime: {query}"
                )
            else:
                await msg.reply_text(
                    f"❌ No exact title match found for: {query}"
                )
            return

    # Ignore commands and overly long messages.
    if query.startswith("/") or len(query) > 100:
        return

    match = find_anime(query)

    if match:
        response_text = (
            f"🍥 *{BOT_TITLE}*\n\n"
            f"📌 *Anime:* `{match['title']}`\n"
            f"🔊 *Audio:* Tamil Audio\n"
            f"🎞️ *Quality:* 480p | 720p | 1080p\n\n"
            f"Click the button below to get the episodes.\n"
            f"🔗 {match['link']}"
        )

        keyboard = [[
            InlineKeyboardButton(
                "🟢 DOWNLOAD NOW",
                url=match["link"],
            )
        ]]

        await msg.reply_text(
            response_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )

    else:
        suggestions = get_random_suggestions(count=4)
        buttons = [
            [
                InlineKeyboardButton(
                    f"👉 {anime['title']}",
                    url=anime["link"],
                )
            ]
            for anime in suggestions
        ]

        not_found_text = (
            f"❌ *Anime Not Found:* `{query}`\n\n"
            "Please check the spelling and try again."
        )

        await msg.reply_text(
            not_found_text,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            parse_mode=ParseMode.MARKDOWN,
        )


# ---------------------------------------------------------
# Callback Query Handler
# ---------------------------------------------------------
async def callback_query_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data.startswith("admin_"):
        user = query.from_user

        if not is_admin(user.id):
            await query.message.reply_text(
                "⛔ You don't have permission to use the admin panel."
            )
            return

        if data == "admin_add":
            ADMIN_ACTIONS[user.id] = "add"
            await query.message.reply_text(
                "➕ Send the anime in this format:\n"
                "Anime Name - https://t.me/link\n\n"
                "Use /admin to cancel and reopen the panel."
            )
            return

        if data == "admin_remove":
            ADMIN_ACTIONS[user.id] = "remove"
            await query.message.reply_text(
                "➖ Send the exact anime title you want to remove.\n\n"
                "Use /admin to cancel."
            )
            return

        if data == "admin_import":
            ADMIN_ACTIONS.pop(user.id, None)
            await query.message.reply_text(
                "📥 Upload your .txt anime list in this private chat.\n"
                "Each line should be:\n"
                "Anime Name - https://t.me/link"
            )
            return

        if data == "admin_list":
            ADMIN_ACTIONS.pop(user.id, None)
            await send_list_page(query.message, 0)
            return

        if data == "admin_cancel":
            ADMIN_ACTIONS.pop(user.id, None)
            await query.message.reply_text(
                "Admin action cancelled."
            )
            return

    if data.startswith("browse_list_"):
        page = int(data.split("_")[-1])
        await send_list_page(query.message, page, edit=True)


# ---------------------------------------------------------
# Application Initialization
# ---------------------------------------------------------
def main():
    if not BOT_TOKEN:
        logger.error(
            "BOT_TOKEN is missing! Set it in Render environment variables."
        )
        return

    load_database()
    logger.info(
        "Loaded %s anime entries from %s.",
        len(anime_database),
        DB_FILE,
    )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("add", add_anime_command))

    # TXT import in private chat
    app.add_handler(
        MessageHandler(
            filters.Document.FileExtension("txt")
            & filters.ChatType.PRIVATE,
            import_txt_handler,
        )
    )

    # Admin panel and list pagination buttons
    app.add_handler(
        CallbackQueryHandler(callback_query_handler)
    )

    # Search for non-command text in groups and private chats
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
        app.run_polling(
            allowed_updates=Update.ALL_TYPES
        )


if __name__ == "__main__":
    main()
